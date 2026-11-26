"""도메인 프로필: 프롬프트 본문에는 도메인 낱말이 없고, 낱말·예시·지식은 프로필의 자리로 들어간다.

새 도메인을 더할 때 프롬프트 규칙은 그대로이고 낱말·예시·목표 상태 어휘만 바뀐다는 뜻이다.
"""

import pytest

from tsdgen import domains, prompts
from tsdgen.resources import load_resources

DOMAIN_WORDS = ("주방", "냉장고", "전자레인지", "머그", "조리대", "가정", "가구", "그릇", "레몬")


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


# 낱말뿐 아니라 가정용 조작을 전제한 표현도 본문에 두지 않는다 (예시와 어휘는 프로필 자리로 받는다)
CURRENT = [("generate", "generate"), ("checklist", "checklist"), ("resolvability", "resolvability"),
           ("probe", "probe")]
CURRENT += [("rewrite", f"rewrite_{v}") for v in ("natural", "explicit", "underspecified")]
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
    text = prompts.template_path(settings, "generate", "generate").read_text(encoding="utf-8")
    filled = prompts.fill(text, domains.prompt_values(settings), strict=False)
    assert "팔레트는 지게차로만 옮긴다." in filled

