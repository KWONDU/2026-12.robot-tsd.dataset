"""select 스테이지: 시나리오당 전달 샘플 선별 (스펙 6.5).

best_of_k: accepted 중 검수 총점 최고 1건을 유지하고 나머지는 alternate.
동점이면 generator 다양성이 높아지도록 지금까지 덜 뽑힌 모델을 우선한다.
all: 모든 accepted를 유지 (전량 생성 모드).
"""

from collections import Counter

from tsdgen.config import Settings
from tsdgen.log import get_logger
from tsdgen.models import Sample, dump
from tsdgen.registry import SELECTION_STRATEGIES
from tsdgen.stages.common import load_samples, stage, stage_output
from tsdgen.util import write_jsonl

log = get_logger("tsdgen.select")


def _generator_of(sample: Sample) -> str:
    """생성 모델 이름. 생성 정보가 없는 샘플도 선별을 멈추지 않게 빈 이름으로 다룬다."""
    return sample.meta.generator.model if sample.meta.generator else ""


def _score(sample: Sample) -> int:
    verification = sample.meta.verification
    if verification is None:
        return -1
    checklist = verification.checklist.total if verification.checklist else 0
    probe = verification.sufficiency_probe.score if verification.sufficiency_probe else 0
    return checklist + probe


@SELECTION_STRATEGIES.register("best_of_k")
def best_of_k(by_scenario: dict[str, list[Sample]]) -> list[Sample]:
    winner_counts: Counter[str] = Counter()
    out: list[Sample] = []
    for scenario_id in sorted(by_scenario):
        candidates = by_scenario[scenario_id]
        accepted = [s for s in candidates if s.meta.status == "accepted"]
        if accepted:
            best = max(
                accepted,
                key=lambda s: (_score(s), -winner_counts[_generator_of(s)]),
            )
            winner_counts[_generator_of(best)] += 1
            for sample in candidates:
                if sample.meta.status == "accepted" and sample is not best:
                    sample.meta.status = "alternate"
        out.extend(candidates)
    return out


@SELECTION_STRATEGIES.register("all")
def keep_all(by_scenario: dict[str, list[Sample]]) -> list[Sample]:
    return [s for scenario_id in sorted(by_scenario) for s in by_scenario[scenario_id]]


@stage("select")
def run(settings: Settings) -> dict:
    in_path = stage_output(settings, "verify")
    out_path = stage_output(settings, "select")
    samples = load_samples(in_path)
    if not samples:
        raise FileNotFoundError("no verified samples; run the verify stage first")

    by_scenario: dict[str, list[Sample]] = {}
    for sample in samples:
        by_scenario.setdefault(sample.scenario_id, []).append(sample)
    selected = SELECTION_STRATEGIES.of(settings)(by_scenario)
    # 선별은 전량 재계산이 안전하다 (idempotent). 이전 결과에 이어 붙이지 않는다.
    written = write_jsonl(out_path, (dump(s) for s in selected))
    statuses = Counter(s.meta.status for s in selected)
    log.info(f"{written} samples ({dict(statuses)}) -> {out_path}")
    return {"written": written, "statuses": dict(statuses), "output": str(out_path)}
