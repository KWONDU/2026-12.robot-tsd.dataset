"""스테이지 공용 헬퍼와 스테이지 등록 규약."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tsdgen.config import Settings
from tsdgen.models import Sample
from tsdgen.registry import STAGES
from tsdgen.runs import stage_file
from tsdgen.util import read_jsonl


@dataclass(frozen=True)
class Stage:
    """등록된 스테이지 하나. 스테이지가 무엇을 하는 종류인지는 스스로 선언한다.

    run 스테이지가 스테이지 이름을 보고 분기하지 않게 하기 위한 것이다. 예산 가드를
    어디에 걸지(calls_llm), 파이프라인 순서에 넣을 수 있는지(orchestrates)를 이름이 아니라
    선언으로 판단하므로, 스테이지를 추가·교체해도 run과 config 검증은 바뀌지 않는다.
    """

    name: str
    run: Callable[[Settings], dict]
    calls_llm: bool = False
    """LLM을 호출해 비용이 드는 스테이지. run은 이런 스테이지 중 첫 번째 직전에 예산 가드를 건다."""
    orchestrates: bool = False
    """다른 스테이지를 돌리는 스테이지. 파이프라인 순서(project.pipeline_order)에 넣을 수 없다."""

    def __call__(self, settings: Settings) -> dict:
        return self.run(settings)


def stage(name: str, *, calls_llm: bool = False, orchestrates: bool = False) -> Callable:
    """데코레이터. 함수를 Stage로 감싸 등록한다. 등록한 이름이 TSDGEN_STAGE 값이 된다."""

    def register(fn: Callable[[Settings], dict]) -> Callable[[Settings], dict]:
        STAGES.register(name)(
            Stage(name=name, run=fn, calls_llm=calls_llm, orchestrates=orchestrates)
        )
        return fn

    return register


def scenarios_path(settings: Settings) -> Path:
    return settings.path("scenarios") / f"{settings.source.adapter}.jsonl"


def stage_output(settings: Settings, stage_name: str) -> Path:
    return settings.run_dir() / stage_file(stage_name)


def load_samples(path: Path) -> list[Sample]:
    return [Sample.model_validate(rec) for rec in read_jsonl(path)]

