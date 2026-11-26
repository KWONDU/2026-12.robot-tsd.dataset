"""Tier B: 시연 첫 프레임 MuJoCo 상태에서 물체 pose와 가구 상태 읽기 (작은 합성 모델, 네트워크 불필요)."""

import gzip

import numpy as np
import pytest
from tests.conftest import FAKE_TASKS, robocasa_adapter

from tsdgen.adapters import robocasa_fetch
from tsdgen.adapters.robocasa_sim import (
    MODEL_FILE,
    STATES_FILE,
    JointReading,
    SimSnapshot,
    read_fixture_states,
)
from tsdgen.registry import STAGES
from tsdgen.stages.common import scenarios_path
from tsdgen.util import read_jsonl

# 가짜 ep_meta(conftest)의 이름과 맞춘 모델. 메시 자산은 배포처럼 파일이 없는 경로를 가리킨다.
MODEL_XML = """
<mujoco>
  <asset><mesh name="m" file="/nonexistent/mug.stl"/></asset>
  <worldbody>
    <body name="obj_main"><freejoint/><geom mesh="m"/></body>
    <body name="distr_counter_main"><freejoint/><geom type="box" size=".02 .02 .02"/></body>
    <body name="microwave_main_body">
      <joint name="microwave_main_microjoint" type="hinge" range="-1.571 0" limited="true"/>
      <geom type="box" size=".1 .1 .1"/>
    </body>
    <body name="stack_1_dining_area_group_body">
      <joint name="stack_1_dining_area_group_slidejoint" type="slide" range="-0.6 0" limited="true"/>
      <geom type="box" size=".1 .1 .1"/>
    </body>
  </worldbody>
</mujoco>
"""
NQ, NV = 16, 14  # 자유 관절 2개(7+7, 6+6) + 1자유도 관절 2개
MUG_POSE = [1.0, 2.0, 0.9, 1.0, 0.0, 0.0, 0.0]
APPLE_POSE = [1.2, 2.0, 0.9, 1.0, 0.0, 0.0, 0.0]


def write_sim_files(ep_dir, microwave=-1.571, drawer=0.0):
    """첫 행이 [시각, qpos, qvel]인 상태 행렬과 모델을 쓴다. 기본값은 전자레인지 열림, 서랍 닫힘."""
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / MODEL_FILE).write_bytes(gzip.compress(MODEL_XML.encode("utf-8")))
    qpos = [*MUG_POSE, *APPLE_POSE, microwave, drawer]
    row = np.array([0.0, *qpos, *([0.0] * NV)])
    np.savez(ep_dir / STATES_FILE, states=np.stack([row, row]))


def test_snapshot_reads_pose_and_fixture_joints(tmp_path):
    write_sim_files(tmp_path)
    sim = SimSnapshot.load(tmp_path)
    assert sim.object_pose("obj_main", 3) == {"pos": MUG_POSE[:3], "quat": MUG_POSE[3:]}
    assert sim.object_pose("microwave_main_body", 3) is None  # 자유 관절이 아니면 pose 없음
    assert sim.object_pose("missing_main", 3) is None
    joints = sim.fixture_joints(["microwave_main", "stack_1_dining_area_group"])
    assert [j.name for j in joints["microwave_main"]] == ["microjoint"]
    assert [j.name for j in joints["stack_1_dining_area_group"]] == ["slidejoint"]


def test_snapshot_missing_files_is_none(tmp_path):
    assert SimSnapshot.load(tmp_path) is None


DOOR = {"door_open": {"joint": "door", "rule": "door_fraction",
                      "params": {"open_min": 0.9, "closed_max": 0.005}}}


@pytest.mark.parametrize(("value", "expected"), [(1.5, True), (0.0, False), (0.8, None)])
def test_door_rule_is_three_valued(value, expected):
    """원본 기준 그대로: 0.9 이상 열림, 0.005 이하 닫힘, 그 사이는 판단하지 않는다."""
    joints = [JointReading("doorhinge", value, 0.0, 1.57)]
    assert read_fixture_states(joints, DOOR) == {"door_open": expected}


def test_door_rule_negative_range_and_all_doors():
    """범위가 음수 쪽이면 뒤집어 정규화하고(fixture.py), 문이 여럿이면 모두 열려야 열림이다."""
    open_left = JointReading("leftdoorhinge", -1.57, -1.57, 0.0)
    closed_right = JointReading("rightdoorhinge", 0.0, 0.0, 1.57)
    assert read_fixture_states([open_left], DOOR) == {"door_open": True}
    assert read_fixture_states([open_left, closed_right], DOOR) == {"door_open": None}


