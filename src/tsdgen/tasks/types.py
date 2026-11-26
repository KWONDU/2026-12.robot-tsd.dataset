"""작업 유형들. 유형 하나가 장면을 보고 그 장면에서 성립하는 작업(지시문, 목표 상태, signature)을 내놓는다.

유형은 설명문이 달라지는 축을 따라 나눴다: 무엇을 어디로 옮기는지, 어떤 상태로 만드는지, 여러 일을 어떻게
잇는지, 대상을 무엇으로 가르는지(크기), 만족하는 경우가 여럿인지. 출발지가 닫혀 있는지 같은 장면 상태는
signature에 들어가서, 같은 작업이라도 선행 조건이 달라지는 상황을 따로 센다.
이미 성립해 있는 상태를 목표로 삼는 작업(닫힌 문을 닫아)은 만들지 않는다.
"""

from collections.abc import Iterator
from itertools import combinations

from tsdgen.tasks.base import (
    TASK_TYPES,
    Task,
    Words,
    at,
    compartment_phrase,
    entry_shut,
    movable,
    origin,
    places,
    source_phrase,
    state,
)
from tsdgen.tasks.view import SceneView, Thing


def _the(words: Words, obj: Thing) -> str:
    return f"the {words.obj(obj)}"


@TASK_TYPES.register("relocate")
def relocate(view: SceneView, words: Words) -> Iterator[Task]:
    """물체 하나를 다른 곳으로 옮긴다."""
    for obj in movable(view):
        for place in places(view, words, obj):
            yield Task("relocate", f"Put {_the(words, obj)} {place.phrase}.", ((at(obj, place),),),
                       ("relocate", obj.category, place.key, origin(view, obj),
                        entry_shut(view, words, place)))


@TASK_TYPES.register("fetch_and_close")
def fetch_and_close(view: SceneView, words: Words) -> Iterator[Task]:
    """여닫는 가구(또는 스스로 여닫히는 칸) 안의 물체를 꺼내 다른 곳에 두고 그것을 닫는다."""
    for obj in movable(view):
        source, inner = view.source(obj), view.compartment(obj)
        closer = inner or source
        key = words.door_key(closer) if closer else None
        if key is None:
            continue
        first, again = (compartment_phrase(words, inner) if inner
                        else source_phrase(view, words, source))
        for place in places(view, words, obj):
            text = f"Take {_the(words, obj)} out of {first} and put it {place.phrase}. Then close {again}."
            if inner:  # 받는 곳도 서랍일 수 있으니 '그 서랍'이 가리키는 것이 헷갈리지 않게 먼저 닫는다
                text = f"Take {_the(words, obj)} out of {first} and close {again}. Then put it {place.phrase}."
            yield Task("fetch_and_close", text, ((at(obj, place), state(closer, key, False)),),
                       ("fetch_and_close", obj.category, place.key, origin(view, obj)))


@TASK_TYPES.register("store_and_close")
def store_and_close(view: SceneView, words: Words) -> Iterator[Task]:
    """물체를 여닫는 가구(또는 그 칸)에 넣고 문을 닫는다."""
    for obj in movable(view):
        for place in places(view, words, obj):
            door = words.door_of(place)
            if door is None:
                continue
            closer, key, name = door
            text = f"Put {_the(words, obj)} {place.phrase} and close the {name}."
            yield Task("store_and_close", text, ((at(obj, place), state(closer, key, False)),),
                       ("store_and_close", obj.category, place.key, origin(view, obj),
                        closer.states.get(key)))


@TASK_TYPES.register("heat")
def heat(view: SceneView, words: Words) -> Iterator[Task]:
    """데울 수 있는 것을 가열 기기에 넣고 문을 닫아 정해진 시간 동안 돌린다."""
    for category in words.spec["heaters"]:
        heater = view.only(category)
        key = words.door_key(heater) if heater else None
        if key is None:
            continue
        trait = (words.spec.get("heater_traits") or {}).get(category, "microwavable")
        for obj in movable(view):
            source = view.source(obj)
            if not view.has(obj, trait) or (source and source.ref == heater.ref):
                continue
            pool = words.spec["durations_s"]  # 시간은 상황을 가르지 않는다: 물체마다 하나를 정해 쓴다
            seconds = pool[sum(map(ord, obj.category)) % len(pool)]
            text = (f"Put {_the(words, obj)} in the {words.fixture(heater)}, close the door and "
                    f"run it for {seconds} seconds.")
            # 정해진 시간 동안 돌린 뒤의 끝 상태는 '안에 있고 문이 닫혀 있다'까지다 (가동은 시간 조건이 말한다)
            goal = ((("relation", obj.ref, "in", heater.ref, True), state(heater, key, False)),)
            yield Task("heat", text, goal,
                       ("heat", obj.category, category, origin(view, obj), view.shut(heater)),
                       (seconds, heater.ref))


