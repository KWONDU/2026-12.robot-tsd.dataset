"""OpenAI 호환 chat/completions 백엔드 (OpenAI, vLLM 서버 공용)."""

from tsdgen.config import ModelCfg, Settings
from tsdgen.llm.backends.transport import apply_model_params, post_json, resolve_api_key
from tsdgen.llm.base import Backend, LLMRequest, LLMResponse
from tsdgen.llm.schema import strict_capable, strict_schema
from tsdgen.registry import LLM_BACKENDS


@LLM_BACKENDS.register("openai_compat")
class OpenAICompatBackend(Backend):
    def complete(self, req: LLMRequest, model_cfg: ModelCfg, settings: Settings) -> LLMResponse:
        body: dict = {
            "model": model_cfg.model,
            "messages": req.messages,
            "temperature": req.temperature,
            model_cfg.token_param: req.max_output_tokens,
        }
        if req.seed is not None:
            body["seed"] = req.seed
        # 역할이 원하는 값을 먼저, 모델의 호환성 조정을 나중에 적용한다
        apply_model_params(body, req.extra)
        apply_model_params(body, model_cfg.params)
        if req.json_schema is not None and model_cfg.supports_json_schema:
            strict = model_cfg.json_schema_strict and strict_capable(req.json_schema)
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": req.json_schema.get("title", "output"),
                    "schema": strict_schema(req.json_schema) if strict else req.json_schema,
                    "strict": strict,
                },
            }
        data = post_json(
            f"{model_cfg.base_url}/chat/completions",
            body=body,
            headers={"Authorization": f"Bearer {resolve_api_key(model_cfg)}"},
            timeout=settings.llm.retry.request_timeout_s,
        )
        usage = data.get("usage") or {}
        return LLMResponse(
            text=data["choices"][0]["message"]["content"] or "",
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            billed_usd=usage.get("cost"),  # OpenRouter가 알려 주는 실제 청구액 (OpenAI는 주지 않는다)
            cached_input_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0,
            provider=data.get("provider"),  # OpenRouter가 보낸 제공사
        )
