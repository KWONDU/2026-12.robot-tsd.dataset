"""지도가 있는 장면(제조: 작업장 사이 운반)의 세계 상태 글 조각.

로봇이 미리 가진 지도(구역, 곳, 통로, 시설 알림)는 지금 보이지 않아도 아는 정보이고, 물체는 지금 보이는 것과
공장 시스템이 알려 준 것(다른 곳에 있어 보이지 않음)을 나눠 적는다. 지도가 없는 장면에는 아무것도 더하지 않는다.
"""

from collections import defaultdict
from typing import Any

SYSTEM = "system"  # 물체의 known_by: 공장 시스템이 알려 준 것


def has_map(env: dict) -> bool:
    return bool(env.get("map"))


def split_objects(env: dict) -> tuple[dict, dict]:
    """(지금 보이는 물체만 남긴 환경, 시스템이 알려 준 물체만 남긴 환경)."""
    objects = env.get("objects") or []
    seen = [o for o in objects if o.get("known_by") != SYSTEM]
    told = [o for o in objects if o.get("known_by") == SYSTEM]
    return {**env, "objects": seen}, {**env, "objects": told}


def _zone_lines(world_map: dict, labels: dict[str, str]) -> list[str]:
    members: dict[str, list[str]] = defaultdict(list)
    for station, zone in world_map.get("stations", {}).items():
        members[zone].append(labels.get(station, station))
    lines = []
    for zone in world_map.get("zones") or []:
        flag = f"(출입 제한: {zone['note']})" if zone.get("restricted") and zone.get("note") else (
            "(출입 제한)" if zone.get("restricted") else "")
        places = ", ".join(sorted(members.get(zone["id"], [])))
        lines.append(f"- {zone['name_ko']}{flag}: {places}" if places else f"- {zone['name_ko']}{flag}")
    return lines


def _aisle_lines(world_map: dict, labels: dict[str, str]) -> list[str]:
    lines = []
    for aisle in world_map.get("aisles") or []:
        a, b = labels.get(aisle["a"], aisle["a"]), labels.get(aisle["b"], aisle["b"])
        notes = []
        if aisle.get("closed"):
            notes.append(f"막힘({aisle['note']})" if aisle.get("note") else "막힘")
        elif aisle.get("note"):
            notes.append(aisle["note"])  # 출입 제한 구역을 지나는 통로 ('용접 작업 구역을 지남')
        if aisle.get("one_way"):
            notes.append(f"{a}에서 {b} 쪽으로만")
        tail = f", {', '.join(notes)}" if notes else ""
        lines.append(f"- {a} - {b}: {aisle['name_ko']}, {aisle['length_m']:g} m{tail}")
    return lines


def map_sections(env: dict, labels: dict[str, str]) -> list[tuple[str, list[str]]]:
    """지도에서 나오는 절들: 구역과 곳, 통로, 시설 알림."""
    world_map = env.get("map") or {}
    return [
        ("지도의 구역과 곳(미리 알고 있는 정보)", _zone_lines(world_map, labels)),
        ("지도의 통로", _aisle_lines(world_map, labels)),
        ("시설 알림", [f"- {n}" for n in world_map.get("notices") or []]),
    ]


def system_title() -> str:
    return "공장 시스템이 알려 준 물품(지금 보이지 않음)"


def describe(env: dict, labels: dict[str, str],
             placement_lines: Any) -> tuple[list[str], list[tuple]]:
    """지도가 있는 장면의 (로봇 위치 줄, 앞에 둘 절들). placement_lines(env)는 물체를 놓인 곳별 줄로 바꾸는 함수다."""
    _seen, told = split_objects(env)
    told_lines, _loose = placement_lines(told)
    return [], [*map_sections(env, labels), (system_title(), told_lines)]
