"""RoboCasa365 시연 첫 프레임의 시뮬레이터 상태 읽기 (Tier B, opt-in).

배포 tar의 extras/episode_*/에는 ep_meta.json 옆에 MuJoCo 모델(model.xml.gz)과 시연 전 구간의
상태 행렬(states.npz)이 있다. 상태 한 행은 [시각, qpos(nq), qvel(nv)]이고 첫 행이 태스크 초기화
직후의 장면이다. 여기서 물체 위치·자세와 가구 관절 값을 읽는다.

모델의 메시·텍스처는 배포에 들어 있지 않으므로 컴파일 전에 지운다. 관절 배치(qpos 주소)는
메시와 무관해서 위치·관절 값은 그대로 맞다. 반대로 접촉 판정은 메시가 있어야 하므로 여기서
만들지 않는다(in_contact 관계를 내지 않는 이유).

가구 상태 판정 기준은 RoboCasa fixture 코드를 그대로 옮긴 것이고, 값은 어댑터 자원
(resources/adapters/robocasa365.yaml의 sim_states)에 있다. 판정 방식은 SIM_STATE_RULES 등록으로 고른다.
"""

import gzip
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tsdgen.registry import Registry

MODEL_FILE = "model.xml.gz"
STATES_FILE = "states.npz"
STATES_KEY = "states"
SIM_FILES = frozenset({MODEL_FILE, STATES_FILE})
_STRIPPED_ASSETS = ("mesh", "texture", "hfield")  # 배포에 파일이 없는 자산
_PLACEHOLDER_GEOM = {"type": "sphere", "size": "0.005"}  # 메시 대신 넣는 자리표시 (관절 배치와 무관)
FREE_JOINT_POS, FREE_JOINT_WIDTH = 3, 7  # 자유 관절 qpos: 위치 3 + 쿼터니언 4 (MuJoCo 규약)

SIM_STATE_RULES = Registry("robocasa sim state rule", axes=())
"""이름 -> (관절 값 목록, params) -> True / False / None(판단 불가).

None은 상태 키를 비워 둔다는 뜻이다. 반쯤 열린 문처럼 원본 기준으로 열림도 닫힘도 아닌 경우다."""


@dataclass(frozen=True)
class JointReading:
    """가구 관절 하나의 첫 프레임 값과 가동 범위."""

    name: str  # 가구 이름을 뗀 관절 이름 (예: microjoint, knob_front_left_joint)
    value: float
    low: float
    high: float


def _open_fraction(joint: JointReading) -> float:
    """RoboCasa Fixture.get_joint_state와 같은 정규화: 0은 닫힘, 1은 완전 열림."""
    fraction = (joint.value - joint.low) / (joint.high - joint.low)
    return 1 - fraction if joint.low < 0 else fraction


@SIM_STATE_RULES.register("door_fraction")
def _door_fraction(joints: list[JointReading], params: dict) -> bool | None:
    """모든 문이 open_min 이상이면 열림, 모두 closed_max 이하면 닫힘 (fixture.py is_open/is_closed)."""
    fractions = [_open_fraction(j) for j in joints if j.high > j.low]
    if not fractions:
        return None
    if all(f >= params["open_min"] for f in fractions):
        return True
    if all(f <= params["closed_max"] for f in fractions):
        return False
    return None


@SIM_STATE_RULES.register("door_between")
def _door_between(joints: list[JointReading], params: dict) -> bool | None:
    """원본 기준으로 열림도 닫힘도 아닌 문(일부만 열림)이면 참, 그 밖에는 값을 내지 않는다.
    눈에 띄지 않을 만큼만 벌어진 문(가장 많이 열린 문이 partly_min 미만)은 일부 열림으로 적지 않는다."""
    fractions = [_open_fraction(j) for j in joints if j.high > j.low]
    decided = _door_fraction(joints, params) is not None
    if not fractions or decided or max(fractions) < params["partly_min"]:
        return None
    return True


@SIM_STATE_RULES.register("angle_band")
def _angle_band(joints: list[JointReading], params: dict) -> bool | None:
    """한 관절이라도 각도(한 바퀴로 접은 값)가 [low, high] 안이면 켜짐 (stove.py 손잡이, sink.py 수전)."""
    if not joints:
        return None
    return any(params["low"] <= j.value % math.tau <= params["high"] for j in joints)


