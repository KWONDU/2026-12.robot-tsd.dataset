"""작업 유형들 (제조: 공장 바닥에서 작업장 사이를 오가는 운반). 지도가 있는 장면만 쓴다.

흐름은 RoboCup Logistics League 규칙집의 공정 순서(공급 → 가공 → 조립 → 검사 → 출하)를 따르고, 보관·공구·폐기·충전은
공장 운영에 늘 있는 일이다. signature에는 물건 종류, 출발·도착 곳의 종류, 길의 성질(막힌 통로·출입 제한 구역 때문에
돌아감, 한 방향 통로, 지나는 통로 수)을 넣는다. 그 성질이 다르면 설명문(공간 조건, 제약)이 달라진다.
"""

from collections.abc import Iterator

from tsdgen.tasks.base import RELATION, TASK_TYPES, Task, Words
from tsdgen.tasks.factory_view import FactoryView, FactoryWords, Item, target_ref
from tsdgen.tasks.view import SceneView

ON, IN, ROBOT_AT = "on", "in", "robot_at"


def _setup(view: SceneView, words: Words) -> tuple[FactoryView, FactoryWords, dict] | None:
    if not view.scene.get("map"):
        return None
    factory = FactoryView(view.scene, view.traits)
    return factory, FactoryWords(words.spec, factory), words.spec["factory"]


def _stage(spec: dict, category: str) -> int | None:
    """공정 순서에서의 자리 (공급 0 ... 출하 4). 공정 밖의 곳이면 None."""
    order = spec["flow"]
    return order.index(category) if category in order else None


def _destinations(factory: FactoryView, spec: dict, item: Item) -> list[str]:
    """그 물건을 보낼 다음 공정의 곳들 (입구 버퍼가 있는 곳)."""
    here = _stage(spec, factory.category(item.station))
    if here is None:
        return []
    return [
        s
        for s in factory.fixtures
        if (_stage(spec, factory.category(s)) or -1) > here and spec["input"] in factory.regions(s)
    ]


def _goal(item: Item, station: str, region: str | None, relation: str = ON) -> tuple:
    return (RELATION, item.ref, relation, target_ref(station, region), True)


def _outputs(factory: FactoryView, spec: dict) -> list[Item]:
    return [i for i in factory.items if i.region == spec["output"] and carried(factory, spec, i)]


def carried(factory: FactoryView, spec: dict, item: Item) -> bool:
    """공정으로 나르는 물건인가 (버릴 것은 폐기물 수거에서만 다룬다)."""
    return factory.has(item, spec["carry_trait"]) and not any(
        factory.has(item, t) for t in spec["waste_traits"]
    )


def situation(factory: FactoryView, legs: tuple = (), wait: tuple = (), **extra) -> tuple:
    """설명문이 담아야 하는 상황 사실: 지나야 하는 구간들에서 돌아가게 만드는 통로(까닭별), 물건을 넣으려고
    기다려야 하는 멈춘 곳, 그 밖의 값(시간 창, 가공 시간). 순서가 정해지지 않은 작업은 구간을 넘기지 않는다."""
    found: list[tuple] = []
    detour = factory.detour_facts(*legs)
    if detour:
        found.append(("detour", detour))  # 코드가 고른 길의 까닭별 통로 (난이도 단계와 기록용)
    moving = tuple((a, b) for a, b in legs if a != b)
    if moving:
        found.append(("legs", moving))  # 검수가 지도로 다시 따질 구간 (길이가 같은 가장 짧은 길이 여럿이어도 맞게)
    stopped = tuple(s for s in dict.fromkeys(wait) if factory.running(s) is False)
    if stopped:
        found.append(("wait_for", stopped))
    found.extend(sorted(extra.items()))
    return tuple(found)


def item_name(factory: FactoryView, names: FactoryWords, item: Item) -> str | None:
    """물건을 가리키는 말. 로봇이 지금 있는 곳의 물건이면 '여기 출구 버퍼의 그것'으로 (그 칸에 그 종류가 하나일 때)."""
    if not item.seen:
        return names.item(item)
    same = [
        i
        for i in factory.kinds[item.category]
        if (i.station, i.region) == (item.station, item.region)
    ]
    return (
        f"the {names.obj(item.category)} on the {names.spec['region_en'][item.region]} here"
        if len(same) == 1
        else None
    )


