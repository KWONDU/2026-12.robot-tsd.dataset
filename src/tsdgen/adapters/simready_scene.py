"""SimReady 창고 팩에서 뽑은 '놓인 에셋 목록'을 씬 상태의 설비·물체·관계로 옮긴다.

입력은 에셋마다 이름과 축에 맞춘 상자(min_m, max_m)뿐이다. 무엇이 무엇 위·안에 있는지는 원천이 알려 주지
않으므로 상자의 위치로 정한다: 바닥이 다른 것의 윗면에 닿아 있고 수평으로 겹치면 그 위, 용기의 상자 안에
들어 있으면 그 안, 설비의 범위 안에서 더 낮은 높이에 놓였으면 그 설비의 칸(선반)이다. 받침을 찾지 못한
것은 장면에서 뺀다(위치를 말할 수 없는 물체를 지어내지 않는다).

종류·이름·성질을 정하는 표와 기준값은 어댑터 자원(resources/adapters/simready_warehouse.yaml)에 있다.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

ON, IN = "on", "in"
SOURCE = "geometry"  # 관계의 출처: 좌표로 계산한 것
FLOOR_ID = "fx_floor_1"
X, Y, Z = 0, 1, 2


@dataclass
class Item:
    """놓인 에셋 하나와 그것에 대해 정한 것."""

    asset: str
    lo: list[float]
    hi: list[float]
    kind: str
    color: str | None
    entity_id: str = ""
    support: "Item | None" = None  # 받치는 물체나 설비 (바닥이면 None)
    relation: str = ON
    level: float | None = None  # 설비의 칸에 놓였을 때 그 높이
    on_floor: bool = False
    regions: list[float] = field(default_factory=list)  # 설비: 칸의 높이들 (위에서부터)
    region_keys: list[str] = field(default_factory=list)  # 설비: 그 칸들의 이름표


def kind_of(asset: str, vocab: dict[str, Any]) -> tuple[str, str | None]:
    """에셋 이름 -> (종류 낱말, 색 낱말). 접두사, 변형 번호, 색을 뺀 나머지가 종류다."""
    parts = [p for p in re.sub(vocab["name_prefix"], "", asset.lower()).split("_") if p]
    color = next((p for p in parts if p in vocab["colors"]), None)
    noise = re.compile(vocab["name_noise"])
    stem = [p for p in parts if p not in vocab["colors"] and not noise.fullmatch(p)]
    return "_".join(stem), color


def _overlap(a: Item, b: Item) -> float:
    """수평으로 겹치는 넓이 (겹치지 않으면 0)."""
    spans = [min(a.hi[k], b.hi[k]) - max(a.lo[k], b.lo[k]) for k in (X, Y)]
    return spans[0] * spans[1] if min(spans) > 0 else 0.0


def _inside(a: Item, b: Item, gap: float) -> bool:
    """a의 가운데가 b의 수평 범위 안이고, a의 바닥이 b의 바닥과 윗면 사이에 있다 (용기 안)."""
    center = [(a.lo[k] + a.hi[k]) / 2 for k in (X, Y)]
    within = all(b.lo[k] <= center[k] <= b.hi[k] for k in (X, Y))
    # 바닥의 가로·세로가 용기보다 크면 담긴 것이 아니라 얹힌 것이다 (포갠 크레이트)
    fits = all(x <= y + gap for x, y in zip(sorted(_span(a)), sorted(_span(b)), strict=True))
    return within and fits and b.lo[Z] - gap <= a.lo[Z] < b.hi[Z] - gap


def _span(item: Item) -> list[float]:
    return [item.hi[k] - item.lo[k] for k in (X, Y)]


def classify(raw: list[dict], vocab: dict[str, Any]) -> tuple[list[Item], list[Item], Counter]:
    """놓인 에셋을 설비와 물체로 가른다. 표에 없는 종류, 크기를 읽지 못한 것, 표에서 무거운 것으로 적지 않았는데
    손으로 옮길 무게를 넘는 물체는 뺀다."""
    geometry = vocab["geometry"]
    fixtures, objects, skipped = [], [], Counter()
    for entry in raw:
        kind, color = kind_of(entry["asset"], vocab)
        item = Item(entry["asset"], entry["min_m"], entry["max_m"], kind, color)
        size = [item.hi[k] - item.lo[k] for k in (X, Y, Z)]
        mass = entry.get("mass_kg")
        if min(size) <= geometry["min_size_m"]:
            skipped["no size"] += 1
        elif kind in vocab["fixtures"]:
            fixtures.append(item)
        elif kind in vocab["objects"] and (vocab["objects"][kind].get("heavy") or mass is None
                                           or mass <= vocab["graspable_max_kg"]):
            objects.append(item)
        else:
            skipped[kind] += 1
    return fixtures, objects, skipped


def _find_support(obj: Item, objects: list[Item], fixtures: list[Item],
                  vocab: dict[str, Any]) -> bool:
    """물체의 받침을 정한다: 용기 물체 안, 다른 물체 위, 설비(윗면이나 칸), 바닥 순. 찾으면 참."""
    geometry, gap = vocab["geometry"], vocab["geometry"]["support_gap_m"]
    holders = [o for o in objects if o is not obj and "holder" in vocab["objects"][o.kind]["traits"]
               and _inside(obj, o, gap)]
    below = [o for o in objects
             if o is not obj and abs(obj.lo[Z] - o.hi[Z]) <= gap and _overlap(obj, o) > 0
             and o.lo[Z] + o.hi[Z] < obj.lo[Z] + obj.hi[Z]]  # 받침은 더 낮은 것이다
    hosts = [f for f in fixtures
             if _overlap(obj, f) > 0 and f.lo[Z] - gap <= obj.lo[Z] <= f.hi[Z] + gap]
    if holders:
        holder = min(holders, key=lambda o: o.hi[Z] - o.lo[Z])
        # 물체 높이의 일정 비율 넘게 윗면 위로 솟으면 얕은 용기(납작한 크레이트)에 얹힌 것이다
        below_rim = min(obj.hi[Z], holder.hi[Z]) - obj.lo[Z]
        deep = below_rim >= geometry["rim_share"] * (obj.hi[Z] - obj.lo[Z])
        obj.support, obj.relation = holder, IN if deep else ON
    elif below:
        obj.support = max(below, key=lambda o: _overlap(obj, o))
    elif hosts:
        host = max(hosts, key=lambda f: _overlap(obj, f))
        obj.support = host
        obj.relation = vocab["fixtures"][host.kind].get("containment", ON)
        step = geometry["level_step_m"]
        obj.level = round(round(obj.lo[Z] / step) * step, 3)
    elif obj.lo[Z] < geometry["floor_height_m"]:
        obj.on_floor = True
    else:
        return False
    return True


def place(objects: list[Item], fixtures: list[Item],
          vocab: dict[str, Any]) -> tuple[list[Item], int]:
    """물체마다 받침을 정하고, 받침을 찾지 못한 것과 그 위에 놓인 것을 뺀다. (남은 물체, 뺀 수)
    서로 파고든 물체(한쪽이 다른 쪽을 담는 것도 아닌데 상자가 크게 겹침)는 무엇이 무엇 위인지 말할 수 없어 뺀다."""
    tangled = _tangled(objects, vocab)
    kept = [o for o in objects
            if id(o) not in tangled and _find_support(o, objects, fixtures, vocab)]
    while True:  # 빠진 물체 위에 있던 것도 뺀다
        alive = {id(o) for o in kept} | {id(f) for f in fixtures}
        still = [o for o in kept if o.support is None or id(o.support) in alive]
        if len(still) == len(kept):
            return kept, len(objects) - len(kept)
        kept = still


def _volume(item: Item) -> float:
    return (item.hi[X] - item.lo[X]) * (item.hi[Y] - item.lo[Y]) * (item.hi[Z] - item.lo[Z])


def _tangled(objects: list[Item], vocab: dict[str, Any]) -> set[int]:
    """작은 쪽 부피의 일정 비율 넘게 겹치는데 어느 쪽도 다른 쪽을 담는 용기가 아닌 물체들."""
    share, gap = vocab["geometry"]["tangle_share"], vocab["geometry"]["support_gap_m"]
    found: set[int] = set()
    for i, a in enumerate(objects):
        for b in objects[i + 1:]:
            spans = [min(a.hi[k], b.hi[k]) - max(a.lo[k], b.lo[k]) for k in (X, Y, Z)]
            common = spans[0] * spans[1] * spans[2]
            if min(spans) <= 0 or common <= share * min(_volume(a), _volume(b)):
                continue
            holds = [(o, other) for o, other in ((a, b), (b, a))
                     if "holder" in vocab["objects"][o.kind]["traits"] and _inside(other, o, gap)]
            if not holds:
                found |= {id(a), id(b)}
    return found


def _name(entry: dict, color: str | None, vocab: dict[str, Any]) -> tuple[str, str]:
    """(분류, 한국어 이름). 색이 있으면 분류와 이름에 붙인다 (crate_yellow, 노란 크레이트)."""
    if color:
        return f'{entry["category"]}_{color}', f'{vocab["colors"][color]} {entry["ko"]}'
    return entry["category"], entry["ko"]


def _fixture_entries(fixtures: list[Item], objects: list[Item],
                     vocab: dict[str, Any]) -> list[dict]:
    """설비 목록(바닥 포함). 칸은 물체가 놓인 높이마다 하나이고 위에서부터 번호를 붙인다."""
    counts: Counter[str] = Counter()
    floor = vocab["floor"]
    entries = [{"id": FLOOR_ID, "category": floor["category"], "name_ko": floor["ko"]}]
    for fixture in sorted(fixtures, key=lambda f: (f.lo[X], f.lo[Y])):
        spec = vocab["fixtures"][fixture.kind]
        counts[spec["category"]] += 1
        fixture.entity_id = f'fx_{spec["category"]}_{counts[spec["category"]]}'
        levels = sorted({o.level for o in objects if o.support is fixture and o.level is not None},
                        reverse=True)
        fixture.regions = levels if spec.get("shelves") and len(levels) > 1 else []
        regions = _region_entries(fixture, spec, vocab)
        raised = _raised_on(fixture, fixtures, objects, vocab)
        entries.append({"id": fixture.entity_id, "category": spec["category"],
                        "name_ko": spec["ko"], "location_desc": raised, "regions": regions})
    return entries


def _region_entries(fixture: Item, spec: dict, vocab: dict[str, Any]) -> list[dict]:
    """설비의 칸 목록. 선반은 위에서부터 번호를 붙이고, 맨 위 면이 따로 있는 설비(작업대)는 그 면을 상판으로 적는다."""
    shelf, top, geometry = vocab["shelf"], vocab["top"], vocab["geometry"]
    margin = geometry["support_gap_m"] + geometry["level_step_m"] / 2
    is_top = [bool(spec.get("top")) and abs(z - fixture.hi[Z]) <= margin for z in fixture.regions]
    shelves = is_top.count(False)
    entries, number = [], 0
    for flag in is_top:
        if flag:
            entries.append({"key": top["key"], "name_ko": top["ko"]})
            continue
        number += 1
        name = f'{shelf["ko"]} {number}' if shelves > 1 else shelf["ko"]
        entries.append({"key": f'{shelf["key"]}{number}', "name_ko": name})
    fixture.region_keys = [e["key"] for e in entries]
    return entries


def _raised_on(fixture: Item, fixtures: list[Item], objects: list[Item],
               vocab: dict[str, Any]) -> str | None:
    """바닥이 아니라 다른 것 위에 올려진 설비의 위치 설명 (랙에 올린 팔레트). 바닥에 있으면 None."""
    geometry = vocab["geometry"]
    if fixture.lo[Z] < geometry["floor_height_m"]:
        return None
    gap = geometry["support_gap_m"]
    under = [(vocab["fixtures"][f.kind]["ko"], f) for f in fixtures if f is not fixture]
    under += [(vocab["objects"][o.kind]["ko"], o) for o in objects]
    below = [(name, x) for name, x in under
             if _overlap(fixture, x) > 0 and x.lo[Z] - gap <= fixture.lo[Z] <= x.hi[Z] + gap]
    if not below:
        return None
    name = max(below, key=lambda pair: _overlap(fixture, pair[1]))[0]
    return vocab["raised_fixture_desc"].format(support=name)


def _object_entry(obj: Item, vocab: dict[str, Any]) -> dict:
    category, name_ko = _name(vocab["objects"][obj.kind], obj.color, vocab)
    entry: dict[str, Any] = {"id": obj.entity_id, "category": category, "name_ko": name_ko,
                             "size_m": [round(obj.hi[k] - obj.lo[k], 3) for k in (X, Y, Z)]}
    host = obj.support
    if obj.on_floor:
        entry["placement"] = {"fixture_id": FLOOR_ID, "relation": ON}
    elif host is not None and host.entity_id.startswith("fx_"):
        placement: dict[str, Any] = {"fixture_id": host.entity_id, "relation": obj.relation}
        if obj.level in host.regions:
            placement["region"] = host.region_keys[host.regions.index(obj.level)]
        entry["placement"] = placement
    return entry


def build(raw: list[dict], vocab: dict[str, Any]) -> tuple[dict[str, list], dict[str, Any]]:
    """놓인 에셋 목록 -> ({fixtures, objects, relations}, 기록). 기록에는 뺀 것의 수와 원본 이름이 들어간다."""
    fixtures, objects, skipped = classify(raw, vocab)
    objects, dropped = place(objects, fixtures, vocab)
    fixture_entries = _fixture_entries(fixtures, objects, vocab)
    ordered = sorted(objects, key=lambda o: (o.lo[Z], o.lo[X], o.lo[Y]))
    for n, obj in enumerate(ordered, start=1):
        obj.entity_id = f"obj_{n:03d}"
    object_entries = [_object_entry(o, vocab) for o in ordered]
    relations = [{"subj": o.entity_id, "pred": o.relation,
                  "obj": FLOOR_ID if o.on_floor else o.support.entity_id, "source": SOURCE}
                 for o in ordered if o.on_floor or o.support is not None]
    note = {"skipped": dict(skipped), "dropped_no_support": dropped,
            "object_assets": {o.entity_id: o.asset for o in ordered},
            "fixture_assets": {f.entity_id: f.asset for f in fixtures}}
    return {"fixtures": fixture_entries, "objects": object_entries, "relations": relations}, note