def _strip_assets(xml_text: str) -> str:
    root = ET.fromstring(xml_text)
    for asset in root.iter("asset"):
        for element in [e for e in asset if e.tag in _STRIPPED_ASSETS]:
            asset.remove(element)
        for material in asset.iter("material"):
            material.attrib.pop("texture", None)
    for geom in root.iter("geom"):
        if geom.attrib.pop("mesh", None) is not None:
            geom.attrib.update(_PLACEHOLDER_GEOM)
    return ET.tostring(root, encoding="unicode")


class SimSnapshot:
    """에피소드 하나의 첫 프레임. 파일이 없으면 load가 None을 돌려준다."""

    def __init__(self, mujoco: Any, model: Any, qpos: Any):
        self._mj = mujoco
        self._model = model
        self._qpos = qpos

    @classmethod
    def load(cls, episode_dir: Path) -> "SimSnapshot | None":
        if not all((episode_dir / name).exists() for name in SIM_FILES):
            return None
        # Tier B에서만 필요한 선택 의존성이라 여기서 불러온다 (pip install -e .[sim])
        import mujoco
        import numpy

        xml_text = gzip.decompress((episode_dir / MODEL_FILE).read_bytes()).decode("utf-8")
        model = mujoco.MjModel.from_xml_string(_strip_assets(xml_text))
        states = numpy.load(episode_dir / STATES_FILE)[STATES_KEY]
        expected = 1 + model.nq + model.nv
        if states.shape[1] < expected:
            raise ValueError(f"{episode_dir}: state width {states.shape[1]} < 1+nq+nv={expected}")
        return cls(mujoco, model, states[0, 1 : 1 + model.nq])

    def object_pose(self, body_name: str, decimals: int) -> dict[str, list[float]] | None:
        """자유 관절을 가진 물체의 월드 좌표 위치(pos)와 자세(quat, w x y z). 없으면 None."""
        mujoco = self._mj
        body = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        if body < 0 or self._model.body_jntnum[body] < 1:
            return None
        joint = self._model.body_jntadr[body]
        if int(self._model.jnt_type[joint]) != int(mujoco.mjtJoint.mjJNT_FREE):
            return None
        adr = self._model.jnt_qposadr[joint]
        values = [round(float(v), decimals) for v in self._qpos[adr : adr + FREE_JOINT_WIDTH]]
        return {"pos": values[:FREE_JOINT_POS], "quat": values[FREE_JOINT_POS:]}

    def fixture_joints(self, fixture_names: list[str]) -> dict[str, list[JointReading]]:
        """가구 이름 -> 그 가구의 1자유도 관절들. 관절 이름은 '<가구 이름>_<관절>' 꼴이고,
        접두어가 겹치면(cab_1_main_group, cab_1_main_group_1) 가장 긴 이름에 붙인다."""
        mujoco = self._mj
        by_length = sorted(fixture_names, key=len, reverse=True)
        found: dict[str, list[JointReading]] = {name: [] for name in fixture_names}
        # 모델 배열은 numpy 정수라 enum과 `in`으로 비교하면 항상 거짓이다. 정수로 맞춘다.
        one_dof = {int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE)}
        for joint in range(self._model.njnt):
            if int(self._model.jnt_type[joint]) not in one_dof:
                continue
            name = mujoco.mj_id2name(self._model, mujoco.mjtObj.mjOBJ_JOINT, joint) or ""
            owner = next((f for f in by_length if name.startswith(f + "_")), None)
            if owner is None:
                continue
            low, high = (float(v) for v in self._model.jnt_range[joint])
            value = float(self._qpos[self._model.jnt_qposadr[joint]])
            found[owner].append(JointReading(name[len(owner) + 1 :], value, low, high))
        return found


    def _named(self, kind: Any, count: int) -> list[tuple[int, str]]:
        return [(i, self._mj.mj_id2name(self._model, kind, i) or "") for i in range(count)]

    def fixture_regions(self, fixture_names: list[str], markers: dict[str, str],
                        ) -> dict[str, list[tuple[str, float]]]:
        """가구 이름 -> [(칸 이름, 높이)]. 칸은 모델에 '<가구>_reg_<칸>' geom이나 '<가구>_burner_<칸>_place_site'
        site로 들어 있다. markers는 {"geom": 정규식, "site": 정규식}이고 각 정규식의 region 그룹이 칸 이름이다.
        높이는 가구 기준 좌표의 z라서 같은 가구 안에서 위아래 순서를 정하는 데만 쓴다."""
        mujoco = self._mj
        by_length = sorted(fixture_names, key=len, reverse=True)
        model, kinds = self._model, mujoco.mjtObj
        sources = {"geom": (self._named(kinds.mjOBJ_GEOM, model.ngeom), model.geom_pos),
                   "site": (self._named(kinds.mjOBJ_SITE, model.nsite), model.site_pos)}
        found: dict[str, list[tuple[str, float]]] = {name: [] for name in fixture_names}
        for kind, pattern in markers.items():
            names, positions = sources[kind]
            for index, name in names:
                owner = next((f for f in by_length if name.startswith(f + "_")), None)
                match = re.fullmatch(pattern, name[len(owner) + 1:]) if owner else None
                if match:
                    found[owner].append((match.group("region"), round(float(positions[index][2]), 3)))
        return found

    def fixture_boxes(self, fixture_names: list[str], geom_suffixes: list[str]) -> dict[str, tuple]:
        """가구 이름 -> 겉 상자 (월드 좌표 중심, 회전 행렬, 절반 길이). 모델의 '<가구><꼬리>' 상자 geom에서 읽고,
        꼬리는 앞의 것부터 찾는다(RoboCasa get_ext_sites: main, 없으면 bbox). 상자가 없는 가구는 뺀다."""
        mujoco = self._mj
        data = mujoco.MjData(self._model)
        data.qpos[:] = self._qpos
        mujoco.mj_kinematics(self._model, data)
        boxes = {}
        for name in fixture_names:
            ids = (mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_GEOM, name + suffix)
                   for suffix in geom_suffixes)
            geom = next((g for g in ids if g >= 0), -1)
            if geom >= 0:
                rotation = data.geom_xmat[geom].reshape(3, 3).copy()
                half = self._model.geom_size[geom].copy()
                boxes[name] = (data.geom_xpos[geom].copy(), rotation, half)
        return boxes

    def object_size(self, object_name: str, geom_suffix: str, decimals: int) -> list[float] | None:
        """물체의 크기(가로, 세로, 높이, m). 모델의 '<물체><꼬리>' 상자 geom(절반 길이)에서 읽는다."""
        mujoco = self._mj
        geom = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_GEOM, object_name + geom_suffix)
        if geom < 0:
            return None
        return [round(2 * float(v), decimals) for v in self._model.geom_size[geom]]


