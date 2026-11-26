"""가짜 LeRobot 픽스처로 RoboCasa365 어댑터 동작 검증 (실데이터·네트워크 불필요)."""

import json
from pathlib import Path

from tests.conftest import FAKE_TASKS, robocasa_adapter

from tsdgen.adapters.robocasa365 import RoboCasa365Adapter
from tsdgen.models import to_environment_context
from tsdgen.registry import STAGES


def test_adapter_builds_scenarios(settings):
    records = list(robocasa_adapter(settings).iter_scenarios())
    # 태스크 2개 × episodes_per_task 2
    assert len(records) == 4
    by_family = {r.source.task_family for r in records}
    assert by_family == {"atomic", "composite"}

    record = next(r for r in records if r.source.task_name == "FakeTask")
    scene = record.scene_state
    assert record.task_type_id == "rc365:FakeTask"
    assert scene.agent.embodiment == "PandaOmron"
    # 배경 요소(벽)는 제외되고 상호작용 fixture만 남는다
    assert {f.category for f in scene.fixtures} == {"counter", "microwave", "drawer"}
    # 물체는 중립 id + 실제 카테고리, 역할 이름은 provenance에만 남는다
    assert [o.id for o in scene.objects] == ["obj_001", "obj_002"]
    assert [o.category for o in scene.objects] == ["mug", "apple"]
    assert scene.provenance.source_object_names == {"obj_001": "obj", "obj_002": "distr_counter"}
    # placement: counter는 on. 원천의 칸 이름과 배치 기준 옆 가구·방향도 남는다
    placement = scene.objects[0].placement
    microwave = next(f for f in scene.fixtures if f.category == "microwave")
    assert placement.relation == "on" and placement.region == "geom_0"
    assert placement.near_fixture_id == microwave.id and placement.near_side == "left"
    # 명칭 힌트가 있는 fixture만 location_desc가 채워진다
    drawer = next(f for f in scene.fixtures if f.category == "drawer")
    assert drawer.location_desc == "식탁 구역"
    counter = next(f for f in scene.fixtures if f.category == "counter")
    assert counter.location_desc is None
    # Tier A: fixture 상태는 unknown
    assert all(f.state_source == "unknown" for f in scene.fixtures)


def test_adapter_instruction_varies_per_episode(settings):
    records = [
        r for r in robocasa_adapter(settings).iter_scenarios()
        if r.source.task_name == "FakeTask"
    ]
    instructions = {r.source.original_instruction_en for r in records}
    assert len(instructions) == 2


def test_env_context_hides_source_names(settings):
    record = next(iter(robocasa_adapter(settings).iter_scenarios()))
    blob = json.dumps(to_environment_context(record.scene_state), ensure_ascii=False)
    assert "distr_counter" not in blob
    assert "FakeTask" not in blob and "FakeComposite" not in blob
    assert "counter_main" not in blob  # 원 fixture 명칭


def test_prefetch_skips_cached_tasks(settings):
    # 픽스처의 태스크는 이미 캐시 마커가 있으므로 네트워크 접근 없이 cached로 집계된다
    result = STAGES.get("prefetch")(settings)
    robocasa = result["robocasa365"]
    assert robocasa["tasks_in_scope"] == robocasa["cached"] == len(FAKE_TASKS)
    assert robocasa["ok"] == 0 and robocasa["failed"] == 0


def test_prefetch_arena_reports_cached(settings):
    from tsdgen.adapters.arena import ArenaSpecAdapter
    from tsdgen.adapters.robocasa_fetch import METADATA_MARKER

    adapter = ArenaSpecAdapter(settings, settings.adapter_options("isaaclab_arena"))
    adapter.cache_dir().mkdir(parents=True, exist_ok=True)
    (adapter.cache_dir() / METADATA_MARKER).touch()
    assert adapter.prefetch()["cached"] is True


def test_ingest_never_writes_the_same_scenario_twice(settings, monkeypatch):
    """원천이 같은 시나리오를 두 번 내놓아도 데이터가 두 벌이 되면 안 된다.

    한 태스크가 tar 키를 여럿 가질 때 실제로 중복 적재가 일어났다. LLM 예산이 두 배로
    들고 데이터 분포도 틀어지는데, 최종 산출물을 세어 보기 전까지 드러나지 않는다.
    """
    from tsdgen.registry import STAGES
    from tsdgen.stages.common import scenarios_path
    from tsdgen.util import read_jsonl

    original = RoboCasa365Adapter.iter_scenarios

    def twice(self):
        for record in original(self):
            yield record
            yield record.model_copy(deep=True)

    monkeypatch.setattr(RoboCasa365Adapter, "iter_scenarios", twice)
    STAGES.get("ingest")(settings)
    rows = list(read_jsonl(scenarios_path(settings)))
    assert len(rows) == len({r["scenario_id"] for r in rows})


