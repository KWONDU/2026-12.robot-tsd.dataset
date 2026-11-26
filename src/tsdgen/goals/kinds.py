"""목표 단언 종류와 완료 판정 종류의 등록부.

단언 종류마다 "형식이 맞는가(problems)", "씬에서 성립하는가(holds)", "사람이 읽는 한 줄(render)"이 다르므로
종류 하나를 전략 하나로 등록한다. 종류를 늘리는 일은 여기 등록 하나와 사전(goal_kind_ko)의 표시명 하나로
끝나고, 검증기·렌더러·대시보드는 바뀌지 않는다. 등록 이름이 곧 goal_state[].kind의 허용값이다.

어휘는 현재 씬 상태와 같다: 관계 술어는 씬의 관계 어휘, 상태 키는 사전의 state_phrases 키다. 새 상태 키가
필요하면 사전에 먼저 넣는다 (KIST와 협의할 대상이다).
"""

from abc import ABC, abstractmethod
from typing import Any, get_args

from tsdgen.goals.scene_index import (
    FIXTURES,
    REGION_SEP,
    SUPPORT_PREDICATES,
    SceneIndex,
    fixture_of,
    members,
)
from tsdgen.models import (
    STATE_KIND,
    GoalAssertion,
    GoalStatus,
    RelationPred,
)
from tsdgen.registry import Registry

RELATION_PREDICATES = set(get_args(RelationPred))
# 대상이 로봇 자신인 관계. 대상 id를 적지 않는다. held_by는 주어(물체)를 로봇이 들고 있음,
# robot_at은 로봇이 주어(곳)에 와 있음이다. 관계마다 장면 색인에서 참인지 보는 방법이 다르다.
AGENT_RELATIONS = {
    "held_by": lambda subject, index: subject in index.holding,
    "robot_at": lambda subject, index: subject == index.robot_at,
}
# 위(on)와 안(in)은 물체를 받치거나 담는 관계다(SUPPORT_PREDICATES). 메타데이터 배치는 위/안을 가구 분류로
# 정하므로 판정에서는 같은 사실로 본다.
UNKNOWN: GoalStatus = "unknown"

GOAL_KINDS = Registry("goal assertion kind")
"""이름 -> GoalKind. 목표 상태 단언 하나를 검사·판정·표시한다."""

"""이름 -> CompletionKind. 완료 판정 하나를 검사·판정·표시한다."""


def entity_label(ref: str | None, names: dict[str, str]) -> str:
    """사람이 읽는 entity 표기: 이름(id). 이름을 모르면 id만."""
    return f"{names[ref]}({ref})" if ref in names else str(ref)


class GoalKind(ABC):
    @abstractmethod
    def problems(self, assertion: GoalAssertion, index: SceneIndex, lexicon: dict) -> list[str]:
        """형식·접지 위반. 없으면 빈 목록."""

    @abstractmethod
    def holds(self, assertion: GoalAssertion, index: SceneIndex) -> bool | None:
        """씬에서 이 사실이 성립하는가 (value와 무관). 씬 정보로 알 수 없으면 None."""

    @abstractmethod
    def render(self, assertion: GoalAssertion, names: dict[str, str], lexicon: dict) -> str:
        """사람이 읽는 한 줄."""

    def refs(self, assertion: GoalAssertion) -> list[str]:
        return [ref for ref in (assertion.subject, assertion.object) if ref]

    def same_fact(self, a: GoalAssertion, b: GoalAssertion) -> bool:
        """두 단언이 같은 사실을 말하는가 (정답 대조용)."""
        return a.model_dump() == b.model_dump()


def _any(results: list[bool | None]) -> bool | None:
    """여럿 중 하나라도 참이면 참, 모두 거짓이면 거짓, 그 밖에는 알 수 없음."""
    if any(r is True for r in results):
        return True
    return False if results and all(r is False for r in results) else None


def _within(single: str | None, ref: str | None) -> bool:
    """정답 후보의 entity 하나가 생성본의 참조(여럿일 수 있음) 안에 있는가."""
    refs = members(ref)
    # 생성본이 가구의 칸까지 말했으면 그 가구에 있다는 정답 후보도 만족한다
    inside = single is not None and (single in refs or single in map(fixture_of, refs))
    return single == ref or inside


def _negated(text: str, value: bool) -> str:
    return text if value else f"{text} 아님"


