"""공통 바탕: 어휘(Literal), 상태 묶음, 저장 규칙, 엄격 모델과 통과 여부 결과."""

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

RelationPred = Literal["on", "in", "near", "held_by", "robot_at"]  # robot_at: 로봇이 그 곳에 있음 (주어가 곳, 대상 없음. 지도가 있는 도메인)
StateSource = Literal["metadata", "task_source_static", "sim", "default_assumed", "unknown"]
ConstraintType = Literal["precedence", "resource", "physical", "safety", "spatial", "other"]
SampleStatus = Literal["pending", "accepted", "alternate", "rejected", "needs_review"]
# 목표 판정의 세 값. 확인했는데 아님(unsatisfied)과 확인할 정보가 없음(unknown)을 섞지 않는다.
GoalStatus = Literal["satisfied", "unsatisfied", "unknown"]
Specificity = Literal["explicit", "natural", "underspecified"]

# 검수를 마친 샘플이 가질 수 있는 상태 (pending은 아직 검수 전이다)
VERIFIED_STATUSES: tuple[str, ...] = tuple(s for s in get_args(SampleStatus) if s != "pending")
# 전달 대상이 아닌 미달 상태. judge 호출 실패(needs_review)도 미달 쪽에 보존한다.
FAILED_STATUSES: tuple[str, ...] = ("rejected", "needs_review")
# 검수 통과 상태. alternate는 통과했지만 선별에서 차순위로 밀린 것이다.
PASSED_STATUSES: tuple[str, ...] = ("accepted", "alternate")
UNKNOWN_TASK = "?"


def dump(model: BaseModel) -> dict:
    """저장용 JSON dict. pass_ 같은 별칭 필드는 JSON 이름(pass)으로 나간다."""
    return model.model_dump(mode="json", by_alias=True)


def task_label(scenario_id: str, unknown: str = UNKNOWN_TASK) -> str:
    """시나리오 id에서 태스크 이름을 읽는다.

    어댑터는 id를 `<원천 접두어>-<태스크>-...` 모양으로 만든다 (예: rc365-BlendMarinade-...).
    이 규약을 아는 곳을 여기 하나로 모은다. 모양이 다르면 unknown을 돌려준다.
    """
    parts = scenario_id.split("-")
    return parts[1] if len(parts) > 1 else unknown


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PassResult(BaseModel):
    """통과 여부를 담는 검수 결과의 공통 부분.

    `pass`는 파이썬 예약어라 필드명을 pass_로 두고 JSON에서만 pass로 나가게 한다.
    같은 선언을 결과 타입마다 반복하지 않도록 여기 한 번만 둔다.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    pass_: bool = Field(alias="pass")