def test_task_candidates_are_deduplicated(settings):
    """tar 키가 여럿인 태스크도 후보에는 한 번만 올라야 한다."""
    from tsdgen.adapters.robocasa365 import TaskSelection

    options = settings.adapter_options("robocasa365")
    links = {
        "pretrain/atomic/T/20250819/lerobot.tar": "u1",
        "pretrain/atomic/T/20250820/lerobot.tar": "u2",
        "pretrain/atomic/T/20250820/mg/demo/x/lerobot.tar": "u3",
    }
    available = TaskSelection(settings, options, links)._available()
    assert available["atomic"] == [("pretrain", "atomic", "T")]


def test_unknown_fixture_class_gets_lexicon_name_and_is_reported(settings):
    """어휘표에 없는 fixture 클래스는 이름에서 카테고리를 만들고 표시명은 공용 사전에서 찾는다.

    예전에는 클래스명('BlenderLid')이 그대로 한국어 표시명이 되어 전달물까지 나갔다.
    사전에도 없으면 영어가 남으므로, 조용히 두지 않고 보고 대상으로 모은다.
    """
    from tsdgen.adapters.robocasa_scene import SceneBuilder

    adapter = robocasa_adapter(settings)
    builder = SceneBuilder(settings, adapter.lexicon, adapter.vocabulary, adapter.options)
    known = builder._fixture_meta("BlenderLid")
    assert known["category"] == "blender_lid" and known["ko"] == "블렌더 뚜껑"
    guessed = builder._fixture_meta("Toaster2000Deluxe")
    assert guessed["category"] == "toaster2000_deluxe"
    assert guessed["ko"] == "toaster2000_deluxe"  # 사전에 없으면 카테고리 이름 (클래스명 아님)
    assert builder.unmapped_fixture_classes == {"Toaster2000Deluxe"}


def test_delivery_repairs_untranslated_names(settings):
    """이미 적재된 씬에 남은 원문 이름(소문자 카테고리·원본 클래스명 모두)을 전달본에서 고친다."""
    from tsdgen.exporters.kist_json import _fix_untranslated_names
    from tsdgen.resources import load_resources

    env = {
        "fixtures": [{"id": "f1", "category": "blender_lid", "name_ko": "BlenderLid"},
                     {"id": "f2", "category": "cabinet", "name_ko": "수납장"}],
        "objects": [{"id": "o1", "category": "saucepan", "name_ko": "saucepan"},
                    {"id": "o2", "category": "no_such_thing", "name_ko": "no_such_thing"}],
    }
    _fix_untranslated_names(env, load_resources(settings.path("resources")))
    assert [f["name_ko"] for f in env["fixtures"]] == ["블렌더 뚜껑", "수납장"]
    assert [o["name_ko"] for o in env["objects"]] == ["소스팬", "no_such_thing"]


def test_adapter_records_object_held_by_another(settings):
    """다른 물체를 기준으로 놓인 물체(접시 위 바게트)는 그 물체와의 관계로 남는다. 담는 그릇이면 안, 아니면 위."""
    from tsdgen.adapters.robocasa_scene import SceneBuilder

    adapter = robocasa_adapter(settings)
    builder = SceneBuilder(settings, adapter.lexicon, adapter.vocabulary, adapter.options)
    ep_meta = {"fixtures": {"counter_main": {"cls": "Counter"}}, "object_cfgs": [
        {"name": "bread_container", "info": {"cat": "plate"}, "placement": {"fixture": "counter_main"}},
        {"name": "bread", "info": {"cat": "baguette"},
         "placement": {"sample_args": {"reference": "bread_container", "on_top": True}}},
        {"name": "fruit_container", "info": {"cat": "bowl"}, "placement": {"fixture": "counter_main"}},
        {"name": "fruit", "info": {"cat": "apple"},
         "placement": {"sample_args": {"reference": "fruit_container"}}}]}
    scene = builder.build(ep_meta, 0, "T", "pretrain", settings.root, "PandaOmron", {}, None)
    held = {(r.subj, r.pred, r.obj) for r in scene.relations if r.obj.startswith("obj_")}
    assert held == {("obj_002", "on", "obj_001"), ("obj_004", "in", "obj_003")}
    assert scene.objects[1].placement is None


