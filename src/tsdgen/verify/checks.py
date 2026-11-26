"""검수 항목 하나하나를 전략으로 등록한다.

실행 순서와 목록은 config(verify.checks)가 정하고, 등록된 이름이 곧 허용값이다.
앞 항목이 실패하면 뒤는 생략하고 사유를 남긴다(스펙 6.4). 검수 항목을 늘리는
일이 등록 한 줄과 config 한 줄로 끝나게 하려는 것이며, 검사 순서가 if 사슬로
코드에 박히지 않게 한다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from tsdgen import world_state
from tsdgen.config import Settings
from tsdgen.goals.oracle import compare_with_oracle
from tsdgen.goals.validation import check_goal
from tsdgen.llm.client import LLMClient
from tsdgen.models import Sample, TaskSituationDescription
from tsdgen.registry import Registry
from tsdgen.verify import judges
from tsdgen.verify.grounding import check_grounding
from tsdgen.verify.situation import check_situation
from tsdgen.verify.structure import check_structure

VERIFY_CHECKS = Registry("verification check", axes=(lambda s: s.verify.checks,))
"""이름 -> VerifyCheck. 순서는 config의 verify.checks가 정한다."""


@dataclass
class CheckContext:
    """검수 한 건이 쓰는 재료. 앞 항목이 채운 값을 뒤 항목이 본다."""

    settings: Settings
    client: LLMClient
    sample: Sample
    raw_tsd: dict  # 규칙 검사용: 모델이 쓴 이름표를 id로 되짚은 설명문 (json 보기에서는 쓴 그대로)
    lexicon: dict
    tsd: TaskSituationDescription | None = None
    goal_oracle: list | None = None  # 원천이 알려 준 목표 상태 정답 대안들 (없으면 None)
    written_tsd: dict | None = None  # 모델이 쓴 그대로의 설명문 (LLM 채점이 본다). 없으면 raw_tsd
    situation: dict | None = None  # 작업 합성이 남긴 상황 사실 (시나리오 meta.situation, 없으면 None)

    @property
    def env_ctx(self) -> dict:
        return self.sample.input.environment_context

    @property
    def world_text(self) -> str:
        """생성 모델이 받은 세계 상태 글. 채점 모델도 같은 글을 본다."""
        return world_state.model_input(self.sample.input)

    @property
    def tsd_text(self) -> str:
        return self.sample.output.task_situation_description_text


@dataclass
class CheckOutcome:
    """검수 한 건의 결과."""

    value: Any
    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class VerifyCheck:
    """등록된 검수 항목. field는 VerificationResult에서 결과가 앉는 자리다 (대시보드도 이 값을 읽는다)."""

    field: str
    run: Callable[[CheckContext], CheckOutcome]


def verify_check(name: str, *, field: str | None = None) -> Callable:
    """데코레이터. field를 주지 않으면 등록 이름이 곧 결과 필드 이름이다."""

    def register(fn: Callable[[CheckContext], CheckOutcome]) -> Callable:
        VERIFY_CHECKS.register(name)(VerifyCheck(field=field or name, run=fn))
        return fn

    return register


@verify_check("schema", field="schema_valid")
def _check_schema(ctx: CheckContext) -> CheckOutcome:
    try:
        ctx.tsd = TaskSituationDescription.model_validate(ctx.raw_tsd)
    except ValidationError as e:
        return CheckOutcome(False, passed=False, reason=str(e)[:300])
    return CheckOutcome(True, passed=True)


@verify_check("grounding")
def _check_grounding(ctx: CheckContext) -> CheckOutcome:
    result = check_grounding(ctx.tsd, ctx.tsd_text, ctx.env_ctx, ctx.lexicon,
                             ctx.sample.input.user_instruction)
    return CheckOutcome(result, passed=result.pass_)


@verify_check("structure")
def _check_structure(ctx: CheckContext) -> CheckOutcome:
    located = world_state.located(ctx.env_ctx, ctx.settings.contextualize)
    result = check_structure(ctx.tsd, ctx.tsd_text, ctx.raw_tsd, located)
    return CheckOutcome(result, passed=result.pass_)


@verify_check("goal")
def _check_goal(ctx: CheckContext) -> CheckOutcome:
    result = check_goal(ctx.tsd, ctx.env_ctx, ctx.lexicon)
    reason = "; ".join(result.problems[:3]) or ("goal already holds initially" if result.trivial else "")
    return CheckOutcome(result, passed=result.pass_, reason=reason)


@verify_check("goal_oracle")
def _check_goal_oracle(ctx: CheckContext) -> CheckOutcome:
    min_recall = ctx.settings.verify.thresholds.goal_oracle_min_recall
    result = compare_with_oracle(ctx.tsd.task_goal, ctx.goal_oracle, ctx.env_ctx, min_recall)
    reason = f"oracle recall {result.recall} < {min_recall}" if not result.pass_ else ""
    return CheckOutcome(result, passed=result.pass_, reason=reason)


@verify_check("situation")
def _check_situation(ctx: CheckContext) -> CheckOutcome:
    result = check_situation(ctx.written_tsd or ctx.raw_tsd, ctx.situation,
                             ctx.sample.input.entity_labels, ctx.lexicon["situation_words"], ctx.env_ctx)
    return CheckOutcome(result, passed=result.pass_, reason="; ".join(result.missing[:3]))


@verify_check("checklist")
def _check_checklist(ctx: CheckContext) -> CheckOutcome:
    result = judges.run_checklist(
        ctx.client,
        ctx.settings,
        ctx.sample.meta.generator.model,
        ctx.sample.input.user_instruction,
        ctx.world_text,
        ctx.written_tsd or ctx.raw_tsd,
        ctx.tsd_text,
    )
    return CheckOutcome(result, passed=result.pass_)


@verify_check("probe", field="sufficiency_probe")
def _check_probe(ctx: CheckContext) -> CheckOutcome:
    result = judges.run_probe(ctx.client, ctx.settings, ctx.tsd_text)
    return CheckOutcome(result, passed=result.pass_)
