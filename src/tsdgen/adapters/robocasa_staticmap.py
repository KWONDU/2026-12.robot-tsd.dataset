"""RoboCasa 태스크 소스 정적 분석 (스펙 4.1.1의 2단계).

산출물 세 개를 static_map_dir에 만든다.
- task_index.yaml: 태스크명 → family(모듈 경로에서 도출)·activity·모듈 경로
- fixture_state_map.yaml: 태스크 클래스 → fixture_ref → 초기 상태 + 근거 코드
  위치. 사람이 검토할 수 있도록 근거 호출문을 함께 기록한다.
- success_map.yaml: 태스크 클래스 → 성공 판정에서 뽑은 목표 상태 정답 (robocasa_success.py)

한계(정찰 기록 문서 참고): behavior 인자로 분기하는 기반 클래스의 호출은 구체
태스크로 귀속되지 않는다. 같은 클래스에서 상충하는 호출이 발견되면 상태를 비워
두고 근거만 남기며, 적용은 ep_meta.fixture_refs와 ref 이름이 일치할 때만 한다.
"""

import re
from pathlib import Path
from typing import Any

import yaml

from tsdgen.adapters.robocasa_fetch import fetch_task_source_tree
from tsdgen.adapters.robocasa_success import build_success_map
from tsdgen.config import Settings
from tsdgen.log import get_logger
from tsdgen.registry import Registry
from tsdgen.resources import load_adapter_resource

log = get_logger("tsdgen.adapters.robocasa365.staticmap")

ADAPTER = "robocasa365"
TASK_INDEX_FILE = "task_index.yaml"
STATE_MAP_FILE = "fixture_state_map.yaml"
SUCCESS_MAP_FILE = "success_map.yaml"
LAYOUT_MAP_FILE = "layout_map.yaml"
TRAITS_FILE = "object_traits.yaml"
# 시뮬레이터 저장소의 물체 등록부. 종류마다 types와 graspable·washable 같은 성질이 적혀 있다
_OBJECT_REGISTRY = "robocasa/robocasa/models/objects/kitchen_objects.py"
_CATEGORY_RE = re.compile(r"^    (\w+)=dict\(\n(.*?)\n    \),", re.S | re.M)
_TYPES_RE = re.compile(r"types=\(([^)]*)\)")
_TRAIT_RE = re.compile(r"(\w+)=True")
# 시뮬레이터 저장소 안의 주방 배치 파일 (layoutNNN.yaml, 번호가 ep_meta의 layout_id)
_LAYOUT_GLOB = "robocasa/robocasa/models/assets/scenes/kitchen_layouts/*/layout*.yaml"
_OPENING_KEY = "base_opening"  # 조리대 아래가 트여 스툴이 들어가는 쪽 [앞, 뒤]

_CLASS_RE = re.compile(r"^class\s+(\w+)\s*\(", re.MULTILINE)
_KNOB_MODE_RE = re.compile(r"mode\s*=\s*\"(\w+)\"")
_COMPARTMENT_RE = re.compile(r"compartment\s*=\s*\"(\w+)\"")
_REG_TYPE_RE = re.compile(r"reg_type\s*=\s*\"(\w+)\"")
# 냉장고 open_door/close_door의 compartment 인자 -> 상태 키 (fridge.py, 기본은 fridge).
# 냉동실 문을 냉장고 문 열림으로 적던 오류를 시뮬레이터 대조에서 찾아 고쳤다.
_COMPARTMENT_KEYS = {"fridge": "door_open", "freezer": "freezer_door_open"}

STATE_CALLS = Registry("robocasa state-setting call")
"""robocasa 태스크 소스의 메서드 이름 -> (인자 문자열) -> 우리 상태 dict.

해석할 호출을 늘리는 일이 등록 한 줄로 끝나고, 아래 정규식도 등록된 이름에서 만들어진다.
빈 dict를 돌려주면 상태는 채우지 않고 근거만 남긴다."""


def _door_state(args: str, value: bool) -> dict:
    """문 상태 호출의 상태 키. 냉장고 서랍 선반(reg_type="drawer")은 문이 아니라 채우지 않는다."""
    reg_type = _REG_TYPE_RE.search(args)
    if reg_type and reg_type.group(1) != "door":
        return {}
    compartment = _COMPARTMENT_RE.search(args)
    key = _COMPARTMENT_KEYS.get(compartment.group(1)) if compartment else "door_open"
    return {key: value} if key else {}


@STATE_CALLS.register("open_door")
def _open_door(args: str) -> dict:
    return _door_state(args, True)


@STATE_CALLS.register("close_door")
def _close_door(args: str) -> dict:
    return _door_state(args, False)


@STATE_CALLS.register("set_knob_state")
def _set_knob_state(args: str) -> dict:
    mode = _KNOB_MODE_RE.search(args)
    return {"turned_on": mode.group(1) != "off"} if mode else {}


@STATE_CALLS.register("set_door_state")
def _set_door_state(_args: str) -> dict:
    return {}  # 인자 형태가 다양해 해석하지 않는다. 근거만 남긴다.


def _state_call_pattern() -> re.Pattern:
    methods = "|".join(STATE_CALLS.names())
    return re.compile(
        rf"self\.(?P<ref>\w+)\.(?P<method>{methods})\(\s*(?P<args>[^)]*)\)", re.DOTALL
    )


_STATE_CALL_RE = _state_call_pattern()

# 레포 구조가 바뀐 적이 있어(예전 single_stage/multi_stage) 두 명명을 모두 받는다
_FAMILY_DIRS = {"atomic": "atomic", "single_stage": "atomic",
                "composite": "composite", "multi_stage": "composite"}


