"""도메인 프로필: 프롬프트의 도메인 자리를 가정 도메인으로 채우면 자리 도입 전 판과 글자 하나 다르지 않다.

새 도메인을 더할 때 프롬프트 규칙은 그대로이고 낱말·예시·목표 상태 어휘만 바뀐다는 뜻이다.
"""

import pytest

from tsdgen import domains, prompts
from tsdgen.resources import load_resources

# (도메인 자리를 받는 판, 자리 도입 전 판)
SLOTTED = [("generate", "gen_v6", "gen_v5"), ("checklist", "check_v6", "check_v5"),
           ("probe", "probe_v5", "probe_v4"), ("resolvability", "resolve_v3", "resolve_v2")]
SLOTTED += [("rewrite", f"rewrite_v3_{v}", f"rewrite_v2_{v}")
            for v in ("natural", "explicit", "underspecified")]
DOMAIN_WORDS = ("주방", "냉장고", "전자레인지", "머그", "조리대", "가정", "가구", "그릇", "레몬")
# 자리 도입 당시의 목표 상태 키. 어휘는 그 뒤로 늘었으므로(칸의 상태 등) 옛 판과 견줄 때는 이 목록으로 채운다
STATE_KEYS_THEN = ["door_open", "drawer_open", "turned_on", "filled"]


@pytest.mark.parametrize(("role", "new", "old"), SLOTTED)
def test_household_fill_reproduces_previous_prompt(settings, role, new, old):
    new_text = prompts.template_path(settings, role, new).read_text(encoding="utf-8")
    old_text = prompts.template_path(settings, role, old).read_text(encoding="utf-8")
    assert not [w for w in DOMAIN_WORDS if w in new_text]  # 도메인 낱말은 모두 자리로 빠졌다
    lexicon = load_resources(settings.path("resources"))
    values = domains.prompt_values(settings)
    values["DOMAIN_STATE_KEYS"] = domains.state_key_list({"goal_state_keys": STATE_KEYS_THEN}, lexicon)
    assert prompts.fill(new_text, values, strict=False) == old_text


def test_domain_profile_drives_world_text(settings):
    """세계 상태 글의 장소 이름과 가구 절 제목은 장면 도메인의 프로필에서 온다."""
    from tsdgen.world_state import WORLD_STATE_VIEWS

    lexicon = load_resources(settings.path("resources"))
    env = {"domain": "household", "objects": [], "agent": {"embodiment": "PandaOmron"},
           "fixtures": [{"id": "fx_sink_1", "category": "sink", "name_ko": "싱크대", "states": {}}]}
    text = WORLD_STATE_VIEWS.get("text")(env, lexicon, settings.contextualize).text
    household = lexicon["domains"]["household"]
    assert text.startswith(f"장소: {household['name_ko']}") and f"\n{household['fixture_ko']}:\n" in text


def test_domain_comes_from_the_source(settings):
    assert settings.domain == settings.adapter_options(settings.source.adapter).domain
    assert settings.domain in load_resources(settings.path("resources"))["domains"]


def test_newer_prompts_keep_domain_words_out(settings):
    """이후 판도 도메인 낱말을 자리로만 받는다."""
    versions = [("generate", "gen_v7"), ("checklist", "check_v7"), ("generate", "gen_v8"),
                ("checklist", "check_v8"), ("resolvability", "resolve_v4")]
    versions += [("rewrite", f"rewrite_v4_{v}") for v in ("natural", "explicit", "underspecified")]
    for role, version in versions:
        text = prompts.template_path(settings, role, version).read_text(encoding="utf-8")
        assert not [w for w in DOMAIN_WORDS if w in text], version


# 지금 쓰는 판: 낱말뿐 아니라 가정용 조작을 전제한 표현도 본문에 두지 않는다 (예시와 어휘는 프로필 자리로 받는다)
CURRENT = [("generate", "gen_v14"), ("checklist", "check_v13"), ("resolvability", "resolve_v6"),
           ("probe", "probe_v6")]
CURRENT += [("rewrite", f"rewrite_v6_{v}") for v in ("natural", "explicit", "underspecified")]
HOUSEHOLD_PHRASES = ("선반", "화구", "서랍", "캐비닛", "싱크대", "닫힌 문", "문을", "켜라", "끄라", "열라",
                     "닫으라", "꺼내라", "치우라", "the counter", "음식", "on(위)")


@pytest.mark.parametrize(("role", "version"), CURRENT)
def test_current_prompts_carry_no_household_wording(settings, role, version):
    text = prompts.template_path(settings, role, version).read_text(encoding="utf-8")
    assert not [w for w in (*DOMAIN_WORDS, *HOUSEHOLD_PHRASES) if w in text]
    prompts.fill(text.split(prompts.USER_MARKER)[0], {
        **domains.prompt_values(settings), "GOLDEN_EXAMPLES": "", "SCORE_MAX": "2"}, strict=True)


def test_domain_knowledge_is_injected_from_the_profile(settings, monkeypatch):
    """도메인에서만 필요한 지식은 프로필의 knowledge에 적으면 프롬프트의 자리에 들어간다."""
    profile = {**domains.profile(settings), "knowledge": "팔레트는 지게차로만 옮긴다."}
    monkeypatch.setattr(domains, "profile", lambda _settings: profile)
    text = prompts.template_path(settings, "generate", "gen_v14").read_text(encoding="utf-8")
    filled = prompts.fill(text, domains.prompt_values(settings), strict=False)
    assert "팔레트는 지게차로만 옮긴다." in filled

