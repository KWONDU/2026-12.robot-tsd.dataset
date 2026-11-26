"""sample 스테이지: 층화 전략으로 n_scenarios건 선별 → runs/<run_id>/sample.jsonl."""

from tsdgen.config import Settings
from tsdgen.log import get_logger
from tsdgen.models import ScenarioRecord, dump
from tsdgen.registry import SAMPLING_STRATEGIES
from tsdgen.runs import EXCLUDED
from tsdgen.sampling.filters import apply_filters
from tsdgen.stages.common import scenarios_path, stage, stage_output
from tsdgen.util import append_jsonl, read_jsonl, write_jsonl

log = get_logger("tsdgen.sample")


def _sampled_by(settings: Settings, run_id: str) -> list[dict]:
    """다른 실행이 고른 장면 기록. 그 실행이 없으면 조용히 넘어가지 않고 멈춘다."""
    path = settings.path("runs") / run_id / stage_output(settings, "sample").name
    if not path.exists():
        raise FileNotFoundError(f"run '{run_id}' has no sample output: {path}")
    return list(read_jsonl(path))


@stage("sample")
def run(settings: Settings) -> dict:
    out_path = stage_output(settings, "sample")
    if out_path.exists():
        total = sum(1 for _ in read_jsonl(out_path))
        log.info(f"already sampled ({total}) -> {out_path}")
        return {"written": 0, "total": total, "output": str(out_path)}

    records = [ScenarioRecord.model_validate(r) for r in read_jsonl(scenarios_path(settings))]
    if not records:
        raise FileNotFoundError("no scenarios found; run the ingest stage first")

    records, excluded = apply_filters(settings, records)
    if excluded:
        excluded_path = settings.run_dir() / EXCLUDED
        write_jsonl(excluded_path, excluded)
        log.info(f"filtered out {len(excluded)} scenarios -> {excluded_path}")

    seen_tasks = {r["task_type_id"] for run_id in settings.sampling.exclude_tasks_of_runs
                  for r in _sampled_by(settings, run_id)}
    records = [r for r in records if r.task_type_id not in seen_tasks]
    pinned = settings.sampling.same_scenarios_as_run
    if pinned:
        wanted = {r["scenario_id"] for r in _sampled_by(settings, pinned)}
        chosen = [r for r in records if r.scenario_id in wanted]
        chosen.sort(key=lambda r: r.scenario_id)
    else:
        strategy = SAMPLING_STRATEGIES.of(settings)()
        chosen = strategy(records, settings.project.n_scenarios, settings.sampling_options(),
                          settings.project.seed)
    written = append_jsonl(out_path, (dump(r) for r in chosen))
    log.info(f"{written}/{len(records)} scenarios -> {out_path}")
    return {"written": written, "total": written, "output": str(out_path)}
