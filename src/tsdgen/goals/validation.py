"""목표 상태의 규칙 검사 (LLM 불필요).

검사하는 것:
- 형식과 접지: 단언 종류가 등록되어 있는가, 주어·대상 id가 씬에 있는가, 술어·상태 키가 어휘 안인가.
- 설명문과의 일관성: 목표 단언이 가리키는 entity가 대상 물체(필수·핵심 수단·보조)에 올라 있는가.
- 역할: 역할은 목표 상태에서 따라 나온다. 목표 단언의 주어(위치나 상태가 바뀌는 것)는 필수, 성립해야 하는
  위·안 관계의 대상(받는 곳)은 핵심 수단이다(그 자신이 주어이기도 하면 필수). 같은 입력이면 같은 역할이 나오게 하는 규칙이다.
- 표현 누락: 목표 상태도, 상태로 쓸 수 없는 기준(시간 조건, other 제약)도 없으면 완료를 판정할 근거가 없다.
- 무의미한 목표: 초기 씬에서 이미 목표 상태가 성립하는데 그 밖에 남은 기준(시간 조건, other 제약)도 없으면
  지시를 수행할 일이 없다는 뜻이라 잘못 옮긴 목표로 본다.
"""

from typing import Any

from tsdgen.goals.kinds import GOAL_KINDS, SUPPORT_PREDICATES, target_roles
from tsdgen.goals.scene_index import SceneIndex
from tsdgen.goals.verifier import SATISFIED, verify_goal
from tsdgen.models import GoalCheckResult, TaskGoal, TaskSituationDescription


def _assertion_problems(goal: TaskGoal, index: SceneIndex, lexicon: dict, targets: set) -> list:
    found = []
    places = [(f"goal_state[{c}][{i}]", a) for c, case in enumerate(goal.goal_state)
              for i, a in enumerate(case)]
    for where, assertion in places:
        if assertion.kind not in GOAL_KINDS.names():
            found.append(f"{where}: unknown kind '{assertion.kind}'; allowed {GOAL_KINDS.names()}")
            continue
        kind = GOAL_KINDS.get(assertion.kind)
        found += [f"{where}: {p}" for p in kind.problems(assertion, index, lexicon)]
        outside = [ref for ref in kind.refs(assertion) if ref not in targets]
        found += [f"{where}: '{ref}' is not listed in target_objects" for ref in outside]
    return found


def _role_problems(goal: TaskGoal, roles: dict[str, str]) -> list:
    """목표 상태에서 따라 나오는 역할과 설명문의 역할이 다른 곳."""
    assertions = [a for case in goal.goal_state for a in case]
    subjects = {a.subject for a in assertions}
    receivers = {a.object for a in assertions
                 if a.kind == "relation" and a.predicate in SUPPORT_PREDICATES and a.value and a.object}
    found = [f"'{ref}' is a goal_state subject, so its role must be required (is {roles[ref]})"
             for ref in sorted(subjects) if roles.get(ref) not in (None, "required")]
    # 받는 곳은 핵심 수단이다. 그 자체가 조작 대상(데울 냄비)이라 필수로 올린 것은 앞선 역할이라 맞다
    found += [f"'{ref}' receives objects in goal_state, so its role must be key_means (is {roles[ref]})"
              for ref in sorted(receivers - subjects) if roles.get(ref) == "auxiliary"]
    return found


STATE_FREE_CONSTRAINT = "other"  # 목표 상태로 쓸 수 없는 기준(정도·과정)을 적는 제약 유형


def _state_free_criteria(tsd: TaskSituationDescription) -> bool:
    """상태로 확인할 수 없는 기준이 하나라도 있는가: 시간 조건, 또는 정도·과정을 적은 제약."""
    return bool(tsd.spatiotemporal_conditions.time) or any(
        c.type == STATE_FREE_CONSTRAINT for c in tsd.constraints)


def check_goal(
    tsd: TaskSituationDescription, env: dict[str, Any], lexicon: dict
) -> GoalCheckResult:
    goal = tsd.task_goal
    index = SceneIndex.from_env(env)
    roles = target_roles(tsd.target_objects)
    problems = _assertion_problems(goal, index, lexicon, set(roles))
    problems += _role_problems(goal, roles)
    if not goal.goal_state and not _state_free_criteria(tsd):
        problems.append("neither goal_state nor a state-free criterion (time, other constraint) is given")

    initial = verify_goal(goal, env)
    trivial = initial.status == SATISFIED and not _state_free_criteria(tsd)
    return GoalCheckResult(
        problems=problems,
        initial_status=initial.status,
        assertion_status=initial.assertion_status,
        trivial=trivial,
        **{"pass": not problems and not trivial},
    )
