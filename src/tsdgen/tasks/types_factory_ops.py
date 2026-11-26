"""작업 유형들 (제조, 이어서): 출하 시간 창, 보관·꺼내기, 공구 가져오기, 폐기물 수거, 충전소·대기 구역으로 가기,
두 곳 배송, 출구 버퍼 비우기. 출하 시간 창은 규칙집의 주문 시간 창을 따른다. 낱말과 규칙은 도메인 프로필의
task_synthesis.factory에 있다.
"""

from collections.abc import Iterator
from itertools import combinations

from tsdgen.tasks.base import RELATION, TASK_TYPES, Task, Words
from tsdgen.tasks.types_factory import (
    ON,
    ROBOT_AT,
    _destinations,
    _goal,
    _outputs,
    _setup,
    carried,
    situation,
)
from tsdgen.tasks.view import SceneView


@TASK_TYPES.register("ship_window")
def ship_window(view: SceneView, words: Words) -> Iterator[Task]:
    setup = _setup(view, words)
    if setup is None:
        return
    factory, names, spec = setup
    for item in _outputs(factory, spec):
        name = names.item(item)
        docks = factory.stations(spec["shipping"]) if name else []
        for dock in docks:
            legs = ((factory.here, item.station), (item.station, dock))
            traits = factory.route_traits(*legs)
            for start, end in spec["windows"] if traits is not None else []:
                text = (
                    f"Deliver {name} to {names.place(dock, spec['input'])} between {start} and {end} "
                    "seconds from now."
                )
                yield Task(
                    "ship_window",
                    text,
                    ((_goal(item, dock, spec["input"]),),),
                    (
                        "ship_window",
                        item.category,
                        factory.category(item.station),
                        (start, end),
                        traits,
                    ),
                    facts=situation(factory, legs, window_s=(start, end)),
                )


@TASK_TYPES.register("store")
def store(view: SceneView, words: Words) -> Iterator[Task]:
    """출구 버퍼의 물건을 자동 창고에 (정해진 선반, 또는 아무 선반)."""
    setup = _setup(view, words)
    if setup is None:
        return
    factory, names, spec = setup
    for item in _outputs(factory, spec):
        name = names.item(item)
        for storage in factory.stations(spec["storage"]) if name else []:
            legs = ((factory.here, item.station), (item.station, storage))
            traits = factory.route_traits(*legs)
            if traits is None:
                continue
            # 'Put X on <출구 버퍼> in <창고>'는 어디에 놓으라는지 헷갈려 'Move X to <창고>'로 쓴다 (확인 실행에서 오독 1건)
            text = f"Move {name} to {names.station(storage)}."
            yield Task(
                "store",
                text,
                ((_goal(item, storage, None),),),
                ("store", item.category, factory.category(item.station), "any", traits),
                facts=situation(factory, legs),
            )
            for shelf in [r for r in factory.regions(storage) if r.startswith(spec["shelf"])]:
                text = f"Move {name} to {names.place(storage, shelf)}."
                yield Task(
                    "store",
                    text,
                    ((_goal(item, storage, shelf),),),
                    ("store", item.category, factory.category(item.station), shelf, traits),
                    facts=situation(factory, legs),
                )


def _fetch(
    view: SceneView,
    words: Words,
    type_id: str,
    source_key: str,
    region_key: str,
    trait: str,
    skip_waste: bool = False,
) -> Iterator[Task]:
    """한 곳(창고 선반, 공구 반출대)의 물건을 가공·조립 곳의 입구 버퍼로."""
    setup = _setup(view, words)
    if setup is None:
        return
    factory, names, spec = setup
    targets = [s for s in factory.fixtures if factory.category(s) in spec["work_stations"]]
    for item in factory.items:
        if (
            factory.category(item.station) != spec[source_key]
            or not factory.has(item, spec[trait])
            or (skip_waste and not carried(factory, spec, item))
        ):
            continue
        if not (item.region or "").startswith(spec[region_key]):
            continue
        name = names.item(item)
        for dest in targets if name else []:
            legs = ((factory.here, item.station), (item.station, dest))
            traits = factory.route_traits(*legs)
            if traits is None:
                continue
            where = "" if " on " in name else f" from {names.station(item.station)}"
            text = f"Bring {name}{where} to {names.place(dest, spec['input'])}."
            yield Task(
                type_id,
                text,
                ((_goal(item, dest, spec["input"]),),),
                (type_id, item.category, factory.category(dest), factory.running(dest), traits),
                facts=situation(factory, legs, (dest,)),
            )


@TASK_TYPES.register("retrieve")
def retrieve(view: SceneView, words: Words) -> Iterator[Task]:
    yield from _fetch(view, words, "retrieve", "storage", "shelf", "carry_trait", skip_waste=True)


@TASK_TYPES.register("tool_fetch")
def tool_fetch(view: SceneView, words: Words) -> Iterator[Task]:
    yield from _fetch(view, words, "tool_fetch", "tool_crib", "counter", "tool_trait")


