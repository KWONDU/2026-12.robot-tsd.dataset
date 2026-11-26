"""같은 물체가 여럿이고 서로 쌓이거나 담긴 장면(창고·작업장)의 여러 단계 작업 유형.

types_stock.py의 한 단계 유형(전부·하나·맨 위 것·깔린 것을 옮기기)을 잇는다. 유형마다 설명문이 달라지는 축이
다르다: 자리로 가리키기(작업대 위에 있는 것 전부), 두 물체의 자리 바꾸기, 용기 비우기, 용기에 담아 옮기기,
쌓는 순서(어느 쪽이 아래든 되면 경우가 둘), 쌓인 것을 펼치거나 그대로 옮기기, 서로 다른 것을 한 곳으로 모으기.
장면 규격만 보므로 원천을 알지 못하고, 낱말과 상한은 도메인 프로필의 task_synthesis에서 읽는다.
"""

from collections.abc import Iterator
from itertools import combinations

from tsdgen.goals.scene_index import any_of
from tsdgen.tasks.base import RELATION, TASK_TYPES, Place, Task, Words, at, fits
from tsdgen.tasks.types_stock import GRASPABLE, STACKABLE, Stock, _plural, _stacks, piles
from tsdgen.tasks.view import SceneView, Thing

HOLDER = "holder"  # 다른 물체를 담는 용기의 성질 (프로필의 holder_trait가 바꾼다: 가정은 receptacle)
CLEAR_WORD = {"in": "out of"}  # 자리를 비울 때 그 자리와의 관계에 맞는 말 (없으면 off)
BRING_WORD = {"on": "to"}
INTO_WORD = {"on": "onto", "in": "into"}  # 옮겨 갈 곳 (그냥 on이면 "선반 위의 더미"로 읽힌다)  # 가져다 놓는 곳을 'to'로 말할 수 있는 관계 (안에 넣는 곳은 'and put them in')


def _the(words: Words, thing: Thing) -> str:
    return f"the {words.obj(thing)}"


def _spot_key(stock: Stock, spot: tuple[str, str | None]) -> tuple:
    """signature에 넣는 자리: 설비 종류와 칸 종류·순서."""
    fixture_id, region = spot
    return (stock.view.fixtures[fixture_id]["category"], region)


def _spots(stock: Stock) -> dict[tuple, list[Thing]]:
    found: dict[tuple, list[Thing]] = {}
    for ref, thing in stock.things.items():
        spot = stock.spot(ref)
        if spot is not None:
            found.setdefault(spot, []).append(thing)
    return found


@TASK_TYPES.register("clear_spot")
def clear_spot(view: SceneView, words: Words) -> Iterator[Task]:
    """한 자리(작업대 상판, 랙 선반)에 있는 것을 전부 다른 곳으로 옮겨 그 자리를 비운다."""
    stock = Stock(view)
    for spot, things in _spots(stock).items():
        source = stock.spot_place(words, spot)
        if source is None or not 2 <= len(things) <= words.spec["clear_max"]:
            continue
        if not all(view.has(t, GRASPABLE) for t in things):
            continue
        stacked = any(stock.under.get(t.ref) in stock.things for t in things)
        kinds = tuple(sorted(t.category for t in things))
        for place in stock.destinations(words, things):
            where = source.phrase.removeprefix(f"{source.relation} ")
            text = (f"Clear everything {CLEAR_WORD.get(source.relation, 'off')} {where} "
                    f"and put it all {place.phrase}.")
            yield Task("clear_spot", text, (tuple(at(t, place) for t in things),),
                       ("clear_spot", _spot_key(stock, spot), kinds, place.key, stacked))


