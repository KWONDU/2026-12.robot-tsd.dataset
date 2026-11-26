"""도메인 프로필: 도메인마다 달라지는 낱말·예시·목표 상태 어휘·골든 예시 (resources/domains/<도메인>.yaml).

도메인은 원천이 정한다(settings.domain). 프롬프트는 도메인 낱말을 자리로 받고, 여기서 그 값을 만든다.
세계 상태 글도 같은 프로필에서 장소 이름과 가구 낱말을 가져간다. 새 도메인은 프로필 파일 하나와 그 도메인
원천 어댑터를 더하면 되고, 프롬프트·코드는 바뀌지 않는다.
"""

import re
from typing import Any

from tsdgen.resources import load_domain, load_resources

EXAMPLE_PREFIX = "EX_"  # 프롬프트 예시 자리 이름의 앞머리
SLOT_RE = re.compile(r"<<([A-Z_]+)>>")  # 프로필 문장 안의 예시 자리 (프롬프트와 같은 모양)


def profile(settings: Any) -> dict[str, Any]:
    return load_domain(settings.path("resources"), settings.domain)


def state_key_list(domain: dict[str, Any], lexicon: dict[str, Any]) -> str:
    """'door_open(문 열림), turned_on(가동 중)'처럼 목표 상태에 쓸 수 있는 상태 키와 그 풀이.
    상태 키가 없는 도메인(설비에 여닫거나 켜는 상태가 없는 창고)은 사전의 '없음' 문구를 쓴다."""
    phrases = lexicon["state_phrases"]
    keys = ", ".join(f"{key}({phrases[key][True]})" for key in domain["goal_state_keys"])
    return keys or lexicon["no_state_keys_ko"]


def prompt_values(settings: Any) -> dict[str, str]:
    """프롬프트의 도메인 자리 값 (DOMAIN_ROBOT 등과 EX_ 예시)."""
    domain = profile(settings)
    lexicon = load_resources(settings.path("resources"))
    values = {
        "DOMAIN_ROBOT": domain["robot_ko"],
        "DOMAIN_PLACE": domain["place_ko"],
        "DOMAIN_FIXTURE": domain["fixture_ko"],
        "DOMAIN_STATE_KEYS": state_key_list(domain, lexicon),
        "DOMAIN_RELATIONS": ", ".join(f"{k}({v})" for k, v in domain["goal_relations"].items()),
        "DOMAIN_KNOWLEDGE": (domain.get("knowledge") or "").strip(),
        # 채점 프롬프트의 지식 문단. 생성에게 시키는 계산(공장의 가장 짧은 길)을 규칙 검사가 맡는
        # 도메인은 그 계산 지시를 뺀 문단을 따로 둔다. 없으면 생성과 같다
        "DOMAIN_KNOWLEDGE_CHECK": (
            domain.get("knowledge_check") or domain.get("knowledge") or ""
        ).strip(),
    }
    values.update({f"{EXAMPLE_PREFIX}{k}": v for k, v in domain["prompt_examples"].items()})
    # 도메인마다 다른 규칙 문장 (키가 곧 자리 이름). 문장 안의 자리(<<EX_...>>, <<DOMAIN_FIXTURE>>)는
    # 위에서 만든 값으로 채운다
    for slot, text in domain["prompt_rules"].items():
        values[slot] = SLOT_RE.sub(lambda m: values[m.group(1)], (text or "").strip())
    return values
