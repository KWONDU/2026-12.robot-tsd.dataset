"""작업상황설명문(TaskSituationDescription)과 목표 상태·완료 판정."""

from typing import Any

from pydantic import Field, field_validator, model_validator

from tsdgen.models.base import ConstraintType, StrictModel


class SpaceCondition(StrictModel):
    """공간 조건: 작업이 시작되는 곳과 끝나는 곳. 순서가 정해지지 않아 후보가 여럿이면 후보를 모두 '혹은'으로 잇는다."""

    start: str | None = Field(default=None, description="작업이 시작되는 곳 (옮길 물체가 처음 있는 곳)")
    end: str | None = Field(default=None, description="작업이 끝나는 곳")


class SpatiotemporalConditions(StrictModel):
    time: str | None = None
    space: SpaceCondition | None = None


class _TSDEntry(StrictModel):
    """대상 물체 한 칸의 공통 부분. ref는 세계 상태의 이름(같은 이름의 아무 것이면 번호 없는 이름)이고,
    위치는 세계 상태가 놓인 곳을 줄 때만 쓴다."""

    ref: str
    location: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _drop_display_name(cls, data: Any) -> Any:
        """2.3까지는 ref와 같은 값을 name_ko에 한 번 더 적었다. 읽을 때 버린다."""
        return {k: v for k, v in data.items() if k != "name_ko"} if isinstance(data, dict) else data


class TSDRequiredObject(_TSDEntry):
    state: str | None = None


class TSDKeyMeans(_TSDEntry):
    state: str | None = None
    alternatives: list[str] = Field(default_factory=list)


class TSDAuxiliaryObject(_TSDEntry):
    note: str | None = None


class TSDTargetObjects(StrictModel):
    required: list[TSDRequiredObject] = Field(default_factory=list)
    key_means: list[TSDKeyMeans] = Field(default_factory=list)
    auxiliary: list[TSDAuxiliaryObject] = Field(default_factory=list)


class TSDConstraint(StrictModel):
    type: ConstraintType
    description: str


STATE_KIND = "state"
OLD_COMPLETION = "completion_conditions"
OLD_STATE_KINDS = ("object_state", "fixture_state")


class GoalAssertion(StrictModel):
    """작업을 마쳤을 때 성립해야 하는(또는 성립하지 않아야 하는) 상태 단언 하나. 행동이나 순서가 아니라 상태이며, 현재 씬 상태와 같은 어휘(관계 술어, 상태 키, entity id)로 쓴다. 적은 것만 확인 대상이고, 적지 않은 상태는 "바뀌면 안 됨"이 아니라 "상관없음"이다."""

    # 설명(description)은 전달 스키마와 모델의 출력 스키마에 그대로 실린다
    kind: str = Field(description="단언 종류: relation(관계), state(물체나 가구의 상태)")
    subject: str = Field(description="주어 entity id (환경 컨텍스트의 물체·가구 id)")
    predicate: str = Field(description="relation이면 관계 술어, 상태 단언이면 상태 키")
    object: str | None = Field(default=None, description="relation의 대상. 로봇이 드는 관계와 상태 단언은 null")
    value: bool = Field(default=True, description="true면 성립해야 하고 false면 성립하지 않아야 한다")

    @field_validator("kind", mode="before")
    @classmethod
    def _one_state_kind(cls, value: Any) -> Any:
        """3.0까지는 상태 단언을 주어 종류에 따라 object_state·fixture_state로 나눠 적었다. 읽을 때 state로 모은다."""
        return STATE_KIND if value in OLD_STATE_KINDS else value


GOAL_STATE_DOC = "작업을 마쳤을 때의 상태. 만족하는 경우마다 단언 목록 하나 (경우 안은 모두 성립, 경우 중 하나면 된다)"


class TaskGoal(StrictModel):
    """작업 목표. 사람이 읽는 요약과, 그것을 기계가 확인할 수 있게 옮긴 목표 상태."""

    summary: str = Field(description="사람이 읽는 최종 목적 한 문장")
    goal_state: list[list[GoalAssertion]] = Field(default_factory=list, description=GOAL_STATE_DOC)

    @model_validator(mode="before")
    @classmethod
    def _read_single_case(cls, data: Any) -> Any:
        """2.2까지는 목표 상태가 경우 하나(단언 목록)였다: 경우 하나로 감싼다. 빈 경우는 상태 목표가 없다는 뜻이라 뺀다."""
        if isinstance(data, dict):  # 3.2까지 있던 완료 판정은 설명문을 읽을 때 다른 칸으로 옮긴다
            data = {k: v for k, v in data.items() if k != OLD_COMPLETION}
        cases = data.get("goal_state") if isinstance(data, dict) else None
        if not cases:
            return data
        flat = not any(isinstance(c, list) for c in cases)
        return {**data, "goal_state": [cases] if flat else [c for c in cases if c]}


class TaskSituationDescription(StrictModel):
    # 판 번호는 설명문마다 싣지 않는다 (데이터셋 전체가 한 판이고 config schema_versions가 정한다)
    task_goal: TaskGoal
    spatiotemporal_conditions: SpatiotemporalConditions
    target_objects: TSDTargetObjects
    constraints: list[TSDConstraint] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _read_goal_as_text(cls, data: Any) -> Any:
        """예전 산출물 읽기: 2.1까지 있던 schema_version은 버리고, 1.x의 문장 task_goal은 summary로 옮긴다."""
        if not isinstance(data, dict):
            return data
        data = {k: v for k, v in data.items() if k != "schema_version"}
        goal = data.get("task_goal")
        data = {**data, "task_goal": {"summary": goal}} if isinstance(goal, str) else data
        return _read_completion_conditions(_read_space_text(data))


def _read_completion_conditions(data: dict) -> dict:
    """3.2까지는 task_goal에 완료 판정(completion_conditions)이 있었다: 목표 상태로 못 쓰는 기준을 적던 칸이다.
    3.3에서 없앴다. 읽을 때 지속 시간은 시간 조건으로(비어 있을 때만), 서술 기준은 other 제약으로 옮긴다.
    목표 상태를 다시 가리키기만 하던 판정(kind goal_state)은 버린다."""
    goal = data.get("task_goal")
    old = goal.get(OLD_COMPLETION) if isinstance(goal, dict) else None
    if not old:
        return data
    conditions = dict(data.get("spatiotemporal_conditions") or {})
    constraints = list(data.get("constraints") or [])
    for item in old:
        if item.get("kind") == "duration":
            conditions["time"] = conditions.get("time") or item.get("description")
        elif item.get("kind") == "unresolved":
            constraints.append({"type": "other", "description": item.get("description")})
    return {**data, "spatiotemporal_conditions": conditions, "constraints": constraints}


def _read_space_text(data: dict) -> dict:
    """3.1까지는 공간 조건이 문장 하나였고, 지시가 위치를 한정한 표현 가운데 목표 상태로 못 옮긴 것을 적었다.
    3.2에서 그런 표현의 자리는 spatial 제약이다. 읽을 때 그리로 옮기고 공간 조건은 비운다."""
    conditions = data.get("spatiotemporal_conditions")
    text = conditions.get("space") if isinstance(conditions, dict) else None
    if not isinstance(text, str):
        return data
    moved = [{"type": "spatial", "description": text}] if text.strip() else []
    return {**data, "spatiotemporal_conditions": {**conditions, "space": None},
            "constraints": [*(data.get("constraints") or []), *moved]}