def read_region_states(joints: list[JointReading], specs: dict[str, dict]) -> dict[str, dict]:
    """칸마다 따로 있는 관절(화구 손잡이)에서 칸별 상태를 읽는다. 칸 이름 -> {상태 키: 값}.
    명세의 joint 정규식은 region 그룹으로 칸 이름을 준다."""
    states: dict[str, dict] = {}
    for key, spec in specs.items():
        pattern = re.compile(spec["joint"], re.IGNORECASE)
        for joint in joints:
            match = pattern.search(joint.name)
            if match:
                value = SIM_STATE_RULES.get(spec["rule"])([joint], spec.get("params") or {})
                if value is not None:
                    states.setdefault(match.group("region"), {})[key] = value
    return states


def read_fixture_states(
    joints: list[JointReading], specs: dict[str, dict]
) -> dict[str, bool | None]:
    """판정 명세(상태 키 -> {joint, rule, params})로 관절 값에서 가구 상태를 읽는다.
    해당 관절이 없는 키는 빼고, 관절은 있는데 판단할 수 없으면 None으로 둔다."""
    states: dict[str, bool | None] = {}
    for key, spec in specs.items():
        if not spec.get("joint") or not spec.get("rule"):
            continue
        pattern = re.compile(spec["joint"], re.IGNORECASE)
        matched = [j for j in joints if pattern.search(j.name)]
        if matched:
            states[key] = SIM_STATE_RULES.get(spec["rule"])(matched, spec.get("params") or {})
    return states
