"""최종 데이터셋 보기: config(dashboard.dataset)가 정한 실행들에서 검수를 통과한 샘플을 모은다. 읽기만 한다.

도메인마다 적힌 순서대로 실행을 읽고, 같은 장면(scenario_id)이 둘 이상이면 뒤에 적은 실행 것을 쓴다(다시 돌린
실행이 앞 실행을 대신한다). 샘플 파일이 커서(실행 하나에 수백 MB) 화면에 필요한 칸만 뽑은 색인을 파일마다 만들고,
파일이 바뀌지 않았으면 다시 쓴다. 장면 상세는 원래 실행의 화면을 그대로 연다(같은 내용을 두 곳에서 그리지 않는다).
"""

import json
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tsdgen.config import Settings
from tsdgen.dashboard.data import RunData, _stamp
from tsdgen.models import task_label
from tsdgen.resources import load_resources
from tsdgen.runs import stage_file

# 실행 목록 맨 위에 고정하는 가상 실행 이름. 실제 실행 이름은 영문자·숫자로 시작하므로 겹치지 않는다
DATASET_ID = "_dataset"
ACCEPTED = "accepted"
COMPOSED_MARK = "--"  # 합성 작업의 시나리오 id는 '<바탕 장면>--<유형>-<해시>' 모양이다
_INDEX: dict[Path, tuple[tuple[int, int], list[dict]]] = {}
_LOCK = threading.Lock()  # 첫 색인은 1분쯤 걸려 여러 요청이 같이 만들지 않게 한다


def _samples_file(settings: Settings, run: str) -> Path | None:
    """실행의 최종 샘플 파일: 선별이 끝났으면 select, 아니면 verify (RunData.samples와 같은 규칙)."""
    data = RunData(settings, run)
    for stage in ("select", "verify"):
        path = data.run_dir / stage_file(stage)
        if path.exists() and path.stat().st_size:
            return path
    return None


def _light(path: Path) -> list[dict]:
    """검수를 통과한 샘플마다 화면에 쓰는 칸만 남긴다."""
    rows = []
    with path.open("rb") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            meta = record.get("meta") or {}
            if meta.get("status") != ACCEPTED:
                continue
            scenario_id = record.get("scenario_id", "")
            template = (meta.get("task_template") or "").split("/")[-1]
            rows.append({
                "scenario_id": scenario_id, "sample_id": record.get("sample_id"),
                "model": (meta.get("generator") or {}).get("model"),
                "task": template or task_label(scenario_id),
                "composed": COMPOSED_MARK in scenario_id,
                "instruction": (record.get("input") or {}).get("user_instruction", ""),
            })
    return rows


def _rows_of(path: Path) -> list[dict]:
    stamp = _stamp(path)
    cached = _INDEX.get(path)
    if cached is None or cached[0] != stamp:
        cached = _INDEX[path] = (stamp, _light(path))
    return cached[1]


def dataset_rows(settings: Settings) -> list[dict]:
    """최종 데이터셋의 샘플 색인 (도메인, 원래 실행 포함). 같은 장면은 뒤에 적은 실행 것이 남는다."""
    rows: list[dict] = []
    with _LOCK:
        for domain, runs in settings.dashboard.dataset.items():
            by_scene: dict[str, dict] = {}
            for run in runs:
                path = _samples_file(settings, run)
                for row in _rows_of(path) if path else []:
                    by_scene[row["scenario_id"]] = {**row, "run": run, "domain": domain}
            rows += by_scene.values()
    return rows


def warm(settings: Settings) -> None:
    """대시보드를 켤 때 색인을 미리 만든다 (첫 화면이 기다리지 않게). 실패해도 화면을 열 때 다시 만든다."""
    if settings.dashboard.dataset:
        threading.Thread(target=dataset_rows, args=(settings,), daemon=True).start()


def _count_table(rows: list[dict], key: str, models: list[str], label) -> dict[str, Any]:
    counts: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        counts[label(row)][row["model"]] += 1
        counts[label(row)]["합"] += 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1]["합"])
    body = [[name, *[c[m] for m in models], c["합"]] for name, c in ordered]
    body.append(["합", *[sum(c[m] for _n, c in ordered) for m in models], len(rows)])
    return {"columns": [key, *models, "합"], "rows": body}


def overview_blocks(settings: Settings) -> list[dict[str, Any]]:
    """최종 데이터셋 개요: 도메인별 수, 도메인 × 에이전트, 도메인마다 유형 × 에이전트."""
    rows = dataset_rows(settings)
    names = load_resources(settings.path("resources"))["domain_ko"]
    models = sorted({row["model"] for row in rows if row["model"]})
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_domain[row["domain"]].append(row)
    domains = list(settings.dashboard.dataset)
    domain_name = lambda r: names.get(r["domain"], r["domain"])  # noqa: E731
    type_name = lambda r: r["task"] if r["composed"] else "원 과제"  # noqa: E731
    summary = [{"label": "전체", "value": len(rows)},
               *[{"label": names.get(d, d), "value": len(by_domain[d])} for d in domains]]
    note = ("config의 dashboard.dataset이 정한 실행들에서 검수를 통과한 것만 모았어요. "
            "같은 장면을 다시 돌린 실행이 있으면 나중 실행 것을 써요.")
    blocks = [{"kind": "stats", "label": "최종 데이터셋 (검수 통과 샘플)", "note": note, "data": summary},
              {"kind": "table", "label": "도메인 × 에이전트",
               "data": _count_table(rows, "도메인", models, domain_name)}]
    blocks += [{"kind": "table", "label": f"{names.get(d, d)}: 유형 × 에이전트",
                "note": "원 과제(합성하지 않은 원천 과제)는 한 줄로 묶었어요.",
                "data": _count_table(by_domain[d], "유형", models, type_name)} for d in domains]
    return blocks
