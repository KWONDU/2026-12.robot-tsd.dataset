"""캐시 키 안정성, JSON 복구, 예산 가드 테스트."""

import pytest

from tsdgen.llm.base import BudgetExceededError, LLMRequest, LLMResponse
from tsdgen.llm.cache import cache_key
from tsdgen.llm.client import UsageMeter, extract_json


def _request(**overrides) -> LLMRequest:
    base = {
        "model_key": "mock",
        "role": "generate",
        "messages": [{"role": "user", "content": "hi"}],
        "json_schema": {"title": "X"},
        "temperature": 0.7,
        "seed": 1,
        "max_output_tokens": 100,
        "prompt_version": "gen_v1",
    }
    base.update(overrides)
    return LLMRequest(**base)


def test_cache_key_stable():
    assert cache_key(_request()) == cache_key(_request())


def test_cache_key_follows_model_overrides():
    """모델 설정이 값을 덮어쓰면(출력 한도 등) 다른 응답이므로 키가 달라진다. 키를 빼는 null만 있으면 예전 키 그대로다."""
    plain = cache_key(_request())
    assert cache_key(_request(), {"temperature": None}) == plain
    assert cache_key(_request(), {"max_tokens": 16000}) != plain


@pytest.mark.parametrize(
    "overrides",
    [
        {"temperature": 0.8},
        {"seed": 2},
        {"prompt_version": "gen_v2"},
        {"messages": [{"role": "user", "content": "bye"}]},
        {"model_key": "gpt-5.4-mini"},
        {"extra": {"reasoning_effort": "low"}},
        {"json_schema": {"title": "Y"}},
    ],
)
def test_cache_key_varies(overrides):
    assert cache_key(_request()) != cache_key(_request(**overrides))


def test_cache_key_covers_every_request_field():
    """응답을 바꿀 수 있는 요청 필드가 키에서 빠지면 설정 변경이 캐시에 묻힌다.

    필드를 새로 추가했을 때 키에 넣는 것을 잊으면 여기서 실패한다.
    """
    import dataclasses

    ignored = {"role"}  # 역할 이름은 라우팅·기록용이고 응답 내용에 영향을 주지 않는다
    base = _request()
    changes = {
        "model_key": "other-model",
        "messages": [{"role": "user", "content": "다른 내용"}],
        "json_schema": {"title": "Other"},
        "temperature": 0.123,
        "seed": 999,
        "max_output_tokens": 4321,
        "prompt_version": "other_v9",
        "extra": {"reasoning_effort": "medium"},
    }
    fields = {f.name for f in dataclasses.fields(base)} - ignored
    assert fields == set(changes), f"요청 필드가 바뀌었다. 캐시 키 검사에 반영할 것: {fields}"
    for name, value in changes.items():
        assert cache_key(base) != cache_key(_request(**{name: value})), f"{name}이 키에 없다"


def test_extract_json_from_fenced_block():
    text = "설명입니다.\n```json\n{\"a\": 1}\n```\n끝."
    assert extract_json(text) == {"a": 1}


def test_extract_json_from_surrounding_prose():
    assert extract_json('결과: {"a": {"b": 2}} 입니다') == {"a": {"b": 2}}


def test_budget_guard_trips(settings):
    settings.estimate.budget_usd = 0.001
    settings.estimate.prices_per_1m_tokens["mock"] = {"input": 100.0, "output": 100.0}
    meter = UsageMeter(settings, settings.run_dir() / "usage.jsonl")
    req = _request()
    meter.record(req, LLMResponse(text="", input_tokens=1_000_000, output_tokens=0))
    with pytest.raises(BudgetExceededError):
        meter.check_budget()


def test_budget_guard_inactive_without_budget(settings):
    settings.estimate.budget_usd = None
    meter = UsageMeter(settings, settings.run_dir() / "usage.jsonl")
    meter.record(_request(), LLMResponse(text="", input_tokens=10, output_tokens=10))
    meter.check_budget()  # 예외 없음


def test_fill_is_single_pass():
    """값 안에 자리 표시자 모양이 있어도 다시 치환하지 않는다.

    입력 텍스트(지시문·씬)가 프롬프트의 다른 자리를 바꾸지 못하게 하기 위한 것이다.
    """
    from tsdgen.prompts import fill

    assert fill("<<A>>/<<B>>", {"A": "<<B>>", "B": "b"}) == "<<B>>/b"
    with pytest.raises(ValueError, match="<<C>>"):
        fill("<<C>>", {})
    assert fill("<<C>>", {}, strict=False) == "<<C>>"


def test_retry_does_not_sleep_after_last_attempt(settings, monkeypatch):
    """마지막 시도가 실패하면 바로 오류를 올린다. 기다린 뒤에 할 일이 없다."""
    from tsdgen.llm import client as client_module
    from tsdgen.llm.base import Backend, LLMError, TransientLLMError
    from tsdgen.registry import LLM_BACKENDS

    class AlwaysBusy(Backend):
        def complete(self, req, model_cfg, settings):
            raise TransientLLMError("429")

    sleeps: list[float] = []
    monkeypatch.setattr(client_module.time, "sleep", sleeps.append)
    monkeypatch.setitem(LLM_BACKENDS._items, "mock", AlwaysBusy)
    llm = client_module.LLMClient(settings, settings.run_dir())
    with pytest.raises(LLMError, match="after retries"):
        llm.complete(_request(model_key="mock"))
    assert len(sleeps) == settings.llm.retry.max_attempts - 1


def test_token_cost_is_unknown_without_prices(settings):
    from tsdgen.llm.client import token_cost

    settings.estimate.prices_per_1m_tokens["mock"] = {"input": None, "output": 1.0}
    assert token_cost(settings, "mock", 10, 10) is None
    settings.estimate.prices_per_1m_tokens["mock"] = {"input": 1.0, "output": 2.0}
    assert token_cost(settings, "mock", 1_000_000, 1_000_000) == 3.0
