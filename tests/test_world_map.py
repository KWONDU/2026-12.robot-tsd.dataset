"""지도가 있는 장면(제조 공장 바닥)의 세계 상태 글: 통로 줄에 막힘, 출입 제한 구역 통과, 한 방향이 적힌다."""

from tsdgen.world_map import map_sections

WORLD_MAP = {
    "zones": [{"id": "machining", "name_ko": "가공 구역", "restricted": False},
              {"id": "restricted", "name_ko": "용접 작업 구역", "restricted": True, "note": "용접 작업 중"}],
    "stations": {"fx_a": "machining", "fx_b": "machining"},
    "aisles": [
        {"a": "fx_a", "b": "fx_b", "name_ko": "통로 1", "length_m": 9.0, "closed": True, "one_way": False,
         "note": "바닥 청소 중"},
        {"a": "fx_a", "b": "fx_b", "name_ko": "통로 2", "length_m": 12.0, "closed": False, "one_way": False,
         "note": "용접 작업 구역을 지남"},
        {"a": "fx_a", "b": "fx_b", "name_ko": "통로 3", "length_m": 15.0, "closed": False, "one_way": True,
         "note": None},
    ],
    "notices": [],
}


def test_aisle_lines_say_why_an_aisle_cannot_be_used():
    sections = dict(map_sections({"map": WORLD_MAP}, {"fx_a": "가공기 1", "fx_b": "가공기 2"}))
    aisles = sections["지도의 통로"]
    assert aisles[0] == "- 가공기 1 - 가공기 2: 통로 1, 9 m, 막힘(바닥 청소 중)"
    assert aisles[1] == "- 가공기 1 - 가공기 2: 통로 2, 12 m, 용접 작업 구역을 지남"  # 출입 제한 구역을 지나는 통로
    assert aisles[2] == "- 가공기 1 - 가공기 2: 통로 3, 15 m, 가공기 1에서 가공기 2 쪽으로만"
    assert "- 용접 작업 구역(출입 제한: 용접 작업 중)" in sections["지도의 구역과 곳(미리 알고 있는 정보)"]
