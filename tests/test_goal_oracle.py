"""원천 성공 판정에서 뽑는 목표 상태 정답과 그 대조.

정답이 틀리면 생성된 목표 상태를 잘못 탈락시키거나 잘못 통과시키고, 대조 결과만 봐서는 드러나지 않는다.
그래서 정적 분석이 성공 판정을 논리식 그대로 읽는지(or는 대안, 변수는 펼침, 해석할 수 없는 판정은 뺌),
behavior 분기와 부정을 제대로 푸는지를 실제 소스 모양으로 못박는다.
"""

import ast
import textwrap

from tsdgen.adapters.robocasa_success import build_success_map, extract_method, oracle_alternatives
from tsdgen.goals.oracle import compare_with_oracle
from tsdgen.goals.success_formula import alternatives_of
from tsdgen.models import GoalAssertion, TaskGoal

STATE_KEYS = {"turned_on": "turned_on", "water_on": "turned_on"}


def _method(body: str) -> ast.FunctionDef:
    source = "def _check_success(self):\n" + textwrap.indent(textwrap.dedent(body), "    ")
    return ast.parse(source).body[0]


def _fact(e: dict) -> tuple:
    return (e["kind"], e["subject"], e["predicate"], e["target"], e["value"])


def _alternatives(body: str, behavior: str | None = None) -> list[list[tuple]]:
    alternatives, _ = alternatives_of(extract_method(_method(body), STATE_KEYS, behavior))
    return [[_fact(e) for e in alt] for alt in alternatives]


def _facts(body: str, behavior: str | None = None) -> list[tuple]:
    """대안이 하나인 판정의 단언 목록."""
    alternatives = _alternatives(body, behavior)
    assert len(alternatives) <= 1, alternatives
    return alternatives[0] if alternatives else []


def test_certain_calls_are_extracted():
    facts = _facts('''
        a = OU.check_obj_in_receptacle(self, "chicken", "tray")
        b = OU.obj_inside_of(self, "bowl", self.fridge)
        c = self.fridge.check_rack_contact(self, "milk")
        gripper_far = OU.gripper_obj_far(self, "chicken")
        return a and b and c and gripper_far
    ''')
    assert facts == [
        ("relation", {"object": "chicken"}, "in", {"object": "tray"}, True),
        ("relation", {"object": "bowl"}, "in", {"fixture_ref": "fridge"}, True),
        ("relation", {"object": "milk"}, "in",
         {"fixture_ref": "fridge", "regions": ["fridge_shelf", "rack"]}, True),
    ]


def test_or_becomes_alternatives_and_unreadable_checks_are_dropped():
    """or의 갈래는 대안이 된다. 인자가 변수인 판정은 그 대안에서 뺀다."""
    alternatives = _alternatives('''
        a = OU.check_obj_in_receptacle(self, "x", "tray")
        b = OU.check_obj_in_receptacle(self, "y", "tray")
        return (a or b) and OU.check_obj_any_counter_contact(self, name)
    ''')
    tray = {"object": "tray"}
    assert alternatives == [[("relation", {"object": "x"}, "in", tray, True)],
                            [("relation", {"object": "y"}, "in", tray, True)]]


def test_an_unreadable_branch_leaves_no_answer():
    """확실히 요구되는 것이 없는 대안이 하나라도 있으면(조건을 모르는 분기) 정답을 내지 않는다."""
    assert _alternatives('''
        if self.flag:
            return True
        return OU.obj_inside_of(self, "z", self.fridge)
    ''') == []


def test_either_bowl_sorting_keeps_both_ways():
    """AfterwashSorting 모양: 물을 잠그고, 레몬 둘은 한 그릇에 브로콜리는 다른 그릇에(어느 쪽이든)."""
    alternatives = _alternatives('''
        water_on = self.sink.get_handle_state(env=self)["water_on"]
        f1b1 = OU.check_obj_in_receptacle(self, "food1", "bowl1")
        f1b2 = OU.check_obj_in_receptacle(self, "food1", "bowl2")
        f2b1 = OU.check_obj_in_receptacle(self, "food2", "bowl1")
        f2b2 = OU.check_obj_in_receptacle(self, "food2", "bowl2")
        f3b1 = OU.check_obj_in_receptacle(self, "food3", "bowl1")
        f3b2 = OU.check_obj_in_receptacle(self, "food3", "bowl2")
        both_1 = f1b1 and f2b1
        both_2 = f1b2 and f2b2
        return (not water_on) and ((both_1 and f3b2) or (both_2 and f3b1))
    ''')
    water = ("state", {"fixture_ref": "sink"}, "turned_on", None, False)

    def into(food, bowl):
        return ("relation", {"object": food}, "in", {"object": bowl}, True)
    assert alternatives == [[water, into("food1", "bowl1"), into("food2", "bowl1"), into("food3", "bowl2")],
                            [water, into("food1", "bowl2"), into("food2", "bowl2"), into("food3", "bowl1")]]


