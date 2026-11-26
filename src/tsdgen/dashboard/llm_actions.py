"""채팅으로 요청받아 실행하는 LLM 분석 (대시보드 산출물 기반).

규칙으로 셀 수 있는 것은 대시보드 화면(views/)이 다 하고, 여기는 "왜"를 묻는 일만 남긴다.
각 액션은 LLM_ACTIONS에 등록된 사양 하나이고, 프롬프트는 prompts/analysis/의
버전 파일이다(수정은 새 버전 파일로만). 호출은 파이프라인과 같은 LLM 계층을
그대로 쓰므로 재시도·캐시·사용량 기록이 똑같이 적용된다. 대시보드 화면은 조회
전용이므로 이 액션들을 부르는 버튼은 없다.

파이프라인 역할(roles.py)과 섞지 않는 이유: 역할은 시나리오당 호출 횟수가
비용 추정의 근거가 되는데, 이 액션들은 요청받아야만 돌므로 그 산식에 넣으면
추정이 오염된다.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tsdgen import prompts
from tsdgen.config import Settings
from tsdgen.dashboard.data import RunData
from tsdgen.dashboard.views import catalog, render
from tsdgen.llm.client import LLMClient
from tsdgen.models import FAILED_STATUSES
from tsdgen.registry import MOCK_RESPONSES, Registry

LLM_ACTIONS = Registry("dashboard llm action")
"""이름 -> ActionSpec. 등록 이름이 곧 액션 이름이고 프롬프트 파일 이름이다."""

PROMPT_DIR = "analysis"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiagnosisIssue(StrictModel):
    aspect: str
    severity: int  # 0 경미 / 1 보통 / 2 심각
    detail: str


class SampleDiagnosis(StrictModel):
    """샘플 하나에 대한 심층 진단."""

    strengths: list[str] = Field(default_factory=list)
    issues: list[DiagnosisIssue] = Field(default_factory=list)
    verdict_opinion: str
    suggested_fix: str | None = None


class FailureCluster(StrictModel):
    name: str
    sample_ids: list[str] = Field(default_factory=list)
    description: str
    remedy: str


class FailureTaxonomy(StrictModel):
    """미달 샘플들의 실패 유형 분류."""

    clusters: list[FailureCluster] = Field(default_factory=list)
    overall: str


class ImprovementItem(StrictModel):
    target: str  # 바꿀 대상 (프롬프트 이름, config 키 등)
    change: str
    rationale: str


class ImprovementAdvice(StrictModel):
    """통계와 실패를 근거로 한 개선 제안."""

    suggestions: list[ImprovementItem] = Field(default_factory=list)
    caution: str


@dataclass(frozen=True)
class ActionSpec:
    """LLM 분석 버튼 하나의 계약."""

    name: str
    title: str
    scope: str  # "sample" | "run"
    description: str
    output_model: type[BaseModel]
    build_values: Any  # (RunData, sample_id | None) -> 프롬프트 자리에 채울 값들


def _sample_digest(sample: dict) -> dict:
    """LLM에 보낼 샘플 요약. 원문 전부가 아니라 판단에 필요한 것만 담는다."""
    verification = (sample.get("meta") or {}).get("verification") or {}
    checklist = verification.get("checklist") or {}
    return {
        "sample_id": sample.get("sample_id"),
        "status": (sample.get("meta") or {}).get("status"),
        "user_instruction": (sample.get("input") or {}).get("user_instruction"),
        "environment_context": (sample.get("input") or {}).get("environment_context"),
        "reasoning": (sample.get("intermediate") or {}).get("reasoning"),
        "tsd": (sample.get("output") or {}).get("task_situation_description"),
        "checklist": dict(checklist.get("items", {})),
        "probe": verification.get("sufficiency_probe"),
        "skipped_reason": verification.get("skipped_reason"),
    }


def _values_sample_diagnosis(data: RunData, sample_id: str | None) -> dict[str, str]:
    sample = data.sample_by_id(sample_id or "")
    if sample is None:
        raise ValueError(f"sample '{sample_id}' not found in run '{data.run_id}'")
    return {"SAMPLE_JSON": json.dumps(_sample_digest(sample), ensure_ascii=False, indent=1)}


def _values_failure_taxonomy(data: RunData, _sample_id: str | None) -> dict[str, str]:
    rejected = [s for s in data.samples()
                if (s.get("meta") or {}).get("status") in FAILED_STATUSES]
    if not rejected:
        raise ValueError("미달 샘플이 없다")
    digests = [_sample_digest(s) for s in rejected]
    for d in digests:  # 분류에는 씬 전체가 필요 없다. 요약 크기를 줄인다.
        d.pop("environment_context", None)
        d.pop("reasoning", None)
    return {"REJECTED_JSON": json.dumps(digests, ensure_ascii=False, indent=1)}


# 개선 제안에 넣지 않는 블록 종류: 파일 원문(code, files, prompts), 구조 나무(json), 분석 실패(error)
SKIPPED_BLOCK_KINDS = {"code", "files", "json", "prompts", "error"}


def _values_improvement(data: RunData, _sample_id: str | None) -> dict[str, str]:
    # 집계만 담는다. 근거 표(fold)는 프롬프트에 넣기엔 크다.
    stats = [
        {"view": page["label"], "label": block.get("label"), "data": block["data"]}
        for page in catalog()
        for block in render(data, page["name"])
        if block["kind"] not in SKIPPED_BLOCK_KINDS
    ]
    return {
        "STATS_JSON": json.dumps(stats, ensure_ascii=False, indent=1),
        "CHECKLIST_GLOSSARY": json.dumps(data.lexicon()["checklist_items"], ensure_ascii=False),
    }


def _register(spec: ActionSpec) -> ActionSpec:
    LLM_ACTIONS.register(spec.name)(spec)
    return spec


SAMPLE_DIAGNOSIS = _register(ActionSpec(
    name="sample_diagnosis",
    title="이 샘플 심층 진단",
    scope="sample",
    description="설명문의 강점·문제를 짚고 채점 결과가 타당했는지 의견을 낸다.",
    output_model=SampleDiagnosis,
    build_values=_values_sample_diagnosis,
))

FAILURE_TAXONOMY = _register(ActionSpec(
    name="failure_taxonomy",
    title="미달 샘플 유형 분류",
    scope="run",
    description="미달 샘플 전체를 실패 유형으로 묶고 유형별 해결 방향을 제안한다.",
    output_model=FailureTaxonomy,
    build_values=_values_failure_taxonomy,
))

IMPROVEMENT_ADVICE = _register(ActionSpec(
    name="improvement_advice",
    title="개선 제안",
    scope="run",
    description="규칙 기반 통계를 근거로 프롬프트·설정에서 바꿀 만한 것을 제안한다.",
    output_model=ImprovementAdvice,
    build_values=_values_improvement,
))


def run_action(
    client: LLMClient, settings: Settings, data: RunData, name: str, sample_id: str | None
) -> dict:
    """액션 하나를 실행해 검증된 결과를 dict로 돌려준다."""
    spec = LLM_ACTIONS.get(name)
    cfg = settings.dashboard
    version = f"{spec.name}_{cfg.prompt_version}"
    system_t, user_t = prompts.load_template(settings, PROMPT_DIR, version)
    messages = [
        {"role": "system", "content": system_t},
        {"role": "user", "content": prompts.fill(user_t, spec.build_values(data, sample_id))},
    ]
    parsed = client.complete_structured(
        model_key=cfg.model,
        role_name=spec.name,
        messages=messages,
        output_model=spec.output_model,
        params=cfg,
        seed=settings.project.seed,
        prompt_version=version,
    )
    return parsed.model_dump(mode="json")


def list_actions() -> list[dict]:
    out = []
    for name in LLM_ACTIONS.names():
        spec = LLM_ACTIONS.get(name)
        out.append({"name": spec.name, "title": spec.title, "scope": spec.scope,
                    "description": spec.description})
    return out


# ---- mock 스텁: API 키 없이도 대시보드 버튼을 시험할 수 있게 한다 ---------------- #
@MOCK_RESPONSES.register("sample_diagnosis")
def _mock_diagnosis(_content: str, _settings: Settings) -> dict:
    return {"strengths": ["mock: 접지 양호"],
            "issues": [{"aspect": "위치 상세", "severity": 1, "detail": "mock 진단"}],
            "verdict_opinion": "mock: 판정 타당", "suggested_fix": None}


_SAMPLE_ID_RE = re.compile(r'"sample_id":\s*"([^"]+)"')


@MOCK_RESPONSES.register("failure_taxonomy")
def _mock_taxonomy(content: str, _settings: Settings) -> dict:
    ids = sorted(set(_SAMPLE_ID_RE.findall(content)))
    return {"clusters": [{"name": "mock 유형", "sample_ids": ids[:3],
                          "description": "mock", "remedy": "mock"}], "overall": "mock"}


@MOCK_RESPONSES.register("improvement_advice")
def _mock_advice(_content: str, _settings: Settings) -> dict:
    return {"suggestions": [{"target": "prompts/generate", "change": "mock", "rationale": "mock"}],
            "caution": "mock"}
