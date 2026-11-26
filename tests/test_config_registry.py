"""config 검증: 미등록 값 거부, judge ≠ generator 제약."""

import pytest

from tsdgen.config import CONFIG_PATH, PROJECT_ROOT, Settings, load_config_document


def _base_data() -> dict:
    return load_config_document(CONFIG_PATH, PROJECT_ROOT)


def test_unknown_adapter_rejected():
    data = _base_data()
    data["source"]["adapter"] = "no_such_adapter"
    with pytest.raises(ValueError, match="no_such_adapter"):
        Settings.model_validate(data)


def test_unknown_stage_rejected():
    data = _base_data()
    data["project"]["pipeline_order"] = ["ingest", "no_such_stage"]
    with pytest.raises(ValueError, match="no_such_stage"):
        Settings.model_validate(data)


def test_unknown_export_format_rejected():
    data = _base_data()
    data["export"]["formats"] = ["jsonl", "csv"]
    with pytest.raises(ValueError, match="csv"):
        Settings.model_validate(data)


def test_judge_must_differ_from_generator():
    data = _base_data()
    data["llm"]["roles"]["checklist_judge"] = {
        gen: gen for gen in data["llm"]["roles"]["generators"]
    }
    with pytest.raises(ValueError, match="differ"):
        Settings.model_validate(data)


def test_unknown_specificity_rejected():
    data = _base_data()
    data["contextualize"]["specificity"] = "casual"
    with pytest.raises(ValueError, match="casual"):
        Settings.model_validate(data)


def test_unbound_model_rejected():
    data = _base_data()
    data["llm"]["roles"]["rewriter"] = "no_such_model"
    with pytest.raises(ValueError, match="no_such_model"):
        Settings.model_validate(data)


def test_unknown_prefetch_source_rejected():
    data = _base_data()
    data["source"]["prefetch_sources"] = ["robocasa365", "no_such_source"]
    with pytest.raises(ValueError, match="no_such_source"):
        Settings.model_validate(data)


def test_unknown_scenario_filter_rejected():
    data = _base_data()
    data["sampling"]["filters"] = ["no_such_filter"]
    with pytest.raises(ValueError, match="no_such_filter"):
        Settings.model_validate(data)


def test_unknown_sampling_strategy_block_rejected():
    """전략별 설정 블록의 이름도 등록된 전략이어야 한다 (오타가 조용히 무시되지 않게)."""
    data = _base_data()
    data["sampling"]["stratifed"] = {"quota_key": "family", "quotas": {}}
    with pytest.raises(ValueError, match="stratifed"):
        Settings.model_validate(data)


def test_sampling_options_are_validated_by_the_strategy():
    data = _base_data()
    data["sampling"]["stratified"]["no_such_key"] = 1
    with pytest.raises(ValueError, match="no_such_key"):
        Settings.model_validate(data)


def test_unknown_fallback_specificity_rejected():
    data = _base_data()
    data["contextualize"]["fallback_specificity"] = "casual"
    with pytest.raises(ValueError, match="casual"):
        Settings.model_validate(data)


def test_orchestrating_stage_cannot_be_in_pipeline():
    data = _base_data()
    data["project"]["pipeline_order"] = ["ingest", "run"]
    with pytest.raises(ValueError, match="orchestrating"):
        Settings.model_validate(data)
