"""작업 합성 (제조 작업장): 서랍마다 따로 여닫히고, 넣고 돌리는 설비가 있는 장면."""

from pathlib import Path

import yaml

from tsdgen.tasks.base import Words
from tsdgen.tasks.compose import tasks_of

TRAITS = {"torque_wrench": ["graspable", "tool"],
          "water_pump": ["graspable", "part", "washable"],
          "camshaft": ["graspable", "part", "washable"]}


def _scene() -> dict:
    drawers = [{"key": f"drawer{n}", "name_ko": f"서랍 {n}", "states": {"drawer_open": n == 2}}
               for n in (1, 2, 3)]
    return {
        "objects": [
            {"id": "obj_001", "category": "torque_wrench",
             "placement": {"fixture_id": "fx_tool_cabinet_1", "relation": "in", "region": "drawer1"}},
            {"id": "obj_002", "category": "water_pump",
             "placement": {"fixture_id": "fx_parts_washer_1", "relation": "in", "region": "chamber"}},
            {"id": "obj_003", "category": "camshaft",
             "placement": {"fixture_id": "fx_workbench_1", "relation": "on", "region": "top"}}],
        "fixtures": [
            {"id": "fx_tool_cabinet_1", "category": "tool_cabinet", "states": {},
             "regions": [{"key": "top", "name_ko": "상판", "states": {}}, *drawers]},
            {"id": "fx_workbench_1", "category": "workbench", "states": {},
             "regions": [{"key": "top", "name_ko": "상판", "states": {}}]},
            {"id": "fx_parts_washer_1", "category": "parts_washer",
             "states": {"door_open": False, "turned_on": True},
             "regions": [{"key": "chamber", "name_ko": "세척실", "states": {}}]}],
        "relations": [{"subj": "obj_001", "pred": "in", "obj": "fx_tool_cabinet_1"},
                      {"subj": "obj_002", "pred": "in", "obj": "fx_parts_washer_1"},
                      {"subj": "obj_003", "pred": "on", "obj": "fx_workbench_1"}],
    }


# 제조 도메인 프로필은 공장 지도 기반 운반으로 바뀌었다. 작업대 유형은 그 전 낱말 표로 본다
VOCAB = Path(__file__).parent / "fixtures" / "workshop_task_synthesis.yaml"


def _tasks(settings, types: list[str]) -> dict:
    words = Words(yaml.safe_load(VOCAB.read_text(encoding="utf-8")))
    return {t.instruction: t for t in tasks_of(_scene(), TRAITS, words, types)}


def test_the_drawer_the_tool_is_in_is_what_gets_closed(settings):
    """서랍마다 따로 여닫히면 꺼낸 서랍을 닫고, 출발지의 닫힘도 그 서랍의 것이다."""
    tasks = _tasks(settings, ["fetch_and_close", "relocate"])
    fetch = tasks["Take the torque wrench out of the drawer it is in and close that drawer. "
                  "Then put it on the top of the workbench."]
    assert ("state", "fx_tool_cabinet_1/drawer1", "drawer_open", None, False) in fetch.cases[0]
    moved = tasks["Put the torque wrench on the top of the workbench."]
    assert ("tool_cabinet", "drawer", True) in moved.signature  # 닫힌 서랍에서 꺼낸다


def test_storing_in_a_middle_drawer_names_it_from_the_top_and_closes_that_drawer(settings):
    tasks = _tasks(settings, ["store_and_close"])
    store = tasks["Put the torque wrench in the second drawer from the top of the tool cabinet "
                  "and close the drawer."]
    assert store.cases[0] == (("relation", "obj_001", "in", "fx_tool_cabinet_1/drawer2", True),
                              ("state", "fx_tool_cabinet_1/drawer2", "drawer_open", None, False))
    assert not [t for t in tasks if "top drawer of the tool cabinet" in t]  # 이미 들어 있는 서랍


def test_machine_tasks_turn_off_before_unloading_and_run_after_loading(settings):
    tasks = _tasks(settings, ["unload_machine", "machine_swap"])
    unload = tasks["Turn off the parts washer, then take the water pump out of it and put it "
                   "on the top of the workbench."]
    assert unload.cases[0][0] == ("state", "fx_parts_washer_1", "turned_on", None, False)
    swap = next(t for t in tasks.values() if t.type_id == "machine_swap")
    assert "then put the camshaft in the parts washer, close the door and run it for" in swap.instruction
    assert swap.duration[1] == "fx_parts_washer_1"
    assert ("relation", "obj_003", "in", "fx_parts_washer_1", True) in swap.cases[0]
