"""같은 물체가 여럿이고 서로 쌓여 있는 장면(창고)의 작업 유형.

가정 주방의 유형은 장면에 하나뿐인 종류만 가리킨다("the mug"). 창고에는 같은 상자가 여럿이라 사람이 말하듯
가리키는 다른 방식이 필요하다: 그 종류 전부, 그 종류 가운데 아무거나 하나, 쌓인 것의 맨 위 것, 다른 물체 밑에
깔린 것. 쌓임은 설명문을 바꾼다(위의 것을 먼저 치워야 한다)는 점에서 signature에 넣는다.
장면 규격만 보므로 원천을 알지 못하고, 낱말과 상한은 도메인 프로필의 task_synthesis에서 읽는다.
"""

from collections import defaultdict
from collections.abc import Iterator
from itertools import combinations

from tsdgen.goals.scene_index import SUPPORT_PREDICATES, any_of
from tsdgen.tasks.base import RELATION, TASK_TYPES, Place, Task, Words, at, places
from tsdgen.tasks.view import SceneView, Thing

GRASPABLE, STACKABLE = "graspable", "stackable"
ON = "on"  # 쌓임(위에 얹힘). 담김(in)은 쌓임이 아니다: 담긴 것은 용기째 옮길 수 있고 '밑에 깔린' 것이 없다


