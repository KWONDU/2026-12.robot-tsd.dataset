"""지도가 있는 장면(제조: 작업장 사이 운반)을 작업 합성이 보는 창: 곳, 곳의 칸, 물건, 통로로 가는 길.

길은 지도의 통로로 찾는다. 막힌 통로와 출입 제한 구역을 지나는 통로는 쓰지 않고, 한 방향 통로는 그 방향으로만 쓴다.
signature에는 돌아가야 하는 까닭(막힘, 출입 제한 구역, 한 방향 통로를 거꾸로 감)을 넣는다. 그 까닭이 다르면
설명문의 공간 제약이 달라지므로 다른 상황이다. 지나는 통로 수처럼 설명문에 드러나지 않는 성질은 넣지 않는다.
"""

import heapq
import itertools
import re
from collections import defaultdict
from dataclasses import dataclass

from tsdgen.models import region_id

DETOUR_CLOSED, DETOUR_RESTRICTED, DETOUR_ONE_WAY = "closed", "restricted", "one_way"


@dataclass(frozen=True)
class Item:
    ref: str
    category: str
    station: str  # 놓인 곳 (가구 id)
    region: str | None  # 놓인 칸 (input, output, shelf3 ...)
    seen: bool  # 로봇이 지금 보는 것인가 (아니면 공장 시스템이 알려 준 것)


