"""층화 선별 전략 (스펙 6.1).

시나리오가 달고 온 층화 이름표(strata)와 씬의 구성으로 층을 나누고, 태스크 중복을
최소화하며 라운드로빈으로 뽑는다. 층이 여럿일 때는 관여 물체·fixture가 많은 쪽을
먼저 뽑아 정보량이 큰 시나리오를 우선한다.

배분 축과 비율은 config가 정한다(quota_key, quotas). 이 전략은 이름표만 보고
나누므로 원천의 어휘(RoboCasa의 atomic/composite 같은 것)를 알지 못한다.
"""

import random

from tsdgen.models import ScenarioRecord
from tsdgen.registry import SAMPLING_STRATEGIES
from tsdgen.sampling.base import SamplingOptions, SamplingStrategy

STRATEGY_NAME = "stratified"


class StratifiedOptions(SamplingOptions):
    quota_key: str  # 배분 축으로 쓸 시나리오 이름표 (ScenarioRecord.strata의 키)
    quotas: dict[str, float]  # 이름표 값 -> 비율


def _stratum_key(record: ScenarioRecord, quota_key: str) -> tuple:
    """배분 축을 뺀 나머지 이름표와 씬 구성으로 층을 만든다."""
    labels = tuple(v for k, v in sorted(record.strata.items()) if k != quota_key)
    fixture_kinds = tuple(sorted({f.category for f in record.scene_state.fixtures}))
    return (
        labels or (record.source.task_name,),
        record.scene_state.layout.layout_id,
        fixture_kinds,
    )


def _richness(record: ScenarioRecord) -> int:
    return len(record.scene_state.objects) + len(record.scene_state.fixtures)


def _quotas(n: int, fractions: dict[str, float], available: dict[str, int]) -> dict[str, int]:
    """비율을 정수 배분으로 바꾼다. 합계는 요청한 n을 넘지 않고, 가능하면 정확히 n이다.

    각 그룹을 따로 반올림하면 합계가 n을 넘길 수 있다(0.3/0.7을 5에 적용하면 2+4=6).
    그러면 100건을 요청했는데 101건이 나가고 비용 추정도 어긋난다. 그래서 내림으로
    나눈 뒤 남은 자리를 소수부가 큰 그룹부터 채우는 최대잉여법을 쓴다.
    """
    exact = {g: n * frac for g, frac in fractions.items() if available.get(g)}
    if not exact:
        return {}
    quotas = {g: min(int(value), available[g]) for g, value in exact.items()}

    # 남은 자리를 소수부가 큰 그룹부터, 가용량이 허락하는 만큼 채운다
    order = sorted(exact, key=lambda g: (-(exact[g] - int(exact[g])), g))
    while sum(quotas.values()) < n:
        progressed = False
        for group in order:
            if sum(quotas.values()) >= n:
                break
            if quotas[group] < available[group]:
                quotas[group] += 1
                progressed = True
        if not progressed:
            break  # 가용량을 다 썼다
    return quotas


def _pick_from_strata(
    strata: dict[tuple, list[ScenarioRecord]],
    quota: int,
    order: list[tuple],
    total_records: int,
) -> list[ScenarioRecord]:
    """층을 라운드로빈으로 돌며 뽑는다. 처음 한 바퀴는 새 태스크를 우선해 중복을 줄인다."""
    picked: list[ScenarioRecord] = []
    seen_tasks: set[str] = set()
    round_index = 0
    while len(picked) < quota and any(strata.values()):
        progressed = False
        for key in order:
            if len(picked) >= quota:
                break
            stratum = strata[key]
            if not stratum:
                continue
            index = next(
                (i for i, r in enumerate(stratum) if r.source.task_name not in seen_tasks),
                0 if round_index > 0 else None,
            )
            if index is None:
                continue  # 첫 바퀴에는 이미 뽑은 태스크를 다시 쓰지 않는다
            record = stratum.pop(index)
            seen_tasks.add(record.source.task_name)
            record.meta = {
                **record.meta,
                "sampling": {"strategy": STRATEGY_NAME, "stratum": list(map(str, key))},
            }
            picked.append(record)
            progressed = True
        if not progressed:
            round_index += 1
            if round_index > total_records:
                break
    return picked


def stratified_sample(
    records: list[ScenarioRecord], n: int, options: StratifiedOptions, seed: int
) -> list[ScenarioRecord]:
    rng = random.Random(seed)
    quota_key = options.quota_key
    fractions = options.quotas

    by_group: dict[str, list[ScenarioRecord]] = {}
    for record in records:
        by_group.setdefault(record.strata.get(quota_key, ""), []).append(record)

    available = {group: len(lst) for group, lst in by_group.items()}
    if not set(available) & set(fractions):
        # 이름표가 없으면 배분이 전부 0이 되어 조용히 빈 결과가 나온다. 원천이 strata를
        # 붙이지 않았거나 오래된 캐시를 읽은 경우이므로, 숫자 0 대신 이유를 말하고 멈춘다.
        raise ValueError(
            f"quota_key '{quota_key}'에 해당하는 이름표가 시나리오에 없다. "
            f"발견된 이름표: {sorted(available)}, config가 기대한 값: {sorted(fractions)}. "
            "원천이 strata를 붙이는지 확인하고, 예전에 적재한 시나리오라면 다시 적재할 것."
        )

    chosen: list[ScenarioRecord] = []
    for group, quota in _quotas(n, fractions, available).items():
        strata: dict[tuple, list[ScenarioRecord]] = {}
        for record in by_group.get(group, []):
            strata.setdefault(_stratum_key(record, quota_key), []).append(record)
        for stratum in strata.values():
            # 정보량이 큰 시나리오를 층 안에서 먼저 뽑는다
            stratum.sort(key=lambda r: (-_richness(r), r.scenario_id))
        order = sorted(strata, key=lambda _k: rng.random())
        chosen.extend(_pick_from_strata(strata, quota, order, len(records)))

    chosen.sort(key=lambda r: r.scenario_id)
    return chosen


@SAMPLING_STRATEGIES.register(STRATEGY_NAME)
class StratifiedSampling(SamplingStrategy):
    options_model = StratifiedOptions

    def __call__(
        self, records: list[ScenarioRecord], n: int, options: StratifiedOptions, seed: int
    ) -> list[ScenarioRecord]:
        return stratified_sample(records, n, options, seed)
