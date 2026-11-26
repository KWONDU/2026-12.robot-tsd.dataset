"""공장 바닥 배치를 규칙으로 만든다 (제조: 작업장 사이를 오가는 운반).

근거 두 가지를 따른다.
- 배치 규칙: RoboCup Logistics League 2025 규칙집의 스마트 팩토리. 바닥을 구역 격자로 나누고 기계 하나가 구역 하나의
  가운데에 선다. 기계의 입구·출구 앞 구역은 비워 두고(다른 기계나 비워 둔 구역과 겹치지 않음), 세 기계가 한 줄
  (가로·세로·대각)로 서지 않으며, 빈 구역은 모두 이어져 있어야 한다. 로봇이 들어오는 진입 구역에는 기계를 두지 않는다.
- 작업장 수와 물동량: 공개 시설 배치 벤치마크(Armour & Buffa, Bazaraa, van Camp, Bozer)의 부서와 부서 사이 물동량.
  물동량이 많은 작업장끼리 가깝게 놓는다(시설 배치 문제의 목적과 같다).
좌표 단위는 구역 칸이다. 통로는 두 곳 사이의 빈 구역 최단 경로이고, 그 사이에 다른 곳이 끼지 않을 때만 통로로 잇는다.
"""

import random
from collections import deque
from dataclasses import dataclass, field

Cell = tuple[int, int]
DIRECTIONS = ((1, 0), (0, 1), (-1, 0), (0, -1))  # 기계가 놓이는 방향 (입구에서 출구 쪽)


@dataclass
class Station:
    key: str  # 배치 안의 이름 (s1, s2 ...)
    role: str  # 원천 역할: supply, process, assembly, inspection, shipping, storage, tool_crib, charger, waste, bay
    cell: Cell = (0, 0)
    access: list[Cell] = field(default_factory=list)  # 로봇이 서는 구역 (입구·출구 앞)


@dataclass
class Layout:
    width: int
    height: int
    stations: list[Station]
    entry: list[Cell]  # 진입 구역
    paths: dict[tuple[str, str], list[Cell]] = field(
        default_factory=dict
    )  # 통로: 두 곳 사이 빈 구역 경로


def _neighbors(cell: Cell, width: int, height: int):
    x, y = cell
    for dx, dy in DIRECTIONS:
        nx, ny = x + dx, y + dy
        if 0 <= nx < width and 0 <= ny < height:
            yield nx, ny


def _three_in_row(cell: Cell, taken: set[Cell]) -> bool:
    x, y = cell
    for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
        line = [(x + k * dx, y + k * dy) for k in range(-2, 3)]
        for start in range(3):
            window = line[start : start + 3]
            if cell in window and all(c == cell or c in taken for c in window):
                return True
    return False


def _connected(free: set[Cell], width: int, height: int) -> bool:
    if not free:
        return False
    start = next(iter(free))
    seen, queue = {start}, deque([start])
    while queue:
        for nxt in _neighbors(queue.popleft(), width, height):
            if nxt in free and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return len(seen) == len(free)


def _candidates(
    layout: Layout, taken: set[Cell], blocked: set[Cell]
) -> list[tuple[Cell, list[Cell]]]:
    """빈 칸마다 놓을 수 있는 방향: 입구·출구 앞 칸이 비어 있고 다른 기계·비워 둔 칸과 겹치지 않는다."""
    found = []
    closed = taken | blocked | set(layout.entry)
    for x in range(layout.width):
        for y in range(layout.height):
            cell = (x, y)
            if (
                cell in taken
                or cell in blocked
                or cell in layout.entry
                or _three_in_row(cell, taken)
            ):
                continue
            for dx, dy in DIRECTIONS[:2]:  # 앞뒤 두 칸을 비우므로 두 방향이면 네 방향을 다 본다
                front, back = (x + dx, y + dy), (x - dx, y - dy)
                inside = all(
                    0 <= c[0] < layout.width and 0 <= c[1] < layout.height for c in (front, back)
                )
                # 앞뒤 칸은 다른 기계나 다른 기계의 앞뒤 칸과 겹치지 않고, 진입 구역도 아니다
                if inside and not ({front, back} & closed):
                    found.append((cell, [front, back]))
    return found


