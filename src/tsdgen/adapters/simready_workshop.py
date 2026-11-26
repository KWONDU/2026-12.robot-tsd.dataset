"""SimReady 작업장 어댑터: 에셋 목록과 작업대 틀로 제조 작업장 장면을 만든다 (규칙 생성, 시뮬레이터 없음).

이 원천에는 작업 지시가 없다. 내놓는 것은 바탕 장면뿐이고, 지시와 목표 상태는 작업 합성(compose 스테이지)이
얹는다. 장면 하나는 작업대 틀 2개나 3개(조립 작업대, 공구 보관, 기계 투입, 키팅, 폐기, 부품 랙, 전동 공구)다.
에셋 목록은 scripts/simready_catalog.py가 미리 만들고, 틀과 이름표는 resources/adapters/simready_workshop.yaml에 있다.
"""

import json
import random
from collections.abc import Iterator
from typing import Any

from tsdgen.adapters.base import AdapterOptions, SourceAdapter
from tsdgen.adapters.simready_workshop_build import (
    SceneMaker,
    fixture_specs,
    item_specs,
)
from tsdgen.log import get_logger
from tsdgen.models import ScenarioRecord
from tsdgen.registry import SOURCE_ADAPTERS
from tsdgen.resources import load_adapter_resource
from tsdgen.util import stable_hash

log = get_logger("tsdgen.workshop")
NAME = "simready_workshop"
LAYOUT = "layout"


class WorkshopSceneOptions(AdapterOptions):
    count: int  # 만들 장면 수
    stations_per_scene: list[int]  # 한 장면에 둘 작업대 틀 수의 후보
    min_objects: int
    max_objects: int


class SimReadyWorkshopOptions(AdapterOptions):
    domain: str
    description_ko: str
    hf_repo: str
    license: str
    catalog_file: str  # scripts/simready_catalog.py가 쓴 에셋 목록 (프로젝트 루트 기준)
    simulator_tag: str
    split: str
    robot: str
    scenes: WorkshopSceneOptions


@SOURCE_ADAPTERS.register(NAME)
class SimReadyWorkshopAdapter(SourceAdapter):
    options_model = SimReadyWorkshopOptions

    def __init__(self, settings: Any, options: AdapterOptions | None = None):
        super().__init__(settings, options)
        self.vocab = load_adapter_resource(settings.path("resources"), NAME)

    def _catalog(self) -> list[dict]:
        path = self.settings.root / self.options.catalog_file
        if not path.exists():
            raise FileNotFoundError(f"catalog not found: {path} (run scripts/simready_catalog.py first)")
        return json.loads(path.read_text(encoding="utf-8"))["assets"]

    def object_traits(self) -> dict[str, list[str]]:
        return {spec.category: spec.traits for spec in item_specs(self._catalog(), self.vocab)}

    def _station(self, maker: SceneMaker, name: str) -> None:
        station = self.vocab["stations"][name]
        tray = station.get("tray_fill") or []
        for _ in range(1 + station.get("extra_fixtures", 0)):
            placed = maker.place_fixture(maker.rng.choice(station["fixture"]))
            if placed:
                maker.fill(*placed, station.get("fill") or {}, tray)
        if station.get("beside"):
            placed = maker.place_fixture(maker.rng.choice(station["beside"]))
            if placed:
                maker.fill(*placed, station.get("beside_fill") or {}, tray)

    def _record(self, chosen: tuple[str, ...], maker: SceneMaker, index: int) -> ScenarioRecord:
        options, key = self.options, stable_hash([index, list(chosen)], length=10)
        scene = {
            "schema_version": self.settings.schema_versions["scene_state"],
            "scene_id": f"{NAME}/{key}", "domain": options.domain, "simulator": options.simulator_tag,
            "layout": {"layout_name": "+".join(chosen)},
            "fixtures": maker.fixtures, "objects": maker.objects, "relations": maker.relations,
            "agent": {"embodiment": options.robot},
            "provenance": {"dataset_path": options.hf_repo, "episode_index": index,
                           "original_task_name": LAYOUT, "extraction_tier": "A",
                           "source_object_names": {k: v for k, v in maker.assets.items()
                                                   if k.startswith("obj_")},
                           "source_fixture_names": {k: v for k, v in maker.assets.items()
                                                    if k.startswith("fx_")}},
        }
        return ScenarioRecord.model_validate({
            "scenario_id": f"{NAME}-{options.split}-{key}",
            "source": {"simulator": options.simulator_tag, "task_name": LAYOUT, "task_family": LAYOUT,
                       "activity": None, "split": options.split, "dataset_source": NAME,
                       "episode_index": index, "original_instruction_en": ""},
            "task_type_id": f"{options.domain}:{LAYOUT}",
            "scene_state": scene,
            "strata": {"family": LAYOUT},
            "meta": {"stations": list(chosen)},
        })

    def iter_scenarios(self) -> Iterator[ScenarioRecord]:
        rows = self._catalog()
        items, fixtures = item_specs(rows, self.vocab), fixture_specs(rows, self.vocab)
        options = self.options.scenes
        names = sorted(self.vocab["stations"])
        made = 0
        for index in range(options.count * 3):
            rng = random.Random(f"{self.settings.project.seed}-{index}")
            chosen = tuple(sorted(rng.sample(names, rng.choice(options.stations_per_scene))))
            maker = SceneMaker(items, fixtures, self.vocab, rng)
            for name in chosen:
                self._station(maker, name)
            if not options.min_objects <= len(maker.objects) <= options.max_objects:
                continue
            made += 1
            yield self._record(chosen, maker, index)
            if made >= options.count:
                break
        log.info(f"{made} workshop scenes ({len(items)} item assets, {len(fixtures)} fixture kinds)")
