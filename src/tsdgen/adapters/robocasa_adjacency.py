"""가구 옆의 조리대: RoboCasa가 "싱크대 옆 조리대"를 고르는 규칙을 그대로 옮긴 것.

RoboCasa(kitchen.py get_fixture의 ref 인자)는 기준 가구의 위치를 품은 조리대가 있으면 그 조리대를, 없으면
겉 상자 꼭짓점 사이 거리가 가장 가까운 조리대와 그 거리에서 tie_m 안쪽인 조리대들 가운데 하나를 고른다
(fixture_pairwise_dist). 여기서는 고를 수 있는 조리대를 모두 돌려준다. 지시문의 "스토브 옆 조리대"가 가리키는
것이 이 조리대들이라, 세계 상태에 이 관계가 없으면 그런 지시를 풀 수 없다.
상자는 시뮬레이터 첫 프레임의 가구 겉 상자(중심, 회전 행렬, 절반 길이)다.
"""

import itertools
import math
from typing import Any

Box = tuple[Any, Any, Any]  # (중심 xyz, 3x3 회전 행렬, 절반 길이 xyz), numpy 배열


def _corners(box: Box) -> list[Any]:
    center, rotation, half = box
    signs = itertools.product((-1, 1), repeat=3)
    return [center + rotation @ (half * sign) for sign in signs]


def _contains_2d(box: Box, point: Any) -> bool:
    """점이 상자의 바닥 넓이 안에 있는가 (상자 기준 좌표의 x, y)."""
    center, rotation, half = box
    local = rotation.T @ (point - center)
    return bool(abs(local[0]) <= half[0] and abs(local[1]) <= half[1])


def _distance(a: Box, b: Box) -> float:
    return min(math.dist(p, q) for p in _corners(a) for q in _corners(b))


def counters_next_to(reference: Box, counters: dict[str, Box], tie_m: float) -> list[str]:
    """기준 가구 옆의 조리대 이름들. 기준 가구를 품은 조리대가 있으면 그것, 없으면 가장 가까운 것과 그 근처."""
    holding = [name for name, box in counters.items() if _contains_2d(box, reference[0])]
    if holding or not counters:
        return holding[:1]
    distances = {name: _distance(reference, box) for name, box in counters.items()}
    nearest = min(distances.values())
    return [name for name, d in distances.items() if d - nearest < tie_m]
