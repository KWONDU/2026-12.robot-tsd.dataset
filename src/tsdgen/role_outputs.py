"""역할별 LLM 구조화 출력 스키마. 모델이 돌려줘야 하는 JSON 모양이며, 역할(roles.py)이 하나씩 짝지어 쓴다."""

from pydantic import Field

from tsdgen.models import ChecklistItem, PassResult, StrictModel, TaskSituationDescription


class GenerateOutput(StrictModel):
    """generate 역할의 응답 스키마."""

    reasoning: str
    task_situation_description: TaskSituationDescription


class RewriteOutput(StrictModel):
    user_instruction_ko: str


class ChecklistJudgeOutput(StrictModel):
    items: dict[str, ChecklistItem]


class ProbeJudgeOutput(StrictModel):
    """분해 시도는 폐기 대상이므로 저장 필드와 분리되어 있다 (스펙 6.4-5)."""

    decomposition_attempt: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    score: int


class ResolvabilityJudgeOutput(PassResult):
    rationale: str
