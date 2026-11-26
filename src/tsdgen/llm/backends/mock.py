"""결정론 mock 백엔드.

API 키 없이 파이프라인 전체(통합 테스트 포함)를 검증하는 용도다. 프롬프트의 문장을
읽지 않으므로 생성 품질에 대해서는 아무것도 말해 주지 않는다. 배관과 계약이 실제로
이어져 있는지만 확인한다.

역할마다 응답 스텁을 MOCK_RESPONSES에 등록한다. 요청이 이미 역할 이름을 들고 오므로
백엔드는 등록된 스텁을 꺼내 부르기만 하면 되고, 역할이 늘어도 이 파일에 분기가 생기지
않는다. 등록 누락은 테스트가 잡는다.
"""

import json

from tsdgen.config import ModelCfg, Settings
from tsdgen.llm.base import Backend, LLMError, LLMRequest, LLMResponse
from tsdgen.registry import LLM_BACKENDS, MOCK_RESPONSES
from tsdgen.resources import load_resources

CHARS_PER_TOKEN = 3  # mock 사용량 집계용 근사치. 실제 과금과 무관하다.


def _last_user_content(req: LLMRequest) -> str:
    for message in reversed(req.messages):
        if message["role"] == "user":
            return message["content"]
    raise LLMError("mock backend: no user message")


def _extract_block(content: str, header: str) -> str:
    idx = content.rfind(header)
    if idx < 0:
        return ""
    block = content[idx + len(header) :]
    for next_header in ("\n\n유저", "\n\n원 지시문", "\n\n환경 ", "\n\nTSD", "\n\n재작성"):
        cut = block.find(next_header)
        if cut >= 0:
            block = block[:cut]
    return block.strip()


def _extract_json_block(content: str, header: str) -> dict:
    block = _extract_block(content, header)
    start = block.find("{")
    if start < 0:
        raise LLMError(f"mock backend: no JSON after '{header}'")
    return json.loads(block[start:])


def _strip_place(title: str) -> str:
    """'조리대 4(아일랜드 조리대)' -> '조리대 4'."""
    return title.split("(", 1)[0].strip()


def _sections(block: str) -> dict[str, list[str]]:
    """세계 상태 글을 '제목:' 아래의 '- ' 줄 묶음으로 나눈다."""
    found: dict[str, list[str]] = {}
    section = ""
    for line in block.splitlines():
        if line.endswith(":") and not line.startswith("- "):
            section = line[:-1]
            found[section] = []
        elif line.startswith("- ") and section:
            found[section].append(line[2:])
    return found


def _env_from_text(block: str, lexicon: dict) -> dict:
    """자연어 세계 상태(world_state text 보기)에서 이름표를 다시 읽어 mock이 쓰는 모양으로 만든다.

    이름표가 곧 id 노릇을 한다. 묶어 줄인 줄(서랍 1부터 18까지)과 여러 이름이 든 줄은 건너뛴다 (mock은 몇 개면 된다).
    놓인 곳 구절('냉장고 냉동실 선반 위', '조리대 2 위, 싱크대 옆')은 가구 목록의 이름으로 시작하면 그 가구로 읽는다.
    """
    relation_of = {ko: key for key, ko in lexicon["relation_ko"].items()}
    sections = _sections(block)
    fixtures: dict[str, dict] = {}
    fixture_headings = {d.get("fixture_ko") for d in lexicon.get("domains", {}).values()}
    for line in (line for heading in fixture_headings for line in sections.get(heading, [])):
        head = line.partition(": ")[0]
        if "," not in head and "부터" not in head:
            name = _strip_place(head)
            fixtures[name] = {"id": name, "name_ko": name, "states": {}}
    objects = [{"id": name, "name_ko": name, "states": {}, "placement": None}
               for line in sections.get("그 밖의 물체", []) for name in line.split(", ")]
    for line in sections.get("물체가 놓인 곳", []):
        place, _, names = line.partition(": ")
        host = max((f for f in fixtures if place.startswith(f)), key=len, default=None)
        words = place.split(",")[0].split()
        relation = relation_of.get(words[-1]) if words else None
        placement = {"fixture_id": host, "relation": relation} if host and relation else None
        objects += [{"id": name, "name_ko": name, "states": {}, "placement": placement}
                    for name in names.split(", ")]
    return {"fixtures": list(fixtures.values()), "objects": objects}


def _environment(content: str, lexicon: dict) -> dict:
    """프롬프트의 세계 상태. 자연어 보기면 글에서, 아니면 JSON에서 읽는다."""
    text = _extract_block(content, "환경 설명:")
    return _env_from_text(text, lexicon) if text else _extract_json_block(content, "환경 컨텍스트:")


def _state_text(states: dict, lexicon: dict) -> str | None:
    phrases = lexicon["state_phrases"]
    parts = [phrases[k][v] for k, v in states.items() if k in phrases and v in phrases[k]]
    return ", ".join(parts) if parts else None


def _location_of(entity: dict, fixtures_by_id: dict, lexicon: dict) -> str | None:
    placement = entity.get("placement")
    if placement:
        fixture = fixtures_by_id.get(placement["fixture_id"])
        if fixture:
            relation_ko = lexicon["relation_ko"][placement["relation"]]
            return f"{fixture['name_ko']} {relation_ko}"
    return entity.get("location_desc")


def _pick_required(objects: list, fixtures_by_id: dict, lexicon: dict) -> list[dict]:
    """조작 대상. 씬의 첫 물체를 쓴다."""
    return [
        {
            "ref": o["id"],
            "location": _location_of(o, fixtures_by_id, lexicon),
            "state": _state_text(o.get("states", {}), lexicon),
        }
        for o in objects[:1]
    ]