def test_angle_band_and_unmatched_keys():
    spec = {"turned_on": {"joint": "^knob_.*_joint$", "rule": "angle_band",
                          "params": {"low": 0.35, "high": 5.93}}}
    off = JointReading("knob_front_left_joint", 0.0, 0.0, 3.14)
    on = JointReading("knob_rear_left_joint", 1.0, 0.0, 3.14)
    assert read_fixture_states([off], spec) == {"turned_on": False}
    assert read_fixture_states([off, on], spec) == {"turned_on": True}
    assert read_fixture_states([JointReading("lever", 1.0, 0.0, 1.0)], spec) == {}


def test_fetch_keeps_sim_files_only_for_scenario_episodes():
    want = robocasa_fetch._want_member
    assert want(["lerobot", "extras", "episode_000001", "states.npz"], sim_episodes=2)
    assert not want(["lerobot", "extras", "episode_000002", "states.npz"], sim_episodes=2)
    assert not want(["lerobot", "extras", "episode_000000", "model.xml.gz"], sim_episodes=0)
    assert want(["lerobot", "extras", "episode_000005", "ep_meta.json"], sim_episodes=0)


def enable_tier_b(settings):
    """Tier B를 켜고 가짜 캐시의 에피소드 0에만 시뮬레이터 파일을 둔다 (에피소드 1은 Tier A로 남는다)."""
    settings.source.options["robocasa365"]["sim_state"] = True
    for task, (split, family) in FAKE_TASKS.items():
        task_dir = robocasa_fetch.task_cache_dir(settings, split, family, task)
        (task_dir / robocasa_fetch.SIM_MARKER).touch()
        write_sim_files(task_dir / "extras" / "episode_000000")


def test_adapter_tier_b_fills_pose_and_states(settings):
    enable_tier_b(settings)
    records = list(robocasa_adapter(settings).iter_scenarios())
    tier_b = [r.scene_state for r in records if r.source.episode_index == 0]
    tier_a = [r.scene_state for r in records if r.source.episode_index == 1]  # 파일 없음

    scene = tier_b[0]
    assert scene.provenance.extraction_tier == "B"
    assert scene.objects[0].pose == {"pos": MUG_POSE[:3], "quat": MUG_POSE[3:]}
    by_category = {f.category: f for f in scene.fixtures}
    assert by_category["microwave"].states == {"door_open": True}
    assert by_category["microwave"].state_source == "sim"
    assert by_category["drawer"].states == {"drawer_open": False}  # 서랍은 서랍 열림으로 부른다
    assert by_category["counter"].states == {}  # 관절 없는 가구는 그대로
    assert scene.provenance.sim_state_keys == {
        by_category["microwave"].id: ["door_open"], by_category["drawer"].id: ["drawer_open"]}
    assert all(s.provenance.extraction_tier == "A" and s.objects[0].pose is None for s in tier_a)


def test_ingest_rebuilds_only_records_of_another_tier(settings):
    """같은 id라도 추출 단계가 설정과 다르면 새로 만든 기록으로 바꾸고, 같으면 그대로 둔다."""
    first = STAGES.get("ingest")(settings)
    assert first["replaced"] == 0
    assert STAGES.get("ingest")(settings)["written"] == 0
    enable_tier_b(settings)
    second = STAGES.get("ingest")(settings)
    assert (second["written"], second["replaced"], second["total"]) == (0, 2, first["total"])
    tiers = [r["scene_state"]["provenance"]["extraction_tier"]
             for r in read_jsonl(scenarios_path(settings))]
    assert sorted(tiers) == ["A", "A", "B", "B"]
    assert STAGES.get("ingest")(settings)["replaced"] == 0


def test_counters_next_to_a_fixture_follow_the_source_rule():
    """기준 가구를 품은 조리대가 있으면 그것, 없으면 가장 가까운 조리대와 그 거리에서 tie_m 안쪽인 조리대."""
    import numpy

    from tsdgen.adapters.robocasa_adjacency import counters_next_to

    def box(x, y, half=0.5):
        return (numpy.array([x, y, 0.0]), numpy.eye(3), numpy.array([half, half, 0.1]))

    counters = {"left": box(-1.2, 0), "right": box(1.25, 0), "far": box(5, 0)}
    assert counters_next_to(box(0, 0, 0.2), counters, 0.10) == ["left", "right"]
    assert counters_next_to(box(0, 0, 0.2), counters, 0.01) == ["left"]
    assert counters_next_to(box(5.1, 0.1, 0.2), counters, 0.10) == ["far"]  # 품고 있는 조리대