class Stock:
    """장면의 모든 물체와 쌓임. SceneView는 종류가 하나뿐인 물체만 보여 주므로 여기서 전부를 본다."""

    def __init__(self, view: SceneView):
        self.view = view
        relations = view.scene.get("relations") or []
        self.under = {r["subj"]: r["obj"] for r in relations if r["pred"] in SUPPORT_PREDICATES}
        self.relation_of = {r["subj"]: r["pred"] for r in relations if r["pred"] in SUPPORT_PREDICATES}
        self.above: dict[str, list[str]] = defaultdict(list)
        for upper, lower in self.under.items():
            self.above[lower].append(upper)
        self.things: dict[str, Thing] = {}
        self.region_of: dict[str, str | None] = {}  # 설비에 바로 놓인 물체의 칸 이름
        for obj in view.scene.get("objects") or []:
            self.region_of[obj["id"]] = (obj.get("placement") or {}).get("region")
            holder = self.under.get(obj["id"])
            size = obj.get("size_m")
            self.things[obj["id"]] = Thing(
                obj["id"], obj["category"], {}, (obj.get("placement") or {}).get("fixture_id"),
                holder if holder in self.things or holder not in view.fixtures else None,
                volume=size[0] * size[1] * size[2] if size else None)
        self.groups: dict[str, list[Thing]] = defaultdict(list)  # 손으로 옮길 수 있는 것만
        self.kinds: dict[str, list[Thing]] = defaultdict(list)  # 종류별 전부
        for thing in self.things.values():
            self.kinds[thing.category].append(thing)
            if view.has(thing, GRASPABLE):
                self.groups[thing.category].append(thing)
        self.spots = {ref: self._spot(ref) for ref in self.things}

    def base(self, ref: str) -> str | None:
        """물체가 얹힌 설비의 id (다른 물체 위·안이면 따라 내려간다)."""
        seen = set()
        while ref in self.under and ref not in seen:
            seen.add(ref)
            ref = self.under[ref]
        return ref if ref in self.view.fixtures else None

    def spot(self, ref: str) -> tuple[str, str | None] | None:
        return self.spots.get(ref)

    def _spot(self, ref: str) -> tuple[str, str | None] | None:
        """물체가 결국 놓인 자리: (설비 id, 칸 이름). 다른 물체 위·안이면 맨 아래 물체의 자리다."""
        seen = set()
        while ref in self.under and self.under[ref] not in self.view.fixtures and ref not in seen:
            seen.add(ref)
            ref = self.under[ref]
        fixture = self.under.get(ref)
        return (fixture, self.region_of.get(ref)) if fixture in self.view.fixtures else None

    def pile(self, ref: str) -> list[str]:
        """그 물체 위·안에 (거슬러 올라가며) 놓인 물체들, 아래에서 위 순서."""
        found, frontier = [], [ref]
        while frontier:
            uppers = sorted(u for r in frontier for u in self.above.get(r, ()) if u in self.things)
            found += uppers
            frontier = uppers
        return found

    def at_spot(self, spot: tuple[str, str | None]) -> list[Thing]:
        """그 자리에 있는 물체 전부 (바로 놓인 것과 그 위·안에 쌓인 것)."""
        return [t for ref, t in self.things.items() if self.spot(ref) == spot]

    def spot_place(self, words: Words, spot: tuple[str, str | None]) -> Place | None:
        """자리를 가리키는 받는 곳. 말로 가리킬 수 없는 자리(같은 종류 설비가 여럿, 가운데 선반)는 None."""
        fixture_id, region = spot
        entry = self.view.fixtures[fixture_id]
        host = Thing(fixture_id, entry["category"], entry.get("states") or {})
        relation = self.spot_relation(spot)
        if region is None:
            if len(self.view.fixtures_of(host.category)) != 1:
                return None
            return Place(host, relation, f"{relation} the {words.fixture(host)}", (host.category,), None)
        if self.view.only(host.category) is None:
            return None
        target = next((r for r in self.view.regions(host) if r.ref.endswith(f"/{region}")), None)
        phrase = words.region(target, host) if target else None
        if phrase is None:
            return None
        return Place(target, relation, f"{relation} {phrase}", (host.category, target.category, target.rank),
                     host)

    def spot_relation(self, spot: tuple[str, str | None]) -> str:
        """그 자리에 바로 놓인 물체와 설비의 관계 (선반 위 on, 서랍·폐기함 안 in). 장면에 적힌 것을 따른다."""
        found = {self.relation_of.get(ref) for ref, at_spot in self.spots.items()
                 if at_spot == spot and self.under.get(ref) == spot[0]}
        return found.pop() if len(found) == 1 and None not in found else ON

    def on_top(self, ref: str) -> list[str]:
        """그 물체 위에 얹힌 물체들 (안에 담긴 것은 빼고)."""
        return [u for u in self.above.get(ref, ())
                if u in self.things and self.relation_of.get(u) == ON]

    def refer(self, words: Words, thing: Thing) -> str | None:
        """사람이 말하듯 물체 하나를 가리키는 말. 그 종류가 하나뿐이면 이름으로, 여럿이면 그 자리에 하나뿐일 때
        자리로 가린다('작업대 상판 위의 노란 크레이트'). 어느 쪽으로도 하나로 가릴 수 없으면 None."""
        same = self.kinds[thing.category]
        if len(same) == 1:
            return f"the {words.obj(thing)}"
        spot = self.spot(thing.ref)
        if spot is None or self.under.get(thing.ref) != spot[0]:
            return None  # 다른 물체 위·안에 있으면 그 자리에 '있다'고 말하지 않는다 (크레이트 위의 상자는 바닥에 있지 않다)
        if sum(1 for t in same if self.spot(t.ref) == spot) != 1:
            return None
        place = self.spot_place(words, spot)
        # 관계절로 가린다: "the X on the shelf"만 쓰면 뒤에 오는 받는 곳(on a pallet)과 붙어 어느 쪽이 자리인지 헷갈린다
        return f"the {words.obj(thing)} that is {place.phrase}" if place else None

    def referable(self, words: Words, trait: str | None = None) -> list[tuple[Thing, str]]:
        """가리킬 수 있는 물체와 그 말 (trait를 주면 그 성질이 있는 것만)."""
        found = []
        for thing in self.things.values():
            if trait and not self.view.has(thing, trait):
                continue
            phrase = self.refer(words, thing)
            if phrase:
                found.append((thing, phrase))
        return found

    def origin(self, things: list[Thing]) -> tuple:
        """signature에 넣는 출발지: 물체들이 얹힌 설비의 종류들."""
        bases = {self.base(t.ref) for t in things}
        return tuple(sorted(self.view.fixtures[b]["category"] for b in bases if b))

    def covered(self, thing: Thing, own: set[str]) -> bool:
        """그 물체 위나 안에 묶음 밖의 다른 물체가 놓여 있는가 (먼저 치워야 닿는다)."""
        return any(upper not in own for upper in self.above.get(thing.ref, ()))

    def destinations(self, words: Words, things: list[Thing]) -> list[Place]:
        """묶음의 물체를 모두 둘 수 있는 곳: 물체마다 둘 수 있는 곳의 교집합에서 지금 있는 설비를 뺀다."""
        probes = [Thing(t.ref, t.category, {}, self.base(t.ref), t.holder) for t in things]
        shared = None
        for probe in probes:
            found = {p.key: p for p in places(self.view, words, probe)}
            shared = found if shared is None else {k: v for k, v in shared.items() if k in found}
        own = {t.ref for t in things}
        found = [p for p in (shared or {}).values() if not set(p.thing.ref.split("|")) & own]
        return found + (self._stack_places(words, things[0]) if len(things) == 1 else [])

    def _stack_places(self, words: Words, thing: Thing) -> list[Place]:
        """물체 하나를 쌓을 수 있는 다른 물체: 가리킬 수 있고 위가 비어 있으며 더 큰 상자류."""
        if not self.view.has(thing, STACKABLE) or thing.volume is None:
            return []
        ratio = words.spec["size_ratio_min"]
        return [Place(other, "on", f"on top of {phrase}", ("stack", other.category))
                for other, phrase in self.referable(words, STACKABLE)
                if other.ref != thing.ref and not self.above.get(other.ref)
                and self.under.get(thing.ref) != other.ref
                and (other.volume or 0) >= thing.volume * ratio]