@TASK_TYPES.register("deliver")
def deliver(view: SceneView, words: Words) -> Iterator[Task]:
    """다음 공정으로 보내기. 받는 곳이 점검으로 멈춰 있으면 deliver_to_stopped(설명문에 기다림이 들어간다)."""
    setup = _setup(view, words)
    if setup is None:
        return
    factory, names, spec = setup
    for item in _outputs(factory, spec):
        name = item_name(factory, names, item)
        for dest in _destinations(factory, spec, item) if name else []:
            legs = ((factory.here, item.station), (item.station, dest))
            traits = factory.route_traits(*legs)
            if traits is None:
                continue
            stopped = factory.running(dest) is False
            # 멈춘 곳으로 보내면 기다림이, 로봇이 지금 있는 곳의 물건이면 집으러 가는 길이 빠져 설명문이 달라진다
            type_id = (
                "deliver_to_stopped"
                if stopped
                else ("deliver_from_here" if item.seen else "deliver")
            )
            verb = "Take" if item.seen else "Bring"
            text = f"{verb} {name} to {names.place(dest, spec['input'])}."
            yield Task(
                type_id,
                text,
                ((_goal(item, dest, spec["input"]),),),
                (
                    type_id,
                    item.category,
                    factory.category(item.station),
                    factory.category(dest),
                    item.seen,
                    traits,
                ),
                facts=situation(factory, legs, (dest,)),
            )


@TASK_TYPES.register("deliver_any_running")
def deliver_any_running(view: SceneView, words: Words) -> Iterator[Task]:
    """같은 종류 곳 가운데 돌고 있는 아무 곳으로 (멈춘 곳이 섞여 있을 때만). 만족하는 경우가 여럿이다."""
    setup = _setup(view, words)
    if setup is None:
        return
    factory, names, spec = setup
    for item in _outputs(factory, spec):
        name = names.item(item)
        later = _destinations(factory, spec, item) if name else []
        for category in sorted({factory.category(s) for s in later}):
            group = [s for s in later if factory.category(s) == category]
            running = [s for s in group if factory.running(s)]
            if len(running) < 2 or len(running) == len(group):
                continue
            label = words.spec["fixture_en"].get(category, category.replace("_", " "))
            text = f"Bring {name} to the input buffer of any {label} that is running."
            cases = tuple((_goal(item, s, spec["input"]),) for s in running)
            approach = ((factory.here, item.station),)  # 받는 곳이 여럿이라 그 뒤 길은 하나로 정해지지 않는다
            traits = factory.route_traits(*approach)
            yield Task(
                "deliver_any_running",
                text,
                cases,
                (
                    "deliver_any_running",
                    item.category,
                    factory.category(item.station),
                    category,
                    len(group) - len(running),
                    traits,
                ),
                facts=situation(factory, approach),
            )


@TASK_TYPES.register("chain")
def chain(view: SceneView, words: Words) -> Iterator[Task]:
    """공급대의 자재를 가공기에 넣고, 가공이 끝나면(정해진 시간) 다음 공정으로 (규칙집의 공정 사슬)."""
    setup = _setup(view, words)
    if setup is None:
        return
    factory, names, spec = setup
    machines = [s for s in factory.fixtures if factory.category(s) == spec["machining"]]
    for item in _outputs(factory, spec):
        name = names.item(item)
        if not name or factory.category(item.station) != spec["supply"]:
            continue
        seconds = spec["processing_s"][sum(map(ord, item.category)) % len(spec["processing_s"])]
        for machine in machines:
            for dest in [
                s
                for s in _destinations(factory, spec, item)
                if (_stage(spec, factory.category(s)) or 0) > (_stage(spec, spec["machining"]) or 0)
            ]:
                legs = ((factory.here, item.station), (item.station, machine), (machine, dest))
                traits = factory.route_traits(*legs)
                if traits is None:
                    continue
                text = (
                    f"Take {name} to {names.place(machine, spec['input'])}. When it has been machined "
                    f"(about {seconds} seconds), bring it to {names.place(dest, spec['input'])}."
                )
                yield Task(
                    "chain",
                    text,
                    ((_goal(item, dest, spec["input"]),),),
                    (
                        "chain",
                        item.category,
                        factory.category(dest),
                        factory.running(machine),
                        factory.running(dest),
                        traits,
                    ),
                    (seconds, machine),
                    situation(factory, legs, (machine, dest), duration_s=seconds),
                )
