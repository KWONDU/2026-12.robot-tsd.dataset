"""데이터 원천 어댑터 계약.

파이프라인은 이 계약만 알고 개별 데이터셋을 알지 않는다. RoboCasa365든 Arena든
나중에 붙일 무엇이든, 여기 정의된 네 가지를 구현하면 나머지 스테이지는 바뀌지
않는다. 어댑터 이름은 registry 등록값이고 그 이름이 곧 config의 허용값이다.

  options_model  이 어댑터가 config에서 받는 설정의 스키마. 어댑터가 스스로 선언하므로
                 데이터셋을 추가해도 config.py를 고치지 않는다.
  prefetch()     원천 데이터를 캐시에 확보한다. 받을 것이 없으면 그대로 둔다.
  prepare()      시나리오를 만들기 전에 필요한 원천별 사전 처리. 예를 들어 RoboCasa는
                 원본에 없는 가구 초기 상태를 시뮬레이터 소스에서 역산해 둔다.
                 이런 단계는 데이터셋마다 다르므로 공용 스테이지가 아니라 여기 둔다.
  iter_scenarios() 원천을 공통 규격(ScenarioRecord)으로 내놓는다. 이것만 필수다.
  synthesize()   원천의 생성기로 새 장면을 만들어 같은 규격으로 내놓는다 (RoboCasa는 시뮬레이터 reset).
  goal_oracle()  원천이 성공 판정을 알면 그것을 목표 상태 단언(정답)으로 옮긴다. 모르면 None.

씬 상태 규격이 시뮬레이터 독립이라(스펙 3절 6번) 새 데이터셋이 할 일은 자기 원본을
그 규격으로 옮기는 것뿐이다. 파이프라인의 나머지 절반(재작성·생성·검수·내보내기)은
어떤 데이터셋에서 왔는지 알지 못한다.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from tsdgen.models import GoalAssertion, ScenarioRecord
from tsdgen.registry import SOURCE_ADAPTERS

# 캐시에 원천을 다 받았음을 표시하는 파일. 원천마다 받는 것이 달라도 이 규약은 공유한다.
CACHE_COMPLETE_MARKER = ".metadata_complete"


class AdapterOptions(BaseModel):
    """어댑터 설정의 기반. 모르는 키는 오타이므로 거부한다."""

    model_config = ConfigDict(extra="forbid")


class SourceAdapter(ABC):
    """데이터 원천 하나."""

    options_model: ClassVar[type[AdapterOptions]] = AdapterOptions

    def __init__(self, settings: Any, options: AdapterOptions | None = None):
        self.settings = settings
        self.options = options if options is not None else self.options_model()

    def prefetch(self) -> dict:
        """원천 데이터를 캐시에 확보한다. 받을 것이 없는 원천은 그대로 둔다."""
        return {"source": self.name(), "skipped": "이 원천은 선수신할 것이 없다"}

    def fetch_videos(self, records: list) -> dict:
        """선별된 시나리오들의 시연 영상을 캐시에 확보한다. 열람용이며 LLM 입력이 아니다."""
        raise NotImplementedError(f"{self.name()} 어댑터는 시연 영상 수신을 지원하지 않는다")

    def video_file(self, scenario: dict) -> Any | None:
        """시나리오의 시연 영상 파일 경로. 없으면(미수신·미지원) None."""
        return None

    def goal_oracle(self, scenario: dict) -> list[list[list[GoalAssertion]]] | None:
        """이 시나리오의 목표 상태 정답 대안들. 성공하는 경우가 여러 가지면(어느 그릇이든) 대안이 여럿이다.
        대안 하나는 항목 목록이고, 항목 하나는 후보 단언 목록이라 그중 하나와 같으면 맞다.
        원천이 성공 판정을 모르면 None이다 (그러면 정답 대조는 건너뛴다). 작업을 합성한 시나리오는 원천과
        상관없이 만들 때 정한 목표 상태가 정답이다."""
        # 작업 합성 모듈이 어댑터 계약을 쓰므로 위에서 임포트하면 순환한다
        from tsdgen.tasks.compose import composed_oracle

        return composed_oracle(scenario)

    def object_traits(self) -> dict[str, list[str]]:
        """물체 종류 -> 성질 이름들. 원천이 알려 주지 않으면 비어 있다 (작업 합성이 조합을 거르는 데 쓴다)."""
        return {}

    def prepare(self) -> dict:
        """시나리오 생성 전에 필요한 원천별 사전 처리. 없으면 그대로 둔다."""
        return {"source": self.name(), "skipped": "이 원천은 사전 처리가 없다"}

    def synthesize(self) -> Iterator[ScenarioRecord]:
        """원천의 생성기(시뮬레이터 등)로 새 장면을 만들어 공통 규격으로 내놓는다. 못 하는 원천은 그대로 둔다."""
        raise NotImplementedError(f"{self.name()} 어댑터는 장면 합성을 지원하지 않는다")

    @abstractmethod
    def iter_scenarios(self) -> Iterator[ScenarioRecord]:
        """원천을 공통 규격으로 내놓는다."""

    @classmethod
    def name(cls) -> str:
        for registered in SOURCE_ADAPTERS.names():
            if SOURCE_ADAPTERS.get(registered) is cls:
                return registered
        return cls.__name__


def make_adapter(settings: Any, name: str | None = None) -> SourceAdapter:
    """등록된 어댑터를 설정과 함께 만든다. name이 없으면 config가 고른 어댑터다."""
    adapter_name = name or settings.source.adapter
    return SOURCE_ADAPTERS.get(adapter_name)(settings, settings.adapter_options(adapter_name))
