"""LLM judge 호출 (checklist 교차 검수, sufficiency probe, resolvability).

모델 선택·파라미터·스키마는 역할 정의가 들고 있으므로 여기서는 프롬프트를 만들고
판정 결과를 우리 자료형으로 옮기는 일만 한다.
"""

from tsdgen import prompts, roles
from tsdgen.config import Settings
from tsdgen.llm.client import LLMClient
from tsdgen.models import (
    ChecklistResult,
    ResolvabilityResult,
    SufficiencyProbeResult,
)
from tsdgen.resources import load_resources
from tsdgen.roles import call_role


def run_checklist(
    client: LLMClient,
    settings: Settings,
    generator_model: str,
    instruction: str,
    world: str,
    tsd: dict,
    tsd_text: str,
) -> ChecklistResult:
    """world는 생성 모델이 받은 세계 상태 글, tsd는 생성 모델이 쓴 그대로의 설명문이다 (같은 눈높이로 채점)."""
    role = roles.CHECKLIST
    values = {
        "USER_INSTRUCTION": instruction,
        "ENVIRONMENT_CONTEXT": world,
        "TSD_JSON": prompts.as_json(tsd),
        "TSD_TEXT": tsd_text,
    }
    parsed = call_role(client, settings, role, values, generator=generator_model)

    expected = list(load_resources(settings.path("resources"))["checklist_items"])
    missing = [key for key in expected if key not in parsed.items]
    if missing:
        raise ValueError(f"checklist judge omitted items: {missing}")
    items = {key: parsed.items[key] for key in expected}
    threshold = settings.verify.thresholds.checklist_min_item_score
    return ChecklistResult(
        rubric_version=role.prompt_version(settings),
        judge_model=role.model_for(settings, generator_model),
        items=items,
        total=sum(item.score for item in items.values()),
        **{"pass": all(item.score >= threshold for item in items.values())},
    )


def run_probe(client: LLMClient, settings: Settings, tsd_text: str) -> SufficiencyProbeResult:
    role = roles.PROBE
    parsed = call_role(client, settings, role, {"TSD_TEXT": tsd_text})
    # decomposition_attempt는 KIST 영역이므로 저장하지 않고 여기서 폐기한다 (스펙 6.4-5)
    return SufficiencyProbeResult(
        judge_model=role.model_for(settings, None),
        missing_info=parsed.missing_info,
        score=parsed.score,
        **{"pass": parsed.score >= settings.verify.thresholds.probe_min_score},
    )


def run_resolvability(
    client: LLMClient, settings: Settings, original_en: str, rewritten_ko: str, world: str
) -> ResolvabilityResult:
    values = {
        "ORIGINAL_INSTRUCTION": original_en,
        "REWRITTEN_INSTRUCTION": rewritten_ko,
        "ENVIRONMENT_CONTEXT": world,
    }
    parsed = call_role(client, settings, roles.RESOLVABILITY, values)
    return ResolvabilityResult(rationale=parsed.rationale, **{"pass": parsed.pass_})
