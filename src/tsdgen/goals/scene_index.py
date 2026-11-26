"""목표 판정에 쓰는 씬 조회 색인.

환경 컨텍스트(모델 입력용 씬 상태 dict)를 한 번 훑어, 단언을 판정할 때 필요한 조회(id의 종류, 상태,
배치, 관계, 로봇이 든 것)를 바로 할 수 있게 만든다. 초기 씬이든 작업 뒤에 관측한 씬이든 같은 규격이면
같은 색인으로 판정한다.
"""

from dataclasses import dataclass, field
from typing import Any

OBJECTS = "objects"
FIXTURES = "fixtures"
REGION_SEP = "/"  # 가구의 칸 id: <가구 id>/<칸> (models.scene.region_id와 같은 규약)
SUPPORT_PREDICATES = {"on", "in"}  # 물체를 받치거나 담는 관계
ANY_SEP = "|"  # 참조 하나가 여러 entity 중 아무 것을 뜻할 때 id를 잇는 표시 (예: fx_counter_1|fx_counter_2)


def members(ref: str | None) -> list[str]:
    """참조가 가리키는 entity id들. 보통은 하나, '아무 조리대' 같은 참조는 여럿."""
    return [part for part in (ref or "").split(ANY_SEP) if part]


def any_of(ids: list[str]) -> str:
    return ANY_SEP.join(ids)


def fixture_of(ref: str | None) -> str | None:
    """칸 id면 그 칸이 속한 가구 id, 아니면 그대로."""
    return ref.split(REGION_SEP, 1)[0] if ref else ref


@dataclass
class SceneIndex:
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)  # id -> entity dict
    kinds: dict[str, str] = field(default_factory=dict)  # id -> OBJECTS | FIXTURES
    relations: set[tuple[str, str, str]] = field(default_factory=set)  # (주어, 술어, 대상)
    placements: dict[str, str] = field(default_factory=dict)  # 물체 id -> 놓인 가구 id
    region_placements: dict[str, str] = field(default_factory=dict)  # 물체 id -> 놓인 칸 id (칸을 알 때)
    holding: set[str] = field(default_factory=set)
    robot_at: str | None = None  # 로봇이 지금 있는 곳 (가구 id)

    @classmethod
    def from_env(cls, env: dict[str, Any]) -> "SceneIndex":
        index = cls()
        for kind in (OBJECTS, FIXTURES):
            for entity in env.get(kind, []):
                index.entities[entity["id"]] = entity
                index.kinds[entity["id"]] = kind
        for fixture in env.get(FIXTURES, []):
            for region in fixture.get("regions") or []:  # 칸도 가리킬 수 있는 대상이다 (상태는 칸의 것)
                rid = f'{fixture["id"]}{REGION_SEP}{region["key"]}'
                index.entities[rid] = region
                index.kinds[rid] = FIXTURES
        for obj in env.get(OBJECTS, []):
            placement = obj.get("placement")
            if placement:
                index.placements[obj["id"]] = placement["fixture_id"]
                rid = f'{placement["fixture_id"]}{REGION_SEP}{placement.get("region")}'
                if rid in index.entities:
                    index.region_placements[obj["id"]] = rid
        index.relations = {(r["subj"], r["pred"], r["obj"]) for r in env.get("relations", [])}
        index.holding = set((env.get("agent") or {}).get("holding") or [])
        index.robot_at = (env.get("agent") or {}).get("base_near_fixture_id")
        return index

    def kind_of(self, ref: str | None) -> str | None:
        """참조의 종류. 여럿을 가리키면 모두 같은 종류일 때만 그 종류다."""
        kinds = {self.kinds.get(m) for m in members(ref)}
        return kinds.pop() if len(kinds) == 1 else None

    def fixture_under(self, ref: str) -> str | None:
        """물체가 얹혀 있는 가구. 가구에 바로 놓였으면 그 가구, 다른 물체 위·안에 있으면(접시 위 스테이크)
        받치는 물체를 따라 내려가 찾는다."""
        seen: set[str] = set()
        while ref and ref not in seen:
            if ref in self.placements:
                return self.placements[ref]
            seen.add(ref)
            held = ((o, p) for s, p, o in self.relations if s == ref)
            holders = sorted(o for o, p in held
                             if p in SUPPORT_PREDICATES and self.kinds.get(o) == OBJECTS)
            ref = holders[0] if holders else None
        return None

    def states_of(self, ref: str) -> dict[str, Any]:
        return (self.entities.get(ref) or {}).get("states") or {}