def _pick_key_means(env: dict, placement_ids: set, lexicon: dict) -> list[dict]:
    """수단 fixture. 물체가 놓인 곳이 아닌 fixture를 고른다."""
    pool = [f for f in env["fixtures"] if f["id"] not in placement_ids] or env["fixtures"]
    return [
        {
            "ref": f["id"],
            "location": f.get("location_desc"),
            "state": _state_text(f.get("states", {}), lexicon),
            "alternatives": [],
        }
        for f in pool[:1]
    ]


def _pick_auxiliary(placement_ids: set, fixtures_by_id: dict) -> list[dict]:
    """위치 기준이 되는 보조 물체."""
    return [
        {
            "ref": fixtures_by_id[fid]["id"],
            "location": fixtures_by_id[fid].get("location_desc"),
            "note": "대상 물체의 초기 위치 기준",
        }
        for fid in sorted(placement_ids)
        if fid in fixtures_by_id
    ][:1]


def _mock_goal(instruction: str, required: list[dict], key_means: list[dict]) -> dict:
    """필수 물체를 핵심 수단에 두는 목표. 핵심 수단은 물체가 놓이지 않은 가구라 초기 씬에서는 성립하지 않는다."""
    summary = f"지시 '{instruction}'에 해당하는 작업을 완수하는 것"
    if not (required and key_means):
        return {"summary": summary, "goal_state": []}
    goal = {"kind": "relation", "subject": required[0]["ref"], "predicate": "in",
            "object": key_means[0]["ref"], "value": True}
    return {"summary": summary, "goal_state": [[goal]]}


@MOCK_RESPONSES.register("generate")
def _mock_generate(content: str, settings: Settings) -> dict:
    """환경 컨텍스트에 실제로 있는 entity만 인용해 접지 검사를 통과하는 응답을 만든다."""
    lexicon = load_resources(settings.path("resources"))
    instruction = _extract_block(content, "유저 입력:").splitlines()[0]
    env = _environment(content, lexicon)
    fixtures_by_id = {f["id"]: f for f in env["fixtures"]}
    placement_ids = {
        o["placement"]["fixture_id"] for o in env["objects"] if o.get("placement")
    }

    names = {e["id"]: e["name_ko"] for e in [*env["objects"], *env["fixtures"]]}
    required = _pick_required(env["objects"], fixtures_by_id, lexicon)
    key_means = _pick_key_means(env, placement_ids, lexicon)
    auxiliary = _pick_auxiliary(placement_ids, fixtures_by_id)
    constraints = []
    if required and key_means:
        constraints.append(
            {
                "type": "precedence",
                "description": (
                    f"{names[required[0]['ref']]}에 접근하는 동작이 "
                    f"{names[key_means[0]['ref']]} 조작에 선행해야 함"
                ),
            }
        )
    goal = _mock_goal(instruction, required, key_means)
    if not goal["goal_state"]:  # 상태로 쓸 목표가 없으면 기준을 제약에 남긴다
        constraints.append({"type": "other", "description": "지시한 작업이 끝나야 함"})
    reasoning = (
        f"① 지시 해석: '{instruction}' 지시의 목표를 추출함. "
        f"② 씬 접지: 관련 entity {[o['ref'] for o in required + key_means]}를 식별함. "
        "③ 역할 부여: 조작 대상을 필수 물체로, 수단 fixture를 핵심 수단으로 분류함. "
        "④ 제약 도출: 접근 순서에서 선후 제약을 도출함."
    )
    return {
        "reasoning": reasoning,
        "task_situation_description": {
            "task_goal": goal,
            "spatiotemporal_conditions": {"time": None, "space": None},
            "target_objects": {
                "required": required,
                "key_means": key_means,
                "auxiliary": auxiliary,
            },
            "constraints": constraints,
        },
    }


@MOCK_RESPONSES.register("rewrite")
def _mock_rewrite(content: str, _settings: Settings) -> dict:
    original = _extract_block(content, "원 지시문:").splitlines()[0]
    return {"user_instruction_ko": f"({original}) 이 작업 좀 해 줘"}


@MOCK_RESPONSES.register("checklist")
def _mock_checklist(_content: str, settings: Settings) -> dict:
    lexicon = load_resources(settings.path("resources"))
    return {
        "items": {
            key: {"score": 2, "rationale": "mock 판정: 항목 충족으로 간주"}
            for key in lexicon["checklist_items"]
        }
    }


@MOCK_RESPONSES.register("probe")
def _mock_probe(_content: str, _settings: Settings) -> dict:
    return {
        "decomposition_attempt": ["이동", "잡기", "이동", "놓기"],
        "missing_info": [],
        "score": 2,
    }


@MOCK_RESPONSES.register("resolvability")
def _mock_resolvability(_content: str, _settings: Settings) -> dict:
    return {"pass": True, "rationale": "mock 판정: 해소 가능으로 간주"}


@LLM_BACKENDS.register("mock")
class MockBackend(Backend):
    needs_api_key = False

    def complete(self, req: LLMRequest, model_cfg: ModelCfg, settings: Settings) -> LLMResponse:
        payload = MOCK_RESPONSES.get(req.role)(_last_user_content(req), settings)
        text = json.dumps(payload, ensure_ascii=False)
        prompt_chars = sum(len(m["content"]) for m in req.messages)
        return LLMResponse(
            text=text,
            input_tokens=prompt_chars // CHARS_PER_TOKEN,
            output_tokens=len(text) // CHARS_PER_TOKEN,
        )
