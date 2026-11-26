"""공장 바닥 어댑터 (제조): 작업장 사이를 오가는 운반 장면을 규칙으로 만든다 (시뮬레이터 없음, LLM 없음).

장면 하나 = 공개 시설 배치 벤치마크 인스턴스 하나(작업장 수, 물동량) + RoboCup Logistics League 규칙의 배치 + 늘 있는
지원 곳(자동 창고, 공구 보관소, 충전소, 폐기물 수거장, 로봇 대기 구역) + 로봇이 미리 가진 지도 + 물건.
지시와 정답은 작업 합성(compose)이 얹는다. 표는 resources/adapters/factory_floor.yaml.
"""

import json
import random
from collections.abc import Iterator
from contextlib import closing
from typing import Any

from tsdgen.adapters import factory_map
from tsdgen.adapters.base import AdapterOptions, SourceAdapter
from tsdgen.adapters.factory_layout import Layout, Station, connect, grid_size, place
from tsdgen.adapters.factory_scene import FactoryScene
from tsdgen.adapters.simready_workshop_build import item_specs
from tsdgen.cpu_pool import ordered_map
from tsdgen.log import get_logger
from tsdgen.models import ScenarioRecord
from tsdgen.registry import SOURCE_ADAPTERS
from tsdgen.resources import load_adapter_resource
from tsdgen.util import stable_hash

log = get_logger("tsdgen.factory")
NAME = "factory_floor"
LAYOUT = "layout"
ITEMS_VOCAB = "simready_workshop"  # 물건 이름표는 작업장 표를 함께 쓴다


class FactorySceneOptions(AdapterOptions):
    count: int
    # 장면을 나눠 만들 작업자 프로세스 수. 장면마다 seed가 따로라 몇으로 하든 결과는 같다 (1이면 한 프로세스)
    workers: int = 1


class FactoryFloorOptions(AdapterOptions):
    domain: str
    description_ko: str
    flp_file: str  # scripts/flp_extract.py가 쓴 벤치마크 인스턴스 (프로젝트 루트 기준)
    catalog_file: str  # scripts/simready_catalog.py가 쓴 에셋 목록
    references: list[str]  # 근거 (규칙집, 벤치마크, 에셋)
    license: str
    simulator_tag: str
    split: str
    robot: str
    scenes: FactorySceneOptions


