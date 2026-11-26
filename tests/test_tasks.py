"""작업 합성: 장면에 있는 것으로 작업 유형의 빈칸을 채우고, 서로 다른 상황만 고른다."""

from types import SimpleNamespace

from tsdgen import domains
from tsdgen.tasks.base import Words
from tsdgen.tasks.compose import ORACLE_KEY, select, tasks_of, to_scenario

TRAITS = {"mug": ["graspable", "microwavable", "receptacle", "washable"],
          "apple": ["fruit", "fridgable", "graspable", "washable"]}
TYPES = ["relocate", "fetch_and_close", "heat", "wash", "set_state"]


def _scene(cabinet_open: bool) -> dict:
    return {
        "objects": [{"id": "obj_001", "category": "mug", "placement": {"fixture_id": "fx_cabinet_1"}},
                    {"id": "obj_002", "category": "apple", "placement": {"fixture_id": "fx_counter_1"}}],
        "fixtures": [{"id": "fx_cabinet_1", "category": "cabinet", "states": {"door_open": cabinet_open}},
                     {"id": "fx_counter_1", "category": "counter", "states": {}},
                     {"id": "fx_counter_2", "category": "counter", "states": {}},
                     {"id": "fx_sink_1", "category": "sink", "states": {"turned_on": False}},
                     {"id": "fx_microwave_1", "category": "microwave", "states": {"door_open": False}}],
        "relations": [],
    }


def _words(settings) -> Words:
    return Words(domains.profile(settings)["task_synthesis"])


def test_tasks_come_with_their_goal_and_respect_object_traits(settings):
    tasks = list(tasks_of(_scene(False), TRAITS, _words(settings), TYPES))
    by_text = {t.instruction: t for t in tasks}
    wash = by_text["Put the apple in the sink and turn on the faucet."]
    assert wash.cases == ((("relation", "obj_002", "in", "fx_sink_1", True),
                           ("state", "fx_sink_1", "turned_on", None, True)),)
    counter = by_text["Put the mug on a counter."]
    assert counter.cases[0][0][3] == "fx_counter_1|fx_counter_2"  # 조리대 아무 것
    assert not [t for t in tasks if t.type_id == "heat" and "apple" in t.instruction]  # 데울 수 없는 것
    assert "Open the microwave." in by_text and "Close the microwave." not in by_text  # 이미 닫혀 있다


def test_the_same_situation_is_chosen_once_and_state_changes_make_a_new_one(settings):
    """배경만 다른 같은 상황은 한 번만 고르고, 출발지가 열려 있느냐처럼 설명문이 달라지는 상태는 따로 센다."""
    words, options = _words(settings), SimpleNamespace(per_signature=1, per_type=100)
    scenes = [_scene(False), _scene(False), _scene(True)]
    found = [({"scenario_id": f"s{i}"}, task) for i, scene in enumerate(scenes)
             for task in tasks_of(scene, TRAITS, words, ["fetch_and_close"])]
    chosen, counts = select(found, options, seed=1)
    made = counts["fetch_and_close"]["made"]
    assert made == 3 * (made // 3) and len(chosen) == 2 * (made // 3)


def test_composed_scenario_carries_instruction_and_answer(settings):
    task = next(tasks_of(_scene(False), TRAITS, _words(settings), ["wash"]))
    base = {"scenario_id": "rc365-X-synth-sim_reset-ep1", "task_type_id": "rc365:X", "strata": {},
            "source": {"simulator": "robocasa365", "task_name": "X", "task_family": "atomic",
                       "split": "synth", "dataset_source": "sim_reset", "episode_index": 1,
                       "original_instruction_en": "old"},
            "scene_state": __import__("json").loads(
                (settings.root / "resources/golden/heatmug_sample.json").read_text())["scene_state"]}
    record = to_scenario(base, task, "household")
    assert record.source.split == "composed" and record.source.original_instruction_en == task.instruction
    assert record.source.task_name == "wash" and record.meta[ORACLE_KEY][0][0]["predicate"] == "in"