class FactoryView:
    def __init__(self, scene: dict, traits: dict[str, list[str]]):
        self.scene, self.traits = scene, traits
        self.fixtures = {f["id"]: f for f in scene.get("fixtures") or []}
        self.map = scene.get("map") or {}
        self.here = (scene.get("agent") or {}).get("base_near_fixture_id")
        self.items = [
            Item(
                o["id"],
                o["category"],
                o["placement"]["fixture_id"],
                o["placement"].get("region"),
                o.get("known_by") != "system",
            )
            for o in scene.get("objects") or []
            if o.get("placement")
        ]
        self.kinds: dict[str, list[Item]] = defaultdict(list)
        for item in self.items:
            self.kinds[item.category].append(item)
        self.restricted = {z["name_ko"] for z in self.map.get("zones") or [] if z.get("restricted")}
        self._routes: dict[tuple, tuple[float, list[dict]] | None] = {}
        self._graphs: dict[bool, dict] = {}
        self._blocked: dict[tuple, dict | None] = {}

    def has(self, item: Item, trait: str) -> bool:
        return trait in self.traits.get(item.category, ())

    def stations(self, category: str) -> list[str]:
        return [f for f, e in self.fixtures.items() if e["category"] == category]

    def category(self, station: str) -> str:
        return self.fixtures[station]["category"]

    def regions(self, station: str) -> list[str]:
        return [r["key"] for r in self.fixtures[station].get("regions") or []]

    def running(self, station: str) -> bool | None:
        return (self.fixtures[station].get("states") or {}).get("turned_on")

    def _edges(self, *, blocked: bool) -> dict[str, list[tuple[str, float, dict]]]:
        graph: dict[str, list[tuple[str, float, dict]]] = defaultdict(list)
        for aisle in self.map.get("aisles") or []:
            passes_restricted = any(name in (aisle.get("note") or "") for name in self.restricted)
            if blocked and (aisle.get("closed") or passes_restricted):
                continue
            graph[aisle["a"]].append((aisle["b"], aisle["length_m"], aisle))
            if not (blocked and aisle.get("one_way")):
                graph[aisle["b"]].append((aisle["a"], aisle["length_m"], aisle))
        return graph

    def route(
        self, start: str, goal: str, *, blocked: bool = True
    ) -> tuple[float, list[dict]] | None:
        """가장 짧은 길 (길이, 지나는 통로들). blocked가 참이면 막힌 통로·출입 제한 구역·반대 방향을 피한다.
        한 장면 안에서 같은 길은 한 번만 찾는다."""
        key = (start, goal, blocked)
        if key not in self._routes:
            if blocked not in self._graphs:
                self._graphs[blocked] = self._edges(blocked=blocked)
            self._routes[key] = _shortest(self._graphs[blocked], start, goal)
        return self._routes[key]

    def blocked(self, start: str, goal: str) -> dict[str, tuple[str, ...]] | None:
        """한 구간의 가장 짧은 길(막힘을 따지지 않은 길)에 쓸 수 없는 통로가 있어 돌아가야 할 때, 까닭별 그 통로 이름.
        까닭은 막힘, 출입 제한 구역 통과, 한 방향 통로를 거꾸로 감이다. 돌아갈 필요가 없으면 빈 dict,
        아예 갈 수 없으면 None. 설명문의 공간 제약이 담아야 하는 사실이고, 한 장면 안에서 구간마다 한 번만 찾는다."""
        key = (start, goal)
        if key not in self._blocked:
            self._blocked[key] = self._why_detour(start, goal)
        return self._blocked[key]

    def _why_detour(self, start: str, goal: str) -> dict[str, tuple[str, ...]] | None:
        if start == goal:
            return {}
        allowed, free = self.route(start, goal), self.route(start, goal, blocked=False)
        if allowed is None or free is None:
            return None
        if allowed[0] <= free[0] + 1e-6:
            return {}
        causes: dict[str, list[str]] = defaultdict(list)
        here = start
        for aisle in free[1]:  # 막힘을 따지지 않은 길을 따라가며 방향까지 본다
            forward = aisle["a"] == here
            here = aisle["b"] if forward else aisle["a"]
            cause = self.cause_of(aisle, forward)
            if cause:
                causes[cause].append(aisle["name_ko"])
        return {cause: tuple(names) for cause, names in sorted(causes.items())}

    def cause_of(self, aisle: dict, forward: bool) -> str | None:
        """그 방향(a에서 b로 가면 forward)으로 지날 때 쓸 수 없는 까닭. 쓸 수 있으면 None."""
        if aisle.get("closed"):
            return DETOUR_CLOSED
        if any(name in (aisle.get("note") or "") for name in self.restricted):
            return DETOUR_RESTRICTED
        return DETOUR_ONE_WAY if aisle.get("one_way") and not forward else None

    def blocking_options(self, start: str, goal: str, cap: int = 64) -> list[frozenset[str]]:
        """막힘을 따지지 않은 가장 짧은 길은 길이가 같은 것이 여럿일 수 있다. 돌아가야 하는 구간이면 그 길마다
        쓸 수 없는 통로 이름의 묶음을, 아니면 빈 목록을 준다. 설명문은 이 가운데 한 묶음을 다 적으면 맞다."""
        if not self.blocked(start, goal):
            return []
        graph = self._graphs.setdefault(False, self._edges(blocked=False))
        from_start, to_goal = _distances(graph, start), _distances(graph, goal)
        best = from_start[goal]
        options: set[frozenset[str]] = set()
        stack: list[tuple[str, frozenset[str]]] = [(start, frozenset())]
        seen: set[tuple[str, frozenset[str]]] = set()  # 같은 곳에 같은 까닭으로 온 길은 한 번만 본다
        while stack and len(options) < cap:
            node, causes = stack.pop()
            if (node, causes) in seen:
                continue
            seen.add((node, causes))
            if node == goal:
                options.add(causes)
                continue
            for nxt, step, aisle in graph[node]:
                if abs(from_start[node] + step + to_goal.get(nxt, float("inf")) - best) > 1e-6:
                    continue  # 가장 짧은 길에 들지 않는 통로
                cause = self.cause_of(aisle, aisle["a"] == node)
                stack.append((nxt, causes | {aisle["name_ko"]} if cause else causes))
        return sorted(options, key=lambda o: (len(o), sorted(o)))

    def route_traits(self, *legs: tuple[str, str]) -> tuple | None:
        """구간(출발, 도착)마다 돌아가야 하는 까닭들. 갈 수 없는 구간이 있으면 None.
        설명문에 드러나는 성질만 담는다: 어느 구간에서 무엇 때문에 돌아가는지는 제약 문장을 바꾸지만,
        지나는 통로 수나 순방향으로 지나는 한 방향 통로는 설명문을 바꾸지 않는다."""
        per_leg = []
        for start, goal in legs:
            found = self.blocked(start, goal)
            if found is None:
                return None
            per_leg.append(tuple(sorted(found)))
        return tuple(per_leg)

    def detour_facts(self, *legs: tuple[str, str]) -> tuple:
        """여러 구간에서 돌아가게 만드는 통로 이름을 까닭별로 모은 것 (작업 합성이 정답 사실로 남긴다)."""
        merged: dict[str, set[str]] = defaultdict(set)
        for start, goal in legs:
            for cause, names in (self.blocked(start, goal) or {}).items():
                merged[cause].update(names)
        return tuple((cause, tuple(sorted(names))) for cause, names in sorted(merged.items()))


