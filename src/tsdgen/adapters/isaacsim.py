"""Isaac Sim synthetic 생성 어댑터 자리 (P3, 인터페이스만)."""

from collections.abc import Iterator

from tsdgen.adapters.base import SourceAdapter
from tsdgen.models import ScenarioRecord
from tsdgen.registry import SOURCE_ADAPTERS


@SOURCE_ADAPTERS.register("isaacsim_synthetic")
class IsaacSimSyntheticAdapter(SourceAdapter):
    def iter_scenarios(self) -> Iterator[ScenarioRecord]:
        raise NotImplementedError(
            "isaacsim_synthetic adapter is a P3 placeholder; use source.adapter=robocasa365"
        )
