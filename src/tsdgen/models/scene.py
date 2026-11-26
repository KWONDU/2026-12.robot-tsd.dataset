"""씬 상태(SceneState)와 시나리오 기록. 모델 입력용 정제(to_environment_context)도 여기 있다."""

from typing import Any, Literal

from pydantic import Field

from tsdgen.models.base import RelationPred, StateSource, StrictModel
from tsdgen.util import stable_hash


class LayoutInfo(StrictModel):
    layout_id: int | None = None
    layout_name: str | None = None
    style_id: int | None = None
    style_name: str | None = None


REGION_SEP = "/"  # 가구의 칸을 가리키는 id: <가구 id>/<칸 key> (예: fx_fridge_1/freezer_shelf2)


def region_id(fixture_id: str, key: str) -> str:
    return f"{fixture_id}{REGION_SEP}{key}"


class RegionEntry(StrictModel):
    """가구 안의 칸 하나 (씬 상태 0.5): 냉장실 선반, 화구, 랙 등. 같은 종류가 여럿이면 위에서부터 번호를 붙인다."""

    key: str  # 원천의 칸 이름 그대로 (fridge_shelf3, front_left)
    name_ko: str  # 가구 안에서의 이름 (냉장실 선반 2, 앞 왼쪽 화구)
    states: dict[str, Any] = Field(default_factory=dict)  # 칸마다 따로 있는 상태 (화구의 켜짐)


class FixtureEntry(StrictModel):
    id: str
    category: str
    name_ko: str
    location_desc: str | None = None
    states: dict[str, Any] = Field(default_factory=dict)
    state_source: StateSource = "unknown"
    regions: list[RegionEntry] = Field(default_factory=list)


class PlacementInfo(StrictModel):
    fixture_id: str
    relation: RelationPred
    # 원천이 알려 준 자세한 자리: 가구 안의 칸(원천 이름, 예 freezer_shelf2), 배치 기준 옆 가구와 그 방향
    region: str | None = None
    near_fixture_id: str | None = None
    near_side: Literal["left", "right"] | None = None


class ObjectEntry(StrictModel):
    id: str
    category: str
    name_ko: str
    states: dict[str, Any] = Field(default_factory=dict)
    placement: PlacementInfo | None = None
    pose: dict[str, list[float]] | None = None
    size_m: list[float] | None = None  # 가로, 세로, 높이 (m). Tier B에서 모델의 물체 상자로 채운다
    size_class: str | None = None
    role: str | None = None  # 상호운용용. 모델 입력으로 내보낼 때는 항상 제거
    # 어떻게 아는 물체인가 (씬 상태 0.6, 지도가 있는 장면): perceived는 로봇이 지금 보는 것, system은 공장 시스템이
    # 알려 준 것(다른 곳에 있어 보이지 않음). 비어 있으면 보는 것이다
    known_by: Literal["perceived", "system"] | None = None


class MapZone(StrictModel):
    """지도의 구역 하나 (씬 상태 0.6): 가공·조립·보관처럼 곳을 묶는 구역. restricted면 로봇이 들어가지 않는다."""

    id: str
    name_ko: str
    restricted: bool = False
    note: str | None = None  # 출입을 막는 까닭 (지게차 작업 중 등)


class MapAisle(StrictModel):
    """두 곳(가구 id)을 잇는 통로 하나. one_way면 a에서 b로만 다닌다."""

    a: str
    b: str
    name_ko: str
    length_m: float
    closed: bool = False
    one_way: bool = False
    note: str | None = None  # 막힌 까닭 (바닥 공사 등)


class FacilityMap(StrictModel):
    """로봇이 미리 가진 지도 (씬 상태 0.6): 지금 보이지 않아도 아는 정적 정보와 시설 알림.
    곳은 가구(FixtureEntry)이고, 여기서는 그 곳이 어느 구역인지와 곳 사이의 통로만 적는다."""

    zones: list[MapZone]
    stations: dict[str, str]  # 가구 id -> 구역 id
    aisles: list[MapAisle]
    notices: list[str] = Field(default_factory=list)


class RelationEntry(StrictModel):
    subj: str
    pred: RelationPred
    obj: str
    source: str = "metadata"


class AgentInfo(StrictModel):
    embodiment: str
    base_near_fixture_id: str | None = None
    holding: list[str] = Field(default_factory=list)


class Provenance(StrictModel):
    dataset_path: str
    episode_index: int
    original_task_name: str
    extraction_tier: Literal["A", "B"]  # A: 메타데이터만, B: 시연 첫 프레임 시뮬레이터 상태까지
    assumed_defaults: list[str] = Field(default_factory=list)
    # Tier B에서 시뮬레이터 첫 프레임으로 정한 가구 상태 키 (fixture id -> 키 목록)
    sim_state_keys: dict[str, list[str]] = Field(default_factory=dict)
    source_object_names: dict[str, str] = Field(default_factory=dict)
    source_fixture_names: dict[str, str] = Field(default_factory=dict)
    source_fixture_refs: dict[str, str] = Field(default_factory=dict)


class SceneState(StrictModel):
    schema_version: str
    scene_id: str
    domain: str
    simulator: str
    layout: LayoutInfo
    fixtures: list[FixtureEntry]
    objects: list[ObjectEntry]
    relations: list[RelationEntry]
    agent: AgentInfo
    constraints: list[str] = Field(default_factory=list)
    provenance: Provenance
    map: FacilityMap | None = None  # 지도가 있는 장면만 (제조: 작업장 사이를 오가는 운반)


class SourceInfo(StrictModel):
    simulator: str
    task_name: str
    task_family: str
    activity: str | None = None
    split: str
    dataset_source: str
    episode_index: int
    original_instruction_en: str


class ScenarioRecord(StrictModel):
    scenario_id: str
    source: SourceInfo
    task_type_id: str
    scene_state: SceneState
    # 선별 전략이 쓸 층화 이름표. 어떤 축으로 나눌지는 원천이 알고 전략은 이름만 본다.
    # 덕분에 층화 전략에 데이터셋 어휘(atomic/composite 등)가 박히지 않는다.
    strata: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


def to_environment_context(scene: SceneState) -> dict[str, Any]:
    """SceneState를 모델 입력용 환경 컨텍스트로 정제한다.

    제거 대상: provenance 전체(원래 물체 이름·태스크명 포함), 물체 role,
    태스크명이 드러나는 scene_id. id는 중립 id만 노출한다.
    """
    data = scene.model_dump(exclude={"provenance"}, exclude_none=False)
    data["scene_id"] = "scene_" + stable_hash(scene.scene_id, length=12)
    if data.get("map") is None:  # 지도가 없는 장면은 0.5와 같은 모양으로 둔다
        data.pop("map", None)
    for obj in data["objects"]:
        obj.pop("role", None)
        if obj.get("known_by") is None:
            obj.pop("known_by", None)
    return data
