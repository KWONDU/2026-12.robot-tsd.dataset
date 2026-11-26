"""목표 상태(task_goal.goal_state): 판정기의 세 값, 규칙 검사, 렌더링, 이전 형식 읽기."""

import copy
import json

import pytest

from tsdgen.config import PROJECT_ROOT
from tsdgen.goals.validation import check_goal
from tsdgen.goals.verifier import combine, verify_goal
from tsdgen.models import SceneState, TaskGoal, TaskSituationDescription, to_environment_context
from tsdgen.render import SECTION_ORDER, render_tsd_text
from tsdgen.resources import load_resources
from tsdgen.verify.structure import check_field_order

GOLDEN = json.loads((PROJECT_ROOT / "resources" / "golden" / "heatmug_sample.json").read_text())
LEXICON = load_resources(PROJECT_ROOT / "resources")


def _env() -> dict:
    return to_environment_context(SceneState.model_validate(GOLDEN["scene_state"]))


def _tsd(timed: bool = True, **goal) -> TaskSituationDescription:
    """골든 설명문에서 목표만 바꾼 것. timed가 False면 상태로 쓸 수 없는 기준(시간 조건)을 뺀다."""
    raw = copy.deepcopy(GOLDEN["task_situation_description"])
    raw["task_goal"] = {**raw["task_goal"], **goal}
    if not timed:
        raw["task_goal"].pop("completion_conditions", None)
        raw["spatiotemporal_conditions"]["time"] = None
    return TaskSituationDescription.model_validate(raw)


def _relation(subject="obj_001", predicate="in", target="fx_microwave_1", value=True) -> dict:
    return {"kind": "relation", "subject": subject, "predicate": predicate, "object": target,
            "value": value}


@pytest.mark.parametrize(
    ("assertion", "expected"),
    [
        (_relation(), "unsatisfied"),  # 머그는 식탁 위에 있다 (배치가 알려져 있음)
        (_relation(target="fx_dining_table_1", predicate="on"), "satisfied"),
        (_relation(target="fx_dining_table_1", predicate="in"), "satisfied"),  # 위/안은 같은 받침
        (_relation(value=False), "satisfied"),
        ({"kind": "state", "subject": "fx_microwave_1", "predicate": "turned_on",
          "value": True}, "unsatisfied"),
        ({"kind": "state", "subject": "fx_dining_table_1", "predicate": "door_open",
          "value": False}, "unknown"),  # 식탁에는 그 상태 정보가 없다
        ({"kind": "relation", "subject": "obj_001", "predicate": "held_by", "value": False},
         "satisfied"),  # 로봇은 아무것도 들고 있지 않다
    ],
)
def test_verifier_separates_false_from_unknown(assertion, expected):
    goal = TaskGoal(summary="x", goal_state=[assertion])
    assert verify_goal(goal, _env()).assertion_status == [expected]


def test_one_satisfied_case_is_enough():
    """목표 상태가 여러 경우면 한 경우가 성립하면 성립, 모든 경우가 아니면 불성립이다."""
    inside, on_table = _relation(), _relation(target="fx_dining_table_1", predicate="on")
    either = TaskGoal(summary="x", goal_state=[[inside], [on_table]])
    neither = TaskGoal(summary="x", goal_state=[[inside], [_relation(value=True, predicate="on")]])
    assert verify_goal(either, _env()).status == "satisfied"
    assert verify_goal(either, _env()).assertion_status == ["unsatisfied", "satisfied"]
    assert verify_goal(neither, _env()).status == "unsatisfied"


def test_combine_is_three_valued():
    assert combine(["satisfied", "satisfied"]) == "satisfied"
    assert combine(["satisfied", "unknown"]) == "unknown"
    assert combine(["unknown", "unsatisfied"]) == "unsatisfied"
    assert combine([]) == "unknown"


def test_completion_conditions_of_older_versions_are_moved_on_read():
    """3.2까지 있던 완료 판정: 지속 시간은 시간 조건으로(비어 있을 때만), 서술 기준은 other 제약으로 옮긴다."""
    raw = copy.deepcopy(GOLDEN["task_situation_description"])
    raw["spatiotemporal_conditions"]["time"] = None
    raw["task_goal"]["completion_conditions"] = [
        {"kind": "duration", "description": "30초 동안 가동됨", "value": 30, "unit": "sec"},
        {"kind": "unresolved", "description": "충분히 데워짐"},
        {"kind": "goal_state", "description": "목표 상태가 모두 성립하면 완료"}]
    tsd = TaskSituationDescription.model_validate(raw)
    assert tsd.spatiotemporal_conditions.time == "30초 동안 가동됨"
    assert [c.description for c in tsd.constraints if c.type == "other"] == ["충분히 데워짐"]
    assert "completion_conditions" not in tsd.task_goal.model_dump()