def test_guard_clause_counts_as_required():
    facts = _facts('''
        if not OU.check_obj_fixture_contact(self, "pan", self.stove):
            return False
        return True
    ''')
    assert facts == [("relation", {"object": "pan"}, "on", {"fixture_ref": "stove"}, True)]


def test_behavior_branch_and_negation_resolve_state():
    body = '''
        turned_on = self.microwave.get_state()["turned_on"]
        if self.behavior == "turn_on":
            return turned_on
        elif self.behavior == "turn_off":
            return not turned_on
    '''
    on, off = _facts(body, "turn_on"), _facts(body, "turn_off")
    assert on == [("state", {"fixture_ref": "microwave"}, "turned_on", None, True)]
    assert off == [("state", {"fixture_ref": "microwave"}, "turned_on", None, False)]
    assert _alternatives(body) == [on, off]  # behavior를 모르면 두 갈래가 대안으로 남는다


def test_state_dict_read_through_a_variable():
    facts = _facts('''
        handle_state = self.sink.get_handle_state(env=self)
        water_on = handle_state["water_on"]
        return water_on
    ''')
    assert facts == [("state", {"fixture_ref": "sink"}, "turned_on", None, True)]


def test_subclass_behavior_and_inherited_check(tmp_path):
    (tmp_path / "doors.py").write_text(textwrap.dedent('''
        class ManipulateDoor(Kitchen):
            def _check_success(self):
                if self.behavior == "open":
                    return self.fxtr.is_open(env=self)
                elif self.behavior == "close":
                    return self.fxtr.is_closed(env=self)

        class OpenDoor(ManipulateDoor):
            def __init__(self, *args, **kwargs):
                super().__init__(behavior="open", *args, **kwargs)

        class OpenCabinet(OpenDoor):
            pass
    '''), encoding="utf-8")
    success = build_success_map(tmp_path, STATE_KEYS)
    ((entry,),) = success["OpenCabinet"]["alternatives"]
    assert (entry["subject"], entry["predicate"], entry["value"]) == (
        {"fixture_ref": "fxtr"}, "door_open", True)


def _scene() -> dict:
    return {
        "fixtures": [{"id": "fx_counter_1", "category": "counter"},
                     {"id": "fx_counter_2", "category": "counter"},
                     {"id": "fx_fridge_1", "category": "fridge"}],
        "provenance": {"source_object_names": {"obj_001": "milk", "obj_002": "tray"},
                       "source_fixture_names": {"fx_fridge_1": "fridge_main_group"},
                       "source_fixture_refs": {"fridge": "fridge_main_group"}},
    }


def test_oracle_names_become_scene_ids():
    entries = [
        {"kind": "relation", "subject": {"object": "milk"}, "predicate": "in",
         "target": {"fixture_ref": "fridge"}, "value": True},
        {"kind": "relation", "subject": {"object": "tray"}, "predicate": "on",
         "target": {"fixture_category": "counter"}, "value": True},
        {"kind": "relation", "subject": {"object": "missing"}, "predicate": "on",
         "target": {"fixture_ref": "fridge"}, "value": True},  # 씬에 없는 이름은 뺀다
    ]
    ((fridge, counters),) = oracle_alternatives({"alternatives": [entries]}, _scene())
    assert [(a.subject, a.object) for a in fridge] == [("obj_001", "fx_fridge_1")]
    assert [a.object for a in counters] == ["fx_counter_1", "fx_counter_2"]  # 아무 조리대나


def _assertion(subject, target, predicate="in", value=True) -> GoalAssertion:
    return GoalAssertion(kind="relation", subject=subject, predicate=predicate, object=target,
                         value=value)


def test_recall_counts_only_changes_and_treats_on_in_alike():
    env = {"objects": [{"id": "obj_001", "placement": {"fixture_id": "fx_counter_1", "relation": "on"}},
                       {"id": "obj_002", "placement": {"fixture_id": "fx_counter_2", "relation": "on"}}],
           "fixtures": [{"id": "fx_counter_1"}, {"id": "fx_counter_2"}, {"id": "fx_fridge_1"}]}
    oracle = [[[_assertion("obj_001", "fx_fridge_1")],  # 바뀌어야 하는 조건
               [_assertion("obj_002", "fx_counter_2", "on")]]]  # 이미 성립: 재현율에서 제외
    written = TaskGoal(summary="s", goal_state=[_assertion("obj_001", "fx_fridge_1", "on")])
    result = compare_with_oracle(written, oracle, env, None)
    assert result.matched == [True, False] and result.already_holds == [False, True]
    assert result.recall == 1.0 and result.pass_
    missed = compare_with_oracle(TaskGoal(summary="s"), oracle, env, 0.5)
    assert missed.recall == 0.0 and not missed.pass_  # 하한을 주면 탈락시킨다
    assert compare_with_oracle(TaskGoal(summary="s"), None, env, 0.5).pass_  # 정답이 없으면 통과