class RelationKind(GoalKind):
    """주어-관계-대상. 씬의 관계 어휘를 그대로 쓴다."""

    def problems(self, assertion: GoalAssertion, index: SceneIndex, lexicon: dict) -> list[str]:
        found = []
        if assertion.predicate not in RELATION_PREDICATES:
            found.append(f"relation predicate '{assertion.predicate}' is not one of "
                         f"{sorted(RELATION_PREDICATES)}")
        if index.kind_of(assertion.subject) is None:
            found.append(f"unknown subject '{assertion.subject}'")
        if assertion.predicate in AGENT_RELATIONS:
            if assertion.object is not None:
                found.append(f"'{assertion.predicate}' relates to the robot; object must be null")
        elif index.kind_of(assertion.object) is None:
            found.append(f"unknown object '{assertion.object}'")
        elif assertion.object == assertion.subject and len(members(assertion.subject)) == 1:
            # 여럿 중 아무 것을 가리키는 이름(통조림 위에 통조림)은 같은 말이어도 같은 물체가 아니다
            found.append("subject and object are the same entity")
        return found

    def _holds_one(self, subject: str, predicate: str, target: str | None,
                   index: SceneIndex) -> bool | None:
        if predicate in AGENT_RELATIONS:
            return AGENT_RELATIONS[predicate](subject, index)
        support = predicate in SUPPORT_PREDICATES
        same = SUPPORT_PREDICATES if support else {predicate}
        if any((subject, p, target) in index.relations for p in same):
            return True
        if support and index.kind_of(target) == FIXTURES and subject in index.placements:
            if REGION_SEP not in (target or ""):
                return index.placements[subject] == target  # 물체는 한 가구에만 놓인다
            if index.placements[subject] != fixture_of(target):
                return False  # 다른 가구에 있으면 그 칸에도 없다
            placed = index.region_placements.get(subject)
            return placed == target if placed else None  # 같은 가구인데 어느 칸인지 모르면 알 수 없음
        return None

    def holds(self, assertion: GoalAssertion, index: SceneIndex) -> bool | None:
        """참조가 여럿을 가리키면(아무 조리대) 하나라도 성립하면 성립, 모두 아니면 불성립이다."""
        return _any([self._holds_one(s, assertion.predicate, t, index)
                     for s in members(assertion.subject)
                     for t in (members(assertion.object) or [None])])

    def render(self, assertion: GoalAssertion, names: dict[str, str], lexicon: dict) -> str:
        relation = lexicon["relation_ko"].get(assertion.predicate, assertion.predicate)
        target = "로봇" if assertion.object is None else entity_label(assertion.object, names)
        text = f"{entity_label(assertion.subject, names)}: {target} {relation}"
        return _negated(text, assertion.value)

    def same_fact(self, a: GoalAssertion, b: GoalAssertion) -> bool:
        """a(생성본)가 b(정답 후보)와 같은 사실인가. a의 참조가 여럿을 가리키면 b의 것이 그 안에 있으면 된다."""
        predicates = {a.predicate, b.predicate}
        same_predicate = len(predicates) == 1 or predicates <= SUPPORT_PREDICATES
        same_ends = (a.kind, a.value) == (b.kind, b.value) and _within(b.subject, a.subject) \
            and _within(b.object, a.object)
        return same_ends and same_predicate


GOAL_KINDS.register("relation")(RelationKind())


class EntityStateKind(GoalKind):
    """entity 하나(물체, 가구, 가구의 칸)의 상태 키가 어떤 값이어야 하는지. 상태 키 어휘는 사전의 state_phrases다.
    주어가 물체인지 가구인지는 주어 자신이 말해 주므로 종류로 다시 나누지 않는다."""

    def problems(self, assertion: GoalAssertion, index: SceneIndex, lexicon: dict) -> list[str]:
        found = []
        if index.kind_of(assertion.subject) is None:
            found.append(f"unknown subject '{assertion.subject}'")
        if assertion.predicate not in lexicon["state_phrases"]:
            found.append(f"state key '{assertion.predicate}' is not one of "
                         f"{sorted(lexicon['state_phrases'])}")
        if assertion.object is not None:
            found.append("state assertions take no object")
        return found

    def holds(self, assertion: GoalAssertion, index: SceneIndex) -> bool | None:
        values = [index.states_of(m).get(assertion.predicate) for m in members(assertion.subject)]
        return _any([v if isinstance(v, bool) else None for v in values])

    def same_fact(self, a: GoalAssertion, b: GoalAssertion) -> bool:
        return (a.kind, a.predicate, a.value) == (b.kind, b.predicate, b.value) \
            and _within(b.subject, a.subject)

    def render(self, assertion: GoalAssertion, names: dict[str, str], lexicon: dict) -> str:
        phrases = lexicon["state_phrases"].get(assertion.predicate, {})
        state = phrases.get(assertion.value, f"{assertion.predicate}={assertion.value}")
        return f"{entity_label(assertion.subject, names)}: {state}"


GOAL_KINDS.register(STATE_KIND)(EntityStateKind())


def target_roles(target_objects: Any) -> dict[str, str]:
    """설명문 대상 물체의 참조 -> 역할 (required, key_means, auxiliary)."""
    return {entry.ref: role for role in ("required", "key_means", "auxiliary")
            for entry in getattr(target_objects, role)}
