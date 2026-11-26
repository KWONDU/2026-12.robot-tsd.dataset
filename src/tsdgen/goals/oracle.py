"""생성된 목표 상태를 원천의 성공 판정(정답)과 대조한다.

정답 항목 하나는 후보 단언 목록이다(예: "아무 조리대 위"는 씬의 조리대 수만큼 후보). 생성본에 후보 중
하나와 같은 사실을 말하는 단언이 있으면 그 항목을 맞힌 것으로 본다. 같은 사실인지는 단언 종류가 판단한다
(관계의 위/안은 같은 받침 관계로 본다). 재현율만 잰다: 정답은 성공에 필요한 최소 조건이라, 생성본이 그보다
더 적은 것은 누락이지만 더 적은 것 외의 추가 단언이 틀렸다고는 말할 수 없다.

성공하는 경우가 여러 가지인 태스크(레몬은 한 그릇에, 브로콜리는 다른 그릇에, 어느 쪽이든)는 정답 대안이
여럿이다. 설명문의 목표 상태도 지시를 만족하는 경우를 모두 적는다. 정답 대안마다 설명문의 가장 가까운 경우와
대조하고, 재현율은 대안들의 평균이다. 그래서 경우 하나만 적고 다른 경우를 빠뜨리면 그만큼 낮아진다.

초기 씬에서 이미 성립하는 정답 항목(예: 쟁반이 식탁 위에 그대로 있을 것)은 재현율에서 뺀다. 목표 상태는
바뀌어야 하는 것을 적는 자리이고 적지 않은 상태는 "상관없음"이므로, 그대로 두면 되는 조건을 적지 않았다고
누락으로 셀 수 없다. 이런 항목은 already_holds로 따로 기록한다.
"""

from typing import Any

from tsdgen.goals.kinds import GOAL_KINDS, SUPPORT_PREDICATES
from tsdgen.goals.scene_index import REGION_SEP, SceneIndex, fixture_of, members
from tsdgen.goals.verifier import SATISFIED, assertion_status
from tsdgen.models import GoalAssertion, GoalOracleResult, TaskGoal

RELATION, NEAR = "relation", "near"


def _implies(g: GoalAssertion, c: GoalAssertion, index: SceneIndex) -> bool:
    """생성 단언 g가 정답 후보 c를 함의하는가: 물체를 '가구 F 위에 놓인 것'의 위·안·옆에 두면 그 물체도 F에 있다.

    원천의 성공 판정은 "접시 옆에 놓아"를 조리대 접촉으로 재는데, 설명문은 지시대로 접시와의 관계로 쓴다.
    둘은 같은 최종 상태라 놓친 것으로 세지 않는다. 기준 물체가 처음 씬에서 얹혀 있는 가구를 본다
    (접시 위 스테이크 옆이면 접시가 놓인 조리대)."""
    if not (g.kind == c.kind == RELATION and g.value and c.value and g.subject == c.subject):
        return False
    if c.predicate not in SUPPORT_PREDICATES or g.predicate not in SUPPORT_PREDICATES | {NEAR}:
        return False
    if any(index.fixture_under(m) == c.object for m in members(g.object)):
        return True
    # 가구 옆에 둔다고 쓴 것은 그 가구 옆의 조리대에 둔 것이다 (씬의 '조리대 near 가구' 관계)
    refs = members(g.object) if g.predicate == NEAR else []
    return any((c.object, NEAR, m) in index.relations for m in refs)


def _any_region_of(g: GoalAssertion, candidates: list[GoalAssertion]) -> bool:
    """정답이 '그 가구의 칸 여럿 중 아무 데나'이고 생성 단언은 그 가구에 둔다고 쓴 경우. 지시가 가구만 말하면
    (냉장고에 넣어) 설명문도 가구로 쓰는데, 원천은 같은 것을 선반 접촉으로 잰다. 정답이 칸 하나를 못 박은
    경우에는 가구만 쓴 것이 모자라므로 해당하지 않는다."""
    regions = {c.object for c in candidates if REGION_SEP in (c.object or "")}
    if len(regions) < 2 or len(regions) != len(candidates):
        return False
    same = all((c.kind, c.subject, c.value) == (g.kind, g.subject, g.value) for c in candidates)
    supports = {g.predicate, *(c.predicate for c in candidates)} <= SUPPORT_PREDICATES
    return same and supports and {fixture_of(r) for r in regions} == set(members(g.object))


def _covers(generated: list[GoalAssertion], candidates: list[GoalAssertion],
            index: SceneIndex) -> bool:
    return any(_any_region_of(g, candidates) for g in generated) or any(
        g.kind == c.kind and g.kind in GOAL_KINDS.names()
        and (GOAL_KINDS.get(g.kind).same_fact(g, c) or _implies(g, c, index))
        for g in generated
        for c in candidates
    )


def _holds_initially(candidates: list[GoalAssertion], index: SceneIndex) -> bool:
    return any(assertion_status(c, index) == SATISFIED for c in candidates)


def _against(case: list[GoalAssertion], alternative: list[list[GoalAssertion]],
             index: SceneIndex) -> dict:
    already = [_holds_initially(candidates, index) for candidates in alternative]
    matched = [_covers(case, candidates, index) for candidates in alternative]
    counted = [hit for hit, holds in zip(matched, already, strict=True) if not holds]
    recall = round(sum(counted) / len(counted), 3) if counted else None
    return {"already": already, "matched": matched, "recall": recall}


def _score(result: dict) -> tuple:
    """가까운 정도: 재현율(할 일이 없는 대안은 1로 본다), 같으면 맞힌 항목 수."""
    return (1.0 if result["recall"] is None else result["recall"], sum(result["matched"]))


def compare_with_oracle(
    goal: TaskGoal,
    alternatives: list[list[list[GoalAssertion]]] | None,
    env: dict[str, Any],
    min_recall: float | None,
) -> GoalOracleResult:
    """env는 초기 씬(환경 컨텍스트). min_recall이 None이면 기록만 하고 통과시킨다.
    oracle의 항목마다 alternative(몇 번째 정답 대안인지)를 붙이고, matched·already_holds도 같은 순서다."""
    if not alternatives:
        return GoalOracleResult(available=False, **{"pass": True})
    index = SceneIndex.from_env(env)
    cases = goal.goal_state or [[]]
    per_alt = [max((_against(case, alt, index) for case in cases), key=_score)
               for alt in alternatives]
    recalls = [r["recall"] for r in per_alt if r["recall"] is not None]
    recall = round(sum(recalls) / len(recalls), 3) if recalls else None
    return GoalOracleResult(
        available=True,
        oracle=[{"any_of": [c.model_dump() for c in candidates], "alternative": k}
                for k, alt in enumerate(alternatives) for candidates in alt],
        matched=[m for r in per_alt for m in r["matched"]],
        already_holds=[h for r in per_alt for h in r["already"]],
        recall=recall,
        alternatives=len(alternatives),
        chosen=max(range(len(per_alt)), key=lambda k: _score(per_alt[k])),
        **{"pass": min_recall is None or recall is None or recall >= min_recall},
    )
