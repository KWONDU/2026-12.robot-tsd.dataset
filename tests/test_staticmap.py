"""RoboCasa 어댑터의 prepare(): 시뮬레이터 소스 정적 분석.

가구 초기 상태는 원본 데이터에 없고 이 코드가 시뮬레이터 소스에서 역산한다. 여기가
틀리면 씬 정보가 조용히 잘못되고, 접지 검사는 "설명문이 씬과 일치하는가"만 보므로
끝까지 통과해 버린다. 검수기가 잡아 줄 수 없는 자리라 여기에 테스트를 둔다.
"""

import textwrap
from pathlib import Path

import pytest
import yaml
from tests.conftest import robocasa_adapter

from tsdgen.adapters import robocasa_staticmap as staticmap
from tsdgen.registry import STAGES

ATOMIC_SOURCE = '''
class ManipulateDoor(Kitchen):
    def _setup_scene(self):
        if self.behavior == "open":
            self.fxtr.close_door(env=self)
        elif self.behavior == "close":
            self.fxtr.open_door(env=self)


class OpenCabinet(ManipulateDoor):
    def _setup_scene(self):
        self.cab.close_door(env=self)
'''

COMPOSITE_SOURCE = '''
class BoilEggs(Kitchen):
    def _setup_scene(self):
        self.stove.set_knob_state(mode="high", knob=self.knob, env=self, rng=self.rng)
        self.cab.open_door(env=self)

    def _check_success(self):
        egg_in_pot = OU.check_obj_in_receptacle(self, "egg", "pot")
        return egg_in_pot and self.cab.is_closed(env=self)
'''


@pytest.fixture()
def source_tree(settings, monkeypatch) -> Path:
    root = settings.path("static_map_dir") / "task_src"
    (root / "atomic").mkdir(parents=True)
    (root / "composite" / "boiling").mkdir(parents=True)
    (root / "atomic" / "kitchen_doors.py").write_text(
        textwrap.dedent(ATOMIC_SOURCE), encoding="utf-8"
    )
    (root / "composite" / "boiling" / "boil_eggs.py").write_text(
        textwrap.dedent(COMPOSITE_SOURCE), encoding="utf-8"
    )
    monkeypatch.setattr(staticmap, "fetch_task_source_tree", lambda _s, _o: root)
    return root


def _outputs(settings) -> tuple[dict, dict]:
    out = settings.path("static_map_dir")
    index = yaml.safe_load((out / "task_index.yaml").read_text(encoding="utf-8"))
    states = yaml.safe_load((out / "fixture_state_map.yaml").read_text(encoding="utf-8"))
    return index, states


def test_task_index_derives_family_and_activity(settings, source_tree):
    STAGES.get("prepare")(settings)
    index, _ = _outputs(settings)
    assert index["OpenCabinet"]["family"] == "atomic"
    assert index["OpenCabinet"]["activity"] is None
    assert index["BoilEggs"]["family"] == "composite"
    assert index["BoilEggs"]["activity"] == "boiling"


def test_state_is_attributed_to_the_enclosing_class(settings, source_tree):
    STAGES.get("prepare")(settings)
    _, states = _outputs(settings)
    assert states["OpenCabinet"]["cab"]["states"] == {"door_open": False}
    assert states["BoilEggs"]["stove"]["states"] == {"turned_on": True}
    assert states["BoilEggs"]["cab"]["states"] == {"door_open": True}


def test_conflicting_calls_leave_state_empty(settings, source_tree):
    """behavior로 분기하는 기반 클래스는 열림·닫힘 호출을 둘 다 갖는다.

    둘 중 무엇이 실제인지 정적으로는 알 수 없으므로 상태를 비우고 근거만 남겨야 한다.
    임의로 하나를 고르면 씬 정보가 틀리게 되고 아무도 못 잡는다.
    """
    STAGES.get("prepare")(settings)
    _, states = _outputs(settings)
    entry = states["ManipulateDoor"]["fxtr"]
    assert entry["states"] == {}
    assert entry.get("conflicting") is True
    assert len(entry["evidence"]) == 2


def test_evidence_points_at_real_source_lines(settings, source_tree):
    STAGES.get("prepare")(settings)
    _, states = _outputs(settings)
    evidence = states["OpenCabinet"]["cab"]["evidence"][0]
    line = (source_tree / evidence["file"]).read_text(encoding="utf-8").splitlines()
    assert "close_door" in line[evidence["line"] - 1]


def test_adapter_uses_only_states_matching_fixture_refs(settings, source_tree):
    """정적 분석 결과는 ep_meta의 fixture_refs 이름과 맞을 때만 씬에 반영된다."""

    STAGES.get("prepare")(settings)
    records = list(robocasa_adapter(settings).iter_scenarios())
    # 픽스처의 태스크명(FakeTask)은 위 소스에 없으므로 상태가 채워지면 안 된다
    assert all(f.state_source == "unknown" for r in records for f in r.scene_state.fixtures)


def test_prepare_writes_goal_oracle_from_success_checks(settings, source_tree):
    """성공 판정 코드가 목표 상태 정답 표(success_map.yaml)로 옮겨져야 한다."""
    result = STAGES.get("prepare")(settings)
    assert result["goal_oracle_tasks"] == 1
    success = yaml.safe_load(
        (settings.path("static_map_dir") / "success_map.yaml").read_text(encoding="utf-8"))
    (alternative,) = success["BoilEggs"]["alternatives"]
    facts = [(a["kind"], a["predicate"], a["value"]) for a in alternative]
    assert facts == [("relation", "in", True), ("state", "door_open", False)]


def test_fridge_compartment_and_drawer_racks():
    """냉동실 문은 냉장고 문 열림이 아니고(시뮬레이터 대조로 찾은 오류), 서랍 선반은 문 상태가 아니다."""
    open_door = staticmap.STATE_CALLS.get("open_door")
    assert open_door("env=self") == {"door_open": True}
    assert open_door('env=self, compartment="freezer"') == {"freezer_door_open": True}
    assert open_door('self, min=0.8, reg_type="drawer", drawer_rack_index=-1') == {}
    assert staticmap.STATE_CALLS.get("close_door")('self, compartment="fridge"') == {"door_open": False}
