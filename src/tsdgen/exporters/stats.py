"""stats.json 내보내기: 태스크별·모델별 통과율, 체크리스트 항목별 평균, 사용량."""

from collections import defaultdict

from tsdgen.exporters.base import ExportContext
from tsdgen.models import PASSED_STATUSES, VERIFIED_STATUSES, task_label
from tsdgen.registry import EXPORT_FORMATS
from tsdgen.runs import USAGE
from tsdgen.util import read_jsonl, write_json

UNKNOWN = "unknown"


def _pass_rates(samples) -> dict:
    def rate(bucket: dict) -> dict:
        out = {}
        for key, (passed, total) in sorted(bucket.items()):
            out[key] = {"accepted": passed, "total": total, "rate": round(passed / total, 3)}
        return out

    by_model: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_task: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for sample in samples:
        accepted = sample.meta.status in PASSED_STATUSES
        model = sample.meta.generator.model if sample.meta.generator else UNKNOWN
        task = task_label(sample.scenario_id, UNKNOWN)
        for bucket, key in ((by_model, model), (by_task, task)):
            bucket[key][1] += 1
            bucket[key][0] += int(accepted)
    return {"by_model": rate(by_model), "by_task": rate(by_task)}


def _checklist_means(samples) -> dict:
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    for sample in samples:
        verification = sample.meta.verification
        if verification and verification.checklist:
            for key, item in verification.checklist.items.items():
                sums[key][0] += item.score
                sums[key][1] += 1
    return {k: round(v[0] / v[1], 3) for k, v in sorted(sums.items()) if v[1]}


def _goal_summary(samples) -> dict:
    """목표 상태 검사 통과 수, 원천 정답이 있는 샘플 수와 평균 재현율."""
    checked = passed = with_oracle = 0
    recalls = []
    for sample in samples:
        verification = sample.meta.verification
        if verification and verification.goal:
            checked += 1
            passed += int(verification.goal.pass_)
        oracle = verification.goal_oracle if verification else None
        if oracle and oracle.available:
            with_oracle += 1
            if oracle.recall is not None:  # 정답 조건이 모두 처음 씬에서 이미 성립하면 잴 것이 없다
                recalls.append(oracle.recall)
    mean = round(sum(recalls) / len(recalls), 3) if recalls else None
    return {"goal_checked": checked, "goal_passed": passed,
            "oracle_available": with_oracle, "oracle_recall_mean": mean}


def _usage_summary(ctx: ExportContext) -> dict:
    usage_path = ctx.settings.run_dir() / USAGE
    totals: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                 "cost_known": True}
    )
    for entry in read_jsonl(usage_path):
        t = totals[entry["model"]]
        t["calls"] += 1
        t["input_tokens"] += entry.get("input_tokens", 0)
        t["output_tokens"] += entry.get("output_tokens", 0)
        if entry.get("cost_usd") is None:
            t["cost_known"] = False
        elif not entry.get("cached"):
            t["cost_usd"] += entry["cost_usd"]
    return dict(totals)


def build_stats(ctx: ExportContext) -> dict:
    accepted = ctx.by_status("accepted")
    text_lengths = [
        len(s.output.task_situation_description_text) for s in accepted if s.output
    ]
    return {
        "date": ctx.date_str,
        "run_id": ctx.settings.project.run_id,
        "counts": {status: len(ctx.by_status(status)) for status in VERIFIED_STATUSES},
        "scenarios": len(ctx.scenarios),
        "pass_rates": _pass_rates(ctx.samples),
        "checklist_item_means": _checklist_means(ctx.samples),
        "goal": _goal_summary(ctx.samples),
        "mean_tsd_text_chars": (
            round(sum(text_lengths) / len(text_lengths), 1) if text_lengths else None
        ),
        "usage": _usage_summary(ctx),
    }


@EXPORT_FORMATS.register("stats")
def export_stats(ctx: ExportContext) -> None:
    if not ctx.stats:
        ctx.stats = build_stats(ctx)
    write_json(ctx.out_dir / "stats.json", ctx.stats)
