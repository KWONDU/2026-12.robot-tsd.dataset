"""대시보드 화면(view) 등록부와 블록 계약.

화면 하나 = 분석 함수 여러 개, 분석 하나 = 블록 하나(또는 블록 목록). 분석이 None을 돌려주면 그 블록은 숨고,
분석 하나가 예외를 내도 그 자리에 error 블록이 뜨고 나머지는 그대로 그려진다. 화면과 분석의 순서는 등록
순서다. 새 화면은 view("이름", "한국어 제목") 한 줄, 새 분석은 화면 데코레이터 한 줄이다.

블록: {"kind": 렌더러 이름, "label": 제목, "data": 내용, "note": 설명(선택), "width": "half"(선택, 넓은 화면에서
두 블록을 나란히), "fold": 근거 표(선택)}. fold는 {"columns", "rows"} 표이고 화면에는 그리지 않는다(가독성,
사용자 결정 2026-11-03). API 응답에만 남아서 채팅에서 숫자를 확인할 때 에이전트가 읽는다.

kind별 data
- stats [{label, value}], keyvals [{key, value}], table {columns, rows}, donut [{label, value, tone}] (+ unit:
  "usd"면 금액), code 문자열 (+ collapsed: true면 접힘), files [{name, text}] (파일 하나를 골라 원문 보기)
- flow {steps [{key, title, value, caption, state(done, active, waiting), progress {done, expected}?, link?,
  edge?, notes [{label, value}], drops [{label, value, detail?, tone?, link?}]}], outcomes [{label, value, tone,
  link}]}: 파이프라인 흐름도
- funnel {total, rows [{key, label, dropped, remaining, desc?, rule?, link?}], result}: 검수 깔때기
- json {docs [{key, title, source, note, expand, tree, rubric?}]}: JSON 나무 문서들 (tree는 json_tree 노드)
link는 실행 안의 경로(화면 이름 또는 samples/<판정>[/<걸린 단계>])다.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tsdgen.dashboard.data import RunData
from tsdgen.dashboard.records import (  # noqa: F401 - 화면 모듈들이 base에서 가져다 쓴다
    meta_of,
    model_of,
    sid,
    status_of,
    task_of,
    verification,
)
from tsdgen.dashboard.terms import term  # noqa: F401 - 화면 모듈들이 base에서 가져다 쓴다
from tsdgen.log import get_logger
from tsdgen.registry import Registry

Block = dict[str, Any]
Analysis = Callable[[RunData], Block | list[Block] | None]

log = get_logger("tsdgen.dashboard.views")


@dataclass
class View:
    name: str
    label: str
    analyses: list[Analysis] = field(default_factory=list)

    def __call__(self, analysis: Analysis) -> Analysis:
        self.analyses.append(analysis)
        return analysis


VIEWS = Registry("dashboard view")
"""이름 -> View. 등록 순서가 화면 순서다 (names()는 이름순이라 쓰지 않는다)."""
_ORDER: list[str] = []


def view(name: str, label: str) -> View:
    page = View(name, label)
    VIEWS.register(name)(page)
    _ORDER.append(name)
    return page


def catalog() -> list[dict[str, str]]:
    return [{"name": name, "label": VIEWS.get(name).label} for name in _ORDER]


def render(data: RunData, view_name: str) -> list[Block]:
    blocks: list[Block] = []
    for analysis in VIEWS.get(view_name).analyses:
        try:
            produced = analysis(data)
        except Exception as error:  # 분석 하나의 실패가 화면 전체를 막지 않게 한다
            log.exception(f"analysis {analysis.__name__} failed on run {data.run_id}")
            produced = {"kind": "error", "label": analysis.__name__,
                        "data": f"{type(error).__name__}: {error}"[:500]}
        if produced is None:
            continue
        blocks += produced if isinstance(produced, list) else [produced]
    return blocks


def table(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"columns": columns, "rows": rows}


def clock(timestamp: float | None) -> str:
    return datetime.fromtimestamp(timestamp).isoformat(sep=" ", timespec="seconds") if timestamp else "-"


def duration_text(seconds: float) -> str:
    whole, frac = int(seconds), round(seconds % 60, 2)
    if whole >= 3600:
        return f"{whole // 3600}시간 {whole % 3600 // 60}분 {frac}초"
    if whole >= 60:
        return f"{whole // 60}분 {frac}초"
    return f"{frac}초"


def mean(values: list[float], digits: int = 2) -> float | None:
    return round(sum(values) / len(values), digits) if values else None
