"""작업 유형의 공통 재료: 만들어진 작업 하나(Task), 낱말(Words), 유형 등록부.

작업 유형 하나는 장면(SceneView)을 받아 그 장면에서 성립하는 작업들을 내놓는 함수다. 작업은 영어 지시문과
목표 상태를 함께 갖는다: 빈칸을 우리가 채웠으므로 목표 상태가 무엇이어야 하는지 만들 때 정해진다(검수의 정답).
signature는 그 작업 상황을 다른 것과 가르는 열쇠다(유형, 대상의 종류, 받는 곳, 출발지와 그 여닫힘, 조건).
signature가 같은 작업은 배경만 다른 같은 상황이므로 여러 번 고르지 않는다.
"""

from dataclasses import dataclass
from typing import Any

from tsdgen.registry import Registry
from tsdgen.tasks.view import SceneView, Thing

TASK_TYPES = Registry("task type", axes=(lambda s: [t for c in s.compose.values() for t in c.types],))
"""이름 -> (SceneView, Words) -> Task들. 등록 이름이 config(compose.<도메인>.types)의 허용값이다."""

RELATION, STATE = "relation", "state"


@dataclass(frozen=True)
class Task:
    type_id: str
    instruction: str  # 영어 원 지시문 (재작성 단계가 한국어 발화로 바꾼다)
    cases: tuple  # 목표 상태의 경우들. 경우 하나는 단언 (kind, subject, predicate, object, value)의 묶음
    signature: tuple
    duration: tuple | None = None  # (초, 대상 id): 정해진 시간 동안 가동해야 하는 작업
    # 설명문이 담아야 하는 상황 사실 ((이름, 값), ...): 돌아가게 만드는 통로, 기다려야 하는 곳, 시간 창 등.
    # 작업 합성이 시나리오 meta.situation으로 남기고 검수 항목 situation이 설명문과 대조한다
    facts: tuple = ()


@dataclass(frozen=True)
class Place:
    """물체를 받는 곳: 가리킬 대상과 관계, 지시문에 쓰는 구절, signature에 넣을 열쇠."""

    thing: Thing
    relation: str
    phrase: str
    key: tuple
    host: Thing | None = None  # 칸이면 그 칸이 속한 가구


def at(obj: Thing, place: Place) -> tuple:
    return (RELATION, obj.ref, place.relation, place.thing.ref, True)


def state(thing: Thing, key: str, value: bool) -> tuple:
    return (STATE, thing.ref, key, None, value)


class Words:
    """지시문에 쓰는 영어 낱말과 작업 규칙 (도메인 프로필의 task_synthesis)."""

    def __init__(self, spec: dict[str, Any]):
        self.spec = spec

    def obj(self, thing: Thing) -> str:
        return self.spec["object_en"].get(thing.category, thing.category.replace("_", " "))

    def fixture(self, thing: Thing) -> str:
        return self.spec["fixture_en"].get(thing.category, thing.category.replace("_", " "))

    def door_key(self, fixture: Thing) -> str | None:
        """그 가구를 여닫는 상태 키 (없으면 여닫는 가구가 아니다)."""
        return next((k for k in self.spec["open_keys"] if k in fixture.states), None)

    def door_name(self, key: str) -> str:
        """상태 키가 여닫는 부분의 이름 (문, 서랍, 뚜껑). 프로필에 없으면 문이다."""
        return (self.spec.get("door_names") or {}).get(key, "door")

    def door_of(self, place: "Place") -> tuple[Thing, str, str] | None:
        """받는 곳에 닿으려면 여닫아야 하는 것: (상태의 주어, 상태 키, 지시문에 쓰는 이름).
        냉동실 칸은 냉동실 문이고, 칸이 스스로 여닫히는 도메인이면 그 칸(서랍)이다."""
        if place.host is None:
            return None
        own = self.spec["region_doors"].get(place.thing.category)
        if own and own["key"] in place.host.states:
            return place.host, own["key"], own["name"]
        inner = self.door_key(place.thing) if self.spec.get("compartments") else None
        if inner:
            return place.thing, inner, self.door_name(inner)
        key = self.door_key(place.host)
        return (place.host, key, f"{self.fixture(place.host)} {self.door_name(key)}") if key else None

    def region(self, region: Thing, host: Thing) -> str | None:
        """칸을 가리키는 구절: 'the top shelf of the fridge', 'the front left burner of the stove'."""
        name = self.spec["region_en"].get(region.category)
        if name is None:
            return None
        order = ""
        if (region.of or 1) > 1:
            if region.rank == 1:
                order = "top "
            elif region.rank == region.of:
                order = "bottom "
            else:  # 가운데 칸은 프로필이 서수를 주면 위에서부터 센다 (없으면 가리키지 않는다)
                ordinals = self.spec.get("ordinals_en") or []
                if region.rank - 1 > len(ordinals):
                    return None
                return f"the {ordinals[region.rank - 2]} {name} from the top of the {self.fixture(host)}"
        return f"the {order}{name} of the {self.fixture(host)}"


