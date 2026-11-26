"""작업 합성의 고르기 balanced: 난이도 단계를 고르게 나누고, 덜 나온 물체 종류·장면을 먼저 고른다."""

import random

from tsdgen.tasks.balance import Profile, SceneFacts, Usage, even_split, least_used, level_of
from tsdgen.tasks.base import Task


def test_levels_share_the_quota_and_short_levels_hand_theirs_on():
    assert even_split(10, {1: 2, 2: 10, 3: 10}) == {1: 2, 2: 4, 3: 4}
    assert sum(even_split(7, {1: 1, 2: 1, 3: 100}).values()) == 7


def test_least_used_objects_and_scenes_come_first():
    entries = [[({}, None, Profile(1, ("socket",), f"s{i}"))] for i in range(5)]
    entries += [[({}, None, Profile(1, ("crankshaft",), "s0")), ({}, None, Profile(1, ("crankshaft",), "s9"))]]
    usage = Usage(scene_cap=20)
    picked = least_used(entries, 2, usage, random.Random(0))
    assert {p.objects for _b, _t, p in picked} == {("socket",), ("crankshaft",)}  # 같은 종류를 두 번 고르지 않는다
    assert usage.objects == {"socket": 1, "crankshaft": 1}
    assert max(usage.scenes.values()) == 1  # 같은 상황이 나온 장면 가운데 덜 쓴 장면을 고른다


def test_closed_drawer_and_a_box_on_top_add_steps():
    scene = {
        "objects": [{"id": "obj_001", "category": "box",
                     "placement": {"fixture_id": "fx_cab_1", "region": "drawer1"}},
                    {"id": "obj_002", "category": "tape", "placement": None}],
        "fixtures": [{"id": "fx_cab_1", "category": "tool_cabinet", "states": {},
                      "regions": [{"key": "drawer1", "states": {"drawer_open": False}}]}],
        "relations": [{"subj": "obj_002", "pred": "on", "obj": "obj_001"}],
    }
    facts = SceneFacts(scene, ("drawer_open",))
    task = Task("relocate", "x", ((("relation", "obj_001", "on", "fx_table_1", True),),), ("relocate",))
    assert level_of(task, facts, level_max=5) == 3  # 옮기기 + 서랍 열기 + 위의 테이프 치우기


def test_a_scene_over_its_cap_waits_until_nothing_else_is_left():
    entries = [[({}, None, Profile(1, ("rare",), "busy"))] for _ in range(3)]
    entries += [[({}, None, Profile(1, ("common",), f"s{i}"))] for i in range(3)]
    usage = Usage(scene_cap=1)
    usage.objects["common"] = 50
    picked = least_used(entries, 4, usage, random.Random(0))
    assert [p.scene for _b, _t, p in picked].count("busy") == 1  # 드문 물체여도 상한을 넘긴 장면은 뒤로


def test_a_repeated_instruction_waits_behind_new_ones():
    entries = [[({}, None, Profile(1, ("towel",), f"s{i}", "Put all the towels in the can."))] for i in range(3)]
    entries += [[({}, None, Profile(1, ("socket",), "s9", "Put the socket in the drawer."))]]
    usage = Usage(scene_cap=None)
    usage.objects["socket"] = 100  # 물체로는 소켓이 훨씬 많이 나왔어도
    picked = least_used(entries, 2, usage, random.Random(0))
    assert sorted(p.text for _b, _t, p in picked) == ["Put all the towels in the can.", "Put the socket in the drawer."]
