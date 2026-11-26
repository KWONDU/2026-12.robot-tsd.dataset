"""모델에게 보여 주는 세계 상태(world state)의 표현. 등록 이름이 config 허용값이다 (contextualize.world_state).

시뮬레이터에서 뽑은 씬 상태(환경 컨텍스트)는 내부 정본으로 그대로 두고, LLM에게는 보기(view)가 만든 글만 준다.
재작성·지시 해소·생성·체크리스트 채점이 모두 이 글을 받는다(한 곳에서 만든다).

- json: 지금까지의 방식. 환경 컨텍스트 JSON과 entity id를 그대로 보인다. 이름표는 id 자신이다.
- text: 실제 로봇이 인식 결과를 자연어로 넘기는 상황에 맞춘 보기. id 없이 한국어 이름으로 쓰고, 같은 이름이
  여럿이면 '조리대 2'처럼 번호를 붙여 구별한다. 좌표는 싣지 않는 대신, 가구 배치가 없는 물체는 좌표로 바로 아래의
  물체를 찾아 '접시 1 위', '밀폐용기 안'처럼 공간 관계로 쓴다(인식기가 좌표보다 관계를 넘기는 것과 같은 모양).
  세계 상태는 장면에 있는 것을 태스크와 무관하게 전부 적는다. 데이터에 없는 정보는 '모름' 같은 말로 채우지
  않고 적지 않는다(놓인 곳이 없는 물체는 '그 밖의 물체'에 이름만, 상태가 없는 가구는 이름만).

모델은 설명문의 ref·subject·object에 이 이름표를 쓴다. 검수는 resolve()로 이름표를 id에 되짚은 뒤 기존 규칙
검사를 그대로 돌린다. 되짚지 못한 이름은 그대로 남아 접지 검사에서 씬에 없는 id로 잡힌다.
"""

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from tsdgen import world_map
from tsdgen.goals.scene_index import any_of
from tsdgen.models import region_id
from tsdgen.registry import Registry
from tsdgen.world_names import is_named, map_pointers, relabel, resolve

__all__ = ["is_named", "map_pointers", "relabel", "resolve"]  # 이름 되짚기는 world_names에 있다 (여기서도 쓴다)

WORLD_STATE_VIEWS = Registry("world state view", select=lambda s: s.contextualize.world_state)
"""이름 -> (환경 컨텍스트, 사전, contextualize 설정) -> RenderedWorld."""


@dataclass(frozen=True)
class RenderedWorld:
    text: str  # 모델에게 그대로 들어가는 글
    labels: dict[str, str]  # entity id -> 모델이 보는 이름


def render(settings: Any, env: dict, lexicon: dict) -> RenderedWorld:
    options = settings.contextualize
    return WORLD_STATE_VIEWS.get(options.world_state)(env, lexicon, options)


def model_input(inp: Any) -> str:
    """샘플·재작성 기록의 입력에서 모델이 받은 세계 상태 글. 이 보기가 생기기 전 기록은 JSON으로 되살린다."""
    data = inp if isinstance(inp, dict) else inp.model_dump()
    return data.get("world_state") or json.dumps(data["environment_context"], ensure_ascii=False,
                                                  indent=1)


# ---- 설명문의 가리키는 칸(ref, subject, object, alternatives)만 바꾸는 도구 ---------------- #

def place_names(env: dict, labels: dict[str, str], lexicon: dict) -> dict[str, str]:
    """세계 상태 글에 나온 가구 안의 칸 이름('냉장고 냉동실 선반', '오븐 랙') -> 그 가구 id.

    칸은 가구의 일부라 설명문이 칸 이름으로 가리켜도 그 가구를 가리킨 것이다. 이 장면의 글에 실제로 나온 칸만
    받는다(사전의 칸 이름과 가구 이름표를 아무렇게나 붙인 이름은 받지 않는다)."""
    fixtures = {f["id"]: f for f in env.get("fixtures") or []}
    found = {}
    for obj in env.get("objects") or []:
        placement = obj.get("placement") or {}
        fixture = fixtures.get(placement.get("fixture_id") or "")
        region = _region(fixture, placement.get("region"), lexicon) if fixture else None
        # 제 이름표가 있는 칸(목록에 적힌 칸)은 뺀다. 여기에 넣으면 번호 뗀 칸 이름이 가구로 되짚인다
        named = fixture and region_id(fixture["id"], placement.get("region") or "") in labels
        if region and not named and fixture["id"] in labels:
            found[f"{labels[fixture['id']]} {region['name']}"] = fixture["id"]
    # 같은 위치 설명을 나눠 가진 같은 이름의 가구들('조리대 4부터 6까지(아일랜드 식탁)')은 그 설명으로 묶어 가리킬 수
    # 있다: 설명이 그 가구들 아무 것의 이름이 된다
    described: dict[str, list[dict]] = defaultdict(list)
    for fixture in fixtures.values():
        if fixture.get("location_desc") and fixture["id"] in labels:
            described[fixture["location_desc"]].append(fixture)
    for desc, group in described.items():
        if len({_base_name(f) for f in group}) == 1:
            found.setdefault(desc, any_of([f["id"] for f in group]))
    return found