@TASK_TYPES.register("move_from_place")
def move_from_place(view: SceneView, words: Words) -> Iterator[Task]:
    """같은 종류가 여러 자리에 있을 때 자리로 골라 옮긴다 ('카트에 있는 노란 크레이트')."""
    stock = Stock(view)
    for category, things in stock.groups.items():
        by_spot: dict[tuple, list[Thing]] = {}
        for thing in things:
            spot = stock.spot(thing.ref)
            if spot is not None:
                by_spot.setdefault(spot, []).append(thing)
        if len(by_spot) < 2:
            continue
        for spot, chosen in by_spot.items():
            source = stock.spot_place(words, spot)
            if source is None or len(chosen) > words.spec["group_max"]:
                continue
            if any(stock.under.get(t.ref) != spot[0] for t in chosen):
                continue  # 다른 물체 위·안에 있는 것까지 '그 자리에 있는 것'이라고 하면 틀린 말이 된다
            name = (f"the {words.obj(chosen[0])}" if len(chosen) == 1
                    else f"the {_plural(words, category)}")
            own = {t.ref for t in chosen}
            covered = any(stock.covered(t, own) for t in chosen)
            for place in stock.destinations(words, chosen):
                text = f"Put {name} that {'is' if len(chosen) == 1 else 'are'} {source.phrase} {place.phrase}."
                yield Task("move_from_place", text, (tuple(at(t, place) for t in chosen),),
                           ("move_from_place", category, len(chosen), _spot_key(stock, spot),
                            place.key, covered))


def _bare(stock: Stock, words: Words) -> list[tuple[Thing, str, Place]]:
    """가리킬 수 있는 물체 가운데 설비에 바로 놓였고 위가 비어 있는 것과 그 말, 그 자리."""
    found = []
    for obj, phrase in stock.referable(words, GRASPABLE):
        spot = stock.spot(obj.ref)
        if spot is None or stock.under.get(obj.ref) != spot[0] or stock.above.get(obj.ref):
            continue
        fixture_id, region = spot
        entry = stock.view.fixtures[fixture_id]
        host = Thing(fixture_id, entry["category"], entry.get("states") or {})
        target = (next((r for r in stock.view.regions(host) if r.ref.endswith(f"/{region}")), None)
                  if region else host)
        if target is not None:
            relation = stock.spot_relation(spot)
            found.append((obj, phrase, Place(target, relation, "", _spot_key(stock, spot), host)))
    return found


def accepts(view: SceneView, words: Words, place: Place, obj: Thing) -> bool:
    """그 자리가 이 물체를 받는가: 프로필의 받는 곳 규칙(destinations, region_destinations)이 있으면 그것을 따른다.
    규칙이 없는 자리(바닥 등)는 받는다. 필터 압착기에 엔진 오일 통을 넣는 맞바꾸기를 막는다."""
    host = place.host or place.thing
    if place.thing is host:
        rule = words.spec["destinations"].get(host.category)
    else:
        rule = (words.spec["region_destinations"].get(host.category) or {}).get(place.thing.category)
    return rule is None or fits(view, obj, rule)


@TASK_TYPES.register("swap_places")
def swap_places(view: SceneView, words: Words) -> Iterator[Task]:
    """두 물체의 자리를 서로 바꾼다 (한쪽을 잠시 다른 데 두어야 한다). 서로의 자리가 받는 물체여야 한다."""
    stock = Stock(view)
    for (first, name_a, here), (second, name_b, there) in combinations(_bare(stock, words), 2):
        if here.thing.ref == there.thing.ref or first.category == second.category:
            continue  # 같은 종류끼리 바꾸면 끝 상태가 처음과 같아 보인다
        if not (accepts(view, words, there, first) and accepts(view, words, here, second)):
            continue
        text = f"Swap the places of {name_a} and {name_b}."
        goal = (at(first, there), at(second, here))
        yield Task("swap_places", text, (goal,),
                   ("swap_places", tuple(sorted(((first.category, here.key),
                                                 (second.category, there.key))))))