def _distances(graph: dict, start: str) -> dict[str, float]:
    """한 곳에서 모든 곳까지의 가장 짧은 거리 (막힘을 따지지 않은 길, 양방향)."""
    found: dict[str, float] = {}
    queue = [(0.0, start)]
    while queue:
        length, node = heapq.heappop(queue)
        if node in found:
            continue
        found[node] = length
        for nxt, step, _aisle in graph[node]:
            if nxt not in found:
                heapq.heappush(queue, (length + step, nxt))
    return found


def _shortest(graph: dict, start: str, goal: str) -> tuple[float, list[dict]] | None:
    queue: list[tuple[float, int, str, list[dict]]] = [
        (0.0, 0, start, [])
    ]  # 같은 길이는 넣은 순서로
    seen: set[str] = set()
    counter = itertools.count(1)
    while queue:
        length, _order, node, used = heapq.heappop(queue)
        if node == goal:
            return length, used
        if node in seen:
            continue
        seen.add(node)
        for nxt, step, aisle in graph[node]:
            if nxt not in seen:
                heapq.heappush(queue, (length + step, next(counter), nxt, [*used, aisle]))
    return None


class FactoryWords:
    """지시문에 쓰는 곳·칸·물건 이름 (도메인 프로필의 task_synthesis)."""

    def __init__(self, spec: dict, view: FactoryView):
        self.spec, self.view = spec, view

    def obj(self, category: str) -> str:
        return self.spec["object_en"].get(category, category.replace("_", " "))

    def station(self, station: str) -> str:
        category = self.view.category(station)
        name = self.spec["fixture_en"].get(category, category.replace("_", " "))
        if len(self.view.stations(category)) == 1:
            return f"the {name}"
        number = re.search(r"_(\d+)$", station)
        return f"{name} {number.group(1)}" if number else f"the {name}"

    def place(self, station: str, region: str | None) -> str:
        if region is None:
            return self.station(station)
        kind = region.rstrip("0123456789")
        label = self.spec["region_en"].get(kind, kind)
        number = region[len(kind) :]
        if number:
            return f"{label} {number} of {self.station(station)}"
        return f"the {label} of {self.station(station)}"

    def item(self, item: Item) -> str | None:
        """물건을 가리키는 말: 그 종류가 하나면 이름으로, 여럿이면 놓인 칸으로 (그 칸에 하나뿐일 때)."""
        same = self.view.kinds[item.category]
        if len(same) == 1:
            return f"the {self.obj(item.category)}"
        here = [i for i in same if (i.station, i.region) == (item.station, item.region)]
        if len(here) != 1:
            return None
        return f"the {self.obj(item.category)} on {self.place(item.station, item.region)}"


def target_ref(station: str, region: str | None) -> str:
    return region_id(station, region) if region else station
