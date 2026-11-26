"""LLM 역할 정의.

역할 하나가 프롬프트 파일, 출력 스키마, 호출 파라미터 묶음, 시나리오당 호출
횟수, 담당 모델 라우팅을 함께 들고 있다. 이것들이 예전에는 프롬프트 빌더·스테이지·
비용 추정·mock 백엔드에 흩어져 있어서, 역할을 하나 늘리면 다섯 파일을 고쳐야 했다.

새 역할 추가는 여기 Role 하나를 등록하고 프롬프트 파일과 mock 응답을 붙이는
것으로 끝난다. 등록된 이름이 곧 프롬프트 디렉토리 이름이고 config의
prompts_versions·estimate.assumed_output_tokens 키다. 호출부는 call_role에 유저
메시지 자리 값만 넘기며, 메시지 조립·모델 선택·파라미터·출력 검증은 여기서 끝난다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from operator import attrgetter
from typing import Any

from pydantic import BaseModel

from tsdgen import prompts
from tsdgen.llm.client import LLMClient
from tsdgen.registry import ROLES
from tsdgen.role_outputs import (
    ChecklistJudgeOutput,
    GenerateOutput,
    ProbeJudgeOutput,
    ResolvabilityJudgeOutput,
    RewriteOutput,
)

GENERATION_PARAMS = attrgetter("llm.generation")
JUDGING_PARAMS = attrgetter("llm.judging")


def _no_variant(_settings: Any) -> str | None:
    return None


def _no_system_values(_settings: Any) -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class Role:
    """LLM 호출 역할 하나의 계약."""

    name: str
    output_model: type[BaseModel]
    params: Callable[[Any], Any]
    """settings -> 이 역할이 쓰는 호출 파라미터 묶음 (temperature, 최대 출력 토큰, 재시도)."""
    calls_per_scenario: Callable[[Any], int]
    """settings -> 시나리오 한 건을 처리할 때 이 역할이 호출되는 횟수. 비용 추정의 근거이며,
    통합 테스트가 실제 호출 기록과 대조해 어긋나면 실패한다."""
    model_for: Callable[[Any, str | None], str]
    """(settings, 이 슬롯의 생성 모델) -> 이 호출을 맡을 모델 키. 역할별 라우팅의 단일 출처다."""
    variant: Callable[[Any], str | None] = _no_variant
    """settings -> 기본 프롬프트 변형. 있으면 파일 버전 뒤에 _<변형>으로 붙는다."""
    system_values: Callable[[Any], dict[str, str]] = _no_system_values
    """settings -> 시스템 메시지의 자리 값 (예: 생성 프롬프트의 예시)."""

    def prompt_version(self, settings: Any, variant: str | None = None) -> str:
        """프롬프트 파일 버전. variant를 주면 역할의 기본 변형 대신 그것을 쓴다."""
        chosen = variant or self.variant(settings)
        base = settings.prompts_versions[self.name]
        return f"{base}_{chosen}" if chosen else base

    def assumed_output_tokens(self, settings: Any) -> int:
        return settings.estimate.assumed_output_tokens[self.name]

    def models(self, settings: Any) -> list[str]:
        """이 역할을 맡는 모델 전부. 생성 슬롯마다 담당이 다를 수 있어 생성 모델마다 묻는다."""
        return sorted({self.model_for(settings, g) for g in settings.llm.roles.generators})


def _once(_settings: Any) -> int:
    return 1


def _per_generation(settings: Any) -> int:
    return settings.llm.per_scenario_k


def generator_for_slot(settings: Any, slot: int) -> str:
    """시나리오당 slot번째 생성에 배정된 모델. 생성기 목록을 라운드로빈한다."""
    generators = settings.llm.roles.generators
    return generators[slot % len(generators)]


def _register(role: Role) -> Role:
    ROLES.register(role.name)(role)
    return role


REWRITE = _register(
    Role(
        name="rewrite",
        model_for=lambda s, _gen: s.llm.roles.rewriter,
        output_model=RewriteOutput,
        params=GENERATION_PARAMS,
        calls_per_scenario=_once,
        # 구체성 수준마다 프롬프트 파일이 다르다: rewrite_natural.txt
        variant=lambda s: s.contextualize.specificity,
    )
)

RESOLVABILITY = _register(
    Role(
        name="resolvability",
        model_for=lambda s, _gen: s.llm.roles.resolvability_judge,
        output_model=ResolvabilityJudgeOutput,
        params=JUDGING_PARAMS,
        calls_per_scenario=_once,
    )
)

GENERATE = _register(
    Role(
        name="generate",
        model_for=lambda _s, gen: gen,
        output_model=GenerateOutput,
        params=GENERATION_PARAMS,
        calls_per_scenario=_per_generation,
        system_values=prompts.golden_values,
    )
)

CHECKLIST = _register(
    Role(
        name="checklist",
        model_for=lambda s, gen: s.llm.roles.checklist_judge[gen],
        output_model=ChecklistJudgeOutput,
        params=JUDGING_PARAMS,
        calls_per_scenario=_per_generation,
    )
)

PROBE = _register(
    Role(
        name="probe",
        model_for=lambda s, _gen: s.llm.roles.probe_judge,
        output_model=ProbeJudgeOutput,
        params=JUDGING_PARAMS,
        calls_per_scenario=_per_generation,
    )
)


def role(name: str) -> Role:
    return ROLES.get(name)


def all_roles() -> list[Role]:
    return [ROLES.get(name) for name in ROLES.names()]


def call_role(
    client: LLMClient,
    settings: Any,
    role: Role,
    values: dict[str, str],
    *,
    generator: str | None = None,
    seed: int | None = None,
    variant: str | None = None,
) -> BaseModel:
    """역할 하나를 호출하고 검증된 출력을 돌려준다.

    values는 유저 메시지 자리에 들어갈 값이다. 어느 모델이 맡는지, 어떤 파라미터를 쓰는지,
    출력 스키마와 프롬프트 버전이 무엇인지는 전부 Role이 안다. variant로 기본 변형을 바꾸면
    기록되는 프롬프트 버전도 그 변형을 따른다.
    """
    return client.complete_structured(
        model_key=role.model_for(settings, generator),
        role_name=role.name,
        messages=prompts.build_messages(settings, role, values, variant=variant),
        output_model=role.output_model,
        params=role.params(settings),
        seed=settings.project.seed if seed is None else seed,
        prompt_version=role.prompt_version(settings, variant),
    )
