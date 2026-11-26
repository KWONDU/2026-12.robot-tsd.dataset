"""Isaac Lab-Arena 환경 스펙(env graph) 자료형.

실제 배포된 스펙 파일(data/raw/isaaclab_arena/isaaclab_arena_environments/)을 그대로
읽고 쓸 수 있는 최소 모델이다. 필드 이름과 구조는 Arena 쪽이 정한 것이므로 우리
명명 규칙을 적용하지 않고 원문을 따른다. 모르는 키가 들어와도 잃지 않도록 각
항목의 params는 자유 dict로 둔다.

관계 종류는 실제 스펙에서 쓰이는 것만 상수로 둔다. Arena에는 포함(in) 관계가
없어서 "캐비닛 안"은 내부 prim을 object_reference로 잡고 on으로 표현해야 한다.
"""

from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

# 실제 스펙 파일에서 관찰된 관계 종류
ON = "on"
NEXT_TO = "next_to"
IS_ANCHOR = "is_anchor"
ROTATE_AROUND_SOLUTION = "rotate_around_solution"

ObjectType = Literal["base", "articulation"]


class ArenaModel(BaseModel):
    """Arena 쪽 스키마를 따르므로 모르는 키도 버리지 않고 보존한다."""

    model_config = ConfigDict(extra="allow")


class Registered(ArenaModel):
    id: str
    registry_name: str
    params: dict[str, Any] = Field(default_factory=dict)


class ObjectReference(ArenaModel):
    id: str
    parent_id: str
    prim_path: str
    object_type: ObjectType = "base"
    params: dict[str, Any] = Field(default_factory=dict)


class Relation(ArenaModel):
    kind: str
    subject: str | None = None
    reference: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class Subtask(ArenaModel):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


class Task(ArenaModel):
    composition: str = "atomic"
    description: str = ""
    subtasks: list[Subtask] = Field(default_factory=list)


class ArenaEnvSpec(ArenaModel):
    """환경 스펙 하나. Arena의 cli_runner가 읽는 그 YAML이다."""

    env_name: str
    embodiment: Registered | None = None
    background: Registered | None = None
    objects: list[Registered] = Field(default_factory=list)
    object_references: list[ObjectReference] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    task: Task = Field(default_factory=Task)

    def anchors(self) -> set[str]:
        return {r.subject for r in self.relations if r.kind == IS_ANCHOR and r.subject}

    def placements(self) -> list[Relation]:
        """물체를 어디에 두는지 정하는 관계만 (배치 수정자와 anchor 제외)."""
        return [r for r in self.relations if r.kind in (ON, NEXT_TO) and r.reference]

    def to_yaml(self) -> str:
        data = self.model_dump(mode="json", exclude_none=True)
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def load_spec(text: str) -> ArenaEnvSpec:
    return ArenaEnvSpec.model_validate(yaml.safe_load(text))


def is_env_spec(document: Any) -> bool:
    """env spec YAML인지 판별한다. 같은 디렉토리에 실험 설정 등 다른 YAML이 섞여 있다."""
    return isinstance(document, dict) and "env_name" in document