def _plural(words: Words, category: str) -> str:
    return words.spec["object_plural_en"].get(category, category.replace("_", " ") + "s")


@TASK_TYPES.register("move_all")
def move_all(view: SceneView, words: Words) -> Iterator[Task]:
    """그 종류의 물체를 전부 한 곳으로 옮긴다."""
    stock = Stock(view)
    for category, things in stock.groups.items():
        if not 2 <= len(things) <= words.spec["group_max"]:
            continue
        own = {t.ref for t in things}
        covered = any(stock.covered(t, own) for t in things)
        for place in stock.destinations(words, things):
            text = f"Put all the {_plural(words, category)} {place.phrase}."
            yield Task("move_all", text, (tuple(at(t, place) for t in things),),
                       ("move_all", category, len(things), place.key, stock.origin(things), covered))


@TASK_TYPES.register("move_one")
def move_one(view: SceneView, words: Words) -> Iterator[Task]:
    """그 종류 가운데 아무거나 하나를 옮긴다 (어느 것이든 되므로 목표 상태의 주어가 '그 종류 아무 것'이다)."""
    stock = Stock(view)
    for category, things in stock.groups.items():
        if len(things) < 2:
            continue
        subject = any_of(sorted(t.ref for t in things))
        for place in stock.destinations(words, things):
            text = f"Put one of the {_plural(words, category)} {place.phrase}."
            goal = ((RELATION, subject, place.relation, place.thing.ref, True),)
            yield Task("move_one", text, (goal,),
                       ("move_one", category, place.key, stock.origin(things)))


def piles(stock: Stock, category: str) -> int:
    """그 종류끼리 얹힌 더미의 수 (맨 위가 비었든 아니든). '그 종류의 더미'로 가리키려면 하나뿐이어야 한다."""

    def on_same(thing: Thing) -> bool:
        lower = stock.things.get(stock.under.get(thing.ref, ""))
        stacked = stock.relation_of.get(thing.ref) == ON
        return lower is not None and lower.category == category and stacked

    return sum(1 for t in stock.kinds[category]
               if not on_same(t)
               and any(stock.things[u].category == category for u in stock.on_top(t.ref)))


def _stacks(stock: Stock) -> dict[str, list[list[Thing]]]:
    """같은 종류끼리 쌓인 묶음(아래에서 위로)을 종류별로. 맨 위에 다른 것이 없는 것만."""
    found: dict[str, list[list[Thing]]] = defaultdict(list)
    for thing in stock.things.values():
        lower = stock.things.get(stock.under.get(thing.ref, ""))
        stacked = stock.relation_of.get(thing.ref) == ON
        if lower is not None and lower.category == thing.category and stacked:
            continue  # 묶음의 맨 아래가 아니다
        chain = [thing]
        while True:
            uppers = [stock.things[u] for u in stock.on_top(chain[-1].ref)]
            same = [u for u in uppers if u.category == thing.category]
            if len(uppers) != 1 or not same:
                break
            chain.append(same[0])
        if len(chain) > 1 and not stock.above.get(chain[-1].ref):
            found[thing.category].append(chain)
    return found


@TASK_TYPES.register("unstack_top")
def unstack_top(view: SceneView, words: Words) -> Iterator[Task]:
    """쌓인 것의 맨 위 것을 내려 다른 곳에 둔다 (그 종류의 쌓인 묶음이 장면에 하나일 때만 가리킬 수 있다)."""
    stock = Stock(view)
    for category, chains in _stacks(stock).items():
        unique = len(chains) == 1 and piles(stock, category) == 1
        if not unique or not view.has(chains[0][-1], GRASPABLE):
            continue
        top, height = chains[0][-1], min(len(chains[0]), words.spec["stack_height_max"])
        for place in stock.destinations(words, [top]):
            text = (f"Take the top {words.obj(top)} off the stack of {_plural(words, category)} "
                    f"and put it {place.phrase}.")
            yield Task("unstack_top", text, ((at(top, place),),),
                       ("unstack_top", category, height, place.key, stock.origin([top])))


