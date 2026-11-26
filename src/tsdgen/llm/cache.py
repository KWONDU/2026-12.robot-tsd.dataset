"""응답 캐시.

키에는 응답을 바꿀 수 있는 요청 요소가 전부 들어가야 한다. 하나라도 빠지면 설정을
바꿨는데 옛 응답이 그대로 돌아오고, 실험 결과가 조용히 오염된다. reasoning_effort처럼
나중에 추가된 파라미터(extra)도 반드시 포함한다.
"""

import json
import os
import threading
from pathlib import Path

from tsdgen.llm.base import LLMRequest, LLMResponse
from tsdgen.util import stable_hash


def cache_key(req: LLMRequest, model_params: dict | None = None) -> str:
    """model_params는 모델 설정(models.yaml params)에서 값을 덮어쓰는 항목이다(출력 한도, 샘플링 값 등).
    값이 있는 항목만 넣는다: 키를 빼라는 null 항목만 있던 예전 설정의 키가 그대로 남게 하려는 것이다."""
    key = {
        "model": req.model_key,
        "prompt_version": req.prompt_version,
        "messages": req.messages,
        "temperature": req.temperature,
        "seed": req.seed,
        "max_output_tokens": req.max_output_tokens,
        "schema": req.json_schema,
        "extra": req.extra,
    }
    overrides = {k: v for k, v in (model_params or {}).items() if v is not None}
    if overrides:
        key["model_params"] = overrides
    return stable_hash(key, length=32)


class ResponseCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, req: LLMRequest, model_params: dict | None = None) -> LLMResponse | None:
        path = self._path(cache_key(req, model_params))
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None  # 손상·부분 파일은 캐시 미스로 취급한다
        return LLMResponse(
            text=data["text"],
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cached=True,
        )

    def put(self, req: LLMRequest, resp: LLMResponse, model_params: dict | None = None) -> None:
        path = self._path(cache_key(req, model_params))
        payload = {
            "model": req.model_key,
            "role": req.role,
            "prompt_version": req.prompt_version,
            "text": resp.text,
            "input_tokens": resp.input_tokens,
            "output_tokens": resp.output_tokens,
        }
        # 동시 접근에서 부분 읽기가 없도록 임시 파일에 쓰고 원자적으로 교체한다
        tmp_path = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, path)