def grid_size(stations: int, aspect: float, zones_per_station: float) -> tuple[int, int]:
    """곳 수에 맞춘 바닥 크기 (가로, 세로 칸). 규칙집 경기장은 기계 7대에 구역 56개, 곧 기계당 약 8구역이다."""
    area = stations * zones_per_station
    height = max(6, round((area / aspect) ** 0.5))
    width = max(6, round(area / height))
    return width, height


def place(layout: Layout, flows: dict[tuple[int, int], float], rng: random.Random) -> bool:
    """곳을 물동량이 많은 순서로 하나씩 놓는다. 이미 놓인 곳과의 물동량 × 거리가 가장 작은 칸을 고른다(같으면 seed)."""
    taken = {s.cell for s in layout.stations if s.access}  # 미리 놓인 곳 (로봇 대기 구역)
    blocked = {c for s in layout.stations if s.access for c in s.access}
    weight = {
        i: sum(v for (a, b), v in flows.items() if i in (a, b)) for i in range(len(layout.stations))
    }
    floor = {(x, y) for x in range(layout.width) for y in range(layout.height)}
    for index in sorted(range(len(layout.stations)), key=lambda i: (-weight[i], i)):
        if layout.stations[index].access:
            continue
        station = layout.stations[index]
        options = _candidates(layout, taken, blocked)
        rng.shuffle(options)
        # 이 곳과 물동량이 있고 이미 놓인 곳들 (후보 칸마다 다시 찾지 않는다)
        partners = [
            (v, layout.stations[j].cell)
            for (a, b), v in flows.items()
            for i, j in ((a, b), (b, a))
            if i == index and layout.stations[j].access
        ]
        best, best_cost = None, None
        for cell, access in options:
            cost = sum(v * (abs(cell[0] - x) + abs(cell[1] - y)) for v, (x, y) in partners)
            # 빈 구역이 이어져 있는지는 비용이 더 작을 때만 본다 (가장 비싼 검사)
            if (best_cost is None or cost < best_cost) and _connected(
                floor - taken - {cell}, layout.width, layout.height
            ):
                best, best_cost = (cell, access), cost
        if best is None:
            return False
        station.cell, station.access = best
        taken.add(best[0])
        blocked.update(best[1])
    return True


def _path(
    start: Cell, goals: set[Cell], passable: set[Cell], width: int, height: int
) -> list[Cell] | None:
    previous: dict[Cell, Cell | None] = {start: None}
    queue = deque([start])
    while queue:
        cell = queue.popleft()
        if cell in goals:
            path = [cell]
            while previous[path[-1]] is not None:
                path.append(previous[path[-1]])
            return path[::-1]
        for nxt in _neighbors(cell, width, height):
            if nxt in passable and nxt not in previous:
                previous[nxt] = cell
                queue.append(nxt)
    return None


def connect(layout: Layout) -> None:
    """두 곳 사이 빈 구역 최단 경로가 다른 곳의 앞 칸을 지나지 않으면 그 둘을 통로로 잇는다."""
    machines = {s.cell for s in layout.stations}
    passable = {(x, y) for x in range(layout.width) for y in range(layout.height)} - machines
    owner = {cell: s.key for s in layout.stations for cell in s.access}
    for i, a in enumerate(layout.stations):
        for b in layout.stations[i + 1 :]:
            best = None
            for start in a.access:
                path = _path(start, set(b.access), passable, layout.width, layout.height)
                if path and (best is None or len(path) < len(best)):
                    best = path
            if best and all(owner.get(c) in (None, a.key, b.key) for c in best):
                layout.paths[(a.key, b.key)] = best