def _family_of(rel: str) -> tuple[str, str | None] | None:
    parts = Path(rel).parts
    for i, part in enumerate(parts):
        if part in _FAMILY_DIRS:
            family = _FAMILY_DIRS[part]
            activity = parts[i + 1] if family == "composite" and len(parts) > i + 2 else None
            return family, activity
    return None


def _merge_call(entry: dict, states: dict, evidence: dict) -> None:
    entry["evidence"].append(evidence)
    for key, value in states.items():
        if key in entry["states"] and entry["states"][key] != value:
            # 상충 호출(behavior 분기 등): 자동 적용하지 않고 근거만 남긴다
            entry["states"].pop(key)
            entry["conflicting"] = True
        elif not entry.get("conflicting"):
            entry["states"][key] = value


def _scan_file(path: Path, rel: str, task_index: dict, state_map: dict) -> None:
    family_activity = _family_of(rel)
    if family_activity is None:
        return
    family, activity = family_activity
    text = path.read_text(encoding="utf-8", errors="replace")
    classes = [(m.start(), m.group(1)) for m in _CLASS_RE.finditer(text)]
    for _, name in classes:
        task_index[name] = {"family": family, "activity": activity, "module": rel}

    for match in _STATE_CALL_RE.finditer(text):
        owner = None
        for start, name in classes:
            if start < match.start():
                owner = name
        if owner is None:
            continue
        states = STATE_CALLS.get(match.group("method"))(match.group("args"))
        evidence = {
            "file": rel,
            "line": text.count("\n", 0, match.start()) + 1,
            "call": " ".join(match.group(0).split())[:200],
        }
        entry = state_map.setdefault(owner, {}).setdefault(
            match.group("ref"), {"states": {}, "evidence": []}
        )
        _merge_call(entry, states, evidence)


def _open_based(node: Any) -> list[str]:
    """배치 묶음 하나에서 아래가 트인 조리대의 이름들."""
    if isinstance(node, list):
        return [name for item in node for name in _open_based(item)]
    if not isinstance(node, dict):
        return []
    own = [node["name"]] if node.get("name") and any(node.get(_OPENING_KEY) or []) else []
    return own + [name for value in node.values() for name in _open_based(value)]


def build_layout_map(settings: Settings, options: Any) -> dict:
    """배치 번호 -> 식사용 조리대의 가구 이름. RoboCasa는 이름이 island·dining으로 시작하거나 아래가 트인
    조리대를 식사용 조리대(dining counter)로 본다(fixture_utils.fixture_is_type). 앞의 것은 이름에서 알 수
    있고, 뒤의 것은 배치 파일에만 있어 여기서 뽑아 둔다. 시뮬레이터 저장소가 없으면 빈 표다."""
    simulator = getattr(options, "simulator", None)
    if simulator is None:
        return {}
    layouts: dict = {}
    for path in sorted((settings.root / simulator.root).glob(_LAYOUT_GLOB)):
        blueprint = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        names = [f"{name}_{group}" for group, body in blueprint.items() for name in _open_based(body)]
        if names:
            layouts[int(re.sub(r"\D", "", path.stem))] = {"dining_counters": sorted(names)}
    return layouts


def build_object_traits(settings: Settings, options: Any) -> dict:
    """물체 종류 -> 성질 이름들 (집을 수 있는지, 씻을 수 있는지, 전자레인지에 넣을 수 있는지 등과 분류).
    RoboCasa 물체 등록부에 적힌 그대로다. 작업 합성이 말이 되는 조합만 만드는 데 쓴다."""
    simulator = getattr(options, "simulator", None)
    path = settings.root / simulator.root / _OBJECT_REGISTRY if simulator else None
    if path is None or not path.exists():
        return {}
    traits = {}
    for match in _CATEGORY_RE.finditer(path.read_text(encoding="utf-8")):
        types = _TYPES_RE.search(match.group(2))
        names = [t.strip().strip("\"'") for t in types.group(1).split(",")] if types else []
        traits[match.group(1)] = sorted({*filter(None, names), *_TRAIT_RE.findall(match.group(2))})
    return traits


def build_static_maps(settings: Settings, options: Any) -> dict:
    """태스크 색인과 fixture 초기 상태 표를 만든다. RoboCasa 어댑터의 prepare()가 부른다."""
    src_dir = fetch_task_source_tree(settings, options)
    task_index: dict = {}
    state_map: dict = {}
    for path in sorted(src_dir.rglob("*.py")):
        _scan_file(path, str(path.relative_to(src_dir)), task_index, state_map)

    vocabulary = load_adapter_resource(settings.path("resources"), ADAPTER)
    success_map = build_success_map(src_dir, vocabulary.get("success_state_keys") or {})

    out_dir = settings.path("static_map_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        TASK_INDEX_FILE: task_index, STATE_MAP_FILE: state_map, SUCCESS_MAP_FILE: success_map,
        LAYOUT_MAP_FILE: build_layout_map(settings, options),
        TRAITS_FILE: build_object_traits(settings, options),
    }
    for file_name, content in outputs.items():
        (out_dir / file_name).write_text(
            yaml.safe_dump(content, allow_unicode=True, sort_keys=True), encoding="utf-8"
        )
    with_goal = sum(1 for entry in success_map.values() if entry["alternatives"])
    log.info(f"tasks={len(task_index)} state-mapped={len(state_map)} "
             f"goal-oracle={with_goal}/{len(success_map)} -> {out_dir}")
    return {
        "tasks": len(task_index),
        "state_mapped_tasks": len(state_map),
        "goal_oracle_tasks": with_goal,
        "outputs": [str(out_dir / name) for name in outputs],
    }
