"""LLM 호출 파사드: 캐시 → 재시도 → 사용량 계측 → 예산 가드 → JSON 검증."""

import dataclasses
import json
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from tsdgen import registry
from tsdgen.config import Settings
from tsdgen.llm.base import (
    BudgetExceededError,
    LLMError,
    LLMRequest,
    LLMResponse,
    TransientLLMError,
)
from tsdgen.llm.cache import ResponseCache
from tsdgen.runs import USAGE

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """모델 응답에서 JSON 오브젝트를 최대한 복구해 파싱한다."""
    candidate = text.strip()
    fenced = _FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(candidate[start : end + 1])


def token_cost(
    settings: Settings, model_key: str, input_tokens: float, output_tokens: float,
    cached_input_tokens: float = 0,
) -> float | None:
    """토큰 수의 USD 비용. 단가가 비어 있으면 모른다(None)고 답한다 (0으로 치지 않는다).

    실제 사용량 계측과 사전 비용 추정이 같은 산식을 쓰도록 여기 하나만 둔다. 단가에 캐시 입력값
    (cached_input)이 있으면 캐시로 청구된 입력 토큰은 그 값으로 센다 (없으면 정가).
    """
    prices = settings.estimate.prices_per_1m_tokens.get(model_key) or {}
    if prices.get("input") is None or prices.get("output") is None:
        return None
    cached = cached_input_tokens if prices.get("cached_input") is not None else 0
    fresh = input_tokens - cached
    # 캐시로 읽지 않은 입력은 대부분 캐시에 쓰는 것으로 청구된다 (캐시 쓰기 단가가 있으면 그 값)
    fresh_price = prices.get("cache_write") or prices["input"]
    return (fresh * fresh_price + cached * (prices.get("cached_input") or 0)
            + output_tokens * prices["output"]) / 1e6


class UsageMeter:
    def __init__(self, settings: Settings, usage_path: Path):
        self.settings = settings
        self.usage_path = usage_path
        self.spent_usd = 0.0
        self.cost_known = True
        self._lock = threading.Lock()

    def record(self, req: LLMRequest, resp: LLMResponse) -> None:
        """제공사가 청구액을 알려 주면 그 값을, 아니면 토큰 수 × 단가를 쓴다."""
        cost = resp.billed_usd
        if cost is None:
            cost = token_cost(self.settings, req.model_key, resp.input_tokens, resp.output_tokens,
                              resp.cached_input_tokens)
        with self._lock:
            if cost is None:
                self.cost_known = False
            elif not resp.cached:
                self.spent_usd += cost
            entry = {
                "ts": datetime.now(UTC).isoformat(),
                "model": req.model_key,
                "role": req.role,
                "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens,
                "cached": resp.cached,
                "cost_usd": cost,
                "billed": resp.billed_usd is not None,  # 참이면 제공사가 알려 준 실제 청구액
                "cached_input_tokens": resp.cached_input_tokens,
                "provider": resp.provider,
            }
            self.usage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def check_budget(self) -> None:
        budget = self.settings.estimate.budget_usd
        if budget is None or self.settings.estimate.confirm_over_budget:
            return
        with self._lock:
            if self.spent_usd > budget:
                raise BudgetExceededError(
                    f"spent {self.spent_usd:.2f} USD > budget {budget:.2f} USD; "
                    "raise estimate.budget_usd or set estimate.confirm_over_budget"
                )


class LLMClient:
    def __init__(self, settings: Settings, run_dir: Path):
        self.settings = settings
        self.cache = ResponseCache(settings.path("llm_cache"))
        self.meter = UsageMeter(settings, run_dir / USAGE)
        self._semaphores = {
            key: threading.Semaphore(cfg.concurrency)
            for key, cfg in settings.llm.models.items()
        }

    def complete(self, req: LLMRequest) -> LLMResponse:
        known = self.settings.llm.models.get(req.model_key)
        params = known.params if known else None  # 모델 설정이 바꾸는 값도 응답을 바꾸므로 캐시 키에 든다
        cached = self.cache.get(req, params)
        if cached is not None:
            self.meter.record(req, cached)
            return cached

        self.meter.check_budget()
        model_cfg = self.settings.model_cfg(req.model_key)
        if not model_cfg.enabled:
            raise LLMError(f"model '{req.model_key}' is disabled (llm.models.*.enabled)")
        backend = registry.LLM_BACKENDS.get(model_cfg.backend)()

        retry = self.settings.llm.retry
        last_error: Exception | None = None
        for attempt in range(retry.max_attempts):
            try:
                with self._semaphores[req.model_key]:
                    resp = backend.complete(req, model_cfg, self.settings)
                self.cache.put(req, resp, params)
                self.meter.record(req, resp)
                self.meter.check_budget()
                return resp
            except TransientLLMError as e:
                last_error = e
                if attempt + 1 < retry.max_attempts:  # 마지막 시도 뒤에는 기다릴 이유가 없다
                    time.sleep(retry.base_delay_s * (2**attempt))
        raise LLMError(f"model '{req.model_key}' failed after retries: {last_error}")

    def complete_validated(
        self, req: LLMRequest, output_model: type[BaseModel], json_retry: int
    ) -> tuple[BaseModel, LLMResponse]:
        """구조화 출력 호출. 스키마 위반 시 위반 메시지를 붙여 재시도한다."""
        current = req
        last_error = ""
        for _ in range(json_retry + 1):
            resp = self.complete(current)
            try:
                parsed = output_model.model_validate(extract_json(resp.text))
                return parsed, resp
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = str(e)
                retry_note = {
                    "role": "user",
                    "content": (
                        "직전 응답이 요구 스키마를 위반했다. 아래 오류를 고쳐 "
                        f"완전한 JSON 하나만 다시 출력하라.\n오류: {last_error[:1500]}"
                    ),
                }
                current = dataclasses.replace(
                    current, messages=[*current.messages, retry_note]
                )
        raise LLMError(f"structured output failed for {req.role}: {last_error[:500]}")

    def complete_structured(
        self,
        *,
        model_key: str,
        role_name: str,
        messages: list[dict],
        output_model: type[BaseModel],
        params: Any,
        seed: int | None,
        prompt_version: str,
    ) -> BaseModel:
        """구조화 출력 호출 하나를 요청으로 만들어 보내고 검증된 출력을 돌려준다.

        params는 temperature·max_output_tokens·json_retry·extra를 가진 설정 묶음이다
        (파이프라인 역할의 llm.generation/judging, 대시보드 분석의 dashboard 섹션).
        """
        request = LLMRequest(
            model_key=model_key,
            role=role_name,
            messages=messages,
            json_schema=json_schema_of(output_model),
            temperature=params.temperature,
            seed=seed,
            max_output_tokens=params.max_output_tokens,
            prompt_version=prompt_version,
            extra=params.extra,
        )
        parsed, _ = self.complete_validated(request, output_model, params.json_retry)
        return parsed


def json_schema_of(output_model: type[BaseModel]) -> dict:
    schema = output_model.model_json_schema(by_alias=True)
    schema["title"] = output_model.__name__
    return schema