@TASK_TYPES.register("cook")
def cook(view: SceneView, words: Words) -> Iterator[Task]:
    """조리 도구를 화구에 올리고 그 화구를 켠다."""
    for obj in movable(view):
        for place in places(view, words, obj):
            if place.thing.category not in words.spec["burners"]:
                continue
            text = f"Put {_the(words, obj)} {place.phrase} and turn that burner on."
            yield Task("cook", text, ((at(obj, place), state(place.thing, "turned_on", True)),),
                       ("cook", obj.category, place.key, origin(view, obj)))


@TASK_TYPES.register("wash")
def wash(view: SceneView, words: Words) -> Iterator[Task]:
    """씻을 수 있는 것을 개수대에 넣고 물을 튼다."""
    for category in words.spec["washers"]:
        sink = view.only(category)
        if sink is None or sink.states.get("turned_on") is not False:
            continue
        items = [o for o in movable(view) if view.has(o, "washable")
                 and (view.source(o) is None or view.source(o).ref != sink.ref)]
        for group in [*((o,) for o in items), *combinations(items, 2)]:
            names = " and ".join(_the(words, o) for o in group)
            goal = (*(("relation", o.ref, "in", sink.ref, True) for o in group),
                    state(sink, "turned_on", True))
            text = f"Put {names} in the {words.fixture(sink)} and turn on the faucet."
            origins = tuple(sorted((origin(view, o) for o in group), key=repr))
            yield Task("wash", text, (goal,),
                       ("wash", tuple(sorted(o.category for o in group)), origins))


@TASK_TYPES.register("relocate_two")
def relocate_two(view: SceneView, words: Words) -> Iterator[Task]:
    """물체 둘을 같은 곳으로, 또는 서로 다른 곳으로 옮긴다."""
    for first, second in combinations(movable(view), 2):
        first_places = {p.key: p for p in places(view, words, first)}
        second_places = {p.key: p for p in places(view, words, second)}
        for key in sorted(set(first_places) & set(second_places), key=repr):
            place, other = first_places[key], second_places[key]
            text = f"Put {_the(words, first)} and {_the(words, second)} {place.phrase}."
            yield Task("relocate_two", text, ((at(first, place), at(second, other)),),
                       ("relocate_two", tuple(sorted((first.category, second.category))), key,
                        tuple(sorted((origin(view, first), origin(view, second)), key=repr))))
        keys = sorted(set(first_places) | set(second_places), key=repr)
        for key_a, key_b in combinations(keys, 2):
            if key_a in first_places and key_b in second_places:
                place_a, place_b = first_places[key_a], second_places[key_b]
                text = (f"Put {_the(words, first)} {place_a.phrase} and {_the(words, second)} "
                        f"{place_b.phrase}.")
                yield Task("sort_two", text, ((at(first, place_a), at(second, place_b)),),
                           ("sort_two", first.category, key_a, second.category, key_b))


@TASK_TYPES.register("next_to")
def next_to(view: SceneView, words: Words) -> Iterator[Task]:
    """물체를 다른 물체 옆에 둔다."""
    for obj in movable(view):
        for other in view.objects:
            if other.ref in (obj.ref, obj.holder) or other.holder == obj.ref:
                continue
            text = f"Place {_the(words, obj)} next to {_the(words, other)}."
            yield Task("next_to", text, ((("relation", obj.ref, "near", other.ref, True),),),
                       ("next_to", obj.category, other.category, origin(view, obj), origin(view, other)))


@TASK_TYPES.register("load_and_move")
def load_and_move(view: SceneView, words: Words) -> Iterator[Task]:
    """물체를 용기에 담고, 그 용기를 다른 곳으로 옮긴다 (앞의 일이 끝나야 뒤의 일을 할 수 있다)."""
    for obj in movable(view):
        for holder in places(view, words, obj):
            if holder.key[0] != "object" or not view.has(holder.thing, "graspable"):
                continue
            for place in places(view, words, holder.thing):
                if place.key[0] == "object":
                    continue
                text = (f"Put {_the(words, obj)} {holder.phrase}, then put {_the(words, holder.thing)} "
                        f"{place.phrase}.")
                yield Task("load_and_move", text, ((at(obj, holder), at(holder.thing, place)),),
                           ("load_and_move", obj.category, holder.thing.category, place.key,
                            origin(view, obj), entry_shut(view, words, place)))

