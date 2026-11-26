"""테스트 공용 픽스처: 오버라이드된 설정과 가짜 LeRobot 데이터셋."""

import json
from pathlib import Path

import pytest

from tsdgen.adapters.base import make_adapter
from tsdgen.config import CONFIG_PATH, PROJECT_ROOT, Settings, load_config_document

FAKE_TASKS = {
    "FakeTask": ("pretrain", "atomic"),
    "FakeComposite": ("pretrain", "composite"),
}


def _fake_ep_meta(layout_id: int, instruction: str) -> dict:
    return {
        "layout_id": layout_id,
        "style_id": 7,
        "lang": instruction,
        "fixture_refs": {"microwave": "microwave_main"},
        "fixtures": {
            "counter_main": {"cls": "Counter"},
            "microwave_main": {"cls": "Microwave"},
            "stack_1_dining_area_group": {"cls": "Drawer"},
            "wall_1": {"cls": "Wall"},
        },
        "object_cfgs": [
            {
                "name": "obj",
                "type": "object",
                "info": {"cat": "mug"},
                "placement": {"fixture": "counter_main",
                              "sample_region_kwargs": {"ref": "microwave_main", "loc": "left"}},
                "reset_region": {"name": "geom_0"},
            },
            {
                "name": "distr_counter",
                "type": "object",
                "info": {"cat": "apple"},
                "placement": {"fixture": "counter_main"},
            },
        ],
    }


FAKE_SOURCE = "robocasa365"  # 가짜 데이터셋이 흉내 내는 원천 (어댑터 등록 이름)


def robocasa_adapter(settings: Settings):
    """가짜 LeRobot 캐시를 읽는 RoboCasa 어댑터. 테스트 여러 곳이 같은 것을 쓴다."""
    return make_adapter(settings, FAKE_SOURCE)


def build_fake_dataset(raw_cache: Path) -> None:
    links = {}
    for task, (split, family) in FAKE_TASKS.items():
        links[f"{split}/{family}/{task}/20260101/lerobot.tar"] = "https://example.com/s/fake"
        task_dir = raw_cache / split / family / task
        (task_dir / "meta").mkdir(parents=True)
        (task_dir / "meta" / "info.json").write_text(
            json.dumps({"robot_type": "PandaOmron", "total_episodes": 2}), encoding="utf-8"
        )
        episodes = [
            {"episode_index": 0, "tasks": [f"pick the mug and place it in the microwave ({task})"]},
            {"episode_index": 1, "tasks": [f"put the mug into the microwave ({task})"]},
        ]
        (task_dir / "meta" / "episodes.jsonl").write_text(
            "\n".join(json.dumps(e) for e in episodes) + "\n", encoding="utf-8"
        )
        (task_dir / "extras").mkdir()
        (task_dir / "extras" / "dataset_meta.json").write_text(
            json.dumps({"env": task}), encoding="utf-8"
        )
        for idx in range(2):
            ep_dir = task_dir / "extras" / f"episode_{idx:06d}"
            ep_dir.mkdir()
            ep_meta = _fake_ep_meta(layout_id=idx + 1, instruction=episodes[idx]["tasks"][0])
            (ep_dir / "ep_meta.json").write_text(json.dumps(ep_meta), encoding="utf-8")
        (task_dir / ".metadata_complete").touch()
    raw_cache.mkdir(parents=True, exist_ok=True)
    (raw_cache / "box_links_ds.json").write_text(json.dumps(links), encoding="utf-8")


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    data = load_config_document(CONFIG_PATH, PROJECT_ROOT)
    for name in ("raw_cache", "scenarios", "static_map_dir", "llm_cache", "runs", "deliverables",
                 "schema_dir", "tmp"):
        data["paths"][name] = str(tmp_path / name)
    data["project"].update({"run_id": "test", "n_scenarios": 4, "seed": 7})
    # 가짜 데이터셋에는 시연 프레임이 없으므로 실행 설정과 상관없이 Tier A로 고정한다 (Tier B는 전용 테스트가 본다)
    data["source"]["options"]["robocasa365"].update(
        {"tasks": sorted(FAKE_TASKS), "episodes_per_task": 2, "sim_state": False,
         "family_weights": {"atomic": 0.5, "composite": 0.5}}
    )
    data["sampling"]["stratified"]["quotas"] = {"atomic": 0.5, "composite": 0.5}
    # 다른 실행에 기대는 선별 설정은 임시 폴더에 그 실행이 없으므로 끈다 (전용 테스트가 따로 켠다)
    data["sampling"].update({"same_scenarios_as_run": None, "exclude_tasks_of_runs": [],
                             "filters": ["directional_reference"]})  # 실행 설정의 필터와 무관하게 고정
    data["llm"]["roles"] = {
        "rewriter": "mock",
        "generators": ["mock"],
        "checklist_judge": {"mock": "mock-b"},
        "probe_judge": "mock-b",
        "resolvability_judge": "mock-b",
    }
    data["llm"]["per_scenario_k"] = 2
    data["dashboard"]["model"] = "mock"
    data["dashboard"]["run_id"] = None
    data["export"]["kist_generator"] = "mock"
    settings = Settings.model_validate(data)
    build_fake_dataset(settings.path("raw_cache"))
    return settings