def test_every_alternative_is_matched_against_the_closest_case():
    """정답 대안마다 설명문의 가장 가까운 경우와 대조하고 평균한다. 경우를 하나만 적으면 그만큼 낮다."""
    env = {"objects": [], "fixtures": []}
    first, second = [[_assertion("obj_001", "obj_004")]], [[_assertion("obj_001", "obj_005")]]
    one_case = TaskGoal(summary="s", goal_state=[[_assertion("obj_001", "obj_005")]])
    result = compare_with_oracle(one_case, [first, second], env, None)
    assert (result.alternatives, result.chosen, result.recall) == (2, 1, 0.5)
    both = TaskGoal(summary="s", goal_state=[[_assertion("obj_001", "obj_004")],
                                             [_assertion("obj_001", "obj_005")]])
    assert compare_with_oracle(both, [first, second], env, None).recall == 1.0
    assert [o["alternative"] for o in result.oracle] == [0, 1]


def test_old_flat_goal_state_reads_as_one_case():
    """2.2까지의 목표 상태(단언 목록 하나)는 경우 하나로 읽는다."""
    goal = TaskGoal.model_validate({"summary": "s", "goal_state": [
        {"kind": "relation", "subject": "a", "predicate": "in", "object": "b", "value": True}]})
    assert len(goal.goal_state) == 1 and goal.goal_state[0][0].object == "b"
    # 상태로 적을 목표가 없을 때 구조화 출력이 내는 [[]]는 목표 상태 없음([])으로 읽는다
    assert TaskGoal.model_validate({"summary": "s", "goal_state": [[]]}).goal_state == []


def test_stats_skip_oracle_without_recall():
    """정답 조건이 모두 처음 씬에서 이미 성립한 샘플은 재현율이 없다(null). 평균에서 빼고 개수에는 넣는다."""
    from types import SimpleNamespace

    from tsdgen.exporters.stats import _goal_summary

    def sample(recall):
        oracle = SimpleNamespace(available=True, recall=recall)
        return SimpleNamespace(meta=SimpleNamespace(verification=SimpleNamespace(goal=None, goal_oracle=oracle)))

    summary = _goal_summary([sample(1.0), sample(None), sample(0.5)])
    assert summary["oracle_available"] == 3 and summary["oracle_recall_mean"] == 0.75


def test_rack_contact_resolves_to_the_fixture_regions():
    """선반·랙 접촉 판정은 씬에 그 가구의 칸이 있으면 그 종류의 칸들로, 없으면 가구 전체로 되짚는다."""
    entry = {"kind": "relation", "subject": {"object": "milk"}, "predicate": "in", "value": True,
             "target": {"fixture_ref": "fridge", "regions": ["freezer_shelf", "rack"]}}
    scene = _scene()
    ((whole,),) = oracle_alternatives({"alternatives": [[entry]]}, scene)
    assert [a.object for a in whole] == ["fx_fridge_1"]
    scene["fixtures"][2]["regions"] = [{"key": "fridge_shelf0"}, {"key": "freezer_shelf0"},
                                       {"key": "freezer_shelf1"}]
    ((shelves,),) = oracle_alternatives({"alternatives": [[entry]]}, scene)
    assert [a.object for a in shelves] == ["fx_fridge_1/freezer_shelf0", "fx_fridge_1/freezer_shelf1"]


def test_near_an_object_on_a_plate_implies_the_counter_under_it():
    """접시 위 스테이크 옆에 두면 접시가 놓인 조리대에 있는 것이다 (받침을 따라 내려가 가구를 찾는다)."""
    from tsdgen.goals.scene_index import SceneIndex

    env = {"objects": [{"id": "plate", "placement": {"fixture_id": "fx_counter_1"}}, {"id": "steak"},
                       {"id": "shaker"}],
           "fixtures": [{"id": "fx_counter_1"}],
           "relations": [{"subj": "steak", "pred": "on", "obj": "plate"}]}
    index = SceneIndex.from_env(env)
    assert index.fixture_under("steak") == "fx_counter_1" and index.fixture_under("shaker") is None


def test_fixture_level_goal_covers_any_region_answer():
    """정답이 '냉장실 선반 아무 데나'이면 설명문이 '냉장고 안'이라고 써도 맞힌 것이다. 선반 하나를 못 박은 정답은 아니다."""
    from tsdgen.goals.oracle import _any_region_of
    from tsdgen.models import GoalAssertion

    def put(target):
        return GoalAssertion(kind="relation", subject="obj_001", predicate="in", object=target, value=True)

    shelves = [put("fx_fridge_1/fridge_shelf0"), put("fx_fridge_1/fridge_shelf1")]
    assert _any_region_of(put("fx_fridge_1"), shelves)
    assert not _any_region_of(put("fx_fridge_1"), shelves[:1])
    assert not _any_region_of(put("fx_cabinet_1"), shelves)
