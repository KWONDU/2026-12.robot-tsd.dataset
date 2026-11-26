"""목표 상태 표기: 단언 한 줄(render_assertion)과 샘플의 entity 이름표. 샘플 상세가 쓴다.

표기 규칙은 goals 등록부 한 곳에 있으므로 화면이 따로 흉내 내지 않게 여기서만 부른다.
"""

from tsdgen.goals.kinds import GOAL_KINDS
from tsdgen.models import GoalAssertion


def entity_names(sample: dict) -> dict[str, str]:
    """이 샘플의 entity id -> 한국어 이름 (씬의 이름)."""
    env = (sample.get("input") or {}).get("environment_context") or {}
    return {e["id"]: e.get("name_ko", e["id"])
            for e in [*env.get("objects", []), *env.get("fixtures", [])]}


def render_assertion(raw: dict, names: dict[str, str], lexicon: dict) -> str:
    assertion = GoalAssertion.model_validate(raw)
    if assertion.kind not in GOAL_KINDS.names():
        return str(raw)
    return GOAL_KINDS.get(assertion.kind).render(assertion, names, lexicon)