def test_golden_goal_passes_and_is_not_trivial():
    """골든의 목표 상태(머그가 전자레인지 밖)는 초기에도 성립하지만 30초 가열 판정이 남아 있어 무의미하지 않다."""
    result = check_goal(_tsd(), _env(), LEXICON)
    assert result.pass_ and result.initial_status == "satisfied" and not result.trivial


def test_goal_that_already_holds_is_rejected_as_trivial():
    tsd = _tsd(timed=False, goal_state=[_relation(value=False)])
    result = check_goal(tsd, _env(), LEXICON)
    assert result.trivial and not result.pass_


@pytest.mark.parametrize(
    ("goal_state", "fragment"),
    [
        ([{**_relation(), "kind": "teleport"}], "unknown kind"),
        ([_relation(target="fx_oven_9")], "unknown object"),
        ([_relation(predicate="inside_of")], "relation predicate"),
        ([{"kind": "state", "subject": "fx_microwave_1", "predicate": "heated",
           "value": True}], "state key"),
        ([{"kind": "state", "subject": "fx_oven_9", "predicate": "door_open",
           "value": True}], "unknown subject"),
    ],
)
def test_malformed_assertions_are_reported(goal_state, fragment):
    result = check_goal(_tsd(goal_state=goal_state), _env(), LEXICON)
    assert not result.pass_ and any(fragment in p for p in result.problems), result.problems


def test_goal_must_stay_within_target_objects():
    """목표가 가리키는 entity는 설명문의 대상 물체에 올라 있어야 한다 (설명문과 목표가 어긋나지 않게)."""
    raw = copy.deepcopy(GOLDEN["task_situation_description"])
    raw["target_objects"]["auxiliary"] = []  # 식탁을 대상 물체에서 뺀다
    raw["task_goal"]["goal_state"] = [_relation(target="fx_dining_table_1", predicate="on")]
    result = check_goal(TaskSituationDescription.model_validate(raw), _env(), LEXICON)
    assert any("not listed in target_objects" in p for p in result.problems)


def test_some_criterion_must_say_when_the_task_is_done():
    """목표 상태가 비어도 상태로 쓸 수 없는 기준(시간 조건, other 제약)이 있으면 된다. 둘 다 없으면 근거가 없다."""
    timed = check_goal(_tsd(goal_state=[]), _env(), LEXICON)
    assert not any("neither goal_state" in p for p in timed.problems)
    neither = check_goal(_tsd(timed=False, goal_state=[]), _env(), LEXICON)
    assert any("neither goal_state" in p for p in neither.problems)
    raw = copy.deepcopy(GOLDEN["task_situation_description"])
    raw["task_goal"] = {"summary": "그릇을 닦는 것", "goal_state": []}
    raw["spatiotemporal_conditions"]["time"] = None
    raw["constraints"] = [{"type": "other", "description": "그릇이 충분히 닦여야 함"}]
    described = check_goal(TaskSituationDescription.model_validate(raw), _env(), LEXICON)
    assert not any("neither goal_state" in p for p in described.problems)


def test_goal_is_rendered_inside_the_goal_section():
    names = {e["id"]: e["name_ko"] for e in [*_env()["objects"], *_env()["fixtures"]]}
    text = render_tsd_text(_tsd(), LEXICON, names)
    assert check_field_order(text)
    positions = [text.find(section) for section in SECTION_ORDER]
    assert positions == sorted(positions)
    assert "머그(obj_001): 전자레인지(fx_microwave_1) 안 아님" in text
    assert "시간 조건은 가열 시간 30초" in text and "완료 판정" not in text


def test_version_1_description_is_read_with_summary_only():
    """1.x 산출물은 task_goal이 문장이었다. 읽을 때 summary로 옮기고 목표 상태는 비운다."""
    raw = copy.deepcopy(GOLDEN["task_situation_description"])
    raw["task_goal"] = "머그를 데우는 것"
    tsd = TaskSituationDescription.model_validate(raw)
    assert tsd.task_goal.summary == "머그를 데우는 것" and tsd.task_goal.goal_state == []


