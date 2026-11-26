"""작업 합성의 고르기 balanced: 유형마다 난이도 단계를 고르게 채우고, 그 안에서는 덜 나온 물체 종류와 장면을 먼저 고른다.

distinct(무작위)로 고르면 가능한 상황이 많은 쪽으로 쏠린다. 서랍이 처음에 닫혀 있는 비율만큼 '먼저 여는' 작업이
나오고, 조합이 많은 흔한 물체(소켓, 볼트)가 자주 나온다. 여기서는 작업을 만들 때 이미 아는 것만으로 고른다(LLM 없음).

난이도 단계 = 목표 단언 수 + 먼저 열어야 하는 곳(옮길 물체가 든 닫힌 칸·설비, 닫힌 받는 곳) + 옮길 물체 위에 얹힌
물체 수 + 시간 조건 + 만족하는 경우가 여럿인지. 로봇이 지시에 없던 단계까지 해야 하는 만큼 어렵다고 본다.
"""

import heapq
import random
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from tsdgen.goals.scene_index import REGION_SEP, members
from tsdgen.tasks.base import RELATION, Task, Words
from tsdgen.tasks.compose import SELECTIONS, distinct
from tsdgen.tasks.view import OPEN_KEYS

INSIDE, ON_TOP = "in", "on"


@dataclass(frozen=True)
class Profile:
    """고를 때 보는 작업의 모습: 난이도 단계, 목표에 나오는 물체 종류, 바탕 장면."""

    level: int
    objects: tuple[str, ...]
    scene: str
    text: str = ""  # 지시문 (같은 지시문이 여러 상황에 나오면 덜 나온 것부터)


Candidate = tuple[dict, Task, Profile]  # 상황 하나가 나온 장면 하나: (바탕 장면, 작업, 모습)


def _shut(states: dict, open_keys: tuple[str, ...]) -> bool:
    values = [states[k] for k in open_keys if isinstance(states.get(k), bool)]
    return bool(values) and not values[0]


class SceneFacts:
    """장면 하나에서 난이도를 셀 때 보는 것: 물체 종류, 닿으려면 열어야 하는 곳, 위에 얹힌 물체 수."""

    def __init__(self, scene: dict, open_keys: tuple[str, ...]):
        objects = scene.get("objects") or []
        self.category = {o["id"]: o["category"] for o in objects}
        self.closed: set[str] = set()
        for fixture in scene.get("fixtures") or []:
            if _shut(fixture.get("states") or {}, open_keys):
                self.closed.add(fixture["id"])
            for region in fixture.get("regions") or []:
                if _shut(region.get("states") or {}, open_keys):
                    self.closed.add(f'{fixture["id"]}{REGION_SEP}{region["key"]}')
        self.container: dict[str, str] = {}  # 물체 -> 놓인 칸(닫혀 있으면) 또는 설비
        for obj in objects:
            placement = obj.get("placement") or {}
            fixture_id = placement.get("fixture_id")
            if fixture_id:
                region = f'{fixture_id}{REGION_SEP}{placement.get("region")}'
                self.container[obj["id"]] = region if region in self.closed else fixture_id
        self.covers = Counter(r["obj"] for r in scene.get("relations") or []
                              if r["pred"] == ON_TOP and r["obj"] in self.category)


def level_of(task: Task, facts: SceneFacts, level_max: int) -> int:
    case = task.cases[0]
    steps = len(case) + bool(task.duration) + (len(task.cases) > 1)
    for kind, subject, predicate, target, _value in case:
        if kind != RELATION:
            continue
        moved = (members(subject) or [None])[0]
        if moved in facts.category:
            steps += facts.container.get(moved) in facts.closed  # 먼저 열어야 꺼낼 수 있다
            steps += facts.covers[moved]  # 위에 얹힌 것을 먼저 치운다
        steps += predicate == INSIDE and target in facts.closed  # 먼저 열어야 넣을 수 있다
    # 지도가 있는 장면: 돌아가게 만드는 까닭 하나, 기다려야 하는 곳 하나가 한 단계씩 (상황 사실이 없는 작업은 0)
    situation = dict(task.facts)
    steps += len(situation.get("detour", ())) + len(situation.get("wait_for", ()))
    return min(steps, level_max)


def profile_of(base: dict, task: Task, facts: SceneFacts, level_max: int) -> Profile:
    refs = {m for _k, s, _p, t, _v in task.cases[0] for ref in (s, t) for m in members(ref)}
    objects = tuple(sorted({facts.category[r] for r in refs if r in facts.category}))
    return Profile(level_of(task, facts, level_max), objects, base["scenario_id"], task.instruction)


