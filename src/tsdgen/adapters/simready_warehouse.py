"""SimReady 창고 팩 어댑터: 팩에 미리 꾸며진 작업 구역(작업대, 랙, 팔레트 묶음, 카트)을 장면으로 내놓는다.

이 원천에는 작업 지시가 없다. 내놓는 것은 바탕 장면뿐이고, 지시와 목표 상태는 작업 합성(compose 스테이지)이
그 장면에 얹는다. 장면 하나는 작업 구역 한 개에서 몇 개를 나란히 놓은 것이다: 구역 안의 배치는 팩에 든 그대로
두고, 어느 구역들을 한 장면에 둘지만 seed로 고른다(시뮬레이터를 돌리지 않는다).

팩의 장면 파일(USD)을 읽는 일은 scripts/simready_extract.py가 미리 해 두고, 여기서는 그 결과 파일과
종류·이름 표(resources/adapters/simready_warehouse.yaml)만 읽는다.
"""

import json
import random
from collections.abc import Iterator
from typing import Any

from tsdgen.adapters import simready_scene
from tsdgen.adapters.base import AdapterOptions, SourceAdapter
from tsdgen.log import get_logger
from tsdgen.models import ScenarioRecord
from tsdgen.registry import SOURCE_ADAPTERS
from tsdgen.resources import load_adapter_resource
from tsdgen.util import stable_hash

log = get_logger("tsdgen.simready")
NAME = "simready_warehouse"
LAYOUT = "layout"  # 작업이 아직 얹히지 않은 바탕 장면의 태스크·분류 이름
GRASPABLE = "graspable"


class SceneOptions(AdapterOptions):
    """장면을 몇 개, 어떤 크기로 만들지."""

    count: int  # 만들 장면 수
    cells_per_scene: list[int]  # 한 장면에 놓을 작업 구역 수의 후보
    min_objects: int  # 장면의 물체 수 하한 (물체가 없으면 작업을 만들 수 없다)
    max_objects: int  # 장면의 물체 수 상한 (세계 상태 글이 너무 길어지지 않게)
    cell_gap_m: float  # 구역을 나란히 놓을 때의 간격
    max_tries: int  # 조건에 맞는 조합을 찾는 시도 횟수 (장면 수의 배수)


class SimReadyWarehouseOptions(AdapterOptions):
    domain: str  # 장면이 속한 도메인 (sources.yaml)
    description_ko: str
    hf_repo: str  # 팩의 출처 (기록용)
    license: str
    cells_file: str  # scripts/simready_extract.py가 쓴 파일 (프로젝트 루트 기준)
    simulator_tag: str  # 씬 상태의 simulator 칸에 적는 출처 이름
    split: str  # 바탕 장면의 split 이름 (compose.base_split이 이 값을 고른다)
    robot: str
    scenes: SceneOptions


@SOURCE_ADAPTERS.register(NAME)
class SimReadyWarehouseAdapter(SourceAdapter):
    options_model = SimReadyWarehouseOptions

    def __init__(self, settings: Any, options: AdapterOptions | None = None):
        super().__init__(settings, options)
        self.vocab = load_adapter_resource(settings.path("resources"), NAME)

    def object_traits(self) -> dict[str, list[str]]:
        """물체 분류 -> 성질. 색이 붙은 분류(crate_yellow)도 같은 성질을 갖는다. 무거운 것은 집을 수 없다."""
        traits: dict[str, list[str]] = {}
        for spec in self.vocab["objects"].values():
            own = [*spec.get("traits", []), *([] if spec.get("heavy") else [GRASPABLE])]
            colored = (f'{spec["category"]}_{color}' for color in self.vocab["colors"])
            for name in (spec["category"], *colored):
                traits[name] = own
        return traits

    def _cells(self) -> dict[str, list[dict]]:
        path = self.settings.root / self.options.cells_file
        if not path.exists():
            hint = "run scripts/simready_extract.py first"
            raise FileNotFoundError(f"cells file not found: {path} ({hint})")
        return {name: cell["items"] for name, cell in
                json.loads(path.read_text(encoding="utf-8"))["cells"].items() if cell["items"]}

    def _combinations(self, cells: dict[str, list[dict]]) -> Iterator[tuple[str, ...]]:
        """한 장면에 둘 작업 구역의 조합을 seed로 고른다. 같은 조합은 한 번만 낸다."""
        options, rng = self.options.scenes, random.Random(self.settings.project.seed)
        names, seen = sorted(cells), set()
        for _ in range(options.count * options.max_tries):
            chosen = tuple(sorted(rng.sample(names, rng.choice(options.cells_per_scene))))
            if chosen not in seen:
                seen.add(chosen)
                yield chosen

    def _side_by_side(self, cells: dict[str, list[dict]], chosen: tuple[str, ...]) -> list[dict]:
        """구역들을 x축으로 나란히 옮겨 한 목록으로. 구역 안의 배치는 그대로다."""
        merged, cursor = [], 0.0
        for name in chosen:
            items = cells[name]
            left = min(i["min_m"][0] for i in items)
            shift = cursor - left
            merged.extend({**item, "min_m": [item["min_m"][0] + shift, *item["min_m"][1:]],
                           "max_m": [item["max_m"][0] + shift, *item["max_m"][1:]]} for item in items)
            cursor = max(i["max_m"][0] for i in items) + shift + self.options.scenes.cell_gap_m
        return merged

    def _record(self, index: int, chosen: tuple[str, ...], parts: dict,
                note: dict) -> ScenarioRecord:
        options = self.options
        key = stable_hash(list(chosen), length=10)  # 같은 구역 조합이면 설정이 바뀌어도 같은 id다
        scene = {
            "schema_version": self.settings.schema_versions["scene_state"],
            "scene_id": f"{NAME}/{key}", "domain": options.domain,
            "simulator": options.simulator_tag,
            "layout": {"layout_name": "+".join(chosen)}, **parts,
            "agent": {"embodiment": options.robot},
            "provenance": {"dataset_path": options.hf_repo, "episode_index": index,
                           "original_task_name": LAYOUT, "extraction_tier": "A",
                           "source_object_names": note["object_assets"],
                           "source_fixture_names": note["fixture_assets"]},
        }
        return ScenarioRecord.model_validate({
            "scenario_id": f"{NAME}-{options.split}-{key}",
            "source": {"simulator": options.simulator_tag, "task_name": LAYOUT,
                       "task_family": LAYOUT, "activity": None, "split": options.split,
                       "dataset_source": NAME,
                       "episode_index": index, "original_instruction_en": ""},
            "task_type_id": f"{options.domain}:{LAYOUT}",
            "scene_state": scene,
            "strata": {"family": LAYOUT},
            "meta": {"cells": list(chosen), "dropped_no_support": note["dropped_no_support"]},
        })

    def iter_scenarios(self) -> Iterator[ScenarioRecord]:
        cells, options = self._cells(), self.options.scenes
        made = 0
        for chosen in self._combinations(cells):
            parts, note = simready_scene.build(self._side_by_side(cells, chosen), self.vocab)
            if not options.min_objects <= len(parts["objects"]) <= options.max_objects:
                continue
            made += 1
            yield self._record(made, chosen, parts, note)
            if made >= options.count:
                break
        log.info(f"{made} scenes from {len(cells)} cells")