# ---- json 보기 ------------------------------------------------------------- #

@WORLD_STATE_VIEWS.register("json")
def json_view(env: dict, _lexicon: dict, _options: Any = None) -> RenderedWorld:
    ids = [e["id"] for e in [*env.get("fixtures", []), *env.get("objects", [])]]
    return RenderedWorld(json.dumps(env, ensure_ascii=False, indent=1), {i: i for i in ids})


# ---- text 보기 ------------------------------------------------------------- #

def _base_name(entity: dict) -> str:
    return entity.get("name_ko") or entity.get("category") or entity["id"]


def name_labels(env: dict, lexicon: dict | None = None) -> dict[str, str]:
    """entity id -> 이름. 같은 이름이 여럿이면 씬에 나온 순서대로 1부터 번호를 붙인다.
    사전을 주면 글에 목록으로 적는 가구의 칸도 '가구 이름 + 칸 이름'으로 이름을 갖는다(칸 id는 <가구 id>/<칸>)."""
    entities = [*env.get("fixtures", []), *env.get("objects", [])]
    counts = Counter(_base_name(e) for e in entities)
    seen: Counter[str] = Counter()
    labels = {}
    for entity in entities:
        name = _base_name(entity)
        seen[name] += 1
        labels[entity["id"]] = f"{name} {seen[name]}" if counts[name] > 1 else name
    for fixture in env.get("fixtures", []) if lexicon else []:
        for region in listed_regions(fixture, lexicon):
            labels[region_id(fixture["id"], region["key"])] = f"{labels[fixture['id']]} {region['name_ko']}"
    return labels


def listed_regions(fixture: dict, lexicon: dict) -> list[dict]:
    """세계 상태 글에 칸 목록으로 적는 칸들 (사전 region_ko의 listed)."""
    return [r for r in fixture.get("regions") or []
            if (_region(fixture, r["key"], lexicon) or {}).get("listed")]


def _state_words(states: dict, lexicon: dict) -> list[str]:
    phrases, keys = lexicon.get("state_phrases", {}), lexicon.get("state_key_ko", {})
    names = lexicon.get("category_names_ko", {})
    return [phrases.get(k, {}).get(v) or f"{keys.get(k, k)} {names.get(v, v)}"
            for k, v in states.items()]


def _ranges(numbers: list[int]) -> str:
    """[1, 2, 3, 4, 6, 7, 9] -> '1부터 4까지, 6, 7, 9'. 셋 넘게 이어질 때만 구간으로 줄인다."""
    parts, start = [], None
    values = sorted(numbers)
    for i, value in enumerate(values):
        start = value if start is None else start
        if i + 1 < len(values) and values[i + 1] == value + 1:
            continue
        run = list(range(start, value + 1))
        parts += [f"{start}부터 {value}까지"] if len(run) > 2 else [str(n) for n in run]
        start = None
    return ", ".join(parts)


def _numbered_run(labels: list[str]) -> str:
    """같은 이름에 번호만 다른 이름표들을 '서랍 1부터 18까지'나 '캐비닛 1부터 4까지, 6, 9'로 줄인다."""
    stems = {label.rsplit(" ", 1)[0] for label in labels}
    numbers = [label.rsplit(" ", 1)[-1] for label in labels]
    if len(labels) > 1 and len(stems) == 1 and all(n.isdigit() for n in numbers):
        return f"{stems.pop()} {_ranges([int(n) for n in numbers])}"
    return ", ".join(labels)


