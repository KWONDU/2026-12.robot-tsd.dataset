"""Hugging Face datasets 규격 내보내기 자리 (스펙 6.6, 후순위)."""

from tsdgen.exporters.base import ExportContext
from tsdgen.registry import EXPORT_FORMATS


@EXPORT_FORMATS.register("hf_dataset")
def export_hf_dataset(ctx: ExportContext) -> None:
    raise NotImplementedError(
        "hf_dataset export format is a placeholder; remove it from export.formats"
    )