@TASK_TYPES.register("collect_waste")
def collect_waste(view: SceneView, words: Words) -> Iterator[Task]:
    """곳에 놓인 버릴 것(기름 묻은 것, 재활용할 것)을 폐기물 수거장의 수거함에."""
    setup = _setup(view, words)
    if setup is None:
        return
    factory, names, spec = setup
    for item in factory.items:
        if not any(factory.has(item, t) for t in spec["waste_traits"]):
            continue
        name = names.item(item)
        for dump in factory.stations(spec["waste"]) if name else []:
            legs = ((factory.here, item.station), (item.station, dump))
            traits = factory.route_traits(*legs)
            if traits is None or item.station == dump:
                continue
            text = f"Take {name} to {names.place(dump, spec['bin'])}."
            yield Task(
                "collect_waste",
                text,
                ((_goal(item, dump, spec["bin"], "in"),),),
                ("collect_waste", item.category, factory.category(item.station), traits),
                facts=situation(factory, legs),
            )


@TASK_TYPES.register("go_to")
def go_to(view: SceneView, words: Words) -> Iterator[Task]:
    """충전소나 로봇 대기 구역으로 가기 (목표 상태는 로봇이 그 곳에 있음)."""
    setup = _setup(view, words)
    if setup is None:
        return
    factory, names, spec = setup
    for category, phrase in spec["rest"].items():
        for place in factory.stations(category):
            legs = ((factory.here, place),)
            traits = factory.route_traits(*legs)
            if traits is None or place == factory.here:
                continue
            goal = ((RELATION, place, ROBOT_AT, None, True),)
            yield Task(
                "go_to",
                phrase.format(target=names.station(place)),
                (goal,),
                ("go_to", category, factory.category(factory.here), traits),
                facts=situation(factory, legs),
            )


def _one_per_category(factory, stations: list[str], seed: str) -> list[str]:
    """종류마다 곳 하나 (물건마다 다른 곳이 고르게 뽑히도록 물건 id로 고른다)."""
    groups: dict[str, list[str]] = {}
    for station in sorted(stations):
        groups.setdefault(factory.category(station), []).append(station)
    return [group[sum(map(ord, seed)) % len(group)] for group in groups.values()]


@TASK_TYPES.register("deliver_two")
def deliver_two(view: SceneView, words: Words) -> Iterator[Task]:
    """출구 버퍼의 물건 둘을 서로 다른 다음 공정으로 한 번에."""
    setup = _setup(view, words)
    if setup is None:
        return
    factory, names, spec = setup
    items = [i for i in _outputs(factory, spec) if names.item(i)]
    for first, second in combinations(items, 2):
        # 받는 곳은 종류마다 하나만 본다 (signature가 곳의 종류를 보므로 같은 종류의 다른 곳은 같은 상황이다)
        for a in _one_per_category(factory, _destinations(factory, spec, first), first.ref):
            for b in _one_per_category(factory, _destinations(factory, spec, second), second.ref):
                if a == b:
                    continue
                legs = (
                    (factory.here, first.station),
                    (first.station, second.station),
                    (second.station, a),
                    (a, b),
                )
                # 들르는 순서가 정해지지 않아 설명문이 길을 하나로 정하지 않는다: 갈 수 있는지만 보고 길 성질은
                # signature에 넣지 않는다 (넣으면 설명문에 드러나지 않는 차이로 상황 수만 부푼다)
                if factory.route_traits(*legs) is None:
                    continue
                text = (
                    f"Bring {names.item(first)} to {names.place(a, spec['input'])} and "
                    f"{names.item(second)} to {names.place(b, spec['input'])}."
                )
                goal = (_goal(first, a, spec["input"]), _goal(second, b, spec["input"]))
                yield Task(
                    "deliver_two",
                    text,
                    (goal,),
                    (
                        "deliver_two",
                        tuple(sorted((first.category, second.category))),
                        tuple(sorted((factory.category(a), factory.category(b)))),
                        tuple(sorted((factory.running(a) is False, factory.running(b) is False))),
                    ),
                    facts=situation(factory, wait=(a, b)),
                )


@TASK_TYPES.register("clear_output")
def clear_output(view: SceneView, words: Words) -> Iterator[Task]:
    """출구 버퍼에 둘 이상 쌓인 물건을 모두 자동 창고로."""
    setup = _setup(view, words)
    if setup is None:
        return
    factory, names, spec = setup
    stacks: dict[str, list] = {}
    for item in _outputs(factory, spec):
        stacks.setdefault(item.station, []).append(item)
    for station, things in stacks.items():
        if len(things) < 2:
            continue
        for storage in factory.stations(spec["storage"]):
            legs = ((factory.here, station), (station, storage))
            traits = factory.route_traits(*legs)
            if traits is None:
                continue
            text = (
                f"Move everything on {names.place(station, spec['output'])} to "
                f"{names.station(storage)}."
            )
            goal = tuple((RELATION, t.ref, ON, storage, True) for t in things)
            yield Task(
                "clear_output",
                text,
                (goal,),
                (
                    "clear_output",
                    tuple(sorted(t.category for t in things)),
                    factory.category(station),
                    traits,
                ),
                facts=situation(factory, legs),
            )
