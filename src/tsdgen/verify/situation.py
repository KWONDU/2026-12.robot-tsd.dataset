"""상황 사실 대조: 작업 합성이 코드로 정한 사실이 설명문에 들어갔는지 규칙으로 본다 (LLM 없음).

지도가 있는 장면에서 가장 짧은 길이 막힌 통로·출입 제한 구역·거꾸로 가야 하는 한 방향 통로 때문에 돌아가야 하는지,
물건을 넣을 곳이 점검으로 멈춰 기다려야 하는지는 LLM 채점자가 매번 계산하기 어렵지만 코드는 정확히 안다.
그 사실(시나리오 meta.situation)을 설명문의 제약·시간 조건과 글자로 대조한다. 찾는 낱말은 공용 사전의
situation_words에 있다. 이름은 세계 상태 글의 이름 그대로라 글자 대조로 충분하다.
"""

import re

from tsdgen.models import SituationResult
from tsdgen.tasks.factory_view import FactoryView


def aisle_numbers(text: str, words: dict) -> set[str]:
    """글에 나온 통로 번호. '통로 40, 9, 11'처럼 앞말 하나에 번호를 이어 쓴 것도 모두 센다."""
    joins = "|".join(re.escape(j) for j in words["aisle_joins"])
    prefix = re.escape(words["aisle"])
    pattern = rf"{prefix}\s*(\d+(?:\s*(?:{joins})\s*(?:{prefix}\s*)?\d+)*)"
    return {n for run in re.findall(pattern, text) for n in re.findall(r"\d+", run)}


def _names(text: str, name: str) -> bool:
    """이름이 글에 그대로 있는가 ('가공기 1'이 '가공기 12'에 걸리지 않게)."""
    return re.search(rf"{re.escape(name)}(?!\d)", text) is not None


def _seconds(text: str, seconds: int, words: dict) -> bool:
    """초 단위 값이 글에 있는가 (60의 배수면 분으로 쓴 것도 된다)."""
    if re.search(rf"(?<!\d){seconds}(?!\d)", text):
        return True
    minutes = rf"(?<!\d){seconds // 60}\s*{words['minute']}"
    return seconds % 60 == 0 and re.search(minutes, text) is not None


def _number(name: str) -> str:
    return name.split()[-1]


def _routes(facts: dict, scene: dict, said: set[str], labels: dict[str, str]) -> list[str]:
    """구간마다 막힘을 따지지 않은 가장 짧은 길 가운데 하나의 쓸 수 없는 통로를 다 적었는지, 그리고 어느 구간의
    가장 짧은 길에도 들지 않는 쓸 수 없는 통로를 적지 않았는지 (지도로 다시 따진다)."""
    view = FactoryView(scene, {})
    problems, relevant = [], set()
    for start, goal in facts["legs"]:
        options = [{_number(n) for n in option} for option in view.blocking_options(start, goal)]
        relevant.update(*options)
        if options and not any(option <= said for option in options):
            need = " / ".join(", ".join(sorted(o, key=int)) for o in options[:3])
            problems.append(f"detour {labels.get(start, start)} -> {labels.get(goal, goal)}: {need}")
    unusable = {_number(a["name_ko"]) for a in view.map.get("aisles") or []
                if a.get("closed") or a.get("one_way") or a.get("note")}
    problems += [f"extra: {n}" for n in sorted(said & unusable - relevant, key=int)]
    return problems


def check_situation(
    tsd: dict, facts: dict | None, labels: dict[str, str], words: dict, scene: dict | None = None
) -> SituationResult:
    """tsd는 모델이 쓴 그대로의 설명문(이름), labels는 entity id -> 세계 상태 글의 이름, scene은 지도가 든
    환경(구간을 다시 따질 때). 구간(legs)이 없는 예전 기록은 코드가 고른 길의 통로(detour)로 대조한다."""
    if not facts:
        return SituationResult(pass_=True, available=False)
    conditions = tsd.get("spatiotemporal_conditions") or {}
    time_text = conditions.get("time") or ""
    constraint_text = " ".join(c.get("description") or "" for c in tsd.get("constraints") or [])
    missing: list[str] = []
    said = aisle_numbers(constraint_text, words)
    if facts.get("legs") and scene:
        missing += _routes(facts, scene, said, labels)
    else:
        for cause, names in (facts.get("detour") or {}).items():
            missing += [f"{cause}: {name}" for name in names if _number(name) not in said]
    both = f"{time_text} {constraint_text}"
    waits = any(w in both for w in words["wait"])
    for station in facts.get("wait_for") or []:
        name = labels.get(station, station)
        if not (_names(both, name) and waits):
            missing.append(f"wait_for: {name}")
    for key in ("window_s", "duration_s"):
        values = facts.get(key)
        values = values if isinstance(values, list) else [values] if values is not None else []
        missing += [f"{key}: {v}" for v in values if not _seconds(time_text, v, words)]
    return SituationResult(pass_=not missing, available=True, missing=missing)