def _holders(stock: Stock, words: Words) -> list[tuple[Thing, str, list[Thing]]]:
    """가리킬 수 있는 용기와 그 말, 그 안에 든 물체들."""
    found = []
    for holder, phrase in stock.referable(words, words.spec.get("holder_trait", HOLDER)):
        inside = [stock.things[u] for u in stock.above.get(holder.ref, ()) if u in stock.things]
        found.append((holder, phrase, [t for t in inside if stock.view.has(t, GRASPABLE)]))
    return found


def _them(things: list) -> str:
    return "it" if len(things) == 1 else "them"


@TASK_TYPES.register("empty_container")
def empty_container(view: SceneView, words: Words) -> Iterator[Task]:
    """용기 안의 것을 모두 꺼내 다른 곳에 둔다. 빈 용기도 옮기라고 하면 일이 이어진다."""
    stock = Stock(view)
    for holder, name, inside in _holders(stock, words):
        if not 1 <= len(inside) <= words.spec["group_max"]:
            continue
        kinds = tuple(sorted(t.category for t in inside))
        destinations = [p for p in stock.destinations(words, inside)
                        if p.thing.ref != holder.ref and p.thing.category != holder.category]
        for place in destinations:
            text = f"Take everything out of {name} and put {_them(inside)} {place.phrase}."
            yield Task("empty_container", text, (tuple(at(t, place) for t in inside),),
                       ("empty_container", holder.category, kinds, place.key))
            if not view.has(holder, GRASPABLE):
                continue
            for second in stock.destinations(words, [holder]):
                if second.key == place.key:
                    continue
                text = (f"Take everything out of {name}, put {_them(inside)} {place.phrase}, "
                        f"then put the empty {words.obj(holder)} {second.phrase}.")
                goal = (*(at(t, place) for t in inside), at(holder, second))
                yield Task("empty_and_move", text, (goal,),
                           ("empty_and_move", holder.category, kinds, place.key, second.key))


def _small_groups(stock: Stock, words: Words) -> list[tuple[str, list[Thing]]]:
    """용기에 담을 작은 물건: (가리키는 말, 물체들). 종류 하나 전부이거나 하나뿐인 것."""
    found = []
    for category, things in stock.groups.items():
        if not fits(stock.view, things[0], words.spec["receptacle_items"]):
            continue
        if len(things) > words.spec["group_max"]:
            continue
        name = _the(words, things[0]) if len(things) == 1 else f"all the {_plural(words, category)}"
        found.append((name, things))
    return found


@TASK_TYPES.register("pack_items")
def pack_items(view: SceneView, words: Words) -> Iterator[Task]:
    """작은 물건을 용기에 담는다. 담은 용기를 다른 곳으로 옮기라고 하면 일이 이어진다."""
    stock = Stock(view)
    for holder, holder_name, inside in _holders(stock, words):
        spot = Place(holder, "in", f"in {holder_name}", ("object", holder.category))
        for name, things in _small_groups(stock, words):
            if any(stock.under.get(t.ref) == holder.ref for t in things):
                continue
            count = len(things) if len(things) > 1 else 1
            yield Task("pack_items", f"Put {name} in {holder_name}.",
                       (tuple(at(t, spot) for t in things),),
                       ("pack_items", things[0].category, count, holder.category,
                        bool(inside), stock.origin(things)))
            if not view.has(holder, GRASPABLE):
                continue
            for place in stock.destinations(words, [holder]):
                text = f"Put {name} in {holder_name}, then put the {words.obj(holder)} {place.phrase}."
                goal = (*(at(t, spot) for t in things), at(holder, place))
                yield Task("pack_and_move", text, (goal,),
                           ("pack_and_move", things[0].category, count, holder.category, place.key))


