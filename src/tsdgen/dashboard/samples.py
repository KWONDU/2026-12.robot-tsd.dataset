"""샘플 화면 데이터: 목록 행과 상세.

상세에는 원 샘플 레코드에 더해 화면이 바로 그릴 것을 서버에서 만들어 붙인다: 목표 상태·완료 판정·정답 대조를
사람이 읽는 줄로(표기 규칙은 goals 등록부 한 곳), 검수 항목별 통과 여부, 미달 사유, 체크리스트 항목별 점수와
채점 기준(이 실행의 채점 프롬프트), 설명문의 JSON 나무(데이터 구조 화면과 같은 모양).
"""

from collections import defaultdict
from typing import Any

from tsdgen import world_state
from tsdgen.dashboard import rubric
from tsdgen.dashboard.data import RunData
from tsdgen.dashboard.goal_text import entity_names, render_assertion
from tsdgen.dashboard.json_tree import TreeOptions, json_tree
from tsdgen.dashboard.records import meta_of, model_of, status_of, task_of, verification
from tsdgen.dashboard.terms import term
from tsdgen.dashboard.verdicts import check_label, reason_of, rejection_reason
from tsdgen.models import FAILED_STATUSES, TaskGoal, TaskSituationDescription
from tsdgen.verify.checks import VERIFY_CHECKS


def _split_scenarios(data: RunData) -> set[str]:
    """모델끼리 판정(통과·미달)이 갈린 시나리오."""
    verdicts: dict[str, set[bool]] = defaultdict(set)
    for s in data.samples():
        verdicts[s.get("scenario_id", "?")].add(bool(verification(s).get("final_pass")))
    return {scenario for scenario, seen in verdicts.items() if len(seen) == 2}


def sample_rows(data: RunData) -> list[dict[str, Any]]:
    score_max = data.settings.verify.score_max
    names = data.lexicon()["checklist_items"]
    split = _split_scenarios(data)
    rows = []
    for s in data.samples():
        v = verification(s)
        failed = status_of(s) in FAILED_STATUSES
        check = rejection_reason(v, names, score_max)[0] if failed else None
        checklist = v.get("checklist") or {}
        goal, oracle = v.get("goal") or {}, v.get("goal_oracle") or {}
        rows.append({
            "sample_id": s.get("sample_id"),
            "task": task_of(s),
            "status": status_of(s),
            "model": model_of(s),
            "checklist": checklist.get("total"),
            "checklist_max": score_max * len(checklist.get("items", {})) if checklist else None,
            "probe": (v.get("sufficiency_probe") or {}).get("score"),
            "goal": goal.get("pass") if goal else None,
            "oracle_recall": oracle.get("recall") if oracle.get("available") else None,
            "failed_at": check_label(data, check) if check else None,
            "failed_check": check,
            "instruction": (s.get("input") or {}).get("user_instruction", ""),
            "split": s.get("scenario_id") in split,
        })
    return rows


def _shown_labels(sample: dict) -> dict[str, str]:
    """화면에 보일 이름표: 모델이 이름표만 본 보기면 그 이름표, 아니면 비워 둔다(id 그대로)."""
    labels = (sample.get("input") or {}).get("entity_labels") or {}
    return labels if world_state.is_named(labels) else {}


def _display_names(sample: dict) -> dict[str, str]:
    """단언을 한 줄로 읽을 때의 이름 사전. 이름표 보기면 이름표가 곧 이름이라 덧붙일 것이 없다."""
    return {} if _shown_labels(sample) else entity_names(sample)


def _goal_cases(sample: dict, lexicon: dict) -> list[list[dict[str, Any]]]:
    """목표 상태를 경우마다 줄 목록으로. 처음 씬 판정은 검수 기록(경우들을 이어 붙인 순서)에서 읽는다."""
    tsd = (sample.get("output") or {}).get("task_situation_description") or {}
    goal = tsd.get("task_goal") if isinstance(tsd.get("task_goal"), dict) else {}
    cases = TaskGoal.model_validate({"summary": "", **goal}).goal_state if goal else []
    names = _display_names(sample)
    initial = iter((verification(sample).get("goal") or {}).get("assertion_status") or [])
    labels = lexicon["goal_status_ko"]

    def line(a) -> dict[str, Any]:
        status = next(initial, None)
        return {"text": render_assertion(a.model_dump(), names, lexicon),
                "initial": term(labels.get(status, status), status) if status else None}
    return [[line(a) for a in case] for case in cases]


