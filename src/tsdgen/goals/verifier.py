"""목표 판정기: 목표 상태를 씬 상태 하나에 대 보고 만족 / 불만족 / 판단 불가를 돌려준다.

거짓과 모름을 섞지 않는다. 확인했는데 성립하지 않으면 unsatisfied, 씬 정보로 알 수 없으면 unknown이다.
경우 하나 안에서는 단언 하나라도 unsatisfied면 unsatisfied, 전부 satisfied면 satisfied, 나머지는 unknown이다.
목표 상태가 여러 경우면(어느 그릇이든) 한 경우라도 satisfied면 satisfied, 모든 경우가 unsatisfied면 unsatisfied다.
초기 씬에 대면 "이미 이뤄진 목표인가"를, 작업 뒤 관측한 씬에 대면 "완료되었는가"를 판정한다.
"""

from dataclasses import dataclass
from typing import Any

from tsdgen.goals.kinds import GOAL_KINDS, UNKNOWN
from tsdgen.goals.scene_index import SceneIndex
from tsdgen.models import GoalAssertion, GoalStatus, TaskGoal

SATISFIED: GoalStatus = "satisfied"
UNSATISFIED: GoalStatus = "unsatisfied"


@dataclass(frozen=True)
class GoalVerification:
    status: GoalStatus  # 목표 상태 전체
    assertion_status: list[GoalStatus]  # 단언별 (경우들을 차례로 이어 붙인 순서)


def combine(statuses: list[GoalStatus]) -> GoalStatus:
    if not statuses:
        return UNKNOWN
    if UNSATISFIED in statuses:
        return UNSATISFIED
    return SATISFIED if all(s == SATISFIED for s in statuses) else UNKNOWN


def assertion_status(assertion: GoalAssertion, index: SceneIndex) -> GoalStatus:
    if assertion.kind not in GOAL_KINDS.names():
        return UNKNOWN
    holds = GOAL_KINDS.get(assertion.kind).holds(assertion, index)
    if holds is None:
        return UNKNOWN
    return SATISFIED if holds == assertion.value else UNSATISFIED


def assertions_of(goal: TaskGoal) -> list[GoalAssertion]:
    """모든 경우의 단언을 차례대로 (형식·접지 검사와 화면 표기용)."""
    return [a for case in goal.goal_state for a in case]


def _any_case(cases: list[GoalStatus]) -> GoalStatus:
    if SATISFIED in cases:
        return SATISFIED
    return UNSATISFIED if cases and all(c == UNSATISFIED for c in cases) else UNKNOWN


def verify_goal(goal: TaskGoal, env: dict[str, Any]) -> GoalVerification:
    """env는 환경 컨텍스트 규격의 씬 상태 (초기든 관측이든 같은 규격)."""
    index = SceneIndex.from_env(env)
    per_case = [[assertion_status(a, index) for a in case] for case in goal.goal_state]
    per_assertion = [s for case in per_case for s in case]
    status = _any_case([combine(case) for case in per_case]) if per_case else combine([])
    return GoalVerification(status, per_assertion)


def _render(assertion: GoalAssertion, names: dict[str, str], lexicon: dict) -> str:
    """등록되지 않은 종류도 버리지 않고 원문대로 보여 준다."""
    if assertion.kind in GOAL_KINDS.names():
        return GOAL_KINDS.get(assertion.kind).render(assertion, names, lexicon)
    return str(assertion.model_dump())


def render_goal_lines(goal: TaskGoal, names: dict[str, str], lexicon: dict) -> list[str]:
    """목표 상태를 사람이 읽는 줄로. 경우가 하나면 단언마다 한 줄, 여럿이면 경우마다 한 줄."""
    if len(goal.goal_state) == 1:
        return [_render(a, names, lexicon) for a in goal.goal_state[0]]
    return [f"경우 {i}: " + "; ".join(_render(a, names, lexicon) for a in case)
            for i, case in enumerate(goal.goal_state, start=1)]
