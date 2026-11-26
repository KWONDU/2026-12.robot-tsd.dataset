"""배치를 로봇이 미리 가진 지도로 바꾼다: 구역, 곳의 구역, 통로(길이, 막힘, 한 방향, 출입 제한 구역 통과), 시설 알림.

막는 것(막힌 통로, 출입 제한 구역)은 곳이 서로 닿지 못하게 되면 두지 않는다. 지도에서 다닐 수 있는 길이 늘 있다.
"""

import random
from collections import defaultdict, deque

from tsdgen.adapters.factory_layout import Layout


def _reachable(stations: list[str], edges: list[tuple[str, str]]) -> bool:
    graph = defaultdict(set)
    for a, b in edges:
        graph[a].add(b)
        graph[b].add(a)
    seen, queue = {stations[0]}, deque([stations[0]])
    while queue:
        for nxt in graph[queue.popleft()] - seen:
            seen.add(nxt)
            queue.append(nxt)
    return seen >= set(stations)


def _restricted_cells(layout: Layout, rng: random.Random, size: int) -> set:
    """곳·곳의 앞 칸·진입 구역이 아닌 빈 칸에서 이어진 칸 몇 개."""
    occupied = (
        {s.cell for s in layout.stations}
        | {c for s in layout.stations for c in s.access}
        | set(layout.entry)
    )
    free = [
        (x, y) for x in range(layout.width) for y in range(layout.height) if (x, y) not in occupied
    ]
    if not free:
        return set()
    cluster = {rng.choice(free)}
    while len(cluster) < size:
        frontier = [
            (x + dx, y + dy)
            for x, y in cluster
            for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1))
            if (x + dx, y + dy) in free and (x + dx, y + dy) not in cluster
        ]
        if not frontier:
            break
        cluster.add(rng.choice(frontier))
    return cluster


def _open_edges(aisles: list[dict], skip: dict | None = None) -> list[tuple[str, str]]:
    return [
        (x["a"], x["b"])
        for x in aisles
        if x is not skip and not x["closed"] and not x.get("_blocked")
    ]


def _restrict(layout: Layout, aisles: list[dict], keys: list[str], vocab: dict, rng) -> dict | None:
    """출입 제한 구역 하나를 둔다. 그 구역을 지나는 통로는 쓸 수 없고, 곳이 끊기면 두지 않는다."""
    rule = vocab["restricted"]
    if rng.random() >= rule["odds"]:
        return None
    kind = rng.choice(rule["kinds"])
    cells = _restricted_cells(layout, rng, rng.randint(*rule["cells"]))
    crossing = [x for x in aisles if x["_cells"] & cells]
    for aisle in crossing:
        aisle["_blocked"] = True
    if not cells or not _reachable(keys, _open_edges(aisles)):
        for aisle in crossing:
            aisle.pop("_blocked", None)
        return None
    for aisle in crossing:
        aisle["note"] = f"{kind['name']}을 지남"
    return {"id": "restricted", "name_ko": kind["name"], "restricted": True, "note": kind["note"]}


def _close(aisles: list[dict], keys: list[str], names: dict, vocab: dict, rng) -> list[str]:
    """통로 몇 개를 막고(곳이 끊기지 않을 때만) 몇 개를 한 방향으로. 막힌 통로는 시설 알림이 된다."""
    notices = []
    for aisle in rng.sample(aisles, min(len(aisles), rng.choice(vocab["closures"]["count"]))):
        if aisle.get("_blocked") or not _reachable(keys, _open_edges(aisles, skip=aisle)):
            continue
        aisle["closed"], aisle["note"] = True, rng.choice(vocab["closures"]["reasons"])
        ends = f"{names[aisle['a']]} - {names[aisle['b']]}"
        notices.append(f"{aisle['name_ko']}({ends}) 막힘: {aisle['note']}")
    for aisle in rng.sample(aisles, min(len(aisles), rng.choice(vocab["one_way"]["count"]))):
        if not aisle["closed"]:
            aisle["one_way"] = True
    return notices


def build(
    layout: Layout, ids: dict[str, str], names: dict[str, str], vocab: dict, rng: random.Random
) -> dict:
    zones = sorted({vocab["roles"][s.role]["zone"] for s in layout.stations})
    map_zones = [{"id": z, "name_ko": vocab["zones"][z], "restricted": False} for z in zones]
    stations = {ids[s.key]: vocab["roles"][s.role]["zone"] for s in layout.stations}
    cell_m = vocab["grid"]["cell_m"]
    aisles = [
        {"a": ids[a], "b": ids[b], "name_ko": vocab["aisle_name"].format(n=n),
         "length_m": float(len(path) * cell_m), "closed": False, "one_way": False, "note": None,
         "_cells": set(path)}
        for n, ((a, b), path) in enumerate(sorted(layout.paths.items()), start=1)
    ]
    keys = list(stations)
    restricted = _restrict(layout, aisles, keys, vocab, rng)
    if restricted:
        map_zones.append(restricted)
    notices = _close(aisles, keys, names, vocab, rng)
    for aisle in aisles:
        aisle.pop("_cells", None)
        aisle.pop("_blocked", None)
    return {"zones": map_zones, "stations": stations, "aisles": aisles, "notices": notices}
