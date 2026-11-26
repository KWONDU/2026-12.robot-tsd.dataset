"""개요 화면: 이 실행의 결과를 위에서부터 좁혀 본다.

흐름도(파이프라인 단계마다 남은 수와 빠진 수, 최종 판정), 검수 깔때기(검수 항목 순서대로 남은 샘플),
모델별 성적표, 체크리스트 항목 점수. 집계만 보이고 장면·샘플 하나하나는 태스크 화면에 있다. 빠진 수와
판정을 누르면 그 조건으로 걸러진 태스크 목록으로 간다.
"""

from collections import Counter, defaultdict
from collections.abc import Callable

from tsdgen.dashboard import rubric
from tsdgen.dashboard.data import RunData
from tsdgen.dashboard.progress import DONE, run_progress
from tsdgen.dashboard.verdicts import (
    ACCEPTED,
    NEEDS_REVIEW,
    OTHER,
    REJECTED,
    RESOLVABILITY,
    STATUS_TONE,
    TASKS_FILTER,
    check_label,
    reason_of,
)
from tsdgen.dashboard.views.base import (
    Block,
    mean,
    model_of,
    sid,
    status_of,
    table,
    task_of,
    term,
    verification,
    view,
)
from tsdgen.runs import MANIFEST

overview = view("overview", "개요")

HALF = "half"  # 넓은 화면에서 두 블록을 나란히 놓는다


# ---- 흐름도: 단계마다 붙는 부가 정보 ------------------------------------------ #
# 단계 이름 -> (RunData, 진행 행) -> {"value", "caption", "notes", "drops"} 중 그 단계에 있는 것

def _stage_result(data: RunData, stage: str) -> dict:
    return (data.json_file(MANIFEST).get("stage_results") or {}).get(stage) or {}


def _sample_step(data: RunData, row: dict) -> dict:
    names = data.lexicon().get("scenario_filter_ko", {})
    filters = Counter(e.get("filter", OTHER) for e in data.excluded())
    pool = _stage_result(data, "ingest").get("total")
    return {"caption": f"후보 {pool:,}건 중" if pool else None,
            "drops": [{"label": "필터로 뺌", "detail": names.get(f, f), "value": n, "tone": "muted"}
                      for f, n in filters.most_common()]}


def _contextualize_step(data: RunData, row: dict) -> dict:
    records = data.contextualized()
    fallback = sum(1 for r in records if r.get("fallback_used"))
    unresolved = sum(1 for r in records if (r.get("resolvability") or {}).get("pass") is False)
    resolvability = data.lexicon()["verify_check_ko"].get(RESOLVABILITY, RESOLVABILITY)
    notes = [{"label": "다시 쓴 지시", "value": fallback},
             {"label": f"{resolvability} 안 됨", "value": unresolved}]
    return {"notes": [n for n in notes if n["value"]]}


def _generate_step(data: RunData, row: dict) -> dict:
    failed = _stage_result(data, "generate").get("failed")
    if failed is None and data.status() == DONE and row["expected"]:
        failed = max(row["expected"] - row["records"], 0)
    return {"drops": [{"label": "생성 실패", "value": failed, "tone": "warn"}] if failed else []}


def _verify_step(data: RunData, row: dict) -> dict:
    samples = data.rows("verify")
    statuses = Counter(status_of(s) for s in samples)
    passed = sum(1 for s in samples if verification(s).get("final_pass"))
    labels = data.lexicon()["status_ko"]
    drops = [{"label": term(labels.get(st, st), st), "value": statuses[st],
              "tone": STATUS_TONE[st], "link": f"{TASKS_FILTER}/{st}"}
             for st in (REJECTED, NEEDS_REVIEW) if statuses.get(st)]
    return {"value": passed, "caption": f"{len(samples):,}건 중 통과" if samples else None,
            "drops": drops}


def _select_step(data: RunData, row: dict) -> dict:
    accepted = sum(1 for s in data.rows("select") if status_of(s) == ACCEPTED)
    return {"value": accepted, "caption": "시나리오마다 하나씩 채택" if accepted else None,
            "link": f"{TASKS_FILTER}/{ACCEPTED}" if accepted else None}


STEP_DETAILS: dict[str, Callable[[RunData, dict], dict]] = {
    "sample": _sample_step, "contextualize": _contextualize_step, "generate": _generate_step,
    "verify": _verify_step, "select": _select_step,
}


def _step_state(run_status: str, records: int, expected: int | None) -> str:
    if run_status == DONE or (expected and records >= expected):
        return "done"
    return "active" if records else "waiting"


def _multiplier(expected: int | None, previous: int | None) -> int | None:
    """앞 단계보다 기대 레코드가 배로 늘면(시나리오당 생성 수) 그 배수. 화살표와 설명에 적는다."""
    if not expected or not previous or expected <= previous:
        return None
    return round(expected / previous)


def _flow_steps(data: RunData) -> list[dict]:
    progress = run_progress(data)
    steps, previous = [], None
    for row in progress["stages"]:
        detail = STEP_DETAILS.get(row["stage"], lambda _d, _r: {})(data, row)
        state = _step_state(progress["status"], row["records"], row["expected"])
        times, previous = _multiplier(row["expected"], previous), row["expected"]
        caption = detail.get("caption") or (f"시나리오당 {times}개씩" if times else None)
        steps.append({
            "key": row["stage"], "title": row["label"], "state": state,
            "edge": f"×{times}" if times else None,
            "value": detail.get("value", row["records"]), "caption": caption,
            "progress": None if state == "done" else {"done": row["records"],
                                                      "expected": row["expected"]},
            "link": detail.get("link"), "notes": detail.get("notes", []),
            "drops": detail.get("drops", []),
        })
    return steps