def fits(view: SceneView, obj: Thing, rule: dict) -> bool:
    """이 물체를 그 규칙의 자리에 둘 수 있는가: 필요한 성질, 허용 종류, 허용·제외 분류를 본다."""
    if rule.get("trait") and not view.has(obj, rule["trait"]):
        return False
    if rule.get("categories") and obj.category not in rule["categories"]:
        return False
    if rule.get("types") and not any(view.has(obj, t) for t in rule["types"]):
        return False
    return not any(view.has(obj, t) for t in rule.get("not_types") or ())


def source_phrase(view: SceneView, words: Words, source: Thing) -> tuple[str, str]:
    """출발지 가구를 가리키는 말 두 가지 (처음 말할 때, 다시 가리킬 때). 그 종류가 하나뿐이면 이름으로,
    여럿이면 물체가 들어 있는 것으로 가리킨다."""
    name = words.fixture(source)
    if len(view.fixtures_of(source.category)) == 1:
        return f"the {name}", f"the {name}"
    return f"the {name} it is in", f"that {name}"


def places(view: SceneView, words: Words, obj: Thing) -> list[Place]:
    """이 물체를 옮겨 둘 수 있는 곳들. 지금 놓인 가구와 같은 종류인 곳, 성질이 맞지 않는 곳은 뺀다."""
    source = view.source(obj)
    found: list[Place] = []
    for category, rule in words.spec["destinations"].items():
        target = view.any(category) if rule.get("any") else view.only(category)
        if target is None or (source and source.category == category) or not fits(view, obj, rule):
            continue
        article = "a" if rule.get("any") else "the"
        found.append(Place(target, rule["relation"], f"{rule['relation']} {article} {words.fixture(target)}",
                           (category,), None if rule.get("any") else target))
    here = view.placed_in.get(obj.ref) if view.compartments else None  # 같은 설비의 다른 칸으로는 옮길 수 있다
    for category, kinds in words.spec["region_destinations"].items():
        host = view.only(category)
        if host is None or (source and source.ref == host.ref and here is None):
            continue
        for region in view.regions(host):
            rule = kinds.get(region.category)
            phrase = words.region(region, host) if rule else None
            if not phrase or not fits(view, obj, rule) or region.ref == here:
                continue
            found.append(Place(region, rule["relation"], f"{rule['relation']} {phrase}",
                               (category, region.category, region.rank), host))
    if fits(view, obj, words.spec["receptacle_items"]):
        for relation, categories in words.spec["receptacles"].items():
            found += [Place(other, relation, f"{relation} the {words.obj(other)}", ("object", other.category))
                      for other in view.objects
                      if other.category in categories and other.ref not in (obj.ref, obj.holder)]
    return found


def movable(view: SceneView) -> list[Thing]:
    return [obj for obj in view.objects if view.has(obj, "graspable")]


def origin(view: SceneView, obj: Thing) -> tuple:
    """signature에 넣는 출발지: 놓인 가구의 종류와 닫혀 있는지. 스스로 여닫히는 칸에 들어 있으면 그 칸까지."""
    source = view.source(obj)
    inner = view.compartment(obj)
    if inner is not None:
        return (source.category, inner.category, view.shut(inner))
    return (source.category if source else None, view.shut(source))


def entry_shut(view: SceneView, words: Words, place: Place) -> bool | None:
    """signature에 넣는 받는 곳의 닫힘: 받는 칸이 스스로 여닫히면 그 칸, 아니면 그 칸이 속한 가구."""
    if view.compartments and words.door_key(place.thing):
        return view.shut(place.thing)
    return view.shut(place.host)


def compartment_phrase(words: Words, inner: Thing) -> tuple[str, str]:
    """물체가 든 칸(서랍)을 가리키는 말 두 가지 (처음 말할 때, 다시 가리킬 때)."""
    name = words.spec["region_en"][inner.category]
    return f"the {name} it is in", f"that {name}"
