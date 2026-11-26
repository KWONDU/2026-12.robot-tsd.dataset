"""역할별·버전별 프롬프트 템플릿 로더와 메시지 조립.

템플릿은 prompts/<역할>/<버전>.txt 파일로 관리한다. 프롬프트 변경은 새 버전
파일 추가로만 하고 기존 파일은 수정하지 않는다(재현성). 파일 안에서
`---USER---` 줄을 경계로 시스템/유저 메시지를 나누고, `<<이름>>` 자리를
코드가 채운다. 채워지지 않은 자리가 남으면 오류다.

메시지 조립은 역할과 무관하게 하나의 함수가 한다. 어느 파일 버전을 쓸지와 시스템
메시지에 무엇을 채울지는 역할(Role)이 선언하고, 호출부는 유저 메시지 자리에 들어갈
값만 넘긴다. 템플릿이 쓰지 않는 값은 무시되므로, 역할을 추가해도 이 파일과 호출부의
분기는 늘지 않는다.
"""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tsdgen import domains, world_state
from tsdgen.config import Settings
from tsdgen.models import SceneState, to_environment_context
from tsdgen.resources import load_golden, load_resources

if TYPE_CHECKING:
    from tsdgen.roles import Role

USER_MARKER = "---USER---"
PLACEHOLDER_RE = re.compile(r"<<([A-Z_]+)>>")


def as_json(obj: Any) -> str:
    """프롬프트에 싣는 JSON 표기. 모든 역할이 같은 표기를 쓴다."""
    return json.dumps(obj, ensure_ascii=False, indent=1)


def fill(template: str, values: dict[str, str], *, strict: bool = True) -> str:
    """자리 표시자를 한 번에 채운다.

    값 안에 자리 표시자 모양의 문자열이 들어 있어도 다시 치환하지 않는다(단일 패스).
    strict면 values에 없는 자리가 남을 때 오류이고, 아니면 그 자리를 그대로 남긴다
    (전달용 프롬프트처럼 입력 자리를 비워 두고 렌더링할 때).
    """
    leftover: list[str] = []

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name in values:
            return values[name]
        leftover.append(match.group(0))
        return match.group(0)

    filled = PLACEHOLDER_RE.sub(substitute, template)
    if strict and leftover:
        raise ValueError(f"unfilled prompt placeholders: {leftover}")
    return filled


def read_template(path: Path) -> tuple[str, str]:
    """(시스템 템플릿, 유저 템플릿) 원문. 자리 표시자는 채우지 않은 상태다."""
    if not path.exists():
        raise FileNotFoundError(f"prompt template not found: {path}")
    system, _, user = path.read_text(encoding="utf-8").partition(f"{USER_MARKER}\n")
    if not user:
        raise ValueError(f"prompt template {path} has no {USER_MARKER} section")
    return system.strip(), user.strip()


def template_path(settings: Settings, role_name: str, version: str) -> Path:
    return settings.path("prompts") / role_name / f"{version}.txt"


def load_template(settings: Settings, role_name: str, version: str) -> tuple[str, str]:
    return read_template(template_path(settings, role_name, version))


def golden_example(settings: Settings) -> dict:
    """이 실행 도메인의 골든 예시."""
    return load_golden(settings.path("resources"), domains.profile(settings)["golden"])


def golden_parts(settings: Settings, golden: dict | None = None,
                 ) -> tuple[str, "world_state.RenderedWorld", dict]:
    """골든 예시를 지금 세계 상태 보기에 맞춘 (지시, 예시 세계 상태, 예시 출력).

    예시 씬도 실제 입력과 같은 정제와 같은 보기를 거친다. 보기가 이름표를 쓰면(text) 예시 출력의 가리키는 칸도
    그 이름으로 바꾸고, 추론은 골든 파일의 그 보기용 판을 쓴다. golden을 주지 않으면 도메인의 기본 예시다.
    """
    golden = golden or golden_example(settings)
    golden_ctx = to_environment_context(SceneState.model_validate(golden["scene_state"]))
    world = world_state.render(settings, golden_ctx, load_resources(settings.path("resources")))
    view = (golden.get("views") or {}).get(settings.contextualize.world_state) or {}
    output = {
        "reasoning": view.get("reasoning", golden["reasoning"]),
        "task_situation_description": world_state.relabel(golden["task_situation_description"],
                                                          world.labels),
    }
    return golden["user_instruction"], world, output


def golden_examples(settings: Settings) -> list[tuple[str, "world_state.RenderedWorld", dict]]:
    """도메인 프로필이 고른 예시들(golden_examples)을 지금 보기에 맞춘 것."""
    resources = settings.path("resources")
    files = domains.profile(settings).get("golden_examples") or []
    return [golden_parts(settings, load_golden(resources, name)) for name in files]


def examples_block(examples: list[tuple[str, Any, dict]]) -> str:
    """예시 여러 건을 프롬프트에 넣는 글 (<<GOLDEN_EXAMPLES>>)."""
    parts = []
    for i, (instruction, world, output) in enumerate(examples, start=1):
        parts.append(f"예시 {i} 입력(유저 입력):\n{instruction}\n\n예시 {i} 입력(환경 설명):\n{world.text}"
                     f"\n\n예시 {i} 출력:\n{as_json(output)}")
    return "\n\n".join(parts)


def golden_values(settings: Settings) -> dict[str, str]:
    """생성 프롬프트의 예시(shot) 자리 값. 한 건짜리 자리 셋과 목록 자리 하나."""
    instruction, world, output = golden_parts(settings)
    return {"GOLDEN_INSTRUCTION": instruction, "GOLDEN_CONTEXT": world.text,
            "GOLDEN_OUTPUT": as_json(output),
            "GOLDEN_EXAMPLES": examples_block(golden_examples(settings))}


def build_messages(
    settings: Settings, role: "Role", values: dict[str, str], *, variant: str | None = None
) -> list[dict]:
    """역할 하나의 대화 메시지. variant는 역할의 기본 변형(예: 재작성 구체성)을 바꿀 때만 준다."""
    version = role.prompt_version(settings, variant)
    system_t, user_t = load_template(settings, role.name, version)
    system_values = {**domains.prompt_values(settings), **role.system_values(settings)}
    return [
        {"role": "system", "content": fill(system_t, system_values)},
        {"role": "user", "content": fill(user_t, values)},
    ]


def list_prompt_files(prompts_dir: Path) -> list[Path]:
    return sorted(prompts_dir.rglob("*.txt"))
