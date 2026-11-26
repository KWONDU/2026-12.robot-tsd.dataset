"""접지·구조 검사기 단위 테스트 (정탐·오탐 케이스)."""

import json

from tsdgen.config import PROJECT_ROOT
from tsdgen.models import SceneState, TaskSituationDescription, to_environment_context
from tsdgen.render import render_tsd_text
from tsdgen.resources import load_resources
from tsdgen.verify.grounding import check_grounding
from tsdgen.verify.structure import check_structure

GOLDEN = json.loads(
    (PROJECT_ROOT / "resources" / "golden" / "heatmug_sample.json").read_text(encoding="utf-8")
)
LEXICON = load_resources(PROJECT_ROOT / "resources")


LOCATED = {"obj_001"}  # 골든 씬에서 놓인 곳이 있는 물체 (식탁 위 머그)


def _fixture_set():
    tsd = TaskSituationDescription.model_validate(GOLDEN["task_situation_description"])
    scene = SceneState.model_validate(GOLDEN["scene_state"])
    env_ctx = to_environment_context(scene)
    return tsd, render_tsd_text(tsd, LEXICON), env_ctx


def test_grounding_golden_passes():
    tsd, text, env_ctx = _fixture_set()
    result = check_grounding(tsd, text, env_ctx, LEXICON)
    assert result.pass_, result


def test_grounding_detects_unknown_ref():
    tsd, text, env_ctx = _fixture_set()
    tsd.target_objects.required[0].ref = "obj_999"
    result = check_grounding(tsd, text, env_ctx, LEXICON)
    assert result.unknown_refs == ["obj_999"] and not result.pass_


def test_grounding_detects_state_conflict():
    tsd, _, env_ctx = _fixture_set()
    tsd.target_objects.key_means[0].state = "문 열림, 가동 중"
    text = render_tsd_text(tsd, LEXICON)
    result = check_grounding(tsd, text, env_ctx, LEXICON)
    assert result.state_conflicts and not result.pass_


def test_grounding_compartment_phrase_is_checked_on_its_own_key():
    """냉장고 본문 문이 닫혀 있어도 '냉동실 문이 열려 있음'은 모순이 아니다. 냉동실 키로만 검사한다."""
    tsd, _, env_ctx = _fixture_set()
    entry = tsd.target_objects.key_means[0]
    entity = next(f for f in env_ctx["fixtures"] if f["id"] == entry.ref)
    entity["states"] = {"door_open": False, "freezer_door_open": True}
    entry.state = "냉동실 문이 열려 있음"
    assert not check_grounding(tsd, render_tsd_text(tsd, LEXICON), env_ctx, LEXICON).state_conflicts
    entry.state = "냉동실 문이 닫혀 있음"
    result = check_grounding(tsd, render_tsd_text(tsd, LEXICON), env_ctx, LEXICON)
    assert result.state_conflicts == [f"{entry.ref}: '냉동실 문이 닫혀 있음' contradicts "
                                      "freezer_door_open=True"]


def test_grounding_detects_hallucinated_entity():
    tsd, _, env_ctx = _fixture_set()
    tsd.task_goal.summary = "바나나를 전자레인지에 데우는 것"
    text = render_tsd_text(tsd, LEXICON)
    result = check_grounding(tsd, text, env_ctx, LEXICON)
    assert any("banana" in h for h in result.hallucinated_entities) and not result.pass_


def test_structure_golden_passes():
    tsd, text, _ = _fixture_set()
    result = check_structure(tsd, text, GOLDEN["task_situation_description"], LOCATED)
    assert result.pass_, result


def test_structure_detects_numbered_steps():
    tsd, _, _ = _fixture_set()
    tsd.task_goal.summary = "1. 문을 연다\n2. 머그를 넣는다"
    text = render_tsd_text(tsd, LEXICON)
    result = check_structure(tsd, text, {}, LOCATED)
    assert result.has_step_list and not result.pass_


def test_structure_detects_step_words():
    tsd, _, _ = _fixture_set()
    tsd.constraints[0].description = "1단계로 문을 열어야 함"
    text = render_tsd_text(tsd, LEXICON)
    result = check_structure(tsd, text, {}, LOCATED)
    assert result.has_step_list


def test_structure_detects_steps_key():
    tsd, text, _ = _fixture_set()
    raw = {"steps": ["open", "put"]}
    result = check_structure(tsd, text, raw, LOCATED)
    assert result.has_step_list


def test_structure_allows_precedence_prose():
    # "A가 B에 선행해야 함" 서술은 허용된다 (골든 제약이 정확히 그 형태)
    tsd, text, _ = _fixture_set()
    result = check_structure(tsd, text, GOLDEN["task_situation_description"], LOCATED)
    assert not result.has_step_list


def test_structure_requires_locations():
    """세계 상태가 놓인 곳을 주는 물체는 위치를 빠뜨리면 안 되고, 위치가 없는 가구는 비워도 된다."""
    tsd, text, _ = _fixture_set()
    tsd.target_objects.required[0].location = " "
    result = check_structure(tsd, text, {}, LOCATED)
    assert not result.all_objects_have_location and not result.pass_
    tsd, text, _ = _fixture_set()
    tsd.target_objects.auxiliary[0].location = None
    assert check_structure(tsd, text, {}, LOCATED).all_objects_have_location
