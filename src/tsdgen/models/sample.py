"""샘플 한 건(입력, 추론, 출력, 메타)."""

from typing import Any

from pydantic import Field

from tsdgen.models.base import SampleStatus, Specificity, StrictModel
from tsdgen.models.tsd import TaskSituationDescription
from tsdgen.models.verification import VerificationResult


class SampleInput(StrictModel):
    user_instruction: str
    # 재작성 전의 원 지시문. 열람·납품 참조용이며 생성 프롬프트에는 들어가지 않는다.
    # (v1.0 산출물에는 없어서 optional. 대시보드는 없으면 시나리오 기록에서 찾아 보여 준다)
    original_instruction_en: str | None = None
    instruction_specificity: Specificity
    # 시뮬레이터 씬 상태를 정제한 내부 정본. 검수와 열람에 쓰고, 모델이 무엇을 받았는지는 world_state가 말한다.
    environment_context: dict[str, Any]
    # 모델이 실제로 받은 세계 상태 글 (world_state 보기가 만든다, v1.3). 없으면 environment_context JSON을 받은 것이다.
    world_state: str | None = None
    # entity id -> 모델이 본 이름. 설명문의 ref·subject·object를 id로 되짚어 검수할 때 쓴다 (v1.3)
    entity_labels: dict[str, str] = Field(default_factory=dict)


class SampleIntermediate(StrictModel):
    reasoning: str


class GeneratorMeta(StrictModel):
    model: str
    provider: str
    prompt_version: str
    temperature: float
    seed: int


class RewriterMeta(StrictModel):
    model: str
    prompt_version: str


class SampleMeta(StrictModel):
    generator: GeneratorMeta | None = None
    rewriter: RewriterMeta | None = None
    verification: VerificationResult | None = None
    sampling: dict[str, Any] | None = None
    task_template: str | None = None  # 이 샘플을 낳은 태스크 템플릿 '원천/태스크'
    status: SampleStatus = "pending"
    created_at: str | None = None
    pipeline_version: str | None = None
    schema_version: str | None = None  # 이 샘플 레코드의 스키마 버전 (config schema_versions.sample)


class SampleOutput(StrictModel):
    task_situation_description: TaskSituationDescription
    task_situation_description_text: str


class Sample(StrictModel):
    sample_id: str
    scenario_id: str
    domain: str
    lang: str
    input: SampleInput
    intermediate: SampleIntermediate | None = None
    output: SampleOutput | None = None
    meta: SampleMeta = Field(default_factory=SampleMeta)
