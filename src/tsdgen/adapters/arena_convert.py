"""Arena 환경 스펙 ↔ 우리 씬 상태 변환.

씬 상태는 시뮬레이터 독립 규격이고(스펙 3절 6번), Arena는 그 규격에 붙는 소스이자
목적지다. 여기서 두 방향을 모두 구현한다.

  import  Arena 스펙 -> SceneState. ETRI가 전달할 패키지를 바로 파이프라인에 넣기 위한 것.
  export  SceneState -> Arena 스펙. 우리가 만든 시나리오를 Arena에서 빌드·실행하기 위한 것.

두 표현은 표현력이 다르므로 변환은 손실이 있다. 무엇이 오가지 못하는지는
capability_report()가 숫자로 돌려주고, 변환 결과의 provenance에도 남는다.
조용히 버리는 항목이 없도록 하는 것이 이 모듈의 핵심 계약이다.

Arena가 담지 못하는 것: 포함 관계(in), fixture의 의미 상태(문 개폐 등),
매핑 없는 물체 카테고리, RoboCasa 주방 2,500종의 layout·style 구분.
우리 쪽이 담지 못하는 것: prim 경로, 배치 수정자(rotate/random), 좌표 파라미터.
이런 값은 provenance에 원문 그대로 보관해 왕복 시 복원한다.
"""

from typing import Any

from tsdgen.adapters import arena_spec as spec
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
from tsdgen.registry import Registry

SIMULATOR = "isaaclab_arena"

# Arena 스펙에 우리 정보를 실어 보내는 자리. Arena는 해석하지 않지만 왕복 시 복원된다.
CONTAINMENT_HINT = "tsdgen_containment"
STATE_HINT = "tsdgen_states"

RELATION_EXPORT = Registry("scene relation -> Arena relation")
"""우리 관계 술어 -> (Arena 관계 종류, 함께 실을 params).

Arena 관계 어휘는 우리 술어와 일대일이 아니다. 어느 술어를 어떻게 옮길지는
술어마다 다르므로 등록으로 표현하고, 새 술어가 생기면 등록 한 줄로 끝나게 한다.
등록되지 않은 술어는 옮길 수 없다는 뜻이라 호출부가 손실로 보고한다.
"""


@RELATION_EXPORT.register("on")
def _export_on() -> tuple[str, dict]:
    return spec.ON, {}


@RELATION_EXPORT.register("near")
def _export_near() -> tuple[str, dict]:
    return spec.NEXT_TO, {}


@RELATION_EXPORT.register("in")
def _export_in() -> tuple[str, dict]:
    """Arena에는 포함 관계가 없다. on으로 내보내되 원래 술어를 params에 남겨 왕복시킨다."""
    return spec.ON, {CONTAINMENT_HINT: "in"}


BACKGROUND_ID = "kitchen"
FLOOR_ID = "floor"


def _category_from_registry(registry_name: str, reverse: dict[str, str]) -> str:
    """registry_name -> 우리 카테고리. 매핑에 없으면 이름 앞부분을 카테고리로 쓴다."""
    if registry_name in reverse:
        return reverse[registry_name]
    stem = registry_name.split("_")[0]
    return stem or registry_name


def _layout_from_background(registry_name: str | None) -> LayoutInfo:
    """lightwheel_kitchen_<layout>_<style> 형태에서 layout/style 이름을 뽑는다."""
    if not registry_name:
        return LayoutInfo()
    parts = registry_name.split("_")
    if len(parts) >= 4 and parts[0] == "lightwheel" and parts[1] == "kitchen":
        return LayoutInfo(layout_name="_".join(parts[2:-1]), style_name=parts[-1])
    return LayoutInfo(layout_name=registry_name)


def _import_fixtures(
    env_spec: spec.ArenaEnvSpec, names_ko: dict
) -> tuple[list[FixtureEntry], dict[str, str]]:
    """object_references를 fixture로 옮긴다. 바닥은 배경 요소라 싣지 않는다."""
    fixtures: list[FixtureEntry] = []
    ids: dict[str, str] = {}
    for ref in env_spec.object_references:
        if ref.id == FLOOR_ID:
            continue
        category = ref.id
        ids[ref.id] = f"fx_{category}"
        states = dict(ref.params.get(STATE_HINT) or {})
        fixtures.append(
            FixtureEntry(
                id=ids[ref.id],
                category=category,
                name_ko=names_ko.get(category, category),
                states=states,
                state_source="metadata" if states else "unknown",
            )
        )
    return fixtures, ids


