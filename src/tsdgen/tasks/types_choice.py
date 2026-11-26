"""작업 유형들 (이어서): 대상을 고르거나 경우가 갈리는 작업, 상태만 바꾸는 작업.

- by_size: 같은 종류 둘 가운데 큰 것(작은 것)을 옮긴다. 세계 상태의 크기 순서로 대상을 정해야 한다.
- either: 물체 둘을 같은 종류의 용기 둘에 하나씩 나눠 담는다. 만족하는 경우가 둘이다.
- set_state: 문을 여닫거나 켜고 끈다. 지금과 반대인 상태만 목표로 삼는다.
- take_out: 도착지 없이 꺼내기만 한다 (원래 자리에 없어야 한다는 목표).
"""

from collections.abc import Iterator
from itertools import combinations

from tsdgen.tasks.base import (
    TASK_TYPES,
    Place,
    Task,
    Words,
    at,
    compartment_phrase,
    fits,
    movable,
    origin,
    places,
    source_phrase,
    state,
)
from tsdgen.tasks.view import SceneView


@TASK_TYPES.register("by_size")
def by_size(view: SceneView, words: Words) -> Iterator[Task]:
    for pair in view.pairs:
        if not all(t.volume for t in pair) or not all(view.has(t, "graspable") for t in pair):
            continue
        small, large = sorted(pair, key=lambda t: t.volume)
        if large.volume < small.volume * words.spec["size_ratio_min"]:
            continue
        for obj, word in ((large, "larger"), (small, "smaller")):
            for place in places(view, words, obj):
                if place.thing.category == obj.category:
                    continue
                text = f"Put the {word} {words.obj(obj)} {place.phrase}."
                yield Task("by_size", text, ((at(obj, place),),),
                           ("by_size", word, obj.category, place.key, origin(view, obj)))


@TASK_TYPES.register("either")
def either(view: SceneView, words: Words) -> Iterator[Task]:
    for relation, categories in words.spec["receptacles"].items():
        for pair in view.pairs:
            if pair[0].category not in categories:
                continue
            spots = [Place(t, relation, "", ("object", t.category)) for t in pair]
            name = words.obj(pair[0])
            taken = {t.ref for t in pair}  # 이미 그 용기에 담긴 것은 나눠 담을 대상이 아니다
            items = [o for o in movable(view) if fits(view, o, words.spec["receptacle_items"])
                     and o.holder not in taken]
            for first, second in combinations(items, 2):
                text = (f"Put the {words.obj(first)} {relation} one {name} and the {words.obj(second)} "
                        f"{relation} the other {name}.")
                cases = ((at(first, spots[0]), at(second, spots[1])),
                         (at(first, spots[1]), at(second, spots[0])))
                yield Task("either", text, cases,
                           ("either", tuple(sorted((first.category, second.category))), pair[0].category,
                            tuple(sorted((origin(view, first), origin(view, second)), key=repr))))


@TASK_TYPES.register("set_state")
def set_state(view: SceneView, words: Words) -> Iterator[Task]:
    for category, rule in words.spec["switches"].items():
        fixture = view.only(category)
        if fixture is None:
            continue
        targets = [(fixture, f"the {words.fixture(fixture)}")]
        targets += [(r, words.region(r, fixture)) for r in view.regions(fixture)]
        for target, phrase in targets:
            for key, (on_text, off_text) in rule.items():
                if phrase is None or not isinstance(target.states.get(key), bool):
                    continue
                want = not target.states[key]
                text = (on_text if want else off_text).format(target=phrase)
                yield Task("set_state", text, ((state(target, key, want),),),
                           ("set_state", category, target.category, target.rank, key, want))


@TASK_TYPES.register("take_out")
def take_out(view: SceneView, words: Words) -> Iterator[Task]:
    for obj in movable(view):
        source, inner = view.source(obj), view.compartment(obj)
        closer = inner or source
        if closer is None or obj.holder or words.door_key(closer) is None:
            continue
        where = compartment_phrase(words, inner) if inner else source_phrase(view, words, source)
        text = f"Take the {words.obj(obj)} out of {where[0]}."
        yield Task("take_out", text, ((("relation", obj.ref, "in", closer.ref, False),),),
                   ("take_out", obj.category, origin(view, obj)))
