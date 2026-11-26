"""작업 합성이 장면을 보는 창: 가리킬 수 있는 물체·가구·칸과 그 상태.

지시문은 사람이 말하듯 종류 이름으로 대상을 가리키므로("the mug", "the fridge"), 장면 안에서 그 종류가
하나뿐인 것만 대상으로 삼는다. 조리대처럼 여럿이고 어느 것이든 되는 가구는 '아무 것'으로 가리킨다.
장면 규격만 보므로 원천(시뮬레이터)을 알지 못한다.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from tsdgen.goals.scene_index import SUPPORT_PREDICATES, any_of
from tsdgen.models import region_id

OPEN_KEYS = ("door_open", "drawer_open")  # 안의 물체에 닿으려면 열려 있어야 하는 상태 (도메인 프로필이 바꿀 수 있다)


@dataclass(frozen=True)
class Thing:
    """가리킬 대상 하나 (물체, 가구, 가구의 칸)."""

    ref: str  # 목표 상태에 쓰는 id (여럿 중 아무 것이면 id를 이은 것)
    category: str  # 종류 (물체 분류, 가구 분류, 칸 종류)
    states: dict = field(default_factory=dict, compare=False)
    fixture: str | None = None  # 물체가 놓인 가구 id, 칸이 속한 가구 id
    holder: str | None = None  # 물체가 얹힌 다른 물체 id
    rank: int | None = None  # 같은 종류 칸 가운데 위에서부터 몇 번째인지 (1부터)
    of: int | None = None  # 그 종류 칸이 모두 몇 개인지
    volume: float | None = None  # 물체의 부피 (크기를 알 때)


def _volume(obj: dict) -> float | None:
    size = obj.get("size_m")
    return size[0] * size[1] * size[2] if size else None


class SceneView:
    def __init__(self, scene: dict[str, Any], traits: dict[str, list[str]],
                 compartments: bool = False, open_keys: tuple[str, ...] = OPEN_KEYS):
        self.scene = scene
        self.open_keys = open_keys
        self.traits = traits  # 물체 종류 -> 성질 이름들 (원천이 알려 준 것)
        # 칸이 스스로 여닫히는 도메인(서랍마다 따로 열리는 작업대)이면 물체가 든 칸을 출발지의 문으로 본다
        self.compartments = compartments
        self.fixtures = {f["id"]: f for f in scene.get("fixtures") or []}
        objects = scene.get("objects") or []
        self.placed_in = {o["id"]: region_id(o["placement"]["fixture_id"], o["placement"]["region"])
                          for o in objects if (o.get("placement") or {}).get("region")}
        holders = {r["subj"]: r["obj"] for r in scene.get("relations") or []
                   if r["pred"] in SUPPORT_PREDICATES and r["obj"] not in self.fixtures}
        by_category: dict[str, list[Thing]] = {}
        for obj in objects:
            thing = Thing(obj["id"], obj["category"], obj.get("states") or {},
                          (obj.get("placement") or {}).get("fixture_id"), holders.get(obj["id"]),
                          volume=_volume(obj))
            by_category.setdefault(obj["category"], []).append(thing)
        self.objects = [things[0] for things in by_category.values() if len(things) == 1]
        self.pairs = [things for things in by_category.values() if len(things) == 2]

    def has(self, thing: Thing, trait: str) -> bool:
        return trait in self.traits.get(thing.category, ())

    def fixtures_of(self, category: str) -> list[Thing]:
        return [Thing(f["id"], category, f.get("states") or {})
                for f in self.fixtures.values() if f["category"] == category]

    def only(self, category: str) -> Thing | None:
        """그 종류가 장면에 하나뿐일 때의 가구."""
        found = self.fixtures_of(category)
        return found[0] if len(found) == 1 else None

    def any(self, category: str) -> Thing | None:
        """그 종류의 가구 아무 것 (조리대처럼 어느 것이든 되는 받는 곳)."""
        found = self.fixtures_of(category)
        return Thing(any_of([f.ref for f in found]), category) if found else None

    def regions(self, fixture: Thing) -> list[Thing]:
        """가구의 칸들. 같은 종류가 여럿이면 이름 끝 번호가 위에서부터의 순서다."""
        entries = self.fixtures[fixture.ref].get("regions") or []
        kinds: dict[str, int] = {}
        for entry in entries:
            kind = re.sub(r"\d+$", "", entry["key"])
            kinds[kind] = kinds.get(kind, 0) + 1
        found = []
        for entry in entries:
            kind = re.sub(r"\d+$", "", entry["key"])
            number = re.search(r"(\d+)$", entry["name_ko"])
            rank = int(number.group(1)) if number else 1
            found.append(Thing(region_id(fixture.ref, entry["key"]), kind, entry.get("states") or {},
                               fixture.ref, None, rank, kinds[kind]))
        return found

    def source(self, obj: Thing) -> Thing | None:
        """물체가 지금 놓인 가구 (다른 물체 위에 있으면 그 물체가 놓인 가구)."""
        fixture_id = obj.fixture
        if fixture_id is None and obj.holder:
            holder = next((o for o in self.scene["objects"] if o["id"] == obj.holder), None)
            fixture_id = ((holder or {}).get("placement") or {}).get("fixture_id")
        fixture = self.fixtures.get(fixture_id or "")
        if fixture is None:
            return None
        return Thing(fixture["id"], fixture["category"], fixture.get("states") or {})

    def compartment(self, obj: Thing) -> Thing | None:
        """물체가 든 칸이 스스로 여닫히면 그 칸(서랍 하나). compartments가 꺼져 있으면 언제나 None."""
        here = self.placed_in.get(obj.ref) if self.compartments else None
        if here is None or obj.fixture not in self.fixtures:
            return None
        host = Thing(obj.fixture, self.fixtures[obj.fixture]["category"])
        region = next((r for r in self.regions(host) if r.ref == here), None)
        return region if region and self.shut(region) is not None else None

    def shut(self, fixture: Thing | None) -> bool | None:
        """가구가 닫혀 있는가. 여닫는 상태가 없으면 None."""
        values = [fixture.states[k] for k in self.open_keys if fixture and k in fixture.states]
        return (not values[0]) if values else None
