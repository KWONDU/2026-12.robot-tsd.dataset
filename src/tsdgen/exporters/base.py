"""내보내기 공용 컨텍스트."""

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from tsdgen.config import Settings
from tsdgen.models import Sample
from tsdgen.resources import load_template


@dataclass
class ExportContext:
    settings: Settings
    out_dir: Path
    date_str: str
    samples: list[Sample]
    scenarios: list[dict]
    stats: dict = field(default_factory=dict)

    def by_status(self, *statuses: str) -> list[Sample]:
        """상태별 샘플. 여러 상태를 주면 주어진 상태 순서대로 이어 붙인다."""
        return [s for status in statuses for s in self.samples if s.meta.status == status]

    def template(self, name: str) -> str:
        """전달물 문서 템플릿 (규약은 resources.load_template)."""
        return load_template(self.settings.path("resources"), name)

    def scenario(self, scenario_id: str) -> dict | None:
        return self._scenarios_by_id.get(scenario_id)

    @cached_property
    def _scenarios_by_id(self) -> dict[str, dict]:
        return {sc.get("scenario_id"): sc for sc in self.scenarios}
