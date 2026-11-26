"""프롬프트 파일을 화면용으로 읽는다: 이 실행이 쓴 버전과 원문, 섹션 구조, 다른 버전과의 차이.

프롬프트 파일은 모두 같은 뼈대다: "라벨: 내용" 줄로 섹션이 시작하고(역할, 대상, 입력, 지침, 출력, 예시),
지침은 "1." 같은 번호 줄과 그 아래 들여 쓴 줄로 이루어지며, `---USER---` 아래가 매번 바뀌는 유저 메시지다.
`<<이름>>`은 실행할 때 값이 채워지는 자리다. 이 모양을 읽어 섹션 목록으로 돌려준다.
"""

import difflib
import re

from tsdgen import prompts, roles
from tsdgen.dashboard.data import RunData
from tsdgen.runs import PROMPT_SNAPSHOT_DIR

LABEL = re.compile(r"^([가-힣A-Za-z][가-힣A-Za-z0-9 ()/]{0,24}):(?:\s+(.*))?$")
NUMBERED = re.compile(r"^(\d+)\.\s+(.*)$")
SLOT = re.compile(r"<<([A-Z_]+)>>")
DIFF_CONTEXT = 2  # 버전 차이에서 바뀐 줄 앞뒤로 보이는 줄 수


def used_prompt(data: RunData, role_name: str) -> tuple[str, str]:
    """이 실행이 쓴 (버전, 원문). 실행 폴더의 사본이 우선이고, 없으면 지금 설정의 파일."""
    prefix = f"{role_name}_"
    copies = [p for p in data.snapshot_files(PROMPT_SNAPSHOT_DIR) if p.name.startswith(prefix)]
    if copies:
        return copies[0].stem.removeprefix(prefix), copies[0].read_text(encoding="utf-8")
    version = roles.role(role_name).prompt_version(data.settings)
    return version, read_version(data, role_name, version)


def versions(data: RunData, role_name: str) -> list[str]:
    return sorted(p.stem for p in (data.settings.path("prompts") / role_name).glob("*.txt"))


def read_version(data: RunData, role_name: str, version: str) -> str:
    path = prompts.template_path(data.settings, role_name, version)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def split_messages(text: str) -> tuple[str, str]:
    """(시스템 메시지, 유저 메시지) 템플릿."""
    system, _, user = text.partition(f"{prompts.USER_MARKER}\n")
    return system, user


def _slots(section: dict) -> list[str]:
    body = " ".join([section["text"], *[i["text"] + " " + " ".join(i["sub"]) for i in section["items"]]])
    return list(dict.fromkeys(SLOT.findall(body)))


def parse(text: str) -> list[dict]:
    """섹션 목록: [{title, text, items [{text, sub [줄]}], slots [자리 이름]}]."""
    sections: list[dict] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        indented = raw[:1].isspace()
        numbered = NUMBERED.match(line)
        label = None if indented or numbered else LABEL.match(line)
        if label:
            sections.append({"title": label.group(1), "text": label.group(2) or "", "items": []})
            continue
        if not sections:
            sections.append({"title": "", "text": "", "items": []})
        current = sections[-1]
        if numbered:
            current["items"].append({"text": numbered.group(2), "sub": []})
        elif indented and current["items"]:
            current["items"][-1]["sub"].append(line.strip())
        else:
            current["text"] = f"{current['text']}\n{line.strip()}".strip()
    return [{**s, "slots": _slots(s)} for s in sections]


def diff(old: str, new: str) -> list[dict[str, str]]:
    """두 원문의 줄 차이: [{op: same|add|del|gap, text}]. 바뀌지 않은 긴 구간은 gap 한 줄로 줄인다."""
    lines = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=DIFF_CONTEXT):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            lines.append({"op": "gap", "text": ""})
            continue
        op = {"+": "add", "-": "del"}.get(line[:1], "same")
        lines.append({"op": op, "text": line[1:]})
    return lines