@overview
def pipeline_flow(data: RunData) -> Block | None:
    if not data.scenarios():
        return None
    labels = data.lexicon()["status_ko"]
    statuses = Counter(status_of(s) for s in data.samples())
    outcomes = [{"label": term(labels.get(st, st), st), "value": statuses[st],
                 "tone": STATUS_TONE.get(st, "accent"), "link": f"{TASKS_FILTER}/{st}"}
                for st in labels if statuses.get(st)]
    return {"kind": "flow", "label": "데이터 흐름",
            "note": "아래로 갈라진 칸은 그 단계에서 빠진 수예요. 미달 수나 최종 판정을 누르면 해당 태스크 목록이 열려요.",
            "data": {"steps": _flow_steps(data), "outcomes": outcomes}}


@overview
def verify_funnel(data: RunData) -> Block | None:
    """검수 항목을 순서대로 지나며 샘플이 어디서 빠지는지. 앞에서 걸리면 뒤 항목은 돌지 않는다."""
    samples = [s for s in data.samples() if status_of(s) != NEEDS_REVIEW]
    if not samples:
        return None
    failed, evidence = Counter(), []
    for s in samples:
        if status_of(s) != REJECTED:
            continue
        check, quote = reason_of(data, s)
        failed[check] += 1
        evidence.append([check_label(data, check), sid(s), task_of(s), model_of(s), quote])
    remaining, rows = len(samples), []
    for gate in dict.fromkeys([*data.verify_checks(), RESOLVABILITY, *failed]):
        dropped = failed.get(gate, 0)
        remaining -= dropped
        info = rubric.check_info(data, gate)
        rows.append({"key": gate, "label": check_label(data, gate), "dropped": dropped,
                     "remaining": remaining, "desc": info["desc"], "rule": info["rule"],
                     "link": f"{TASKS_FILTER}/{REJECTED}/{gate}" if dropped else None})
    note = "위에서부터 차례로 검사하고, 앞에서 걸린 샘플은 뒤 검사를 건너뛰어요. 줄에 마우스를 올리면 무엇을 보는지, 빠진 수를 누르면 그 태스크들이 나와요."
    review = len(data.samples()) - len(samples)
    if review:
        note += f" 채점 호출이 실패한 재검토 {review}건은 뺐어요."
    return {"kind": "funnel", "label": "검수 단계별로 남은 샘플", "note": note, "width": HALF,
            "data": {"total": len(samples), "rows": rows, "result": "검수 통과"},
            "fold": table(["걸린 단계", "샘플", "태스크", "모델", "근거"],
                          sorted(evidence, key=lambda r: r[0]))}


def _model_metrics(data: RunData, group: list[dict]) -> dict[str, str]:
    v = [verification(s) for s in group]
    passed = sum(1 for x in v if x.get("final_pass"))
    checklist = [x["checklist"]["total"] for x in v if x.get("checklist")]
    full = data.settings.verify.score_max * len(data.lexicon()["checklist_items"])
    probe = [x["sufficiency_probe"]["score"] for x in v if x.get("sufficiency_probe")]
    goal = [x["goal"] for x in v if x.get("goal")]
    oracle = [x["goal_oracle"]["recall"] for x in v
              if (x.get("goal_oracle") or {}).get("recall") is not None]
    return {
        "샘플": f"{len(group)}",
        "검수 통과": f"{passed}/{len(group)} ({passed / len(group) * 100:.0f}%)",
        "채택": f"{sum(1 for s in group if status_of(s) == ACCEPTED)}",
        f"체크리스트 합계 평균 (/{full})": f"{mean(checklist)}" if checklist else "-",
        f"정보 충분성 평균 (/{data.settings.verify.score_max})": f"{mean(probe)}" if probe else "-",
        "목표 검사 통과": f"{sum(1 for g in goal if g.get('pass'))}/{len(goal)}" if goal else "",
        "정답 맞힌 비율 평균": f"{mean(oracle)}" if oracle else "",
    }


@overview
def model_scorecard(data: RunData) -> Block | None:
    groups: dict[str, list[dict]] = defaultdict(list)
    for s in data.samples():
        groups[model_of(s)].append(s)
    if not groups:
        return None
    metrics = {m: _model_metrics(data, g) for m, g in sorted(groups.items())}
    names = list(next(iter(metrics.values())))
    rows = [[name, *[metrics[m][name] for m in metrics]] for name in names
            if any(metrics[m][name] for m in metrics)]  # 이 실행에 값이 없는 지표 줄은 숨긴다
    return {"kind": "table", "label": "생성 모델별 성적표", "width": HALF,
            "note": "같은 시나리오를 모델마다 하나씩 만들었어요. 채점은 생성한 모델과 다른 모델이 해요.",
            "data": table(["", *metrics], rows)}


@overview
def checklist_scores(data: RunData) -> Block | None:
    names, scale = data.lexicon()["checklist_items"], rubric.score_scale(data)
    per_item: dict[str, list[int]] = defaultdict(list)
    for s in data.samples():
        for key, item in ((verification(s).get("checklist") or {}).get("items") or {}).items():
            per_item[key].append(item["score"])
    if not per_item:
        return None
    rows = []
    for key, name in names.items():
        scores = Counter(per_item.get(key, []))
        rows.append([term(name, key), mean(per_item.get(key, [])) or "-",
                     *[scores.get(s["score"], 0) for s in reversed(scale)]])
    columns = ["항목", "평균", *[f"{s['score']}점 {s['label']}" for s in reversed(scale)]]
    return {"kind": "table", "label": "체크리스트 항목별 점수",
            "note": "항목마다 무엇을 보는지는 프롬프트 화면의 체크리스트 채점에 있어요.",
            "data": table(columns, rows)}
