"""판정과 미달 사유를 화면용으로 읽는다. 개요(흐름도·깔때기·성적표)와 샘플 화면이 같이 쓴다.

미달 샘플은 검수 항목을 차례로 돌다 처음 걸린 곳이 사유다(verify 스테이지가 skipped_reason에 남긴다).
모든 항목을 통과했는데도 미달이면 재작성 단계의 지시 해소 판정이 사유다.
"""

from tsdgen.dashboard.data import RunData
from tsdgen.dashboard.records import verification
from tsdgen.dashboard.terms import term
from tsdgen.verify.checks import VERIFY_CHECKS

# 판정 상태 -> 색 (스타일 토큰 이름). 표시명은 공용 사전(status_ko)에 있다. 차순위는 통과분이라 문제 색을 쓰지 않는다.
STATUS_TONE = {"accepted": "good", "alternate": "accent", "rejected": "bad", "needs_review": "bad"}
REJECTED, NEEDS_REVIEW = "rejected", "needs_review"
TASKS_FILTER = "tasks/filter"  # 걸러진 태스크 목록 경로: tasks/filter/<판정>[/<걸린 단계>]
ACCEPTED = "accepted"
RESOLVABILITY = "resolvability"  # 재작성 단계의 지시 해소 판정. 검수 항목을 다 지난 뒤에 반영된다

OTHER = "other"


def _listed_problems(v: dict, check: str, problem_names: dict[str, str]) -> str:
    """걸린 검수 항목의 결과에서 목록으로 남은 문제(씬에 없는 물체 등)를 한 줄로 모은다."""
    registered = VERIFY_CHECKS.names()
    result = v.get(VERIFY_CHECKS.get(check).field) if check in registered else None
    if not isinstance(result, dict):
        return ""
    return " / ".join(f"{problem_names.get(key, key)}: {', '.join(map(str, value))}"
                      for key, value in result.items() if isinstance(value, list) and value)


def rejection_reason(v: dict, names: dict[str, str], score_max: int,
                     problem_names: dict[str, str] | None = None) -> tuple[str, str]:
    """미달 샘플이 걸린 검수 단계(등록 이름, 재작성의 지시 해소는 resolvability)와 그 근거 원문."""
    if v.get("skipped_reason"):
        check = v["skipped_reason"].split(" failed")[0]
        items = (v.get("checklist") or {}).get("items", {})
        zeros = [f"{names.get(k, k)}: {it['rationale']}"
                 for k, it in items.items() if it["score"] == 0]
        listed = _listed_problems(v, check, problem_names or {})
        return check, " / ".join(zeros) or listed or v["skipped_reason"]
    resolvability = v.get("instruction_resolvability") or {}
    if resolvability.get("pass") is False:
        return RESOLVABILITY, resolvability.get("rationale", "")
    if v.get("checklist") and not v["checklist"]["pass"]:
        items = v["checklist"].get("items", {})
        low = [f"{names.get(k, k)} {it['score']}점: {it['rationale']}"
               for k, it in items.items() if it["score"] < score_max]
        return "checklist", " / ".join(low)
    probe = v.get("sufficiency_probe") or {}
    if probe and not probe.get("pass"):
        return "probe", " / ".join(probe.get("missing_info", [])) or "근거 없음"
    return OTHER, "근거 없음"


def reason_of(data: RunData, sample: dict) -> tuple[str, str]:
    """화면에서 쓰는 미달 사유: 사전의 체크리스트 이름과 문제 목록 이름을 붙여 rejection_reason을 부른다."""
    lexicon = data.lexicon()
    return rejection_reason(verification(sample), lexicon["checklist_items"],
                            data.settings.verify.score_max, lexicon.get("verify_problem_ko", {}))


def check_label(data: RunData, check: str) -> str:
    """검수 단계 표기: 한국어 (등록 이름)."""
    return term(data.lexicon()["verify_check_ko"].get(check, check), check)
