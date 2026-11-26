"""JSONL 내보내기: samples/alternates/rejected/scenarios (스펙 6.6)."""

from tsdgen.exporters.base import ExportContext
from tsdgen.models import FAILED_STATUSES, dump
from tsdgen.registry import EXPORT_FORMATS
from tsdgen.util import write_jsonl


@EXPORT_FORMATS.register("jsonl")
def export_jsonl(ctx: ExportContext) -> None:
    outputs = {
        "samples.jsonl": ctx.by_status("accepted"),
        "alternates.jsonl": ctx.by_status("alternate"),
        # 미달은 삭제하지 않고 negative로 보존한다 (judge 호출 실패분 포함)
        "rejected.jsonl": ctx.by_status(*FAILED_STATUSES),
    }
    for filename, samples in outputs.items():
        write_jsonl(ctx.out_dir / filename, (dump(s) for s in samples))
    write_jsonl(ctx.out_dir / "scenarios.jsonl", ctx.scenarios)
