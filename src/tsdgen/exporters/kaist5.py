"""KAIST 공통 메타데이터(5-tuple, JSON-LD) 내보내기 자리 (스펙 4.3, 후순위)."""

from tsdgen.exporters.base import ExportContext
from tsdgen.registry import EXPORT_FORMATS


@EXPORT_FORMATS.register("kaist5")
def export_kaist5(ctx: ExportContext) -> None:
    raise NotImplementedError(
        "kaist5 export format is a placeholder; remove it from export.formats"
    )