def _fixture_title(fixture: dict, labels: dict[str, str]) -> str:
    where = fixture.get("location_desc")
    return f"{labels[fixture['id']]}({where})" if where else labels[fixture["id"]]


def supports(env: dict, radius_m: float, height_m: float) -> dict[str, str]:
    """가구 배치가 없는 물체 id -> 그 바로 아래 물체 id. 좌표가 있을 때만, 수평 거리가 radius_m 이하이고
    높이가 0 이상 height_m 이하로 위에 있는 물체 중 가장 가까운 것."""
    posed = [o for o in env.get("objects") or [] if (o.get("pose") or {}).get("pos")]
    found = {}
    for obj in posed:
        if (obj.get("placement") or {}).get("fixture_id"):
            continue
        x, y, z = obj["pose"]["pos"][:3]
        below = [(math.dist((x, y), other["pose"]["pos"][:2]), other["id"]) for other in posed
                 if other is not obj and 0 <= z - other["pose"]["pos"][2] <= height_m]
        near = [item for item in below if item[0] <= radius_m]
        if near:
            found[obj["id"]] = min(near)[1]
    return found


def _holders(env: dict) -> dict[str, tuple[str, str]]:
    """원천이 다른 물체를 기준으로 놓았다고 알려 준 물체 id -> (기준 물체 id, 관계)."""
    objects = {o["id"] for o in env.get("objects") or []}
    return {r["subj"]: (r["obj"], r["pred"]) for r in env.get("relations") or []
            if r.get("obj") in objects and r.get("subj") in objects}


def located(env: dict, options: Any) -> set[str]:
    """세계 상태가 놓인 곳을 알려 주는 물체 id: 가구 배치나 기준 물체가 있거나, 좌표로 받침 물체를 찾은 물체."""
    placed = {o["id"] for o in env.get("objects") or [] if (o.get("placement") or {}).get("fixture_id")}
    on_object = supports(env, options.support_radius_m, options.support_height_m)
    return placed | set(_holders(env)) | set(on_object)


def _region(fixture: dict, region: str | None, lexicon: dict) -> dict | None:
    """가구 안의 칸 이름과 관계. 원천 칸 이름 끝의 숫자는 뗀다(freezer_shelf2 -> freezer_shelf)."""
    if not region:
        return None
    names, base = lexicon.get("region_ko", {}), re.sub(r"\d+$", "", region)
    return names.get(f"{fixture.get('category')}.{base}") or names.get(base)


def _fixture_place(fixture: dict, placement: dict, labels: dict[str, str], lexicon: dict,
                   fixtures: dict[str, dict]) -> str:
    """'냉장고 냉동실 선반 위', '조리대 2 위, 싱크대 옆'처럼 가구 배치를 한 구절로."""
    relation_ko = lexicon.get("relation_ko", {})
    region = _region(fixture, placement.get("region"), lexicon)
    relation = (region or {}).get("relation") or placement.get("relation")
    named = labels.get(region_id(fixture["id"], placement.get("region") or ""))  # 목록에 있는 칸의 이름
    where = named or " ".join(p for p in (_fixture_title(fixture, labels), (region or {}).get("name")) if p)
    phrase = f"{where} {relation_ko.get(relation, relation)}"
    near = fixtures.get(placement.get("near_fixture_id") or "")
    if near:
        side = lexicon.get("near_side_ko", {}).get(placement.get("near_side") or "either", "")
        phrase += f", {labels[near['id']]} {side}"
    return phrase


