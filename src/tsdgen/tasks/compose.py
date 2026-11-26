"""작업 합성: 장면마다 작업 유형을 돌려 작업을 만들고, 서로 다른 상황만 골라 시나리오로 내놓는다.

같은 signature(유형, 대상의 종류, 받는 곳, 출발지와 그 여닫힘, 조건)는 배경만 다른 같은 상황이다.
그래서 signature마다 정해진 수(per_signature)까지만 고르고, 유형마다 고르는 수에도 상한을 둔다
(쉬운 유형이 수를 다 채우지 않게). 고르는 순서는 seed로 정해져서 같은 설정이면 같은 결과다.
"""

import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from typing import Any

from tsdgen.models import GoalAssertion, ScenarioRecord
from tsdgen.registry import Registry
from tsdgen.tasks.base import TASK_TYPES, Task, Words
from tsdgen.tasks.view import OPEN_KEYS, SceneView
from tsdgen.util import stable_hash

SPLIT = "composed"  # 작업을 합성한 시나리오의 split
DATASET_SOURCE = "task_type"
ORACLE_KEY = "goal_oracle"  # 시나리오 meta에 남기는 정답 (목표 상태의 경우들)
DURATION_KEY = "duration"
SITUATION_KEY = "situation"  # 설명문이 담아야 하는 상황 사실 (검수 항목 situation이 대조한다)

SELECTIONS = Registry("compose selection", axes=(lambda s: [c.selection for c in s.compose.values()],))
"""이름 -> (작업들, 설정, seed, 낱말) -> (고른 것, 유형별 수). 등록 이름이 config(compose.<도메인>.selection)의 허용값이다."""


def tasks_of(scene: dict, traits: dict, words: Words, types: list[str]) -> Iterator[Task]:
    spec = words.spec
    view = SceneView(scene, traits, spec.get("compartments", False), tuple(spec.get("open_keys") or OPEN_KEYS))
    for name in types:
        yield from TASK_TYPES.get(name)(view, words)


def distinct(found: Iterable[tuple[dict, Task]], per_signature: int,
             rng: random.Random | None = None) -> tuple[dict, dict]:
    """같은 상황(signature)은 per_signature개까지만 남긴다. rng가 없으면 signature마다 먼저 온 것을, 있으면 그 상황이
    나온 장면들 가운데 고르게 무작위로(저수지 표집) 남긴다. 돌려주는 것은 (signature -> 남긴 것들, 유형 -> 만든 작업 수)."""
    kept: dict[tuple, list[tuple[dict, Task]]] = defaultdict(list)
    made: dict[str, int] = defaultdict(int)
    seen: Counter = Counter()
    for base, task in found:
        made[task.type_id] += 1
        seen[task.signature] += 1
        if len(kept[task.signature]) < per_signature:
            kept[task.signature].append((base, task))
        elif rng is not None:
            slot = rng.randrange(seen[task.signature])
            if slot < per_signature:
                kept[task.signature][slot] = (base, task)
    return kept, made


def by_type(kept: dict) -> tuple[dict[str, list[tuple[dict, Task]]], Counter]:
    """남긴 것을 유형별로 (signature 순서로 정렬해 같은 설정이면 같은 순서), 유형별 서로 다른 signature 수."""
    grouped: dict[str, list[tuple[dict, Task]]] = defaultdict(list)
    for signature in sorted(kept, key=repr):
        grouped[signature[0]] += kept[signature]
    return grouped, Counter(signature[0] for signature in kept)


@SELECTIONS.register("distinct")
def select(found: Iterable[tuple[dict, Task]], options: Any, seed: int,
           words: Words | None = None) -> tuple[list, dict]:
    """서로 다른 상황만 고른다: signature마다 per_signature개, 유형마다 per_type개까지 무작위로.
    돌려주는 것은 (고른 것, 유형별 [만든 작업 수, 서로 다른 signature 수, 고른 수])."""
    kept, made = distinct(found, options.per_signature)
    grouped, signatures = by_type(kept)
    rng = random.Random(seed)
    chosen, counts = [], {}
    for type_id in sorted(grouped):
        items = grouped[type_id]
        rng.shuffle(items)
        chosen += items[: options.per_type]
        counts[type_id] = {"made": made[type_id], "situations": signatures[type_id],
                           "chosen": min(len(items), options.per_type)}
    return chosen, counts


def composed_oracle(scenario: dict) -> list[list[list[GoalAssertion]]] | None:
    """작업을 합성한 시나리오의 정답 대안들 (만들 때 정한 목표 상태, 경우 하나가 대안 하나). 아니면 None."""
    composed = (scenario.get("meta") or {}).get(ORACLE_KEY)
    if not composed:
        return None
    return [[[GoalAssertion.model_validate(a)] for a in case] for case in composed]


def situation_facts(facts: tuple) -> dict:
    """작업의 상황 사실((이름, 값) 묶음)을 JSON으로. 묶음 안의 (이름, 값) 묶음도 dict로 편다."""

    def plain(value):
        named_groups = isinstance(value, tuple) and value and all(
            isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str)
            and isinstance(v[1], tuple)
            for v in value
        )
        if named_groups:
            return {k: plain(v) for k, v in value}  # (이름, 묶음)들: 까닭별 통로
        return [plain(v) for v in value] if isinstance(value, tuple) else value

    return {name: plain(value) for name, value in facts}


def to_scenario(base: dict, task: Task, domain: str) -> ScenarioRecord:
    """바탕 장면에 합성한 작업을 얹은 시나리오. 장면은 그대로이고 지시와 정답이 새것이다."""
    source = {**base["source"], "task_name": task.type_id, "task_family": "composite",
              "activity": None, "split": SPLIT, "dataset_source": DATASET_SOURCE,
              "original_instruction_en": task.instruction}
    cases = [[GoalAssertion(kind=k, subject=s, predicate=p, object=o, value=v).model_dump()
              for k, s, p, o, v in case] for case in task.cases]
    meta = {ORACLE_KEY: cases, "base_scenario_id": base["scenario_id"]}
    if task.duration:
        meta[DURATION_KEY] = {"seconds": task.duration[0], "subject": task.duration[1]}
    if task.facts:
        meta[SITUATION_KEY] = situation_facts(task.facts)
    key = stable_hash([task.instruction, task.cases], length=8)
    return ScenarioRecord.model_validate({
        "scenario_id": f"{base['scenario_id']}--{task.type_id}-{key}",
        "source": source,
        "task_type_id": f"{domain}:{task.type_id}",
        "scene_state": base["scene_state"],
        "strata": {"family": "composite", "activity": task.type_id},
        "meta": meta,
    })