def _import_objects(
    env_spec: spec.ArenaEnvSpec, reverse: dict, names_ko: dict
) -> tuple[list[ObjectEntry], dict[str, str], dict[str, str]]:
    """objects를 중립 id로 옮긴다. 원래 id는 provenance에 남긴다."""
    objects: list[ObjectEntry] = []
    ids: dict[str, str] = {}
    source_names: dict[str, str] = {}
    for index, item in enumerate(env_spec.objects, start=1):
        obj_id = f"obj_{index:03d}"
        ids[item.id] = obj_id
        source_names[obj_id] = item.id
        category = _category_from_registry(item.registry_name, reverse)
        objects.append(
            ObjectEntry(id=obj_id, category=category, name_ko=names_ko.get(category, category))
        )
    return objects, ids, source_names


def _import_relations(
    env_spec: spec.ArenaEnvSpec,
    objects: list[ObjectEntry],
    object_ids: dict[str, str],
    fixture_ids: dict[str, str],
) -> list[RelationEntry]:
    """배치 관계를 옮기고, fixture 위·안이면 물체의 placement도 채운다."""
    by_id = {o.id: o for o in objects}
    relations: list[RelationEntry] = []
    for rel in env_spec.placements():
        subject = object_ids.get(rel.subject) or fixture_ids.get(rel.subject)
        reference = fixture_ids.get(rel.reference) or object_ids.get(rel.reference)
        if not subject or not reference:
            continue  # 로봇 배치 등 씬 상태에 담지 않는 관계
        predicate = rel.params.get(CONTAINMENT_HINT) or ("on" if rel.kind == spec.ON else "near")
        relations.append(
            RelationEntry(subj=subject, pred=predicate, obj=reference, source=SIMULATOR)
        )
        if subject in by_id and reference.startswith("fx_") and predicate in ("on", "in"):
            by_id[subject].placement = PlacementInfo(fixture_id=reference, relation=predicate)
    return relations


def scene_from_spec(
    env_spec: spec.ArenaEnvSpec,
    mapping: dict,
    *,
    domain: str,
    schema_version: str,
    source_path: str,
) -> SceneState:
    """Arena 스펙을 씬 상태로 읽어들인다."""
    reverse = {v: k for k, v in (mapping["object_registry"] or {}).items() if v}
    names_ko = mapping.get("category_names_ko") or {}

    fixtures, fixture_ids = _import_fixtures(env_spec, names_ko)
    objects, object_ids, source_names = _import_objects(env_spec, reverse, names_ko)
    relations = _import_relations(env_spec, objects, object_ids, fixture_ids)

    embodiment = env_spec.embodiment.registry_name if env_spec.embodiment else "unknown"
    background = env_spec.background.registry_name if env_spec.background else None
    return SceneState(
        schema_version=schema_version,
        scene_id=f"{SIMULATOR}/{env_spec.env_name}",
        domain=domain,
        simulator=SIMULATOR,
        layout=_layout_from_background(background),
        fixtures=fixtures,
        objects=objects,
        relations=relations,
        agent=AgentInfo(embodiment=embodiment),
        provenance=Provenance(
            dataset_path=source_path,
            episode_index=0,
            original_task_name=env_spec.env_name,
            extraction_tier="A",
            source_object_names=source_names,
            source_fixture_names={v: k for k, v in fixture_ids.items()},
        ),
    )


def _background_for(scene: SceneState, backgrounds: dict) -> str:
    """RoboCasa 주방은 2,500종, Arena 주방은 5종이라 이름이 겹칠 때만 대응시킨다."""
    for layout_key, name in (backgrounds.get("by_layout_name") or {}).items():
        if scene.layout.layout_name and layout_key in scene.layout.layout_name:
            return name
    return backgrounds["default"]


def _export_fixtures(
    scene: SceneState, mapping: dict, unmapped: dict[str, list[str]]
) -> tuple[list[spec.ObjectReference], dict[str, str]]:
    """fixture를 배경 내부 prim 참조로 옮긴다. prim 경로가 없으면 옮길 수 없다."""
    prim_paths = mapping["fixture_prim_path"] or {}
    references = [
        spec.ObjectReference(
            id=FLOOR_ID, parent_id=BACKGROUND_ID,
            prim_path=mapping["floor_prim_path"], object_type="base",
        )
    ]
    ref_ids: dict[str, str] = {}
    for fixture in scene.fixtures:
        prim_path = prim_paths.get(fixture.category)
        if not prim_path:
            unmapped["fixtures"].append(f"{fixture.id}({fixture.category})")
            continue
        ref_ids[fixture.id] = fixture.id
        params: dict[str, Any] = {}
        if fixture.states:
            # Arena 씬 그래프에는 의미 상태 슬롯이 없다. 잃지 않도록 params에 실어 둔다.
            params[STATE_HINT] = fixture.states
            unmapped["states"].append(f"{fixture.id}={fixture.states}")
        references.append(
            spec.ObjectReference(
                id=fixture.id, parent_id=BACKGROUND_ID, prim_path=prim_path,
                object_type="base", params=params,
            )
        )
    return references, ref_ids