def _placement_lines(
    env: dict, labels: dict[str, str], lexicon: dict, options: Any
) -> tuple[list[str], list[str]]:
    """놓인 곳이 있는 물체는 곳별로 한 줄, 없는 물체는 이름만 따로 모은다. 놓인 곳은 원천이 알려 준 기준 물체,
    가구 배치(칸·옆 가구 포함), 좌표로 찾은 받침 물체 순으로 정한다."""
    relation_ko = lexicon.get("relation_ko", {})
    containers = set(lexicon.get("container_categories", []))  # 받침이 용기류면 '안', 아니면 '위'
    holders = _holders(env)
    on_object = supports(env, options.support_radius_m, options.support_height_m)
    by_id = {o["id"]: o for o in env.get("objects") or []}
    fixtures = {f["id"]: f for f in env.get("fixtures") or []}
    hosted: dict[str, list[str]] = defaultdict(list)
    loose = []
    for obj in env.get("objects") or []:
        placement = obj.get("placement") or {}
        if obj["id"] in holders:
            holder, relation = holders[obj["id"]]
            hosted[f"{labels[holder]} {relation_ko.get(relation, relation)}"].append(labels[obj["id"]])
        elif placement.get("fixture_id") in fixtures:
            place = _fixture_place(fixtures[placement["fixture_id"]], placement, labels, lexicon, fixtures)
            hosted[place].append(labels[obj["id"]])
        elif obj["id"] in on_object:
            support = by_id[on_object[obj["id"]]]
            relation = "in" if support.get("category") in containers else "on"
            hosted[f"{labels[support['id']]} {relation_ko[relation]}"].append(labels[obj["id"]])
        else:
            loose.append(labels[obj["id"]])
    lines = [f"- {place}: {', '.join(names)}" for place, names in hosted.items()]
    return lines, [f"- {', '.join(loose)}"] if loose else []


def _fixture_lines(env: dict, labels: dict[str, str], lexicon: dict) -> list[str]:
    """장면의 가구 전부. 같은 이름에 상태와 위치 설명이 같으면 한 줄로 묶고, 상태가 있으면 이름 뒤에 붙인다.
    칸이 있는 가구는 따로 적는다. 상태가 있는 줄을 먼저, 상태가 없는 줄을 뒤에 둔다."""
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    beside = _beside(env, labels, lexicon)
    for fixture in env.get("fixtures") or []:
        states = _state_words(fixture.get("states") or {}, lexicon)
        words = ", ".join(states + beside.get(fixture["id"], []))
        regions = _region_words(fixture, lexicon, _all_region_states(env, lexicon))
        own = fixture["id"] if regions else None
        where = fixture.get("location_desc") or ""
        grouped[(_base_name(fixture), words, regions, where, own)].append(fixture)
    lines = []
    stateful_first = sorted(grouped.items(), key=lambda item: not item[0][1])
    for (_name, words, regions, where, _own), members in stateful_first:
        title = _numbered_run([labels[f["id"]] for f in members]) + (f"({where})" if where else "")
        detail = ". ".join(part for part in (words, f"칸: {regions}" if regions else "") if part)
        lines.append(f"- {title}: {detail}" if detail else f"- {title}")
    return lines


def _beside(env: dict, labels: dict[str, str], lexicon: dict) -> dict[str, list[str]]:
    """가구 id -> ['싱크대 옆', '스토브 옆']. 씬이 가구끼리의 근처 관계(조리대 near 싱크대)를 줄 때만 있다."""
    fixtures = {f["id"] for f in env.get("fixtures") or []}
    side = lexicon.get("near_side_ko", {}).get("either", "")
    found: dict[str, list[str]] = defaultdict(list)
    for relation in env.get("relations") or []:
        if relation["pred"] == "near" and {relation["subj"], relation["obj"]} <= fixtures:
            found[relation["subj"]].append(f"{labels[relation['obj']]} {side}".strip())
    return found


def _all_region_states(env: dict, lexicon: dict) -> bool:
    """칸의 상태를 거짓인 것까지 적는가 (도메인 프로필의 region_state_words가 all). 기본은 참인 것만 적는다."""
    profile = lexicon.get("domains", {}).get(env.get("domain"), {})
    return profile.get("region_state_words") == "all"


def _region_words(fixture: dict, lexicon: dict, every_state: bool = False) -> str:
    """가구의 칸 목록 한 구절: '냉장실 선반 1부터 5까지(위에서부터), 랙 1(당겨 빼 놓음), 랙 2(위에서부터)'.
    켜진 화구처럼 상태가 참인 칸은 괄호로 붙이고, 같은 종류가 여럿이면 위에서부터 번호를 붙였다고 알려 준다.
    every_state면 거짓인 상태도 붙인다(닫힌 서랍이 많은 작업장에서 '서랍 닫힘'을 적어야 먼저 열어야 함을 안다)."""
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for region in listed_regions(fixture, lexicon):
        states = region.get("states") or {}
        words = zip(_state_words(states, lexicon), states.values(), strict=False)
        on = [word for word, value in words if value is True or (every_state and value is False)]
        name = region["name_ko"]
        stem = name.rsplit(" ", 1)[0] if name.rsplit(" ", 1)[-1].isdigit() else name
        groups[stem].append((name, f"({', '.join(on)})" if on else ""))
    phrases = []
    for members in groups.values():
        plain = all(not note for _name, note in members)
        names = [name for name, _note in members]
        text = _numbered_run(names) if plain else ", ".join(n + note for n, note in members)
        phrases.append(text + ("(위에서부터)" if len(members) > 1 else ""))
    return ", ".join(phrases)


