"""시나리오 필터: 방향 참조 지시문 제외.

지시문이 참조하는 정보가 추출된 world state에 없으면 태스크가 성립하지 않는다.
필터가 그런 지시만 잡고, 동사 lower나 leftover 같은 오탐은 내지 않아야 한다.
"""

import pytest
from tests.conftest import robocasa_adapter

from tsdgen.models import dump
from tsdgen.registry import STAGES
from tsdgen.sampling.filters import SCENARIO_FILTERS, apply_filters
from tsdgen.stages.common import scenarios_path
from tsdgen.util import append_jsonl, read_jsonl


def _record(settings, instruction):
    record = next(iter(robocasa_adapter(settings).iter_scenarios()))
    record.source.original_instruction_en = instruction
    return record


FLAGGED = [
    "Take the pizza cutter from the drawer on the left and place it on the counter.",
    "Take the pizza cutter from the left drawer.",
    "Place the liquors on the top shelf of the fridge.",
    "Open the second drawer and take out the whisk.",
    "Put the pan on the rear burner.",
    "Take a straw from the drawer in front and place it inside the glass cup.",
]
CLEAN = [
    "Lower the heat of the kettle.",          # lower가 동사
    "Put the leftovers in the fridge.",       # leftover는 방향어가 아님
    "Close the microwave door and press the start button.",
    "Take the cupcakes from the fridge and place them on the empty tray.",
    "Gather the eggs and place them right next to the stove.",  # right가 부사
]


@pytest.mark.parametrize("instruction", FLAGGED)
def test_directional_instructions_are_flagged(settings, instruction):
    record = _record(settings, instruction)
    assert SCENARIO_FILTERS.get("directional_reference")(record) is not None


@pytest.mark.parametrize("instruction", CLEAN)
def test_plain_instructions_pass(settings, instruction):
    record = _record(settings, instruction)
    assert SCENARIO_FILTERS.get("directional_reference")(record) is None


def test_sample_stage_excludes_and_records(settings):
    settings.sampling.filters[:] = ["directional_reference"]  # 실행 설정이 필터를 꺼 두어도 여기서는 켠다
    records = list(robocasa_adapter(settings).iter_scenarios())
    records[0].source.original_instruction_en = "Take the mug from the left cabinet."
    path = scenarios_path(settings)
    append_jsonl(path, (dump(r) for r in records))

    STAGES.get("sample")(settings)

    excluded = list(read_jsonl(settings.run_dir() / "sample_excluded.jsonl"))
    assert len(excluded) == 1
    assert excluded[0]["scenario_id"] == records[0].scenario_id
    assert excluded[0]["filter"] == "directional_reference"
    sampled_ids = {r["scenario_id"] for r in read_jsonl(settings.run_dir() / "sample.jsonl")}
    assert records[0].scenario_id not in sampled_ids


def test_filters_off_keeps_everything(settings):
    settings.sampling.filters.clear()
    records = list(robocasa_adapter(settings).iter_scenarios())
    records[0].source.original_instruction_en = "Take the mug from the left cabinet."
    kept, excluded = apply_filters(settings, records)
    assert not excluded and len(kept) == len(records)


@pytest.mark.parametrize("n", [1, 3, 5, 7, 13, 20, 99, 100])
def test_quota_total_never_exceeds_request(n):
    """요청한 건수보다 많이 뽑으면 안 된다.

    그룹별로 따로 반올림하면 합계가 넘친다(0.3/0.7을 5에 적용하면 2+4=6).
    100건을 요청했는데 101건이 나가면 납품 수량도 비용 추정도 어긋난다.
    """
    from tsdgen.sampling.stratified import _quotas

    quotas = _quotas(n, {"atomic": 0.3, "composite": 0.7}, {"atomic": 60, "composite": 140})
    assert sum(quotas.values()) == n


def test_quota_is_capped_by_availability():
    """가용량보다 많이 요구하면 있는 만큼만 배분하고 조용히 지어내지 않는다."""
    from tsdgen.sampling.stratified import _quotas

    quotas = _quotas(100, {"atomic": 0.5, "composite": 0.5}, {"atomic": 3, "composite": 140})
    assert quotas["atomic"] == 3
    assert sum(quotas.values()) == 100


def test_sample_stage_returns_exactly_requested_count(settings):
    """선별 결과가 config의 n_scenarios와 정확히 같아야 한다."""
    records = list(robocasa_adapter(settings).iter_scenarios())
    append_jsonl(scenarios_path(settings), (dump(r) for r in records))
    settings.project.n_scenarios = 3
    STAGES.get("sample")(settings)
    assert len(list(read_jsonl(settings.run_dir() / "sample.jsonl"))) == 3


def _earlier_run(settings, run_id, records):
    """다른 실행이 골랐던 장면 기록을 만들어 둔다."""
    folder = settings.path("runs") / run_id
    folder.mkdir(parents=True, exist_ok=True)
    append_jsonl(folder / "sample.jsonl", (dump(r) for r in records))


def test_sample_stage_repeats_the_scenes_of_another_run(settings):
    """장면 목록을 고정하면 시나리오 풀이 늘어도 같은 장면을 고른다."""
    records = list(robocasa_adapter(settings).iter_scenarios())
    append_jsonl(scenarios_path(settings), (dump(r) for r in records))
    _earlier_run(settings, "earlier", records[:2])
    settings.sampling.same_scenarios_as_run = "earlier"
    STAGES.get("sample")(settings)
    sampled = {r["scenario_id"] for r in read_jsonl(settings.run_dir() / "sample.jsonl")}
    assert sampled == {r.scenario_id for r in records[:2]}


def test_sample_stage_skips_tasks_other_runs_used(settings):
    records = list(robocasa_adapter(settings).iter_scenarios())
    append_jsonl(scenarios_path(settings), (dump(r) for r in records))
    _earlier_run(settings, "earlier", records[:1])
    settings.sampling.exclude_tasks_of_runs = ["earlier"]
    STAGES.get("sample")(settings)
    sampled = list(read_jsonl(settings.run_dir() / "sample.jsonl"))
    assert sampled and records[0].task_type_id not in {r["task_type_id"] for r in sampled}