def _export_objects(
    scene: SceneState, mapping: dict, unmapped: dict[str, list[str]]
) -> tuple[list[spec.Registered], dict[str, str]]:
    """물체를 Arena 에셋 registry 이름으로 옮긴다. 대응 없는 카테고리는 실을 수 없다."""
    registry = mapping["object_registry"] or {}
    objects: list[spec.Registered] = []
    ref_ids: dict[str, str] = {}
    for obj in scene.objects:
        registry_name = registry.get(obj.category)
        if not registry_name:
            unmapped["objects"].append(f"{obj.id}({obj.category})")
            continue
        ref_ids[obj.id] = obj.id
        objects.append(spec.Registered(id=obj.id, registry_name=registry_name))
    return objects, ref_ids


def _export_relations(
    scene: SceneState,
    object_ref_ids: dict[str, str],
    fixture_ref_ids: dict[str, str],
    unmapped: dict[str, list[str]],
) -> list[spec.Relation]:
    """관계를 옮긴다. anchor는 배치 solver가 좌표를 풀 수 있도록 먼저 고정한다."""
    relations = [spec.Relation(kind=spec.IS_ANCHOR, subject=FLOOR_ID)]
    relations += [
        spec.Relation(kind=spec.IS_ANCHOR, subject=ref_id) for ref_id in fixture_ref_ids.values()
    ]
    for rel in scene.relations:
        subject = object_ref_ids.get(rel.subj) or fixture_ref_ids.get(rel.subj)
        reference = fixture_ref_ids.get(rel.obj) or object_ref_ids.get(rel.obj)
        if not subject or not reference:
            # 양 끝 중 하나가 매핑되지 않아 이 관계는 스펙에 실을 수 없다. 물체·fixture
            # 손실과 별개로 관계 손실도 세어야 실제 배치 재현 가능성을 알 수 있다.
            unmapped["relations"].append(f"{rel.subj} -{rel.pred}-> {rel.obj}")
            continue
        if rel.pred not in RELATION_EXPORT.names():
            unmapped["relations"].append(f"{rel.subj} -{rel.pred}-> {rel.obj} (술어 미지원)")
            continue
        kind, params = RELATION_EXPORT.get(rel.pred)()
        relations.append(
            spec.Relation(kind=kind, subject=subject, reference=reference, params=params)
        )
    return relations


def spec_from_scene(
    scene: SceneState, mapping: dict, *, description: str, env_name: str
) -> tuple[spec.ArenaEnvSpec, dict[str, list[str]]]:
    """씬 상태를 Arena 스펙으로 내보낸다. (스펙, 옮기지 못한 항목) 을 돌려준다."""
    unmapped: dict[str, list[str]] = {
        "objects": [], "fixtures": [], "states": [], "relations": []
    }
    references, fixture_ref_ids = _export_fixtures(scene, mapping, unmapped)
    objects, object_ref_ids = _export_objects(scene, mapping, unmapped)
    relations = _export_relations(scene, object_ref_ids, fixture_ref_ids, unmapped)
    background_name = _background_for(scene, mapping["background_registry"])

    env = spec.ArenaEnvSpec(
        env_name=env_name,
        embodiment=spec.Registered(
            id="robot", registry_name=mapping["embodiment_registry"]["default"]
        ),
        background=spec.Registered(id=BACKGROUND_ID, registry_name=background_name),
        objects=objects,
        object_references=references,
        relations=relations,
        task=spec.Task(composition="atomic", description=description, subtasks=[]),
    )
    return env, {k: v for k, v in unmapped.items() if v}


def capability_report(scene: SceneState, mapping: dict) -> dict:
    """이 씬을 Arena로 내보낼 때 무엇이 살아남는지 세어 준다."""
    registry = mapping["object_registry"] or {}
    prim_paths = mapping["fixture_prim_path"] or {}
    return {
        "objects_total": len(scene.objects),
        "objects_mappable": sum(1 for o in scene.objects if registry.get(o.category)),
        "fixtures_total": len(scene.fixtures),
        "fixtures_mappable": sum(1 for f in scene.fixtures if prim_paths.get(f.category)),
        "containment_relations": sum(1 for r in scene.relations if r.pred == "in"),
        "fixtures_with_states": sum(1 for f in scene.fixtures if f.states),
    }
