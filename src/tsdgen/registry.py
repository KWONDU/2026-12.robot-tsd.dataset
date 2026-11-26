"""이름 → 구현 레지스트리.

config가 고르는 모든 축(stage, source adapter, LLM backend, sampling/selection
strategy, export format, LLM role)은 여기 등록된 이름이 곧 허용값이다.

각 Registry는 자기 값이 config의 어디에서 오는지(axes)를 직접 들고 있고, 생성
시점에 REGISTRIES에 자기를 등록한다. 그래서 config 검증은 축 목록을 따로
나열하지 않고 REGISTRIES를 순회하기만 하면 되며, 축이 늘어도 검증 코드는
바뀌지 않는다. 허용값 목록이 코드 두 군데에 생기지 않게 하는 장치다.

axes가 비어 있는 레지스트리는 config가 직접 고르지 않는 내부 dispatch용이다
(예: 역할별 mock 응답). 이름으로 갈래를 나누는 자리를 if 분기 대신 등록으로
표현하기 위한 것이며, 검증 순회는 이런 레지스트리를 건너뛴다.
"""

from collections.abc import Callable, Iterable, Sequence
from typing import Any

# 모든 축. config 검증이 이 목록을 순회한다.
REGISTRIES: list["Registry"] = []


def _as_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(v) for v in value.values()]
    if isinstance(value, Iterable):
        return [str(v) for v in value]
    return [str(value)]


class Registry:
    """전략 축 하나. 그 축의 config 위치와 허용값의 단일 출처다."""

    def __init__(
        self,
        what: str,
        *,
        axes: Sequence[Callable[[Any], Any]] = (),
        select: Callable[[Any], str] | None = None,
    ):
        self.what = what  # 오류 메시지에 쓰는 사람이 읽을 이름
        self.select = select  # 단일 선택 축. of()가 이걸 쓴다
        self.axes = tuple(axes) or ((select,) if select else ())
        self._items: dict[str, Any] = {}
        REGISTRIES.append(self)

    def register(self, name: str) -> Callable:
        """데코레이터. 등록한 이름이 그대로 config에 쓰는 값이 된다."""

        def keep(obj):
            if name in self._items:
                raise ValueError(f"{self.what} '{name}' is registered twice")
            self._items[name] = obj
            return obj

        return keep

    def get(self, name: str) -> Any:
        if name not in self._items:
            raise ValueError(
                f"unknown {self.what} '{name}'; allowed: {self.names()}"
            )
        return self._items[name]

    def names(self) -> list[str]:
        return sorted(self._items)

    def validate(self, names: Iterable[str]) -> None:
        for name in names:
            self.get(name)

    def of(self, settings: Any) -> Any:
        """config가 고른 전략. 단일 선택 축에서만 쓸 수 있다."""
        if self.select is None:
            raise TypeError(f"{self.what} is not an axis that config picks by a single value")
        return self.get(self.select(settings))

    def configured_names(self, settings: Any) -> list[str]:
        """config가 이 레지스트리에 대해 지정한 모든 이름 (검증용)."""
        return [name for axis in self.axes for name in _as_names(axis(settings))]


def validate_all(settings: Any) -> None:
    """config에 적힌 모든 전략 이름이 등록되어 있는지 확인한다."""
    for reg in REGISTRIES:
        reg.validate(reg.configured_names(settings))


def _model_backends(settings: Any) -> list[str]:
    return [m.backend for m in settings.llm.models.values()]


def _source_names(settings: Any) -> list[str]:
    return [settings.source.adapter, *settings.source.prefetch_sources]


def _stage_names(settings: Any) -> list[str]:
    return list(settings.project.pipeline_order)


def _export_formats(settings: Any) -> list[str]:
    return list(settings.export.formats)


STAGES = Registry("stage", axes=(_stage_names,))
SOURCE_ADAPTERS = Registry(
    "source adapter", axes=(_source_names,), select=lambda s: s.source.adapter
)
LLM_BACKENDS = Registry("llm backend", axes=(_model_backends,))
SAMPLING_STRATEGIES = Registry("sampling strategy", select=lambda s: s.sampling.strategy)
SELECTION_STRATEGIES = Registry("selection strategy", select=lambda s: s.llm.selection)
EXPORT_FORMATS = Registry("export format", axes=(_export_formats,))
VARIATION_BACKENDS = Registry("variation backend")

ROLES = Registry("llm role")
"""역할 이름 → Role. 프롬프트·출력 스키마·파라미터·호출 횟수의 단일 출처 (roles.py)."""

MOCK_RESPONSES = Registry("mock role response")
"""역할 이름 → (프롬프트 본문, settings) -> 응답 dict. 모든 역할에 하나씩 있어야 한다."""


def load_all() -> None:
    """등록 모듈들을 임포트해 레지스트리를 채운다 (config 검증 전에 호출)."""
    from tsdgen import adapters, exporters, roles, sampling, stages, world_state  # noqa: F401
    from tsdgen.llm import backends  # noqa: F401
    from tsdgen.tasks import (  # noqa: F401
        balance,
        types,
        types_choice,
        types_factory,
        types_factory_ops,
        types_machine,
        types_stock,
        types_stock_combo,
    )
    from tsdgen.verify import checks  # noqa: F401