def _size_tiers(members: list[tuple[float, str]], ratio_min: float) -> list[list[str]]:
    """부피가 큰 것부터 단으로 나눈다. 단의 가장 큰 것보다 ratio_min배 넘게 작아지면 다음 단이다."""
    tiers: list[tuple[float, list[str]]] = []
    for volume, label in sorted(members, key=lambda m: (-m[0], m[1])):
        if tiers and tiers[-1][0] < volume * ratio_min:
            tiers[-1][1].append(label)
        else:
            tiers.append((volume, [label]))
    return [labels for _top, labels in tiers]


def _size_lines(env: dict, labels: dict[str, str], ratio_min: float) -> list[str]:
    """같은 이름의 물체끼리 크기 비교: '- 그릇: 큰 것부터 그릇 2, 그릇 1'. 부피 차이가 뚜렷한 것끼리만
    순서를 매기고(부피 비 ratio_min 이상), 비슷한 것은 '병 1·병 3(서로 비슷)'으로 묶는다. 모두 비슷하면 적지 않는다."""
    groups: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for obj in env.get("objects") or []:
        size = obj.get("size_m")
        if size:
            groups[_base_name(obj)].append((size[0] * size[1] * size[2], labels[obj["id"]]))
    lines = []
    for name, members in groups.items():
        tiers = _size_tiers(members, ratio_min)
        if len(tiers) > 1:
            words = [tier[0] if len(tier) == 1 else f"{'·'.join(tier)}(서로 비슷)" for tier in tiers]
            lines.append(f"- {name}: 큰 것부터 {', '.join(words)}")
    return lines


def _object_state_lines(env: dict, labels: dict[str, str], lexicon: dict) -> list[str]:
    return [f"- {labels[o['id']]}: {', '.join(_state_words(o['states'], lexicon))}"
            for o in env.get("objects") or [] if o.get("states")]


def _robot_line(env: dict, labels: dict[str, str]) -> str:
    agent = env.get("agent") or {}
    holding = [labels.get(h, h) for h in agent.get("holding") or []]
    near = labels.get(agent.get("base_near_fixture_id"))
    parts = [f"로봇({agent.get('embodiment', '로봇')})",
             f"{', '.join(holding)}을 들고 있음" if holding else "들고 있는 물체 없음",
             f"{near} 근처에 있음" if near else None]
    return ". ".join(p for p in parts if p) + "."


@WORLD_STATE_VIEWS.register("text")
def text_view(env: dict, lexicon: dict, options: Any) -> RenderedWorld:
    labels = name_labels(env, lexicon)
    profile = lexicon.get("domains", {}).get(env.get("domain"), {})  # 장면 도메인의 낱말 (resources/domains)
    head, map_parts = [], []
    if world_map.has_map(env):  # 지도가 있는 장면: 지도와 시스템이 알려 준 물품을 따로, 물체 절은 보이는 것만
        head, map_parts = world_map.describe(
            env, labels, lambda part: _placement_lines(part, labels, lexicon, options))
        env = world_map.split_objects(env)[0]
    placed, others = _placement_lines(env, labels, lexicon, options)
    sections: list[tuple[str, list[str]]] = [
        ("물체가 놓인 곳", placed),
        ("그 밖의 물체", others),
        ("물체 상태", _object_state_lines(env, labels, lexicon)),
        ("물체 크기", _size_lines(env, labels, options.size_ratio_min)),
        (profile.get("fixture_ko", ""), _fixture_lines(env, labels, lexicon)),
    ]
    domain = profile.get("name_ko", env.get("domain") or "")
    body = [f"장소: {domain}".rstrip(), _robot_line(env, labels), *head]
    body += [f"\n{title}:\n" + "\n".join(lines) for title, lines in [*map_parts, *sections] if lines]
    return RenderedWorld("\n".join(body).strip(), labels)

