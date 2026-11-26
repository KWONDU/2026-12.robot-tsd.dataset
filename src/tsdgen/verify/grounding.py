"""접지 검사 (규칙 기반, LLM 불필요, 스펙 6.4의 2단계).

- 모든 ref가 환경 컨텍스트에 존재하는가
- TSD의 상태 서술이 씬 상태와 모순되지 않는가 (상충 표현 사전 기반)
- TSD 텍스트에 씬에 없는 물체·fixture가 등장하는가 (카테고리·별칭 사전 기반)
"""

import json
import re
from typing import Any

from tsdgen.goals.scene_index import members
from tsdgen.models import GroundingResult, TaskSituationDescription, region_id
from tsdgen.render import space_text


def _all_entries(tsd: TaskSituationDescription) -> list[Any]:
    to = tsd.target_objects
    return [*to.required, *to.key_means, *to.auxiliary]


def _tsd_texts(tsd: TaskSituationDescription, tsd_text: str) -> str:
    parts = [tsd.task_goal.summary, tsd.spatiotemporal_conditions.time or "",
             space_text(tsd.spatiotemporal_conditions.space) or "", tsd_text]
    parts += [c.description for c in tsd.constraints]
    for entry in _all_entries(tsd):
        parts += [entry.location or "", getattr(entry, "state", None) or "",
                  getattr(entry, "note", None) or ""]
    return "\n".join(parts)


def _check_refs(tsd: TaskSituationDescription, known_ids: set[str]) -> list[str]:
    return sorted({e.ref for e in _all_entries(tsd)
                   if not members(e.ref) or any(m not in known_ids for m in members(e.ref))})


def _entity_states(env_ctx: dict) -> dict[str, dict]:
    states = {}
    for entity in [*env_ctx.get("fixtures", []), *env_ctx.get("objects", [])]:
        if entity.get("states"):
            states[entity["id"]] = entity
    return states


def _check_state_conflicts(
    tsd: TaskSituationDescription, env_ctx: dict, lexicon: dict
) -> list[str]:
    """entity별 상태 서술이 씬 상태의 반대 표현을 담고 있으면 모순으로 잡는다."""
    conflict_phrases = lexicon["state_conflict_phrases"]
    qualifiers = lexicon.get("state_conflict_qualifiers") or {}
    entities = _entity_states(env_ctx)
    conflicts = set()
    for entry in _all_entries(tsd):
        entity = entities.get(entry.ref)  # 여럿을 가리키는 참조는 상태 서술을 대조하지 않는다
        state_text = getattr(entry, "state", None)
        if not entity or not state_text:
            continue
        for state_key, actual in entity["states"].items():
            phrase_map = conflict_phrases.get(state_key)
            if phrase_map is None or not isinstance(actual, bool):
                continue
            if any(word in state_text for word in qualifiers.get(state_key, [])):
                continue
            if any(phrase in state_text for phrase in phrase_map.get(not actual, [])):
                conflicts.add(f"{entry.ref}: '{state_text}' contradicts {state_key}={actual}")
    return sorted(conflicts)


# 1글자 별칭("초", "빵" 등)은 다른 단어의 부분 문자열로 오탐되므로 검사에서 제외한다
MIN_ALIAS_CHARS = 2


def _check_hallucinated(
    tsd: TaskSituationDescription, tsd_text: str, env_ctx: dict, lexicon: dict, instruction: str
) -> list[str]:
    """별칭 사전에 있는 명칭이 TSD에 나오는데 그 근거가 씬에도 지시에도 없으면 날조다.

    근거로 인정하는 것: 카테고리 일치, entity 상태값 문자열(내용물 등), 환경 컨텍스트 텍스트에 이미 등장하는
    표현(location_desc의 '조리대 우측' 등), 유저 지시에 나온 말(지시가 '핫초콜릿이 든 소스팬'이라고 했으면
    설명문이 핫초콜릿을 말해도 지어낸 것이 아니다). 명칭이 다른 낱말의 뒷부분으로 나온 것('들어가지'의 '가지')과
    다른 뜻으로 흔히 쓰이는 낱말(사전의 grounding_homographs)은 세지 않는다.
    """
    entities = [*env_ctx.get("objects", []), *env_ctx.get("fixtures", [])]
    present = {e["category"] for e in entities}
    for entity in entities:
        present |= {v for v in (entity.get("states") or {}).values() if isinstance(v, str)}
    evidence = json.dumps(env_ctx, ensure_ascii=False) + instruction
    skipped = set(lexicon.get("grounding_homographs") or [])
    text = _tsd_texts(tsd, tsd_text)

    # 물체와 fixture의 한국어 명칭은 둘 다 공용 사전에서 온다. 원본이 무엇이든
    # 산출물 언어는 같으므로, 이 검사는 데이터셋을 알 필요가 없다.
    candidates = {**lexicon["category_names_ko"], **lexicon["fixture_names_ko"]}
    hallucinated = []
    for category, name_ko in candidates.items():
        if len(name_ko) < MIN_ALIAS_CHARS or name_ko in skipped:
            continue
        mentioned = re.search(rf"(?<![가-힣]){re.escape(name_ko)}", text)
        if mentioned and category not in present and name_ko not in evidence:
            hallucinated.append(f"{name_ko}({category})")
    return sorted(set(hallucinated))


def check_grounding(
    tsd: TaskSituationDescription, tsd_text: str, env_ctx: dict, lexicon: dict, instruction: str = ""
) -> GroundingResult:
    known_ids = {e["id"] for e in [*env_ctx.get("objects", []), *env_ctx.get("fixtures", [])]}
    known_ids |= {region_id(f["id"], r["key"]) for f in env_ctx.get("fixtures", [])
                  for r in f.get("regions") or []}
    unknown_refs = _check_refs(tsd, known_ids)
    state_conflicts = _check_state_conflicts(tsd, env_ctx, lexicon)
    hallucinated = _check_hallucinated(tsd, tsd_text, env_ctx, lexicon, instruction)
    ok = not unknown_refs and not state_conflicts and not hallucinated
    return GroundingResult(
        unknown_refs=unknown_refs,
        state_conflicts=state_conflicts,
        hallucinated_entities=hallucinated,
        **{"pass": ok},
    )
