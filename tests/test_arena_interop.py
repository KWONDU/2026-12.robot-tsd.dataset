"""Arena 환경 스펙 상호운용.

Arena 스펙은 우리가 만든 포맷이 아니라 외부가 정한 규격이므로, 실제 배포된 파일로
검증한다. 캐시가 없으면(prefetch 미실행) 실데이터 테스트는 건너뛴다.

두 표현의 표현력이 다르다는 사실 자체가 계약이다. 무엇이 오가고 무엇이 못 오는지를
테스트로 못박아, 나중에 변환을 고쳤을 때 조용히 손실이 늘어나는 일을 막는다.
"""

import pytest
import yaml

from tsdgen.adapters import arena_convert, arena_spec
from tsdgen.adapters.arena import ArenaSpecAdapter
from tsdgen.models import (
    AgentInfo,
    FixtureEntry,
    LayoutInfo,
    ObjectEntry,
    PlacementInfo,
    Provenance,
    RelationEntry,
    SceneState,
)
from tsdgen.resources import load_arena_mapping

REAL_SPEC = """
env_name: droid_pick_mustard_to_bowl
embodiment: {id: droid, registry_name: droid_abs_joint_pos, params: {stand_height_m: 0.8}}
background: {id: kitchen, registry_name: lightwheel_kitchen_one_wall_coastal, params: {}}
objects:
- {id: mustard_bottle, registry_name: mustard_bottle_hope_robolab, params: {}}
- {id: bowl, registry_name: bowl_ycb_robolab, params: {}}
object_references:
- {id: right_counter_top, parent_id: kitchen, prim_path: counter_main_main_group/top_geometry_right,
   object_type: base, params: {}}
- {id: floor, parent_id: kitchen, prim_path: floor_room/geometry, object_type: base, params: {}}
relations:
- {kind: is_anchor, subject: floor, params: {}}
- {kind: is_anchor, subject: right_counter_top, params: {}}
- {kind: 'on', subject: droid, reference: floor, params: {}}
- {kind: next_to, subject: droid, reference: right_counter_top,
   params: {side: negative_y, distance_m: 0.3}}
- {kind: rotate_around_solution, subject: droid, params: {yaw_rad: 1.57}}
- {kind: 'on', subject: mustard_bottle, reference: right_counter_top, params: {}}
- {kind: 'on', subject: bowl, reference: right_counter_top, params: {}}
task:
  composition: atomic
  description: pick up the mustard bottle and place it in the bowl
  subtasks:
  - {kind: PickAndPlaceTask, params: {pick_up_object: mustard_bottle, destination_location: bowl}}
"""


@pytest.fixture()
def mapping(settings):
    m = dict(load_arena_mapping(settings.path("resources")))
    m["category_names_ko"] = {}
    return m


def _scene(mapping, text=REAL_SPEC):
    return arena_convert.scene_from_spec(
        arena_spec.load_spec(text), mapping,
        domain="household", schema_version="0.2", source_path="fixture.yaml",
    )


def test_spec_model_round_trips_yaml():
    spec = arena_spec.load_spec(REAL_SPEC)
    again = arena_spec.load_spec(spec.to_yaml())
    assert again.model_dump() == spec.model_dump()


def test_import_reads_objects_fixtures_and_placement(mapping):
    scene = _scene(mapping)
    assert scene.simulator == "isaaclab_arena"
    assert [o.category for o in scene.objects] == ["mustard", "bowl"]
    assert [f.id for f in scene.fixtures] == ["fx_right_counter_top"]  # 바닥은 제외
    placed = {o.id: o.placement.fixture_id for o in scene.objects if o.placement}
    assert placed == {"obj_001": "fx_right_counter_top", "obj_002": "fx_right_counter_top"}
    assert scene.agent.embodiment == "droid_abs_joint_pos"


def test_import_parses_layout_from_background(mapping):
    scene = _scene(mapping)
    assert scene.layout.layout_name == "one_wall"
    assert scene.layout.style_name == "coastal"


def test_import_keeps_original_ids_in_provenance(mapping):
    scene = _scene(mapping)
    assert scene.provenance.source_object_names == {"obj_001": "mustard_bottle", "obj_002": "bowl"}
    assert scene.provenance.original_task_name == "droid_pick_mustard_to_bowl"


