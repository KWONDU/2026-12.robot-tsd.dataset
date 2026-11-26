"""실행 정보 화면: 이 실행을 어떤 설정으로 돌렸고 얼마나 들었는지 (내부용, 외부 전달물에는 싣지 않는다).

실행 정보, 비용과 시간, 모델별 지출, 역할별 프롬프트·모델, 설정·프롬프트 원문.
원문은 작업 트리의 현재 파일이 아니라 실행 폴더의 사본(_config/, _prompts/)이다. 사본이 없는 실행만 지금
파일을 보여 주고 그렇다고 적는다.
"""

from collections import Counter, defaultdict
from datetime import datetime

from tsdgen import prompts, roles
from tsdgen.dashboard.data import RunData
from tsdgen.dashboard.views.base import (
    Block,
    clock,
    duration_text,
    model_of,
    table,
    term,
    verification,
    view,
)
from tsdgen.runs import CONFIG_SNAPSHOT_DIR, MANIFEST, PROMPT_SNAPSHOT_DIR, USAGE
from tsdgen.util import read_jsonl

run_info = view("run", "실행 정보")

PALETTE_SIZE = 6  # 스타일 토큰 c1부터 c6까지 돌려 쓴다
HALF = "half"
CURRENT_NOTE = "이 실행에는 사본이 없어서 지금 작업 폴더의 파일이에요. 실행 당시와 다를 수 있어요."


def _prompt_versions(data: RunData) -> dict[str, str]:
    """역할별로 이 실행이 실제로 쓴 프롬프트 버전. manifest가 없으면 샘플 기록에서 찾는다."""
    hashes = data.json_file(MANIFEST).get("prompt_hashes") or {}
    versions = {role: value.split(":")[0] for role, value in hashes.items()}
    for s in data.samples()[:1]:
        meta = s.get("meta") or {}
        versions.setdefault("generate", (meta.get("generator") or {}).get("prompt_version"))
        versions.setdefault("rewrite", (meta.get("rewriter") or {}).get("prompt_version"))
        versions.setdefault("checklist", (verification(s).get("checklist") or {}).get(
            "rubric_version"))
    return {role: version for role, version in versions.items() if version}


@run_info
def run_state(data: RunData) -> Block:
    summary, manifest = data.summary(), data.json_file(MANIFEST)
    labels = data.lexicon()["run_status_ko"]
    items = [
        {"key": "상태", "value": labels.get(summary["status"], summary["status"])},
        {"key": "마지막 갱신", "value": clock(summary["updated"])},
        {"key": "시나리오", "value": summary["n_scenarios"]},
        {"key": "생성 샘플", "value": summary["n_samples"]},
        {"key": "생성 모델", "value": ", ".join(sorted({model_of(s) for s in data.samples()})) or "-"},
        {"key": "코드 버전 (소스 hash)", "value": manifest.get("pipeline_version", "-")},
        {"key": "설정 hash", "value": manifest.get("config_hash", "-")},
    ]
    return {"kind": "keyvals", "label": "실행 정보", "data": items, "width": HALF}


def _spent_by_model(data: RunData) -> Counter[str]:
    """모든 실행의 실제 호출(캐시 제외) 비용을 모델별로 합한 것 = 실제 결제액 내역."""
    spent: Counter[str] = Counter()
    for path in data.settings.path("runs").glob(f"*/{USAGE}"):
        for u in read_jsonl(path):
            if not u.get("cached") and u.get("cost_usd") is not None:
                spent[u.get("model", "?")] += u["cost_usd"]
    return spent


@run_info
def cost_summary(data: RunData) -> Block | None:
    records = data.usage()
    if not records:
        return None
    live = [u for u in records if not u.get("cached")]
    spent = sum(u["cost_usd"] for u in live if u.get("cost_usd") is not None)
    stamps = sorted(datetime.fromisoformat(u["ts"]) for u in records if u.get("ts"))
    span = (stamps[-1] - stamps[0]).total_seconds() if len(stamps) > 1 else 0.0
    n_samples = len(data.samples())
    items = [
        {"key": "이번 실행 지출", "value": f"${spent:.2f}"},
        {"key": "모든 실행 합계", "value": f"${sum(_spent_by_model(data).values()):.2f}"},
        {"key": "LLM 호출", "value": f"{len(records):,}회 (캐시로 재사용 {len(records) - len(live):,}회)"},
        {"key": "LLM 호출에 걸린 시간", "value": duration_text(span)},
        {"key": "샘플당 시간", "value": duration_text(span / n_samples) if n_samples else "-"},
    ]
    return {"kind": "keyvals", "label": "비용과 시간", "data": items, "width": HALF}


@run_info
def roles_and_prompts(data: RunData) -> Block | None:
    usage = data.usage()
    if not usage and not data.samples():
        return None
    role_names, versions = data.lexicon()["role_ko"], _prompt_versions(data)
    by_role: dict[str, dict] = defaultdict(lambda: {"models": set(), "calls": 0, "cost": 0.0})
    for u in usage:
        row = by_role[u.get("role", "?")]
        row["models"].add(u.get("model", "?"))
        row["calls"] += 1
        row["cost"] += 0.0 if u.get("cached") else (u.get("cost_usd") or 0.0)
    rows = [[term(role_names.get(r, r), r), versions.get(r, "-"),
             ", ".join(sorted(by_role[r]["models"])) or "-", by_role[r]["calls"],
             f"${by_role[r]['cost']:.2f}"] for r in role_names]
    return {"kind": "table", "label": "역할별 프롬프트와 모델", "width": HALF,
            "data": table(["역할", "프롬프트 버전", "모델", "호출", "지출"], rows)}


@run_info
def cost_by_model(data: RunData) -> Block | None:
    spent = _spent_by_model(data)
    if not spent:
        return None
    donut = [{"label": model, "value": round(cost, 2), "tone": f"c{i % PALETTE_SIZE + 1}"}
             for i, (model, cost) in enumerate(sorted(spent.items()))]
    return {"kind": "donut", "label": "모델별 지출 (모든 실행)", "data": donut, "unit": "usd",
            "width": HALF}


def _current_prompts(data: RunData) -> list[dict]:
    files = []
    for role in roles.all_roles():
        version = role.prompt_version(data.settings)
        path = prompts.template_path(data.settings, role.name, version)
        if path.exists():
            files.append({"name": f"{role.name}/{version}.txt (지금 파일)",
                          "text": path.read_text(encoding="utf-8")})
    return files


@run_info
def source_files(data: RunData) -> Block | None:
    copies = [*data.snapshot_files(CONFIG_SNAPSHOT_DIR), *data.snapshot_files(PROMPT_SNAPSHOT_DIR)]
    files = [{"name": p.name, "text": p.read_text(encoding="utf-8")} for p in copies]
    if not data.snapshot_files(PROMPT_SNAPSHOT_DIR):
        files += _current_prompts(data)
    if not files:
        return None
    note = "이 실행에 실제로 쓴 설정과 프롬프트 사본이에요. 위에서 파일을 고르세요."
    if not data.snapshot_files(PROMPT_SNAPSHOT_DIR):
        note += f" {CURRENT_NOTE}"
    return {"kind": "files", "label": "설정·프롬프트 원문", "data": files, "note": note}