def test_roles_follow_from_goal_state():
    """목표 단언의 주어는 필수, 성립해야 하는 위·안 관계의 대상은 핵심 수단이어야 한다."""
    raw = copy.deepcopy(GOLDEN["task_situation_description"])
    raw["task_goal"]["goal_state"] = [[_relation(target="fx_dining_table_1", predicate="on")]]
    result = check_goal(TaskSituationDescription.model_validate(raw), _env(), LEXICON)
    assert any("must be key_means (is auxiliary)" in p for p in result.problems)  # 식탁은 보조로 적혀 있다
    raw["target_objects"]["key_means"].append(raw["target_objects"]["required"].pop())
    raw["task_goal"]["goal_state"] = [[_relation()]]
    result = check_goal(TaskSituationDescription.model_validate(raw), _env(), LEXICON)
    assert any("must be required (is key_means)" in p for p in result.problems)


def test_unnumbered_name_means_any_of_them():
    """'조리대'처럼 번호를 뗀 이름은 그 이름의 아무 것이다: 하나라도 성립하면 성립한다."""
    from tsdgen.goals.kinds import GOAL_KINDS
    from tsdgen.goals.scene_index import SceneIndex
    from tsdgen.models import GoalAssertion
    from tsdgen.world_state import resolve

    labels = {"fx_counter_1": "조리대 1", "fx_counter_2": "조리대 2", "obj_001": "그릇"}
    tsd = {"task_goal": {"goal_state": [[{"kind": "relation", "subject": "그릇", "predicate": "on",
                                          "object": "조리대", "value": True}]]}}
    assertion = GoalAssertion.model_validate(resolve(tsd, labels)["task_goal"]["goal_state"][0][0])
    assert assertion.object == "fx_counter_1|fx_counter_2"
    env = {"objects": [{"id": "obj_001", "placement": {"fixture_id": "fx_counter_2", "relation": "on"}}],
           "fixtures": [{"id": "fx_counter_1"}, {"id": "fx_counter_2"}, {"id": "fx_sink_1"}]}
    kind = GOAL_KINDS.get("relation")
    assert kind.problems(assertion, SceneIndex.from_env(env), LEXICON) == []
    assert kind.holds(assertion, SceneIndex.from_env(env)) is True
    env["objects"][0]["placement"]["fixture_id"] = "fx_sink_1"
    assert kind.holds(assertion, SceneIndex.from_env(env)) is False


def test_older_state_kinds_read_as_one_state_kind():
    """3.0까지 object_state·fixture_state로 나눠 적은 상태 단언은 state로 읽힌다 (주어가 종류를 말해 준다)."""
    from tsdgen.models import GoalAssertion

    for old in ("object_state", "fixture_state"):
        assertion = GoalAssertion(kind=old, subject="fx_microwave_1", predicate="door_open")
        assert assertion.kind == "state"


def test_same_any_of_name_on_both_sides_is_not_the_same_entity():
    """'통조림 위에 통조림'처럼 여럿 중 아무 것을 가리키는 이름이 양쪽에 같아도 같은 물체로 보지 않는다."""
    from tsdgen.goals.kinds import GOAL_KINDS
    from tsdgen.goals.scene_index import SceneIndex
    from tsdgen.models import GoalAssertion

    index = SceneIndex.from_env({"objects": [{"id": "obj_001"}, {"id": "obj_002"}], "fixtures": []})
    relation = GOAL_KINDS.get("relation")
    both = GoalAssertion(kind="relation", subject="obj_001|obj_002", predicate="on", object="obj_001|obj_002")
    one = GoalAssertion(kind="relation", subject="obj_001", predicate="on", object="obj_001")
    assert not relation.problems(both, index, LEXICON)
    assert relation.problems(one, index, LEXICON) == ["subject and object are the same entity"]


def test_description_diversity_ignores_numbering_only_differences():
    """가구 번호만 다른 설명문은 같은 것으로 세고, 목표나 위치가 다르면 다른 것으로 센다."""
    from tsdgen import audit

    def record(counter, place, template="t"):
        tsd = {"task_goal": {"goal_state": [[{"kind": "relation", "subject": "머그", "predicate": "on",
                                              "object": counter, "value": True}]],
                             "completion_conditions": []},
               "target_objects": {"required": [{"ref": "머그", "location": place, "state": None}]},
               "constraints": []}
        return {"output": {"task_situation_description": tsd},
                "meta": {"task_template": template, "verification": {"final_pass": True}}}

    same = [record("조리대 3", "캐비닛 2 선반 위"), record("조리대 5", "캐비닛 7 선반 위")]
    assert audit.diversity(same)["distinct_descriptions"] == 1
    other = [*same, record("싱크대", "캐비닛 2 선반 위")]
    assert audit.diversity(other)["distinct_descriptions"] == 2
