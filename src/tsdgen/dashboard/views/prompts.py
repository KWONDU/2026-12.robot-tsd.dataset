"""프롬프트 화면: 파이프라인 순서의 LLM 역할마다, 모델에게 무엇을 시켰는지(프롬프트 구조)와 무엇을 받았는지(출력 구조).

역할마다: 하는 일, 맡은 모델, 이 실행이 쓴 버전, 프롬프트 섹션(역할·대상·입력·지침·출력·예시)과 유저 메시지의
자리, 출력 JSON 나무(이 실행의 채택 샘플 값), 다른 버전과의 차이(최신 버전부터), 원문. 원문은 실행 폴더의 사본을 따른다.
"""

from collections.abc import Callable

from tsdgen import roles
from tsdgen.dashboard import prompt_text, rubric
from tsdgen.dashboard.data import RunData
from tsdgen.dashboard.json_tree import TreeOptions, json_tree
from tsdgen.dashboard.verdicts import ACCEPTED
from tsdgen.dashboard.views.base import Block, sid, status_of, term, verification, view

prompts_view = view("prompts", "프롬프트")


def _example(data: RunData) -> tuple[dict, dict]:
    """출력 예시로 쓸 채택 샘플 하나와 그 시나리오의 재작성 기록."""
    samples = data.samples()
    sample = next((s for s in samples if status_of(s) == ACCEPTED), samples[0] if samples else {})
    context = next((c for c in data.contextualized()
                    if c.get("scenario_id") == sample.get("scenario_id")), {})
    return sample, context


# 역할 이름 -> (샘플, 재작성 기록) -> 그 역할이 실제로 낸 출력 (출력 JSON 나무의 예시 값)
OUTPUT_EXAMPLES: dict[str, Callable[[dict, dict], dict | None]] = {
    "rewrite": lambda _s, c: {"user_instruction_ko": (c.get("input") or {}).get("user_instruction")},
    "resolvability": lambda _s, c: c.get("resolvability"),
    "generate": lambda s, _c: {"reasoning": (s.get("intermediate") or {}).get("reasoning"),
                               "task_situation_description": (s.get("output") or {}).get(
                                   "task_situation_description")},
    "checklist": lambda s, _c: {"items": (verification(s).get("checklist") or {}).get("items")},
    "probe": lambda s, _c: verification(s).get("sufficiency_probe"),
}


def _role_card(data: RunData, name: str, sample: dict, context: dict) -> dict:
    lexicon = data.lexicon()
    role = roles.role(name)
    version, text = prompt_text.used_prompt(data, name)
    system, user = prompt_text.split_messages(text)
    example = OUTPUT_EXAMPLES.get(name, lambda _s, _c: None)(sample, context)
    labels = lexicon.get("schema_field_ko", {})
    return {
        "key": name, "label": term(lexicon["role_ko"].get(name, name), name),
        "purpose": lexicon.get("role_purpose_ko", {}).get(name, ""),
        "models": rubric.judges(data, name), "version": version,
        "system": prompt_text.parse(system), "user": prompt_text.parse(user),
        "output": json_tree(role.output_model.model_json_schema(), example or {},
                            TreeOptions(labels)),
        "diffs": [{"version": other, "lines": prompt_text.diff(
            text, prompt_text.read_version(data, name, other))}
                  for other in reversed(prompt_text.versions(data, name)) if other != version],
        "raw": text,
    }


@prompts_view
def prompt_roles(data: RunData) -> Block:
    lexicon = data.lexicon()
    sample, context = _example(data)
    cards = [_role_card(data, name, sample, context) for name in lexicon["role_ko"]]
    return {"kind": "prompts", "label": "프롬프트",
            "note": "파이프라인 순서대로 LLM이 맡는 역할이에요. 역할을 고르면 그 프롬프트가 무엇을 시키는지, 매번 무엇이 채워 들어가는지, 무엇을 돌려받는지 보여요.",
            "data": {"roles": cards, "example": sid(sample) if sample else None}}
