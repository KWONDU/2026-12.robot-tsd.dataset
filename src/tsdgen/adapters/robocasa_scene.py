"""RoboCasa365 ep_meta.json 한 건을 공통 씬 상태(SceneState)로 옮기는 변환.

원본 fixture 클래스·물체 카테고리를 우리 분류로 옮기는 표는 어댑터 자원(resources/adapters/robocasa365.yaml)에,
한국어 표시명은 공용 사전에 있다. 어느 쪽에도 없는 이름은 조용히 넘기지 않고 모아서 어댑터가 보고한다.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from tsdgen.adapters.robocasa_adjacency import counters_next_to
from tsdgen.adapters.robocasa_sim import SimSnapshot, read_fixture_states, read_region_states
from tsdgen.config import Settings
from tsdgen.models import (
    AgentInfo,
    FixtureEntry,
    LayoutInfo,
    ObjectEntry,
    PlacementInfo,
    Provenance,
    RegionEntry,
    RelationEntry,
    SceneState,
)

SIMULATOR = "robocasa365"
TASK_TYPE_PREFIX = "rc365"
_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _snake(name: str) -> str:
    return _CAMEL_RE.sub("_", name).lower()



@dataclass(frozen=True)
class FixtureRef:
    """생성된 fixture id와 그 카테고리. id 문자열을 되파싱하지 않기 위해 함께 들고 다닌다."""

    id: str
    category: str


DINING_PREFIX = "dining"  # location_hint_prefixes에서 식사용 조리대 설명을 가진 앞머리


class SceneBuilder:
    """ep_meta.json 한 건 → SceneState."""

    def __init__(self, settings: Settings, lexicon: dict, vocabulary: dict, options):
        self.settings = settings
        self.lexicon = lexicon
        self.vocabulary = vocabulary
        self.options = options
        self.unmapped_categories: set[str] = set()
        self.unmapped_fixture_classes: set[str] = set()
        # Tier B에서 정적 분석 값과 시뮬레이터 첫 프레임 값이 다른 경우 (fixture id, 키, 정적, sim)
        self.sim_conflicts: list[tuple[str, str, object, object]] = []
        self.layout_map: dict = {}  # 배치 번호 -> {dining_counters: [...]} (prepare 산출물, 어댑터가 채운다)

    def _fixture_meta(self, cls: str) -> dict:
        """fixture 클래스의 우리 분류. 어휘표에 없는 클래스는 이름에서 카테고리를 만들고
        표시명은 공용 사전에서 찾는다. 사전에도 없으면 영어가 남으므로 모아서 보고한다
        (예전에는 클래스명이 그대로 name_ko가 되어 'BlenderLid'가 전달물까지 나갔다)."""
        known = self.vocabulary["fixture_classes"].get(cls)
        if known:
            return known
        self.unmapped_fixture_classes.add(cls)
        category = _snake(cls)
        ko = (self.lexicon["fixture_names_ko"].get(category)
              or self.lexicon["category_names_ko"].get(category) or category)
        return {"category": category, "ko": ko, "containment": "on", "interactive": True}

    def _location_hint(self, source_name: str, cls: str, dining: tuple = ()) -> str | None:
        """가구 이름 뒤에 붙일 위치 설명. dining은 이 배치에서 아래가 트인 조리대 이름들(layout_map.yaml)로,
        원천이 이름과 상관없이 식사용 조리대로 치는 것이라 이름이 dining으로 시작하는 조리대와 같은 설명을 쓴다."""
        prefixes = (self.vocabulary.get("location_hint_prefixes") or {}).get(cls) or {}
        for prefix, desc in prefixes.items():
            if source_name.startswith(prefix):
                return desc
        if any(source_name == name or source_name.startswith(f"{name}_") for name in dining):
            return prefixes.get(DINING_PREFIX)
        for token, desc in self.vocabulary["location_hint_tokens"].items():
            if token in source_name:
                return desc
        return None

    def _sim_specs(self, meta: dict) -> dict[str, dict]:
        """이 가구 클래스의 시뮬레이터 상태 판정 명세: 기본 명세 위에 클래스별 지정을 덮는다."""
        defaults = self.vocabulary.get("sim_states") or {}
        own = meta.get("sim_states") or {}
        return {key: {**defaults.get(key, {}), **own.get(key, {})} for key in {*defaults, *own}}

    def _apply_sim(
        self, fx_id: str, meta: dict, joints: list, states: dict, state_source: str
    ) -> tuple[dict, str, list[str]]:
        """시뮬레이터 첫 프레임에서 읽힌 키는 그 값으로 바꾸고, 관절은 있는데 원본 기준으로 판단할 수
        없는 키(반쯤 열린 문)는 정적 분석 값이 있어도 비운다. 모든 키가 sim에서 왔을 때만 출처를 sim으로 쓴다."""
        readings = read_fixture_states(joints, self._sim_specs(meta))
        merged = dict(states)
        for key, value in readings.items():
            if value is None:
                merged.pop(key, None)
                continue
            if key in merged and merged[key] != value:
                self.sim_conflicts.append((fx_id, key, merged[key], value))
            merged[key] = value
        decided = sorted(k for k, v in readings.items() if v is not None)
        if merged and set(merged) <= set(decided):
            state_source = "sim"
        return merged, state_source, decided

    def _build_fixtures(
        self, ep_meta: dict, task_states: dict, sim: SimSnapshot | None = None
    ) -> tuple[list[FixtureEntry], dict[str, "FixtureRef"], list[str], dict[str, list[str]]]:
        cfg = self.options
        ref_by_fixture = {v: k for k, v in (ep_meta.get("fixture_refs") or {}).items()}
        entries: list[FixtureEntry] = []
        id_by_source: dict[str, FixtureRef] = {}
        assumed: list[str] = []
        sim_keys: dict[str, list[str]] = {}
        counters: dict[str, int] = {}
        fixture_names = sorted(ep_meta.get("fixtures", {}))
        joints = sim.fixture_joints(fixture_names) if sim else {}
        markers = self.vocabulary.get("sim_regions") or {}
        layout = self.layout_map.get(ep_meta.get("layout_id")) or {}
        dining = tuple(layout.get("dining_counters") or ())
        found_regions = sim.fixture_regions(fixture_names, markers) if sim and markers else {}
        for source_name, spec in sorted(ep_meta.get("fixtures", {}).items()):
            meta = self._fixture_meta(spec.get("cls", ""))
            if not meta["interactive"]:
                continue
            category = meta["category"]
            counters[category] = counters.get(category, 0) + 1
            fx_id = f"fx_{category}_{counters[category]}"
            id_by_source[source_name] = FixtureRef(id=fx_id, category=category)

            states: dict = {}
            state_source = "unknown"
            ref = ref_by_fixture.get(source_name)
            if ref and ref in task_states and task_states[ref]["states"]:
                states = dict(task_states[ref]["states"])
                state_source = "task_source_static"
            elif cfg.assume_defaults and meta.get("default_states"):
                states = dict(meta["default_states"])
                state_source = "default_assumed"
                assumed.append(fx_id)
            if joints.get(source_name):
                states, state_source, decided = self._apply_sim(
                    fx_id, meta, joints[source_name], states, state_source
                )
                if decided:
                    sim_keys[fx_id] = decided
            # 이 가구 종류에서 달리 부르는 상태 키 (서랍의 문 열림은 서랍 열림)
            renamed = meta.get("state_key_aliases") or {}
            states = {renamed.get(key, key): value for key, value in states.items()}
            if fx_id in sim_keys:
                sim_keys[fx_id] = sorted(renamed.get(key, key) for key in sim_keys[fx_id])
            entries.append(
                FixtureEntry(
                    id=fx_id,
                    category=category,
                    name_ko=meta["ko"],
                    location_desc=self._location_hint(source_name, spec.get("cls", ""), dining),
                    states=states,
                    state_source=state_source,
                    regions=self._regions(category, found_regions.get(source_name) or [],
                                          joints.get(source_name) or []),
                )
            )
        return entries, id_by_source, assumed, sim_keys

    def _regions(
        self, category: str, found: list[tuple[str, float]], joints: list
    ) -> list[RegionEntry]:
        """모델에서 찾은 칸을 이름 붙여 옮긴다. 사전(region_ko)에 있는 종류만, 같은 종류가 여럿이면 위에서부터 번호."""
        names = self.lexicon.get("region_ko", {})
        states = read_region_states(joints, self.vocabulary.get("sim_region_states") or {})
        groups: dict[str, list[tuple[str, float]]] = {}
        for key, height in found:
            base = re.sub(r"\d+$", "", key)
            info = names.get(f"{category}.{base}") or names.get(base)
            if info:
                groups.setdefault(info["name"], []).append((key, height))
        entries = []
        for name, members in groups.items():
            ordered = sorted(members, key=lambda m: (-m[1], m[0]))
            for i, (key, _height) in enumerate(ordered, start=1):
                label = f"{name} {i}" if len(ordered) > 1 else name
                entries.append(RegionEntry(key=key, name_ko=label, states=states.get(key) or {}))
        return entries

    def _build_objects(
        self, ep_meta: dict, id_by_source: dict[str, "FixtureRef"], sim: SimSnapshot | None = None
    ) -> tuple[list[ObjectEntry], list[RelationEntry], dict[str, str]]:
        names_ko = self.lexicon["category_names_ko"]
        # 사전에 없는 카테고리는 영어 식별자가 그대로 name_ko에 들어간다. 조용히 두면
        # 한국어 산출물에 영어가 섞이고, 접지 검사가 한국어 근거를 못 찾아 오탐을 낸다.
        # 그래서 모아 두었다가 어댑터가 한 번에 보고한다 (resources/ko_lexicon.yaml에 추가할 것).
        containment = {
            fx["category"]: fx["containment"] for fx in self.vocabulary["fixture_classes"].values()
        }
        objects: list[ObjectEntry] = []
        relations: list[RelationEntry] = []
        source_names: dict[str, str] = {}
        body_suffix = self.vocabulary.get("sim_object_body_suffix", "")
        cfgs = ep_meta.get("object_cfgs", [])
        id_by_name = {c.get("name"): f"obj_{i:03d}" for i, c in enumerate(cfgs, start=1)}
        category_by_name = {c.get("name"): (c.get("info") or {}).get("cat") for c in cfgs}
        containers = set(self.lexicon.get("container_categories", []))
        for i, cfg_entry in enumerate(cfgs, start=1):
            info = cfg_entry.get("info") or {}
            category = info.get("cat") or (cfg_entry.get("obj_groups") or ["unknown"])[0]
            obj_id = f"obj_{i:03d}"
            source_names[obj_id] = cfg_entry.get("name", obj_id)

            placement = None
            placement_cfg = cfg_entry.get("placement") or {}
            fixture = self._fixture_for(placement_cfg.get("fixture"), id_by_source)
            if fixture:
                relation = containment.get(fixture.category, "on")
                placement = PlacementInfo(fixture_id=fixture.id, relation=relation,
                                          **self._placement_detail(cfg_entry, id_by_source))
                relations.append(
                    RelationEntry(subj=obj_id, pred=relation, obj=fixture.id, source="metadata")
                )
            # 다른 물체(접시·그릇 등)를 기준으로 놓인 물체: 그 물체 위나 안
            holder = (placement_cfg.get("sample_args") or {}).get("reference")
            if holder in id_by_name and holder != cfg_entry.get("name"):
                pred = "in" if category_by_name.get(holder) in containers else "on"
                relations.append(
                    RelationEntry(subj=obj_id, pred=pred, obj=id_by_name[holder], source="metadata")
                )
            if category not in names_ko:
                self.unmapped_categories.add(category)
            objects.append(
                ObjectEntry(
                    id=obj_id,
                    category=category,
                    name_ko=names_ko.get(category, category),
                    placement=placement,
                    pose=sim.object_pose(source_names[obj_id] + body_suffix,
                                         self.options.sim_pose_decimals) if sim else None,
                    size_m=self._size(sim, source_names[obj_id]),
                )
            )
        return objects, relations, source_names

    def _fixture_for(self, source: str | int | None, id_by_source: dict[str, "FixtureRef"]):
        """배치가 가리키는 가구. 원천이 가구 이름 대신 종류 번호(RoboCasa FixtureType)를 적은 경우에는
        그 종류의 가구가 씬에 하나일 때만 정한다 (여럿이면 어느 것인지 원천 기록에 없다)."""
        if isinstance(source, str):
            return id_by_source.get(source)
        category = (self.vocabulary.get("fixture_type_ids") or {}).get(source)
        matches = [ref for ref in id_by_source.values() if ref.category == category]
        return matches[0] if len(matches) == 1 else None

    def _adjacency(
        self, id_by_source: dict[str, "FixtureRef"], sim: SimSnapshot | None
    ) -> list[RelationEntry]:
        """가구 옆의 조리대를 '조리대 near 가구' 관계로 남긴다 (규칙과 값은 어댑터 자원의 sim_adjacency).
        시뮬레이터 첫 프레임이 있을 때만 알 수 있다."""
        spec = self.vocabulary.get("sim_adjacency")
        if not sim or not spec:
            return []
        boxes = sim.fixture_boxes(sorted(id_by_source), spec["box_geom_suffixes"])
        counters = {name: box for name, box in boxes.items()
                    if id_by_source[name].category == spec["counter"]}
        found = []
        for name, box in sorted(boxes.items()):
            if id_by_source[name].category in spec["references"]:
                found += [RelationEntry(subj=id_by_source[c].id, pred="near",
                                        obj=id_by_source[name].id, source="sim")
                          for c in counters_next_to(box, counters, spec["tie_m"])]
        return found

    def _size(self, sim: SimSnapshot | None, source_name: str) -> list[float] | None:
        suffix = self.vocabulary.get("sim_object_size_geom_suffix", "")
        if not (sim and suffix):
            return None
        return sim.object_size(source_name, suffix, self.options.sim_pose_decimals)

    def _placement_detail(self, cfg_entry: dict, id_by_source: dict[str, "FixtureRef"]) -> dict:
        """원천 배치 설정의 자세한 자리: 가구 안의 칸(reset_region 이름)과 배치 기준이 된 옆 가구·방향.

        칸 이름 중 화면에 뜻이 없는 것(조리대 표면 geom_0 등)은 사전에 없어서 세계 상태 글에 나오지 않는다.
        """
        placement_cfg = cfg_entry.get("placement") or {}
        region_kwargs = placement_cfg.get("sample_region_kwargs") or {}
        near = id_by_source.get(str(region_kwargs.get("ref")))
        side = region_kwargs.get("loc")
        return {"region": (cfg_entry.get("reset_region") or {}).get("name"),
                "near_fixture_id": near.id if near else None,
                "near_side": side if near and side in ("left", "right") else None}

    def build(
        self,
        ep_meta: dict,
        episode_index: int,
        task: str,
        split: str,
        task_dir: Path,
        embodiment: str,
        task_states: dict,
        sim: SimSnapshot | None = None,
        dataset_source: str | None = None,
    ) -> SceneState:
        """sim이 있으면 Tier B: 물체 pose와 가구 관절 상태를 시연 첫 프레임에서 채운다.
        dataset_source는 장면이 어디서 왔는지(받은 시연, 합성)이고 주지 않으면 설정의 값이다."""
        cfg = self.options
        dataset_source = dataset_source or cfg.dataset_source
        fixtures, id_by_source, assumed, sim_keys = self._build_fixtures(ep_meta, task_states, sim)
        objects, relations, source_names = self._build_objects(ep_meta, id_by_source, sim)
        relations += self._adjacency(id_by_source, sim)
        scene_id = f"{TASK_TYPE_PREFIX}/{task}/{split}/{dataset_source}/ep_{episode_index:04d}"
        return SceneState(
            schema_version=self.settings.schema_versions["scene_state"],
            scene_id=scene_id,
            domain=self.options.domain,
            simulator=SIMULATOR,
            layout=LayoutInfo(layout_id=ep_meta.get("layout_id"), style_id=ep_meta.get("style_id")),
            fixtures=fixtures,
            objects=objects,
            relations=relations,
            agent=AgentInfo(embodiment=embodiment),
            provenance=Provenance(
                dataset_path=str(task_dir.relative_to(self.settings.root)),
                episode_index=episode_index,
                original_task_name=task,
                extraction_tier="B" if sim else "A",
                assumed_defaults=assumed,
                sim_state_keys=sim_keys,
                source_object_names=source_names,
                source_fixture_names={ref.id: name for name, ref in id_by_source.items()},
                source_fixture_refs=dict(ep_meta.get("fixture_refs") or {}),
            ),
        )
