"""구조 검사 (규칙 기반, 스펙 6.4의 3단계).

- 단계 목록 탐지: 번호 매긴 실행 계획, "1단계"류, steps류 키
- 세계 상태가 놓인 곳을 알려 주는 물체는 location이 있음 (위치가 없는 가구 등은 비워도 됨)
- 렌더링 순서 준수 (고정 렌더러 출력의 절 순서 확인)
"""

import re

from tsdgen.models import StructureResult, TaskSituationDescription
from tsdgen.render import SECTION_ORDER

_NUMBERED_LINE_RE = re.compile(r"^\s*\d+\s*[.)]\s+", re.MULTILINE)
_STEP_WORD_RE = re.compile(r"\d+\s*단계|단계\s*\d+|(첫|둘|셋|넷)째\s*단계")
_STEP_KEY_RE = re.compile(r"steps|subtasks|actions|sequence", re.IGNORECASE)


def _collect_texts(tsd: TaskSituationDescription, tsd_text: str) -> str:
    parts = [tsd.task_goal.summary, tsd_text]
    parts += [c.description for c in tsd.constraints]
    return "\n".join(parts)


def detect_step_list(tsd: TaskSituationDescription, tsd_text: str, raw_tsd: dict) -> bool:
    text = _collect_texts(tsd, tsd_text)
    if _STEP_WORD_RE.search(text):
        return True
    # 번호 매긴 줄이 2개 이상 연속되면 실행 계획으로 본다 (단일 번호 인용은 허용)
    if len(_NUMBERED_LINE_RE.findall(text)) >= 2:
        return True
    return any(_STEP_KEY_RE.fullmatch(key) for key in _walk_keys(raw_tsd))


def _walk_keys(obj: object) -> list[str]:
    keys: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.append(str(key))
            keys.extend(_walk_keys(value))
    elif isinstance(obj, list):
        for item in obj:
            keys.extend(_walk_keys(item))
    return keys


def check_field_order(tsd_text: str) -> bool:
    positions = [tsd_text.find(section) for section in SECTION_ORDER]
    return all(p >= 0 for p in positions) and positions == sorted(positions)


def check_locations(tsd: TaskSituationDescription, located: set[str]) -> bool:
    """located: 세계 상태가 놓인 곳을 알려 주는 entity id. 그런 대상은 위치를 빠뜨리면 안 된다."""
    to = tsd.target_objects
    return all((e.location or "").strip() for e in [*to.required, *to.key_means, *to.auxiliary]
               if e.ref in located)


def check_structure(
    tsd: TaskSituationDescription, tsd_text: str, raw_tsd: dict, located: set[str]
) -> StructureResult:
    has_step_list = detect_step_list(tsd, tsd_text, raw_tsd)
    field_order_ok = check_field_order(tsd_text)
    locations_ok = check_locations(tsd, located)
    ok = (not has_step_list) and field_order_ok and locations_ok
    return StructureResult(
        has_step_list=has_step_list,
        field_order_ok=field_order_ok,
        all_objects_have_location=locations_ok,
        **{"pass": ok},
    )
