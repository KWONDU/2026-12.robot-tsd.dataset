"""시나리오 필터: 태스크가 성립하지 않는 시나리오를 선별 단계에서 거른다.

이 데이터셋의 태스크는 "world state와 지시문을 주면 작업에 필요한 정보를 골라
설명문으로 쓰는 것"이다. 지시문이 참조하는 정보가 우리가 추출한 world state에
아예 없으면 골라낼 답이 입력에 존재하지 않으므로, 그 시나리오는 출제 자체가
성립하지 않는다. 그런 경우를 여기서 미리 거른다.

대표 사례가 방향·서수 참조다. "왼쪽 서랍", "맨 위 선반"처럼 방향으로 대상을
지목하는 지시가 전체의 상당수인데, 메타데이터 전용 추출(Tier A)에는 좌표가
없어 어느 서랍이 왼쪽인지 알 수 없다. 씬 XML의 좌표를 파싱해 방향 정보를
world state에 넣게 되면(도입 시 결정 기록에 남길 것) 이 필터를 끄고 해당
시나리오를 되살리면 된다. 필터 목록은 config(sampling.filters)가 정한다.

걸러진 시나리오는 버리지 않고 사유와 함께 runs/<run_id>/sample_excluded.jsonl에
남긴다.
"""

import re

from tsdgen.models import ScenarioRecord
from tsdgen.registry import Registry

SCENARIO_FILTERS = Registry("scenario filter", axes=(lambda s: s.sampling.filters,))
"""이름 -> (ScenarioRecord) -> str | None. 제외 사유를 돌려주면 그 시나리오는 선별에서 빠진다."""

# 방향어가 가구를 수식할 때만 잡는다. "Lower the heat"의 lower(동사)나
# "leftover" 같은 오탐을 피하기 위해 뒤에 가구 명사를 요구하는 패턴을 함께 쓴다.
_FIXTURE_NOUN = (
    r"(shelf|shelves|drawer|drawers|cabinet|burner|rack|door|doors|"
    r"compartment|counter|stove|sink|oven|side|corner|basket)"
)
_DIRECTIONAL_PATTERNS = [
    # "right next to / right away"의 right는 부사이므로 방향 지목이 아니다
    re.compile(r"\b(left|right)\b(?!\s+(next|away|now|after|before))", re.IGNORECASE),
    re.compile(r"\b(leftmost|rightmost)\b", re.IGNORECASE),
    re.compile(
        rf"\b(top|bottom|middle|upper|lower|front|rear|back|closest|nearest|farthest)"
        rf"\s+{_FIXTURE_NOUN}\b",
        re.IGNORECASE,
    ),
    re.compile(rf"\b(first|second|third|fourth)\s+{_FIXTURE_NOUN}\b", re.IGNORECASE),
    # 후치 수식: "the drawer in front", "the counter at the back"
    re.compile(
        rf"\b{_FIXTURE_NOUN}\s+(in|at|on)\s+(the\s+)?(front|back|rear|middle|top|bottom)\b",
        re.IGNORECASE,
    ),
]


@SCENARIO_FILTERS.register("directional_reference")
def directional_reference(record: ScenarioRecord) -> str | None:
    """지시문이 방향·서수로 대상을 지목하는데 씬에는 그 정보가 없는 경우."""
    instruction = record.source.original_instruction_en
    for pattern in _DIRECTIONAL_PATTERNS:
        match = pattern.search(instruction)
        if match:
            return (
                f"instruction locates a target by '{match.group(0)}' "
                "but the Tier A scene state carries no directional information"
            )
    return None


SYNTHESIZED_SPLIT = "synth"  # 합성 장면의 split (어댑터의 합성 모듈이 붙인다)


@SCENARIO_FILTERS.register("synthesized_only")
def synthesized_only(record: ScenarioRecord) -> str | None:
    """합성한 장면만 남긴다 (받은 시연 장면을 뺀다). 합성 장면만 따로 돌려 볼 때 켠다."""
    if record.source.split != SYNTHESIZED_SPLIT:
        return "not a synthesized scene"
    return None


COMPOSED_SPLIT = "composed"  # 작업을 합성한 시나리오의 split (tasks.compose가 붙인다)


@SCENARIO_FILTERS.register("composed_only")
def composed_only(record: ScenarioRecord) -> str | None:
    """작업을 합성한 시나리오만 남긴다."""
    if record.source.split != COMPOSED_SPLIT:
        return "not a composed task"
    return None


def apply_filters(settings, records: list[ScenarioRecord]) -> tuple[list, list[dict]]:
    """config가 켠 필터들을 순서대로 적용한다. (남은 것, 제외 기록) 을 돌려준다."""
    kept, excluded = [], []
    for record in records:
        for name in settings.sampling.filters:
            reason = SCENARIO_FILTERS.get(name)(record)
            if reason:
                excluded.append(
                    {
                        "scenario_id": record.scenario_id,
                        "filter": name,
                        "reason": reason,
                        "instruction": record.source.original_instruction_en,
                    }
                )
                break
        else:
            kept.append(record)
    return kept, excluded
