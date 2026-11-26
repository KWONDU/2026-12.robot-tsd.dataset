"""태스크 화면 데이터: 시뮬레이션 장면(시나리오) 하나마다 무엇을 받아 무엇을 냈는지.

목록은 장면마다 태스크 이름·분류·지시와 모델별 판정. 상세는 시연 첫 프레임, 모델이 받은 세계 상태, 지시(원문과
재작성, 지시 해소 판정), 모델별 샘플 상세, 그 장면으로 채운 생성 프롬프트다. 모두 파이프라인이 남긴 기록을
그대로 보여 주고, 화면에서 따로 묶거나 추정한 정보는 만들지 않는다.
"""

from collections import defaultdict
from typing import Any

from tsdgen import prompts, roles, world_state
from tsdgen.dashboard import prompt_text
from tsdgen.dashboard.data import RunData
from tsdgen.dashboard.records import meta_of, model_of, status_of, task_of, verification
from tsdgen.dashboard.samples import sample_detail
from tsdgen.dashboard.verdicts import ACCEPTED, REJECTED, check_label, reason_of
from tsdgen.models import FAILED_STATUSES
from tsdgen.runs import PROMPT_SNAPSHOT_DIR


def _by_scenario(data: RunData) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for s in data.samples():
        groups[s.get("scenario_id", "?")].append(s)
    return groups


def _sample_brief(data: RunData, sample: dict) -> dict[str, Any]:
    v = verification(sample)
    check = reason_of(data, sample)[0] if status_of(sample) in FAILED_STATUSES else None
    checklist = v.get("checklist") or {}
    return {"sample_id": sample.get("sample_id"), "model": model_of(sample),
            "status": status_of(sample), "failed_check": check,
            "failed_at": check_label(data, check) if check else None,
            "checklist": checklist.get("total"),
            "checklist_max": data.settings.verify.score_max * len(checklist.get("items") or {})}


def task_rows(data: RunData) -> list[dict[str, Any]]:
    contexts = {c.get("scenario_id"): c for c in data.contextualized()}
    groups = _by_scenario(data)
    rows = []
    for scenario in data.scenarios():
        sid = scenario.get("scenario_id", "?")
        source = scenario.get("source") or {}
        context = contexts.get(sid) or {}
        samples = [_sample_brief(data, s) for s in groups.get(sid, [])]
        statuses = {s["status"] for s in samples}
        rows.append({
            "scenario_id": sid, "task": task_of(scenario), "family": source.get("task_family"),
            "activity": source.get("activity"),
            "instruction": (context.get("input") or {}).get("user_instruction", ""),
            "instruction_en": (source.get("original_instruction_en") or "").strip(),
            "samples": samples,
            "accepted": ACCEPTED in statuses,
            "all_rejected": bool(samples) and statuses <= set(FAILED_STATUSES),
            "split": len({s["status"] in FAILED_STATUSES for s in samples}) == 2,
            "checks": sorted({s["failed_check"] for s in samples if s["failed_check"]}),
        })
    return rows


def generation_prompt(data: RunData, sample: dict) -> dict[str, Any] | None:
    """그 장면으로 채운 생성 프롬프트. 유저 메시지는 모델이 받은 그대로(세계 상태 글 포함) 되살리고, 시스템 메시지는
    이 실행이 쓴 템플릿을 보여 준다(골든 예시 자리는 실행 당시 값으로 채워졌지만 그 뒤 예시가 바뀌었을 수 있어 자리로 둔다)."""
    inp = sample.get("input") or {}
    if not inp.get("environment_context"):
        return None
    version = (meta_of(sample).get("generator") or {}).get("prompt_version")
    copy = data.run_dir / PROMPT_SNAPSHOT_DIR / f"{roles.GENERATE.name}_{version}.txt"
    text = copy.read_text(encoding="utf-8") if copy.exists() else \
        prompt_text.read_version(data, roles.GENERATE.name, version or "")
    if not text:
        return None
    system, user = prompt_text.split_messages(text)
    values = {"USER_INSTRUCTION": inp.get("user_instruction", ""),
              "ENVIRONMENT_CONTEXT": world_state.model_input(inp)}
    return {"version": version, "system": system, "user": prompts.fill(user, values, strict=False)}


def task_detail(data: RunData, scenario_id: str) -> dict[str, Any] | None:
    scenario = next((s for s in data.scenarios() if s.get("scenario_id") == scenario_id), None)
    if scenario is None:
        return None
    samples = _by_scenario(data).get(scenario_id, [])
    context = next((c for c in data.contextualized() if c.get("scenario_id") == scenario_id), {})
    source = scenario.get("source") or {}
    return {
        "scenario_id": scenario_id, "task": task_of(scenario), "family": source.get("task_family"),
        "activity": source.get("activity"),
        "instruction_en": (source.get("original_instruction_en") or "").strip(),
        "rewrite": {"instruction": (context.get("input") or {}).get("user_instruction"),
                    "specificity": (context.get("input") or {}).get("instruction_specificity"),
                    "fallback_used": context.get("fallback_used"),
                    "resolvability": context.get("resolvability")},
        "world_state": world_state.model_input(samples[0]["input"]) if samples else None,
        "samples": [{**_sample_brief(data, s), "detail": sample_detail(data, s)} for s in samples],
        "prompt": generation_prompt(data, samples[0]) if samples else None,
        "rejected_any": any(status_of(s) == REJECTED for s in samples),
    }
