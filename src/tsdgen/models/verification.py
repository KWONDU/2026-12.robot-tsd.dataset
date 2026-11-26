"""검수 결과(VerificationResult)와 항목별 결과."""

from typing import Any

from pydantic import Field

from tsdgen.models.base import GoalStatus, PassResult, StrictModel


class GroundingResult(PassResult):
    unknown_refs: list[str] = Field(default_factory=list)
    state_conflicts: list[str] = Field(default_factory=list)
    hallucinated_entities: list[str] = Field(default_factory=list)


class StructureResult(PassResult):
    has_step_list: bool
    field_order_ok: bool
    all_objects_have_location: bool


class ChecklistItem(StrictModel):
    score: int
    rationale: str


class ChecklistResult(PassResult):
    rubric_version: str
    judge_model: str
    items: dict[str, ChecklistItem]
    total: int


class SufficiencyProbeResult(PassResult):
    judge_model: str
    missing_info: list[str] = Field(default_factory=list)
    score: int


class ResolvabilityResult(PassResult):
    rationale: str


class GoalCheckResult(PassResult):
    """목표 상태·완료 판정의 규칙 검사 결과."""

    problems: list[str] = Field(default_factory=list)
    initial_status: GoalStatus  # 목표 상태를 초기 씬에 대 본 판정
    assertion_status: list[GoalStatus] = Field(default_factory=list)  # 단언별 초기 판정
    trivial: bool = False  # 초기 씬에서 이미 목표가 성립하고 그 밖에 남은 완료 기준도 없음


class GoalOracleResult(PassResult):
    """원천이 알려 준 성공 판정(정답)과 생성된 목표 상태의 대조. 원천이 모르면 available=false."""

    available: bool
    oracle: list[dict[str, Any]] = Field(default_factory=list)  # 정답 단언 (entity id 공간)
    matched: list[bool] = Field(default_factory=list)  # 정답 단언별로 생성본에 있었는지
    already_holds: list[bool] = Field(default_factory=list)  # 초기 씬에서 이미 성립 (재현율에서 제외)
    recall: float | None = None
    alternatives: int = 1  # 성공하는 경우가 몇 가지인지 (oracle 항목마다 alternative 번호)
    chosen: int = 0  # 설명문과 가장 잘 맞은 대안의 순번 (0부터)


class SituationResult(PassResult):
    """작업 합성이 남긴 상황 사실(돌아가게 만드는 통로, 기다려야 하는 곳, 시간 창, 가공 시간)이 설명문에 있는지.
    상황 사실이 없는 시나리오(원천 과제, 가정·물류 합성)는 available=false로 통과한다."""

    available: bool
    missing: list[str] = Field(default_factory=list)  # 설명문에서 찾지 못한 사실 ('막힘 통로 18' 등)


class VerificationResult(StrictModel):
    schema_valid: bool
    grounding: GroundingResult | None = None
    structure: StructureResult | None = None
    checklist: ChecklistResult | None = None
    sufficiency_probe: SufficiencyProbeResult | None = None
    instruction_resolvability: ResolvabilityResult | None = None
    goal: GoalCheckResult | None = None
    goal_oracle: GoalOracleResult | None = None
    situation: SituationResult | None = None
    skipped_reason: str | None = None
    final_pass: bool