def test_open_based_counter_is_named_dining_counter(settings):
    """아래가 트인 조리대는 이름과 상관없이 식사용 조리대다 (배치 파일에서 읽은 표를 쓴다)."""
    from tsdgen.adapters.robocasa_scene import SceneBuilder
    from tsdgen.adapters.robocasa_staticmap import _open_based

    blueprint = {"front_group": {"bottom_row": [{"name": "counter_3", "base_opening": [False, True]},
                                                {"name": "counter_4"}]}}
    assert _open_based(blueprint["front_group"]) == ["counter_3"]

    adapter = robocasa_adapter(settings)
    builder = SceneBuilder(settings, adapter.lexicon, adapter.vocabulary, adapter.options)
    builder.layout_map = {27: {"dining_counters": ["counter_3_front_group"]}}
    ep_meta = {"layout_id": 27, "fixtures": {"counter_3_front_group": {"cls": "Counter"},
                                             "counter_4_front_group": {"cls": "Counter"}}}
    entries, _ids, _assumed, _keys = builder._build_fixtures(ep_meta, {})
    hints = [entry.location_desc for entry in entries]
    assert hints == [adapter.vocabulary["location_hint_prefixes"]["Counter"]["dining"], None]


def _synth_settings(settings):
    from tsdgen.adapters.robocasa_synth import RoboCasaSynthesisOptions

    options = settings.adapter_options("robocasa365")
    options.synthesis = RoboCasaSynthesisOptions(kitchens=2, seeds_per_template=2, workers=1)
    return options


def test_synthesis_plan_is_deterministic_and_covers_the_grid(settings):
    """합성 계획은 설정과 seed로 정해진다: 주방 × 템플릿 × seed 수만큼이고 다시 세워도 같다."""
    from tsdgen.adapters import robocasa_synth

    options = _synth_settings(settings)
    tasks = [("pretrain", "atomic", "OpenDrawer"), ("pretrain", "composite", "PrepareCoffee")]
    jobs = robocasa_synth.plan(settings, options, tasks)
    assert len(jobs) == 2 * 2 * 2 and jobs == robocasa_synth.plan(settings, options, tasks)
    assert len({job["out"] for job in jobs}) == len(jobs)  # 에피소드 폴더가 겹치지 않는다
    low, high = options.simulator.layout_ids
    assert all(low <= job["layout"] <= high for job in jobs)


def test_synthesized_episodes_become_scenarios(settings, monkeypatch):
    """reset이 남긴 에피소드 폴더는 받은 시연과 같은 길로 시나리오가 되고, 실패로 기록된 조합은 빠진다."""
    from tsdgen.adapters import robocasa_synth

    adapter = robocasa_adapter(settings)
    adapter.options = _synth_settings(settings)
    source = next((settings.path("raw_cache") / "pretrain").glob("*/*/extras/episode_000000/ep_meta.json"))

    def fake_launch(_settings, _simulator, jobs, _workers):
        for n, job in enumerate(jobs):
            out = Path(job["out"])
            out.mkdir(parents=True)
            if n == 0:
                (out / robocasa_synth.FAILURE_FILE).write_text("{}", encoding="utf-8")
                continue
            for name in robocasa_synth.EPISODE_FILES:
                (out / name).write_bytes(source.read_bytes() if name == "ep_meta.json" else b"")

    monkeypatch.setattr(robocasa_synth, "_launch", fake_launch)
    monkeypatch.setattr("tsdgen.adapters.robocasa365.SimSnapshot.load", lambda _folder: None)
    records = list(adapter.synthesize())
    planned = len(robocasa_synth.plan(settings, adapter.options, [(s, f, t) for t, (s, f) in FAKE_TASKS.items()]))
    assert len(records) == planned - 1
    assert {r.source.split for r in records} == {"synth"} and len({r.scenario_id for r in records}) == len(records)


def test_more_kitchens_keep_the_earlier_ones(settings):
    """주방 수를 늘려도 앞서 고른 주방은 그대로다 (이미 만든 장면을 다시 만들지 않는다)."""
    from tsdgen.adapters import robocasa_synth

    simulator = settings.adapter_options("robocasa365").simulator
    few, more = robocasa_synth.kitchens(simulator, 3, 7), robocasa_synth.kitchens(simulator, 10, 7)
    assert more[:3] == few and len(set(more)) == 10
