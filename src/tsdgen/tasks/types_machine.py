"""작업 유형들 (이어서): 넣고 돌리는 설비(부품 세척기, 필터 압착기)와 여닫는 칸(서랍)이 있는 작업장의 여러 단계 작업.

- machine_swap: 설비 안의 것을 꺼내 다른 곳에 두고, 다른 것을 넣고 문을 닫아 정해진 시간 동안 돌린다.
- unload_machine: 돌고 있는 설비를 끄고 안의 것을 꺼내 다른 곳에 둔다 (끄는 것이 먼저다).
- store_two_and_close: 물체 둘을 여닫는 곳(서랍, 문 달린 설비)에 넣고 닫는다.
어느 설비를 어떤 성질의 물체로 돌리는지는 도메인 프로필(heaters, heater_traits)이 정한다.
"""

from collections.abc import Iterator
from itertools import combinations

from tsdgen.tasks.base import TASK_TYPES, Task, Words, at, movable, origin, places, state
from tsdgen.tasks.view import SceneView, Thing

TURNED_ON = "turned_on"


def _the(words: Words, obj: Thing) -> str:
    return f"the {words.obj(obj)}"


def _machines(view: SceneView, words: Words) -> Iterator[tuple[Thing, str, str]]:
    """장면에 하나뿐이고 여닫는 문이 있는 돌리는 설비: (설비, 문 상태 키, 넣을 수 있는 성질)."""
    traits = words.spec.get("heater_traits") or {}
    for category in words.spec["heaters"]:
        machine = view.only(category)
        key = words.door_key(machine) if machine else None
        if key is not None:
            yield machine, key, traits.get(category, "microwavable")


def _inside(view: SceneView, machine: Thing) -> list[Thing]:
    """설비 안에 바로 놓인 물체들."""
    return [o for o in movable(view) if o.fixture == machine.ref]


def _seconds(words: Words, obj: Thing) -> int:
    pool = words.spec["durations_s"]  # 시간은 상황을 가르지 않는다: 물체마다 하나를 정해 쓴다
    return pool[sum(map(ord, obj.category)) % len(pool)]


@TASK_TYPES.register("machine_swap")
def machine_swap(view: SceneView, words: Words) -> Iterator[Task]:
    for machine, key, trait in _machines(view, words):
        name, door = words.fixture(machine), words.door_name(key)
        inside = _inside(view, machine)
        loads = [o for o in movable(view) if view.has(o, trait) and o not in inside]
        for out in inside:
            for place in places(view, words, out):
                for new in loads:
                    seconds = _seconds(words, new)
                    text = (f"Take {_the(words, out)} out of the {name} and put it {place.phrase}, then put "
                            f"{_the(words, new)} in the {name}, close the {door} and run it for {seconds} "
                            f"seconds.")
                    goal = (at(out, place), ("relation", new.ref, "in", machine.ref, True),
                            state(machine, key, False))
                    yield Task("machine_swap", text, (goal,),
                               ("machine_swap", out.category, new.category, machine.category, place.key,
                                origin(view, new), view.shut(machine)),
                               (seconds, machine.ref))


@TASK_TYPES.register("unload_machine")
def unload_machine(view: SceneView, words: Words) -> Iterator[Task]:
    for machine, _key, _trait in _machines(view, words):
        if machine.states.get(TURNED_ON) is not True:
            continue
        name = words.fixture(machine)
        for obj in _inside(view, machine):
            for place in places(view, words, obj):
                text = f"Turn off the {name}, then take {_the(words, obj)} out of it and put it {place.phrase}."
                goal = (state(machine, TURNED_ON, False), at(obj, place))
                yield Task("unload_machine", text, (goal,),
                           ("unload_machine", obj.category, machine.category, place.key, view.shut(machine)))


@TASK_TYPES.register("store_two_and_close")
def store_two_and_close(view: SceneView, words: Words) -> Iterator[Task]:
    for first, second in combinations(movable(view), 2):
        first_places = {p.key: p for p in places(view, words, first)}
        second_places = {p.key: p for p in places(view, words, second)}
        for place_key in sorted(set(first_places) & set(second_places), key=repr):
            place = first_places[place_key]
            door = words.door_of(place)
            if door is None:
                continue
            closer, key, name = door
            text = f"Put {_the(words, first)} and {_the(words, second)} {place.phrase} and close the {name}."
            goal = (at(first, place), at(second, second_places[place_key]),
                    state(closer, key, False))
            yield Task("store_two_and_close", text, (goal,),
                       ("store_two_and_close", tuple(sorted((first.category, second.category))), place_key,
                        tuple(sorted((origin(view, first), origin(view, second)), key=repr)),
                        closer.states.get(key)))
