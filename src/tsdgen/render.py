"""TSD JSON → 결정론 한국어 산문 렌더러.

서술 순서는 고정이다(확정 설계 3절 4번): 목표 → 시공간 조건 → 대상 물체
(필수 → 핵심 수단 → 보조) → 제약. 목표 상태는 작업 목표를 기계가 확인할 수 있게 옮긴
것이라 목표 절 안에 둔다. 모델이 쓴 산문이 아니라 JSON에서 파생한다.
"""

from tsdgen.goals.kinds import entity_label
from tsdgen.goals.verifier import render_goal_lines
from tsdgen.models import TaskSituationDescription
from tsdgen.models.tsd import SpaceCondition

ABSENT = "없음"
GOAL = "작업 목표:"
GOAL_STATE = "목표 상태:"
CONDITIONS = "시간·공간 조건:"
OBJECTS = "대상 물체:"
CONSTRAINTS = "제약 사항"
# 산문에 반드시 이 순서로 나오는 절 제목. 구조 검사가 같은 목록으로 순서를 확인한다.
SECTION_ORDER = (GOAL, GOAL_STATE, CONDITIONS, OBJECTS, CONSTRAINTS)


def _details(location: str | None, state: str | None = None,
             alternatives: list[str] | None = None, note: str | None = None) -> str:
    """'(위치: ..., 상태: ..., 대안: ..., 비고: ...)'. 비어 있는 칸은 적지 않고, 다 비면 괄호도 없다."""
    parts = [f"{label}: {value}" for label, value in (
        ("위치", location), ("상태", state),
        ("대안", ", ".join(alternatives or [])), ("비고", note)) if value]
    return f"({', '.join(parts)})" if parts else ""


def space_text(space: SpaceCondition | None) -> str | None:
    """'시작 ..., 종료 ...'. 둘 다 비면 None."""
    parts = [f"{label} {value}" for label, value in (
        ("시작", space.start if space else None), ("종료", space.end if space else None)) if value]
    return ", ".join(parts) or None


def _listed(title: str, items: list[str]) -> list[str]:
    """제목 한 줄 + 항목 줄들. 항목이 없으면 '제목 없음' 한 줄."""
    if not items:
        return [f"{title} {ABSENT}"]
    return [title, *(f"- {item}" for item in items)]


def render_tsd_text(tsd: TaskSituationDescription, lexicon: dict,
                    names: dict[str, str] | None = None) -> str:
    """names는 id -> 이름. 설명문이 이름으로 가리키는 보기(text)면 비워 둔다."""
    labels = lexicon["constraint_type_ko"]
    st = tsd.spatiotemporal_conditions
    names = names or {}
    lines = [f"{GOAL} {tsd.task_goal.summary}"]
    lines += _listed(GOAL_STATE, render_goal_lines(tsd.task_goal, names, lexicon))
    lines += [
        f"{CONDITIONS} 시간 조건은 {st.time or ABSENT}, 공간 조건은 {space_text(st.space) or ABSENT}",
        OBJECTS,
    ]
    to = tsd.target_objects
    lines += [
        f"- 필수 물체: {entity_label(o.ref, names)}{_details(o.location, o.state)}"
        for o in to.required
    ]
    lines += [
        f"- 핵심 수단: {entity_label(o.ref, names)}{_details(o.location, o.state, o.alternatives)}"
        for o in to.key_means
    ]
    lines += [
        f"- 보조 물체: {entity_label(o.ref, names)}{_details(o.location, note=o.note)}"
        for o in to.auxiliary
    ]
    constraints = [f"[{labels[c.type]}] {c.description}" for c in tsd.constraints]
    lines += _listed(f"{CONSTRAINTS}:", constraints)
    return "\n".join(lines) + "\n"