@TASK_TYPES.register("stack_pair")
def stack_pair(view: SceneView, words: Words) -> Iterator[Task]:
    """두 물체를 한 곳에 쌓는다. 크기가 비슷하면 어느 쪽이 아래든 되고(경우 둘), 다르면 큰 것을 아래에 둔다."""
    stock = Stock(view)
    free = [(t, p) for t, p in stock.referable(words, STACKABLE)
            if view.has(t, GRASPABLE) and t.volume and not stock.above.get(t.ref)]
    ratio = words.spec["size_ratio_min"]
    for (a, name_a), (b, name_b) in combinations(free, 2):
        (big, big_name), (small, small_name) = sorted(((a, name_a), (b, name_b)),
                                                      key=lambda pair: -pair[0].volume)
        for place in stock.destinations(words, [big, small]):
            if place.key and place.key[0] == "stack":
                continue
            names = tuple(sorted((a.category, b.category)))
            if big.volume < small.volume * ratio:
                text = f"Stack {name_a} and {name_b} {place.phrase}, one on top of the other."
                cases = ((at(a, place), (RELATION, b.ref, "on", a.ref, True)),
                         (at(b, place), (RELATION, a.ref, "on", b.ref, True)))
                yield Task("stack_pair", text, cases, ("stack_pair", names, place.key))
                continue
            text = f"Put {big_name} {place.phrase}, then stack {small_name} on top of it."
            goal = (at(big, place), (RELATION, small.ref, "on", big.ref, True))
            yield Task("move_then_stack", text, (goal,),
                       ("move_then_stack", big.category, small.category, place.key))


@TASK_TYPES.register("unstack_all")
def unstack_all(view: SceneView, words: Words) -> Iterator[Task]:
    """같은 종류끼리 쌓인 것을 풀어 하나씩 늘어놓거나, 쌓인 순서 그대로 다른 곳으로 옮긴다."""
    stock = Stock(view)
    for category, chains in _stacks(stock).items():
        chain = chains[0]
        unique = len(chains) == 1 and piles(stock, category) == 1
        if not unique or len(chain) > words.spec["group_max"]:
            continue
        if not all(view.has(t, GRASPABLE) for t in chain):
            continue
        height = len(chain)
        plural = _plural(words, category)
        for place in stock.destinations(words, chain):
            text = f"Unstack the {plural} and put each of them {place.phrase} on its own."
            yield Task("unstack_all", text, (tuple(at(t, place) for t in chain),),
                       ("unstack_all", category, height, place.key, stock.origin(chain)))
            where = place.phrase.replace(f"{place.relation} ", f"{INTO_WORD[place.relation]} ", 1)
            text = f"Move the stack of {plural} {where}, keeping them stacked in the same order."
            pairs = zip(chain, chain[1:], strict=False)
            goal = (at(chain[0], place),
                    *((RELATION, upper.ref, "on", lower.ref, True) for lower, upper in pairs))
            yield Task("restack", text, (goal,),
                       ("restack", category, height, place.key, stock.origin(chain)))


@TASK_TYPES.register("gather")
def gather(view: SceneView, words: Words) -> Iterator[Task]:
    """서로 다른 물건 둘을 한 곳으로 가져온다. 같은 종류가 여럿이면 그 가운데 하나다 ('테이프 하나')."""
    stock = Stock(view)
    picks = []
    for category, things in stock.groups.items():
        if len(things) == 1:
            picks.append((_the(words, things[0]), things[0].ref, things, "the"))
        else:
            picks.append((f"one of the {_plural(words, category)}",
                          any_of(sorted(t.ref for t in things)), things, "one"))
    for (name_a, ref_a, a, mode_a), (name_b, ref_b, b, mode_b) in combinations(picks, 2):
        for place in stock.destinations(words, [a[0], b[0]]):
            if place.key and place.key[0] in ("stack", "object"):
                continue
            goal = ((RELATION, ref_a, place.relation, place.thing.ref, True),
                    (RELATION, ref_b, place.relation, place.thing.ref, True))
            lead = BRING_WORD.get(place.relation)
            where = (place.phrase.replace(f"{place.relation} ", f"{lead} ", 1) if lead
                     else f"and put them {place.phrase}")
            yield Task("gather", f"Bring {name_a} and {name_b} {where}.", (goal,),
                       ("gather", tuple(sorted(((a[0].category, mode_a), (b[0].category, mode_b)))),
                        place.key))
