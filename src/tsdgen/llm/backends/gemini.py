"""Gemini generateContent 백엔드."""

from tsdgen.config import ModelCfg, Settings
from tsdgen.llm.backends.transport import apply_model_params, post_json, resolve_api_key
from tsdgen.llm.base import Backend, LLMError, LLMRequest, LLMResponse
from tsdgen.registry import LLM_BACKENDS


@LLM_BACKENDS.register("gemini")
class GeminiBackend(Backend):
    def complete(self, req: LLMRequest, model_cfg: ModelCfg, settings: Settings) -> LLMResponse:
        system_parts = [m["content"] for m in req.messages if m["role"] == "system"]
        contents = [
            {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
            for m in req.messages
            if m["role"] != "system"
        ]
        generation_config: dict = {
            "temperature": req.temperature,
            "maxOutputTokens": req.max_output_tokens,
        }
        if req.seed is not None:
            generation_config["seed"] = req.seed
        if req.json_schema is not None and model_cfg.supports_json_schema:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseJsonSchema"] = req.json_schema
        apply_model_params(generation_config, req.extra)
        apply_model_params(generation_config, model_cfg.params)
        body: dict = {"contents": contents, "generationConfig": generation_config}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

        data = post_json(
            f"{model_cfg.base_url}/models/{model_cfg.model}:generateContent",
            body=body,
            headers={"x-goog-api-key": resolve_api_key(model_cfg)},
            timeout=settings.llm.retry.request_timeout_s,
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"unexpected gemini response: {str(data)[:500]}") from e
        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            text="".join(p.get("text", "") for p in parts),
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
        )
