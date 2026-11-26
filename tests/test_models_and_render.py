"""스키마 왕복, 환경 컨텍스트 누출 차단, 렌더러 골든 테스트."""

import json

from tsdgen.config import PROJECT_ROOT
from tsdgen.models import (
    SceneState,
    TaskSituationDescription,
    to_environment_context,
)
from tsdgen.render import render_tsd_text
from tsdgen.resources import load_resources

GOLDEN_PATH = PROJECT_ROOT / "resources" / "golden" / "heatmug_sample.json"
GOLDEN_TEXT_PATH = PROJECT_ROOT / "resources" / "golden" / "heatmug_text.txt"


def _golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_scene_state_roundtrip():
    scene = SceneState.model_validate(_golden()["scene_state"])
    again = SceneState.model_validate(scene.model_dump(mode="json"))
    assert again == scene


def test_tsd_roundtrip():
    tsd = TaskSituationDescription.model_validate(_golden()["task_situation_description"])
    again = TaskSituationDescription.model_validate(tsd.model_dump(mode="json"))
    assert again == tsd


def _string_values(obj):
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _string_values(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _string_values(item)
    elif isinstance(obj, str):
        yield obj


def test_environment_context_blocks_leaks():
    scene = SceneState.model_validate(_golden()["scene_state"])
    env_ctx = to_environment_context(scene)
    blob = json.dumps(env_ctx, ensure_ascii=False)
    values = set(_string_values(env_ctx))
    assert "provenance" not in env_ctx
    assert "HeatMug" not in blob  # 원래 태스크명
    assert '"role"' not in blob  # 물체 역할
    assert scene.scene_id not in blob  # 태스크명이 드러나는 원 scene_id
    for original_name in scene.provenance.source_object_names.values():
        assert original_name not in values  # 역할이 드러나는 원래 물체 이름


def test_render_golden_text_fixed_order():
    golden = _golden()["task_situation_description"]
    golden["spatiotemporal_conditions"]["space"] = {"start": "식탁", "end": "조리대 우측 전자레인지"}
    tsd = TaskSituationDescription.model_validate(golden)
    lexicon = load_resources(PROJECT_ROOT / "resources")
    scene = _golden()["scene_state"]
    names = {e["id"]: e["name_ko"] for e in [*scene["objects"], *scene["fixtures"]]}
    assert render_tsd_text(tsd, lexicon, names) == GOLDEN_TEXT_PATH.read_text(encoding="utf-8")


def test_space_sentence_of_older_versions_is_read_as_spatial_constraint():
    """3.1까지의 공간 조건(문장 하나)은 읽을 때 spatial 제약으로 옮기고 공간 조건은 비운다."""
    golden = _golden()["task_situation_description"]
    sentence = golden["spatiotemporal_conditions"]["space"]
    assert isinstance(sentence, str)
    tsd = TaskSituationDescription.model_validate(golden)
    assert tsd.spatiotemporal_conditions.space is None
    assert ("spatial", sentence) in [(c.type, c.description) for c in tsd.constraints]


def test_space_names_where_the_task_starts_and_ends():
    golden = _golden()["task_situation_description"]
    golden["spatiotemporal_conditions"]["space"] = {"start": "냉장고 혹은 조리대 2", "end": None}
    tsd = TaskSituationDescription.model_validate(golden)
    text = render_tsd_text(tsd, load_resources(PROJECT_ROOT / "resources"))
    assert "공간 조건은 시작 냉장고 혹은 조리대 2\n" in text


def test_render_handles_absent_conditions():
    golden = _golden()["task_situation_description"]
    golden["spatiotemporal_conditions"] = {"time": None, "space": None}
    golden["constraints"] = []
    golden["task_goal"].pop("completion_conditions")  # 예전 칸: 있으면 읽을 때 시간 조건과 제약으로 옮겨진다
    tsd = TaskSituationDescription.model_validate(golden)
    lexicon = load_resources(PROJECT_ROOT / "resources")
    text = render_tsd_text(tsd, lexicon)
    assert "시간 조건은 없음" in text
    assert text.rstrip().endswith("제약 사항: 없음")
