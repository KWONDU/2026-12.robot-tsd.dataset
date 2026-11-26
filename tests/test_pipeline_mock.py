"""MockClient로 파이프라인 전체(run)를 수행하는 통합 테스트 (스펙 10.1)."""

import json
import re

from tsdgen.registry import STAGES
from tsdgen.stages.common import stage_output
from tsdgen.util import read_jsonl


def _deliverable_dir(settings):
    STAGES.get("run")(settings)
    deliverable_dirs = list(settings.path("deliverables").glob("test_*"))
    assert len(deliverable_dirs) == 1
    return deliverable_dirs[0]


def test_full_run_produces_deliverables(settings):
    out_dir = _deliverable_dir(settings)

    samples = list(read_jsonl(out_dir / "samples.jsonl"))
    assert len(samples) == settings.project.n_scenarios  # 시나리오당 1건
    for sample in samples:
        assert sample["meta"]["status"] == "accepted"
        assert sample["meta"]["schema_version"] == settings.schema_versions["sample"]
        verification = sample["meta"]["verification"]
        assert verification["schema_valid"]
        assert verification["grounding"]["pass"]
        assert verification["structure"]["pass"]
        assert verification["goal"]["pass"]
        assert all(
            item["score"] >= 1 for item in verification["checklist"]["items"].values()
        )
        # probe의 분해 시도는 저장되지 않아야 한다 (KIST 영역)
        assert "decomposition_attempt" not in json.dumps(verification)
        text = sample["output"]["task_situation_description_text"]
        assert text.startswith("작업 목표:") and "목표 상태:" in text
        assert sample["output"]["task_situation_description"]["task_goal"]["goal_state"]

    # k=2 생성 중 차순위는 alternate로 보존
    alternates = list(read_jsonl(out_dir / "alternates.jsonl"))
    assert len(alternates) == settings.project.n_scenarios

    # 문서는 README 하나로 통합: 구성·생성 설정 뒤에 읽는 법과 샘플 전건이 이어진다
    readme = (out_dir / "README.md").read_text(encoding="utf-8")
    assert "읽는 법" in readme and "## 샘플 1" in readme and "| 목표 상태 |" in readme
    assert not list(out_dir.glob("review_*.md"))
    stats = json.loads((out_dir / "stats.json").read_text(encoding="utf-8"))
    assert stats["counts"]["accepted"] == settings.project.n_scenarios
    assert stats["goal"]["goal_passed"] == stats["goal"]["goal_checked"] > 0
    assert (settings.run_dir() / "state.sqlite").exists()
    assert (settings.run_dir() / "manifest.json").exists()
    # 실행에 쓴 설정 사본과, 합쳐진 최종 설정·hash가 남는다
    snapshot = settings.run_dir() / "_config"
    assert {"config.yaml", "models.yaml", "sources.yaml", "resolved.yaml", "hash.txt"} <= {
        p.name for p in snapshot.iterdir()}
    manifest = json.loads((settings.run_dir() / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["config_hash"] == (snapshot / "hash.txt").read_text(encoding="utf-8").strip()
    assert (settings.run_dir() / "estimate.json").exists()


# 자연어 보기 이전(JSON 보기)의 설정: 보기와 그 보기용 프롬프트 판을 함께 되돌린다
JSON_VIEW_PROMPTS = {"generate": "gen_v3", "rewrite": "rewrite_v1", "checklist": "check_v3",
                     "resolvability": "resolve_v1"}


def _kist_package(settings):
    pkg = _deliverable_dir(settings) / "kist_package"
    kist_files = sorted((pkg / "samples").glob("*.json"))
    assert len(kist_files) == settings.project.n_scenarios * settings.llm.per_scenario_k
    first = json.loads(kist_files[0].read_text(encoding="utf-8"))
    assert set(first) == {"id", "meta", "input", "output"}
    # 만든 방법은 생성 모델과 태스크 템플릿만 (검수·비용 같은 내부 기록은 없음)
    assert set(first["meta"]) == {"generator_model", "task_template"} and first["meta"]["task_template"]
    assert first["id"] == kist_files[0].stem  # id는 파일명과 같다 (내부 식별자 미노출)
    assert set(first["output"]) == {"reasoning", "task_situation_description"}
    blob = json.dumps(first, ensure_ascii=False)
    # 내부 정보(검수·모델·버전 메타)와 중립 id는 어디에도 없어야 한다
    for token in ("verification", "schema_version", "state_source", "_text",
                  '"obj_0', '"fx_', '"pred": "in"', '"predicate": "in"'):
        assert token not in blob, token
    # 목표 상태는 전달 어휘(in -> inside)를 쓴다
    goal = first["output"]["task_situation_description"]["task_goal"]
    assert set(goal) == {"summary", "goal_state"}
    assert goal["goal_state"][0][0]["predicate"] == "inside"
    schema = json.loads(
        (pkg / "schema" / "task_situation_description.schema.json").read_text(encoding="utf-8"))
    assert "schema_version" not in schema.get("properties", {})
    assert "GoalAssertion" in schema.get("$defs", {})
    prompt = (pkg / "generation_prompt.txt").read_text(encoding="utf-8")
    assert "<<GOLDEN" not in prompt  # 예시(shot)는 채워져 있어야 한다
    assert "<<USER_INSTRUCTION>>" in prompt and "---USER---" in prompt
    assert '"predicate": "in"' not in prompt  # 예시의 목표 상태도 전달 어휘로
    readme = (pkg / "README.md").read_text(encoding="utf-8")
    assert str(len(kist_files)) in readme and "goal_state" in readme
    return first, prompt


def test_kist_package_carries_only_the_interface(settings):
    """KIST 전달용: kist_generator(테스트에선 mock)가 만들고 통과한 샘플 하나가 JSON 하나.
    입력은 모델이 실제로 받은 그대로다. 자연어 보기에서는 세계 상태 글이고, 설명문도 그 글의 이름으로 가리킨다."""
    first, prompt = _kist_package(settings)
    world = first["input"]["environment_context"]
    assert isinstance(world, str) and "물체가 놓인 곳:" in world
    assert not re.search(r"\b(?:obj|fx)_\w*\d", json.dumps(first, ensure_ascii=False))
    required = first["output"]["task_situation_description"]["target_objects"]["required"][0]
    assert required["ref"] in world  # 설명문의 ref는 세계 상태 글에 나온 이름이다
    assert not re.search(r"\b(?:obj|fx)_\w*\d", prompt)  # 예시도 같은 보기로 채운다


def test_kist_package_json_view_keeps_scene_json(settings, monkeypatch):
    """JSON 보기로 되돌리면 전달 입력은 예전처럼 씬 상태 JSON이고, id는 원본 데이터셋 이름으로 바뀐다."""
    monkeypatch.setattr(settings.contextualize, "world_state", "json")
    monkeypatch.setattr(settings, "prompts_versions", {**settings.prompts_versions, **JSON_VIEW_PROMPTS})
    first, _prompt = _kist_package(settings)
    env = first["input"]["environment_context"]
    assert {"scene_id", "domain", "simulator", "layout", "constraints"} <= set(env)
    first_obj = env["objects"][0]
    assert "role" not in first_obj  # 모델 입력에 없던 필드는 지어내지 않는다
    assert not re.fullmatch(r"obj_\d+", first_obj["id"])  # 원본 데이터셋의 실제 이름으로 치환


def test_generate_resume_skips_done(settings):
    for stage_name in ("ingest", "sample", "contextualize", "generate"):
        STAGES.get(stage_name)(settings)
    first = list(read_jsonl(stage_output(settings, "generate")))
    result = STAGES.get("generate")(settings)  # 재실행
    second = list(read_jsonl(stage_output(settings, "generate")))
    assert result["written"] == 0  # 완료 레코드는 건너뛴다 (중복 호출 없음)
    assert len(first) == len(second)


def test_estimate_reports_unknown_cost(settings):
    for stage_name in ("ingest", "sample"):
        STAGES.get(stage_name)(settings)
    estimate = STAGES.get("estimate")(settings)
    assert estimate["n_scenarios"] == settings.project.n_scenarios
    k = settings.llm.per_scenario_k
    assert estimate["calls_per_scenario"] == 2 + 3 * k
    # mock 모델은 단가 0으로 등록되어 있으므로 비용이 0으로 계산된다
    assert estimate["total_cost_usd"] == 0.0


def test_estimate_reports_unknown_cost_when_price_missing(settings):
    """단가가 비어 있는 모델이 하나라도 섞이면 총액을 추정하지 못한다고 말해야 한다.

    모르는 값을 0으로 취급해 총액을 낮게 보고하면 예산 가드가 무력해진다.
    """
    from tsdgen.stages.estimate import build_estimate

    for stage_name in ("ingest", "sample"):
        STAGES.get(stage_name)(settings)
    settings.estimate.prices_per_1m_tokens["mock"] = {"input": None, "output": None}
    estimate = build_estimate(settings)
    assert estimate["total_cost_usd"] is None
    assert estimate["per_model"]["mock"]["cost_usd"] is None
    assert estimate["per_model"]["mock-b"]["cost_usd"] == 0.0