def _condition_lines(sample: dict, lexicon: dict) -> list[str]:
    tsd = (sample.get("output") or {}).get("task_situation_description") or {}
    goal = tsd.get("task_goal") if isinstance(tsd.get("task_goal"), dict) else {}
    kinds = lexicon["completion_kind_ko"]  # 3.2까지의 실행에만 있는 칸이다. 기록대로 보여 준다
    return [f'[{kinds.get(c.get("kind"), c.get("kind"))}] {c.get("description")}'
            for c in goal.get("completion_conditions") or []]


def _oracle_lines(sample: dict, lexicon: dict) -> list[dict[str, Any]]:
    oracle = verification(sample).get("goal_oracle") or {}
    if not oracle.get("available"):
        return []
    names, labels = _display_names(sample), _shown_labels(sample)
    held = oracle.get("already_holds") or [False] * len(oracle["oracle"])
    shown = [world_state.map_pointers({"task_goal": {"goal_state": item["any_of"]}}, labels)
             ["task_goal"]["goal_state"] for item in oracle["oracle"]]
    return [
        {"text": " 또는 ".join(render_assertion(c, names, lexicon) for c in conditions),
         "matched": hit, "already_holds": keep, "alternative": item.get("alternative", 0)}
        for conditions, hit, keep, item in zip(shown, oracle["matched"], held, oracle["oracle"],
                                               strict=False)
    ]


def _oracle_note(sample: dict) -> str | None:
    """성공하는 경우가 여러 가지인 태스크면 어떻게 대조했는지."""
    oracle = verification(sample).get("goal_oracle") or {}
    n = oracle.get("alternatives") or 1
    return (f"성공하는 경우가 {n}가지인 태스크예요. 경우마다 설명문의 가장 가까운 목표 상태와 대조했고, "
            "맞힌 비율은 경우들의 평균이에요") if oracle.get("available") and n > 1 else None


def _check_results(data: RunData, sample: dict) -> list[dict[str, Any]]:
    """이 실행의 검수 순서대로 각 항목의 통과 여부. 앞 항목에서 멈췄으면 뒤는 실행 안 됨(None)."""
    v = verification(sample)
    results = []
    for name in data.verify_checks():
        value = v.get(VERIFY_CHECKS.get(name).field)  # schema는 참거짓 값, 나머지는 결과 객체
        passed = value if isinstance(value, bool) else (value or {}).get("pass")
        results.append({"check": name, "passed": passed})
    return results


def _reason(data: RunData, sample: dict) -> dict[str, str] | None:
    """미달이면 걸린 단계와 사람이 읽을 이유 (결과의 문제 목록까지 풀어 쓴 것)."""
    if status_of(sample) not in FAILED_STATUSES:
        return None
    check, text = reason_of(data, sample)
    return {"check": check_label(data, check), "text": text}


def _checklist_items(data: RunData, sample: dict) -> list[dict[str, Any]]:
    """체크리스트 항목마다 점수·뜻·채점 근거와, 그 항목의 채점 기준(채점 프롬프트 원문)."""
    items = (verification(sample).get("checklist") or {}).get("items") or {}
    names, criteria = data.lexicon()["checklist_items"], rubric.checklist_criteria(data)
    scale = {s["score"]: s["label"] for s in rubric.score_scale(data)}
    return [{"key": key, "label": term(names.get(key, key), key), "criteria": criteria.get(key, ""),
             "score": item["score"], "score_label": scale.get(item["score"], ""),
             "rationale": item.get("rationale", "")} for key, item in items.items()]


def _json_views(data: RunData, sample: dict) -> dict[str, Any]:
    """설명문을 데이터 구조 화면과 같은 JSON 나무로."""
    labels = data.lexicon().get("schema_field_ko", {})
    tsd = (sample.get("output") or {}).get("task_situation_description")
    return {"tsd_json": json_tree(TaskSituationDescription.model_json_schema(), tsd,
                                  TreeOptions(labels)) if tsd else None}


def sample_detail(data: RunData, sample: dict) -> dict[str, Any]:
    lexicon = data.lexicon()
    return {
        **sample,
        "has_frame": data.frame_path(sample) is not None,
        "original_instruction_en": data.original_instruction(sample),
        "view": {
            "goal_cases": _goal_cases(sample, lexicon),
            "condition_lines": _condition_lines(sample, lexicon),
            "oracle_lines": _oracle_lines(sample, lexicon),
            "oracle_note": _oracle_note(sample),
            "checks": _check_results(data, sample),
            "status": meta_of(sample).get("status"),
            "reason": _reason(data, sample),
            "checklist": _checklist_items(data, sample),
            "score_max": data.settings.verify.score_max,
            **_json_views(data, sample),
        },
    }