def even_split(total: int, available: dict[int, int]) -> dict[int, int]:
    """total을 단계마다 고르게 나눈다. 모자란 단계의 몫은 남은 단계가 나눠 갖는다."""
    quotas = dict.fromkeys(available, 0)
    left, open_levels = total, [k for k in sorted(available) if available[k]]
    while left and open_levels:
        share = max(1, left // len(open_levels))
        for level in list(open_levels):
            add = min(share, available[level] - quotas[level], left)
            quotas[level] += add
            left -= add
            if quotas[level] >= available[level]:
                open_levels.remove(level)
            if not left:
                break
    return quotas


class Usage:
    """지금까지 고른 것에 물체 종류와 장면이 몇 번 나왔는지. 적게 나온 것을 먼저 고른다."""

    def __init__(self, scene_cap: int | None):
        self.objects: Counter[str] = Counter()
        self.scenes: Counter[str] = Counter()
        self.texts: Counter[str] = Counter()
        self.scene_cap = scene_cap

    def score(self, entry: list[Candidate]) -> tuple:
        """상황 하나(장면 후보들)의 점수: 같은 지시문이 나온 수, 물체 종류가 나온 수의 최대, 그 합 순으로 적은 것부터.
        지시문이 같으면(물체를 이름으로 말하지 않는 '선반을 비워' 등) 상황이 달라도 작업으로는 겹쳐 보이므로 먼저 본다.
        장면은 겹쳐도 되므로 장면을 덜 쓴 것은 마지막 동점 처리에만 쓴다. 장면 상한(scene_cap)을 주면 후보 장면이
        모두 상한만큼 쓰인 상황은 맨 뒤로 미룬다(남은 것이 없을 때만 고른다)."""
        first = entry[0][2]
        counts = [self.objects[c] for c in first.objects]
        scene = min(self.scenes[c[2].scene] for c in entry)
        over = self.scene_cap is not None and scene >= self.scene_cap
        return over, self.texts[first.text], max(counts, default=0), sum(counts), scene

    def take(self, entry: list[Candidate]) -> Candidate:
        """상황 하나를 고른다: 후보 가운데 가장 덜 쓴 장면의 것."""
        chosen = min(entry, key=lambda c: self.scenes[c[2].scene])
        self.objects.update(chosen[2].objects)
        self.scenes[chosen[2].scene] += 1
        self.texts[chosen[2].text] += 1
        return chosen


def least_used(entries: list[list[Candidate]], quota: int, usage: Usage,
               rng: random.Random) -> list[Candidate]:
    """덜 나온 물체 종류·장면부터 quota개. 점수는 고를수록 오르기만 하므로 꺼낼 때 다시 재는 힙으로 충분하다."""
    heap = [(usage.score(entry), rng.random(), i) for i, entry in enumerate(entries)]
    heapq.heapify(heap)
    picked = []
    while heap and len(picked) < quota:
        score, tie, i = heapq.heappop(heap)
        now = usage.score(entries[i])
        if now != score:
            heapq.heappush(heap, (now, tie, i))
            continue
        picked.append(usage.take(entries[i]))
    return picked


def _entries(kept: dict) -> dict[str, list[list[tuple[dict, Task]]]]:
    """유형 -> 상황마다 장면 후보들 (signature 순서로 정렬해 같은 설정이면 같은 순서)."""
    grouped: dict[str, list[list[tuple[dict, Task]]]] = defaultdict(list)
    for signature in sorted(kept, key=repr):
        grouped[signature[0]].append(kept[signature])
    return grouped


def pick_balanced(kept: dict, made: dict, options: Any, seed: int,
                  words: Words) -> tuple[list, dict]:
    grouped = _entries(kept)
    rng, usage = random.Random(seed), Usage(options.scene_cap)
    open_keys = tuple(words.spec.get("open_keys") or OPEN_KEYS)
    facts: dict[str, SceneFacts] = {}

    def candidate(base: dict, task: Task) -> Candidate:
        if base["scenario_id"] not in facts:
            facts[base["scenario_id"]] = SceneFacts(base["scene_state"], open_keys)
        return base, task, profile_of(base, task, facts[base["scenario_id"]], options.level_max)

    chosen, counts = [], {}
    for type_id in sorted(grouped):
        situations = grouped[type_id]
        rng.shuffle(situations)
        levels: dict[int, list] = defaultdict(list)
        for found in situations[: options.per_type * options.pool_factor]:
            entry = [candidate(base, task) for base, task in found]
            levels[entry[0][2].level].append(entry)
        quotas = even_split(options.per_type, {k: len(v) for k, v in levels.items()})
        picked = [p for level in sorted(levels)
                  for p in least_used(levels[level], quotas[level], usage, rng)]
        chosen += [(base, task) for base, task, _p in picked]
        counts[type_id] = {"made": made[type_id], "situations": len(situations), "chosen": len(picked),
                           "levels": dict(sorted(Counter(p.level for _b, _t, p in picked).items()))}
    return chosen, counts


@SELECTIONS.register("balanced")
def select_balanced(found: Iterable[tuple[dict, Task]], options: Any, seed: int,
                    words: Words) -> tuple[list, dict]:
    """상황마다 그 상황이 나온 장면들에서 후보를 무작위로 남기고(candidates개), 난이도·물체·장면을 고르게 고른다."""
    kept, made = distinct(found, options.candidates, random.Random(seed))
    return pick_balanced(kept, made, options, seed, words)
