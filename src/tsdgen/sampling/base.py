"""선별 전략 계약.

전략 하나는 자기 설정의 스키마(options_model)를 선언하고, 시나리오 목록에서 n건을 고른다.
설정 블록은 config의 sampling.<전략 이름>에 있고 로드 시점에 이 스키마로 검증된다.
전략을 추가해도 config.py와 sample 스테이지는 바뀌지 않는다 (어댑터의 options_model과 같은 규약).
"""

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from tsdgen.models import ScenarioRecord


class SamplingOptions(BaseModel):
    """전략 설정의 기반. 모르는 키는 오타이므로 거부한다."""

    model_config = ConfigDict(extra="forbid")


class SamplingStrategy(ABC):
    options_model: ClassVar[type[SamplingOptions]] = SamplingOptions

    @abstractmethod
    def __call__(
        self, records: list[ScenarioRecord], n: int, options: SamplingOptions, seed: int
    ) -> list[ScenarioRecord]:
        """records에서 n건을 고른다. 같은 seed면 같은 결과여야 한다."""