@TASK_TYPES.register("uncover")
def uncover(view: SceneView, words: Words) -> Iterator[Task]:
    """다른 물체 밑에 깔린 물체를 꺼내 다른 곳에 둔다 (위의 것을 먼저 치워야 한다)."""
    stock = Stock(view)
    for thing, _phrase in stock.referable(words, GRASPABLE):
        uppers = [stock.things[u] for u in stock.on_top(thing.ref)]
        if len(uppers) != 1 or not view.has(uppers[0], GRASPABLE):
            continue
        upper = uppers[0]
        if sum(1 for t in stock.kinds[thing.category]
               if any(stock.things[u].category == upper.category
                      for u in stock.on_top(t.ref))) != 1:
            continue  # '그 밑에 깔린 것'으로 하나를 가릴 수 없다
        for place in stock.destinations(words, [thing]):
            text = (f"Take the {words.obj(thing)} out from under the {words.obj(upper)} "
                    f"and put it {place.phrase}.")
            yield Task("uncover", text, ((at(thing, place),),),
                       ("uncover", thing.category, upper.category, place.key, stock.origin([thing])))


@TASK_TYPES.register("stack_on")
def stack_on(view: SceneView, words: Words) -> Iterator[Task]:
    """물체를 다른 물체 위에 쌓는다. 더 큰 것 위에 더 작은 것만 쌓고, 위가 비어 있는 것끼리만 한다."""
    stock = Stock(view)
    free = [(t, p) for t, p in stock.referable(words, STACKABLE) if not stock.above.get(t.ref)]
    for upper, upper_phrase in free:
        for lower, lower_phrase in free:
            sizes = upper.volume, lower.volume
            if upper.ref == lower.ref or not view.has(upper, GRASPABLE) or None in sizes:
                continue
            too_big = sizes[0] * words.spec["size_ratio_min"] > sizes[1]
            if too_big or stock.under.get(upper.ref) == lower.ref:
                continue
            text = f"Stack {upper_phrase} on top of {lower_phrase}."
            goal = ((RELATION, upper.ref, "on", lower.ref, True),)
            yield Task("stack_on", text, (goal,),
                       ("stack_on", upper.category, lower.category, stock.origin([upper]),
                        stock.origin([lower])))


@TASK_TYPES.register("sort_groups")
def sort_groups(view: SceneView, words: Words) -> Iterator[Task]:
    """두 종류의 물체를 종류별로 서로 다른 곳에 모은다 (두 일이 이어지는 작업)."""
    stock = Stock(view)
    limit = words.spec["group_max"]
    groups = {c: things for c, things in stock.groups.items() if len(things) <= limit}
    for first, second in combinations(sorted(groups), 2):
        a_things, b_things = groups[first], groups[second]
        own = {t.ref for t in (*a_things, *b_things)}
        a_places = {p.key: p for p in stock.destinations(words, a_things)}
        b_places = {p.key: p for p in stock.destinations(words, b_things)}
        covered = any(stock.covered(t, own) for t in (*a_things, *b_things))
        for a_key in sorted(a_places, key=repr):
            for b_key in sorted(b_places, key=repr):
                if a_key == b_key or "stack" in (a_key[0], b_key[0]):
                    continue
                a_place, b_place = a_places[a_key], b_places[b_key]
                text = (f"Put {_group(words, first, a_things)} {a_place.phrase} and "
                        f"{_group(words, second, b_things)} {b_place.phrase}.")
                goal = (*(at(t, a_place) for t in a_things), *(at(t, b_place) for t in b_things))
                yield Task("sort_groups", text, (goal,),
                           ("sort_groups", first, len(a_things), a_key, second, len(b_things), b_key,
                            stock.origin(a_things), stock.origin(b_things), covered))


def _group(words: Words, category: str, things: list[Thing]) -> str:
    """묶음을 가리키는 말: 하나면 'the X', 여럿이면 'all the Xs'."""
    return f"the {words.obj(things[0])}" if len(things) == 1 else f"all the {_plural(words, category)}"