def _our_scene() -> SceneState:
    return SceneState(
        schema_version="0.2", scene_id="rc365/T/ep0", domain="household", simulator="robocasa365",
        layout=LayoutInfo(layout_name="l_shaped"),
        fixtures=[
            # fridge는 prim 경로가 있어 내보낼 수 있고, cabinet은 없어 빠진다
            FixtureEntry(id="fx_fridge_1", category="fridge", name_ko="냉장고",
                         states={"door_open": True}, state_source="task_source_static"),
            FixtureEntry(id="fx_cabinet_1", category="cabinet", name_ko="캐비닛"),
        ],
        objects=[
            ObjectEntry(id="obj_001", category="bowl", name_ko="그릇",
                        placement=PlacementInfo(fixture_id="fx_fridge_1", relation="in")),
            ObjectEntry(id="obj_002", category="cheese_grater", name_ko="치즈 강판",
                        placement=PlacementInfo(fixture_id="fx_cabinet_1", relation="in")),
        ],
        relations=[
            RelationEntry(subj="obj_001", pred="in", obj="fx_fridge_1"),
            RelationEntry(subj="obj_002", pred="in", obj="fx_cabinet_1"),
        ],
        agent=AgentInfo(embodiment="PandaOmron"),
        provenance=Provenance(dataset_path="d", episode_index=0, original_task_name="T",
                              extraction_tier="A"),
    )


def test_export_produces_valid_spec(mapping):
    spec, _ = arena_convert.spec_from_scene(
        _our_scene(), mapping, description="그릇을 꺼내 줘", env_name="test_env")
    again = arena_spec.load_spec(spec.to_yaml())
    assert again.env_name == "test_env"
    assert again.background.registry_name == "lightwheel_kitchen_l_shaped_scandinavian"
    assert again.task.description == "그릇을 꺼내 줘"
    # 바닥과 매핑된 fixture는 anchor로 고정되어야 배치 solver가 풀 수 있다
    assert "floor" in again.anchors()


def test_export_reports_what_it_cannot_carry(mapping):
    """Arena가 담지 못하는 것을 조용히 버리지 않고 보고해야 한다."""
    _, unmapped = arena_convert.spec_from_scene(
        _our_scene(), mapping, description="d", env_name="e")
    # 매핑 없는 물체 카테고리는 제외되고 보고된다
    assert any("cheese_grater" in x for x in unmapped["objects"])
    # Arena 씬 그래프에는 가구 의미 상태 슬롯이 없다
    assert any("door_open" in x for x in unmapped["states"])
    # prim 경로가 없는 fixture는 빠지고, 그 fixture를 가리키던 관계도 함께 보고된다
    assert any("cabinet" in x for x in unmapped["fixtures"])
    assert any("fx_cabinet_1" in x for x in unmapped["relations"])


def test_containment_survives_round_trip(mapping):
    """Arena에는 in 관계가 없다. on으로 내보내되 원래 술어가 복원되어야 한다."""
    spec, _ = arena_convert.spec_from_scene(
        _our_scene(), mapping, description="d", env_name="e")
    kinds = {(r.subject, r.reference): r.kind for r in spec.placements()}
    assert kinds[("obj_001", "fx_fridge_1")] == "on"  # Arena 표현
    back = arena_convert.scene_from_spec(
        spec, mapping, domain="household", schema_version="0.2", source_path="x")
    assert [r.pred for r in back.relations] == ["in"]  # 우리 의미는 복원됨


def test_capability_report_counts_losses(mapping):
    report = arena_convert.capability_report(_our_scene(), mapping)
    assert report["objects_total"] == 2 and report["objects_mappable"] == 1
    assert report["containment_relations"] == 2
    assert report["fixtures_with_states"] == 1


# --- 실제 배포 파일 기반 --------------------------------------------------- #
def _real_specs(settings):
    return ArenaSpecAdapter(settings, settings.adapter_options("isaaclab_arena")).spec_paths()


def test_all_real_specs_parse(settings, mapping):
    from tsdgen.config import PROJECT_ROOT
    real_settings = settings.model_copy(deep=True)
    real_settings.paths.raw_cache = "data/raw"
    real_settings.root = PROJECT_ROOT
    paths = _real_specs(real_settings)
    if not paths:
        pytest.skip("Arena 캐시 없음 (TSDGEN_STAGE=prefetch 필요)")
    parsed = 0
    for path in paths:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not arena_spec.is_env_spec(doc):
            continue
        spec = arena_spec.ArenaEnvSpec.model_validate(doc)
        arena_convert.scene_from_spec(
            spec, mapping, domain="household", schema_version="0.2", source_path=path.name)
        parsed += 1
    assert parsed >= 20, f"env spec을 {parsed}개만 읽었다"
