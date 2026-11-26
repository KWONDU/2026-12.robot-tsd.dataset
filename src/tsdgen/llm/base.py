"""LLM 요청/응답 자료형, 백엔드 계약, 예외."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LLMRequest:
    model_key: str
    role: str  # 역할 이름 (roles.py의 등록 이름, 대시보드 분석이면 액션 이름)
    messages: list[dict] = field(default_factory=list)
    json_schema: dict | None = None
    temperature: float = 0.0
    seed: int | None = None
    max_output_tokens: int = 1024
    prompt_version: str = ""
    extra: dict = field(default_factory=dict)  # 역할이 요청하는 추가 파라미터


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    # 제공사가 알려 준 것 (없으면 비운다): 실제 청구액, 입력 중 캐시로 청구된 토큰 수, 실제로 처리한 제공사
    billed_usd: float | None = None
    cached_input_tokens: int = 0
    provider: str | None = None


class LLMError(Exception):
    """복구 불가 오류 (설정 오류, 4xx 등)."""


class TransientLLMError(LLMError):
    """재시도 대상 오류 (네트워크, 429, 5xx)."""


class BudgetExceededError(LLMError):
    """예산 가드 발동."""


class Backend(ABC):
    """LLM 백엔드 하나. 등록된 이름이 config의 llm.models.*.backend 값이 된다.

    필요한 능력은 백엔드가 스스로 선언한다. config 검증이 백엔드 이름을 보고 분기하지
    않게 하기 위한 것이며, 키가 필요 없는 백엔드를 추가해도 검증 코드는 바뀌지 않는다.
    """

    needs_api_key: bool = True

    @abstractmethod
    def complete(self, req: LLMRequest, model_cfg: Any, settings: Any) -> LLMResponse:
        """요청 하나를 보내고 응답을 돌려준다. 재시도·캐시는 상위 계층이 맡는다."""
