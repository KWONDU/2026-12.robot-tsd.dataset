"""검수 기준을 화면용으로 읽는다: 검수 항목이 하는 일과 통과 기준, 체크리스트 항목별 채점 기준, 채점 모델.

전부 이 실행이 실제로 쓴 것을 따른다. 통과 기준은 실행의 설정 사본(verify.thresholds), 채점 기준은 실행의
채점 프롬프트 사본에서 "- 항목키: 기준" 줄을 읽고, 채점 모델은 호출 기록(usage)에서 읽는다. 사본이 없는
실행만 지금 설정과 파일을 쓴다. 항목이 하는 일의 문구는 사전(verify_check_info_ko)에 있다.
"""

import re
from string import Formatter

from tsdgen import domains, prompts, roles
from tsdgen.dashboard.data import RunData
from tsdgen.runs import PROMPT_SNAPSHOT_DIR

CRITERION = re.compile(r"^\s*-\s*(\w+):\s*(.+)$")


def thresholds(data: RunData) -> dict:
    recorded = (data.config_snapshot().get("verify") or {}).get("thresholds")
    return recorded or data.settings.verify.thresholds.model_dump()


def _filled(template: str | None, info: dict, values: dict) -> str | None:
    """사전 문구의 {이름}을 통과 기준 값으로 채운다. 값이 비어 있으면 off 문구(기준이 꺼져 있음)."""
    if not template:
        return None
    names = [name for _, name, _, _ in Formatter().parse(template) if name]
    if any(values.get(name) is None for name in names):
        return info.get("off", template)
    return template.format(**values)


def check_info(data: RunData, check: str) -> dict[str, str | None]:
    """검수 항목 하나가 하는 일(desc)과 이 실행의 통과 기준(rule)."""
    info = data.lexicon().get("verify_check_info_ko", {}).get(check, {})
    return {"desc": info.get("desc"), "rule": _filled(info.get("rule"), info, thresholds(data))}


def judges(data: RunData, role_name: str) -> list[str]:
    """이 실행에서 그 역할을 실제로 맡은 모델. 호출 기록이 없으면 지금 설정의 모델."""
    used = sorted({u.get("model", "?") for u in data.usage() if u.get("role") == role_name})
    return used or roles.role(role_name).models(data.settings)


def checklist_prompt(data: RunData) -> tuple[str, str]:
    """이 실행이 쓴 채점 프롬프트 (버전 이름, 원문). 사본이 없으면 지금 설정의 파일."""
    name = roles.CHECKLIST.name
    copies = [p for p in data.snapshot_files(PROMPT_SNAPSHOT_DIR) if p.name.startswith(f"{name}_")]
    if copies:
        return copies[0].stem.removeprefix(f"{name}_"), copies[0].read_text(encoding="utf-8")
    version = roles.CHECKLIST.prompt_version(data.settings)
    path = prompts.template_path(data.settings, name, version)
    return version, path.read_text(encoding="utf-8") if path.exists() else ""


def checklist_criteria(data: RunData) -> dict[str, str]:
    """체크리스트 항목 키 -> 채점 기준 문장 (채점 모델이 받은 대로: 프롬프트의 도메인 자리를 채운 글)."""
    names = data.lexicon()["checklist_items"]
    _, template = checklist_prompt(data)
    text = prompts.fill(template, domains.prompt_values(data.settings), strict=False)
    return {m.group(1): m.group(2).strip() for line in text.splitlines()
            if (m := CRITERION.match(line)) and m.group(1) in names}


def score_scale(data: RunData) -> list[dict]:
    labels = data.lexicon().get("score_labels_ko", {})
    return [{"score": s, "label": labels.get(s, str(s))}
            for s in range(data.settings.verify.score_max + 1)]


def checklist_summary(data: RunData) -> dict:
    """체크리스트 한눈에: 척도, 통과 기준, 채점 모델, 프롬프트 버전."""
    return {"scale": score_scale(data), "rule": check_info(data, roles.CHECKLIST.name)["rule"],
            "judges": judges(data, roles.CHECKLIST.name), "version": checklist_prompt(data)[0]}