def roles_from_flows(flows: list[list[float]], shares: dict[str, float]) -> list[str]:
    """물동량으로 부서의 공정 순서를 정해 역할을 준다. 순서는 (나가는 흐름 - 들어오는 흐름)이 큰 쪽이 앞이다:
    앞의 몫은 공급, 뒤의 몫은 출하, 출하 바로 앞의 몫은 검사, 그 사이 앞 절반은 가공, 뒤 절반은 조립.
    대칭으로 적힌 인스턴스(Armour & Buffa 등)는 방향이 없으므로 부서 번호 순서(i < j)를 흐름 방향으로 본다."""
    n = len(flows)
    if all(flows[i][j] == flows[j][i] for i in range(n) for j in range(n)):
        flows = [[flows[i][j] if i < j else 0.0 for j in range(n)] for i in range(n)]
    balance = [sum(flows[i]) - sum(row[i] for row in flows) for i in range(n)]
    order = sorted(range(n), key=lambda i: (-balance[i], i))
    supply = max(1, round(shares["supply"] * n))
    shipping = max(1, round(shares["shipping"] * n))
    inspection = max(1, round(shares["inspection"] * n))
    middle = n - supply - shipping - inspection
    sequence = (
        ["supply"] * supply
        + ["process"] * ((middle + 1) // 2)
        + ["assembly"] * (middle // 2)
        + ["inspection"] * inspection
        + ["shipping"] * shipping
    )
    roles = [""] * n
    for rank, dept in enumerate(order):
        roles[dept] = sequence[rank]
    return roles


@SOURCE_ADAPTERS.register(NAME)
class FactoryFloorAdapter(SourceAdapter):
    options_model = FactoryFloorOptions

    def __init__(self, settings: Any, options: AdapterOptions | None = None):
        super().__init__(settings, options)
        self.vocab = load_adapter_resource(settings.path("resources"), NAME)
        self.items_vocab = load_adapter_resource(settings.path("resources"), ITEMS_VOCAB)

    def _read(self, relative: str) -> Any:
        path = self.settings.root / relative
        if not path.exists():
            raise FileNotFoundError(
                f"missing {path} (see scripts/flp_extract.py, scripts/simready_catalog.py)"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def _items(self):
        return item_specs(self._read(self.options.catalog_file)["assets"], self.items_vocab)

    def object_traits(self) -> dict[str, list[str]]:
        return {spec.category: spec.traits for spec in self._items()}

    def _layout(self, instance: dict, rng: random.Random) -> Layout | None:
        flows = instance["flows"]
        n = len(flows)
        roles = roles_from_flows(flows, self.vocab["role_shares"]) + list(self.vocab["extra_roles"])
        grid = self.vocab["grid"]
        lx, ly = instance["facility"]
        width, height = grid_size(
            len(roles) + 1, max(lx, ly) / min(lx, ly), grid["zones_per_station"]
        )
        entry = [(width - 1 - k, 0) for k in range(grid["entry_cells"] - 1)] + [(width - 1, 1)]
        pair = {(a, b): flows[a][b] + flows[b][a] for a in range(n) for b in range(a + 1, n)}
        weights = {k: v for k, v in pair.items() if v > 0}
        for _ in range(grid["max_tries"]):
            stations = [Station(f"s{i}", role) for i, role in enumerate(roles)]
            bay = Station("bay", "bay", entry[1], [entry[0], entry[2]])
            layout = Layout(width, height, [bay, *stations], entry)
            shifted = {(a + 1, b + 1): v for (a, b), v in weights.items()}  # 대기 구역이 0번이다
            if place(layout, shifted, rng):
                connect(layout)
                return layout
        return None

    def _record(
        self, index: int, instance: dict, scene: FactoryScene, world_map: dict, here: str
    ) -> ScenarioRecord:
        options = self.options
        key = stable_hash([index, instance["name"]], length=10)
        state = {
            "schema_version": self.settings.schema_versions["scene_state"],
            "scene_id": f"{NAME}/{key}",
            "domain": options.domain,
            "simulator": options.simulator_tag,
            "layout": {"layout_name": instance["name"]},
            **scene.record(),
            "agent": {"embodiment": options.robot, "base_near_fixture_id": here},
            "map": world_map,
            "provenance": {
                "dataset_path": "; ".join(options.references),
                "episode_index": index,
                "original_task_name": LAYOUT,
                "extraction_tier": "A",
                "source_object_names": scene.assets,
                "source_fixture_names": {},
            },
        }
        return ScenarioRecord.model_validate(
            {
                "scenario_id": f"{NAME}-{options.split}-{key}",
                "source": {
                    "simulator": options.simulator_tag,
                    "task_name": LAYOUT,
                    "task_family": LAYOUT,
                    "activity": None,
                    "split": options.split,
                    "dataset_source": NAME,
                    "episode_index": index,
                    "original_instruction_en": "",
                },
                "task_type_id": f"{options.domain}:{LAYOUT}",
                "scene_state": state,
                "strata": {"family": LAYOUT},
                "meta": {"flp_instance": instance["name"]},
            }
        )

    def _scene(self, index: int, instances: list[dict], items: list) -> ScenarioRecord | None:
        """index번 장면 하나 (배치를 못 찾으면 None). 장면마다 seed가 따로라 순서와 상관없이 같다."""
        rng = random.Random(f"{self.settings.project.seed}-{NAME}-{index}")
        instance = instances[index % len(instances)]
        layout = self._layout(instance, rng)
        if layout is None:
            return None
        scene = FactoryScene(layout, self.vocab, items, rng)
        scene.stations()
        names = {f["id"]: f["name_ko"] for f in scene.fixtures}
        here = rng.choice([scene.ids[s.key] for s in layout.stations])
        scene.contents(here)
        scene.downtime()
        world_map = factory_map.build(layout, scene.ids, names, self.vocab, rng)
        world_map["notices"] += scene.notices
        return self._record(index, instance, scene, world_map, here)

    def iter_scenarios(self) -> Iterator[ScenarioRecord]:
        instances = self._read(self.options.flp_file)["instances"]
        scenes = self.options.scenes
        made = 0
        indices = range(scenes.count * 2)
        items = self._items()
        made_in_order = ordered_map(
            lambda index: self._scene(index, instances, items), indices, scenes.workers
        )
        with closing(made_in_order) as records:
            for record in records:
                if record is None:
                    continue
                made += 1
                yield record
                if made >= scenes.count:
                    break
        log.info(f"{made} factory floor scenes from {len(instances)} benchmark layouts")
