"""audit 스테이지: 실행 하나의 점검표를 runs/<run_id>/audit.json으로 남긴다 (LLM 불필요, 조회만)."""

from tsdgen import audit as audit_module
from tsdgen.config import Settings
from tsdgen.log import get_logger
from tsdgen.runs import AUDIT, USAGE
from tsdgen.stages.common import stage, stage_output
from tsdgen.util import read_jsonl, write_json
from tsdgen.verify.checks import VERIFY_CHECKS

log = get_logger("tsdgen.audit")


def _spent(settings: Settings) -> dict:
    rows = [u for u in read_jsonl(settings.run_dir() / USAGE) if not u.get("cached")]
    return {"calls": len(rows), "usd": round(sum(u.get("cost_usd") or 0 for u in rows), 3)}


@stage("audit")
def run(settings: Settings) -> dict:
    records = list(read_jsonl(stage_output(settings, "verify")))
    if not records:
        raise FileNotFoundError("no verified samples; run the verify stage first")
    fields = [f for f in (VERIFY_CHECKS.get(name).field for name in settings.verify.checks)
              if f != "schema_valid"]
    result = audit_module.audit(
        records,
        list(read_jsonl(stage_output(settings, "select"))),
        list(read_jsonl(stage_output(settings, "contextualize"))),
        fields,
    )
    result["run_id"] = settings.project.run_id
    result["spent"] = _spent(settings)
    out = settings.run_dir() / AUDIT
    write_json(out, result)
    log.info(f"yield {result['yield']} -> {out}")
    return {"output": str(out)}
