"""export 스테이지: 선별 결과를 deliverables/<run_id>_<date>/로 내보낸다."""

from datetime import UTC, datetime

from tsdgen.config import Settings
from tsdgen.exporters.base import ExportContext
from tsdgen.exporters.stats import build_stats
from tsdgen.log import get_logger
from tsdgen.registry import EXPORT_FORMATS
from tsdgen.stages.common import load_samples, stage, stage_output
from tsdgen.util import read_jsonl

log = get_logger("tsdgen.export")


@stage("export")
def run(settings: Settings) -> dict:
    samples = load_samples(stage_output(settings, "select"))
    if not samples:
        raise FileNotFoundError("no selected samples; run the select stage first")
    scenario_ids = {s.scenario_id for s in samples}
    scenarios = [
        rec.get("scenario", rec)
        for rec in read_jsonl(stage_output(settings, "sample"))
        if rec.get("scenario_id") in scenario_ids
    ]
    date_str = datetime.now(UTC).strftime("%Y%m%d")
    out_dir = settings.path("deliverables") / f"{settings.project.run_id}_{date_str}"
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx = ExportContext(
        settings=settings,
        out_dir=out_dir,
        date_str=date_str,
        samples=samples,
        scenarios=scenarios,
    )
    ctx.stats = build_stats(ctx)
    for format_name in settings.export.formats:
        EXPORT_FORMATS.get(format_name)(ctx)
    log.info(f"formats {settings.export.formats} -> {out_dir}")
    return {"output": str(out_dir), "counts": ctx.stats["counts"]}
