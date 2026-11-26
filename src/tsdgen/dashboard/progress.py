"""실행 진행 상황: 스테이지별 레코드 수와 기대치, LLM 호출 수, API 비용, 경과·남은 시간.

파일로만 계산한다. 기대치는 실행 전 비용 추정(estimate.json)이 있으면 그 값(시나리오 수, 시나리오당 생성 수,
총 호출 수, 추정 비용)을 쓰고, 없으면 선별된 시나리오 수와 설정의 시나리오당 생성 수로 셈한다. 대시보드가
짧은 간격으로 다시 읽으므로 무거운 계산을 하지 않는다.
"""

import time
from datetime import datetime
from typing import Any

from tsdgen.dashboard.data import PROGRESS_STAGES, RunData
from tsdgen.dashboard.terms import term
from tsdgen.runs import ESTIMATE, MANIFEST

# 스테이지 -> 기대 레코드 수를 셀 때 곱하는 단위. 시나리오당 한 건인지, 시나리오당 생성 수만큼인지.
PER_GENERATION = {"generate", "verify", "select"}
DONE, RUNNING = "done", "running"  # data.RunData.status 값


def _generations_per_scenario(data: RunData, estimate: dict) -> int:
    snapshot = (data.json_file(MANIFEST).get("config_snapshot") or {}).get("llm") or {}
    return estimate.get("per_scenario_k") or snapshot.get("per_scenario_k") \
        or data.settings.llm.per_scenario_k


def _stage_rows(data: RunData, n_scenarios: int, k: int) -> list[dict[str, Any]]:
    names = data.lexicon()["stage_ko"]
    rows = []
    for stage in PROGRESS_STAGES:
        expected = n_scenarios * (k if stage in PER_GENERATION else 1)
        records = data.count(stage)
        rows.append({"stage": stage, "label": term(names.get(stage, stage), stage),
                     "records": records, "expected": expected or None})
    return rows


def _timing(usage: list[dict], status: str, updated: float) -> dict[str, float | None]:
    stamps = sorted(datetime.fromisoformat(u["ts"]).timestamp() for u in usage if u.get("ts"))
    if not stamps:
        return {"started": None, "elapsed_s": None}
    end = time.time() if status == RUNNING else max(stamps[-1], updated)
    return {"started": stamps[0], "elapsed_s": end - stamps[0]}


def _percent(stages: list[dict], status: str) -> float | None:
    """전체 진행률: 스테이지 레코드 합 / 기대치 합. 끝난 실행은 100이다 (생성 실패 등으로 모자라도)."""
    if status == DONE:
        return 100.0
    expected = sum(st["expected"] or 0 for st in stages)
    done = sum(min(st["records"], st["expected"] or 0) for st in stages)
    return round(done / expected * 100, 1) if expected else None


def run_progress(data: RunData) -> dict[str, Any]:
    estimate = data.json_file(ESTIMATE)
    n_scenarios = estimate.get("n_scenarios") or data.count("sample")
    k = _generations_per_scenario(data, estimate)
    usage = data.usage()
    live = [u for u in usage if not u.get("cached")]
    status = data.status()
    updated = data.last_modified()
    timing = _timing(usage, status, updated)
    calls_expected = estimate.get("total_calls")
    eta = None
    if status == RUNNING and timing["elapsed_s"] and usage and calls_expected:
        remaining = max(calls_expected - len(usage), 0)
        eta = timing["elapsed_s"] / len(usage) * remaining
    stages = _stage_rows(data, n_scenarios, k)
    return {
        "status": status,
        "updated": updated,
        "percent": _percent(stages, status),
        "stages": stages,
        "calls": {"done": len(usage), "live": len(live), "expected": calls_expected},
        "cost": {
            "spent_usd": round(sum(u["cost_usd"] for u in live if u.get("cost_usd") is not None), 4),
            "with_cache_usd": round(sum(u["cost_usd"] for u in usage
                                        if u.get("cost_usd") is not None), 4),
            "estimate_usd": estimate.get("total_cost_usd"),
        },
        **timing,
        "eta_s": eta,
    }
