"""HTTP 백엔드 공용 통로: 요청 전송, 오류 분류, 모델별 파라미터, API 키.

백엔드마다 다른 것은 요청 본문과 응답 모양뿐이다. 전송·재시도 대상 분류·키 조회는
같으므로 여기 한 곳에 둔다. 한 백엔드가 다른 백엔드 모듈에서 도구를 빌려 쓰지 않게 한다.
"""

import os
from typing import Any

import httpx

from tsdgen.config import ModelCfg
from tsdgen.llm.base import LLMError, TransientLLMError

TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504}


def apply_model_params(body: dict, params: dict) -> None:
    """모델별 파라미터를 요청 본문에 덮어쓴다. null은 그 키를 빼라는 뜻이다.

    모델마다 받는 파라미터가 다르다(reasoning_effort, verbosity, temperature 미지원 등).
    코드에 모델 이름을 두지 않고 config가 그대로 정하게 하기 위한 통로다.
    """
    for key, value in params.items():
        if value is None:
            body.pop(key, None)
        else:
            body[key] = value


def resolve_api_key(model_cfg: ModelCfg) -> str:
    key = os.environ.get(model_cfg.api_key_env or "")
    if not key:
        raise LLMError(
            f"API key env var '{model_cfg.api_key_env}' is not set "
            f"(required by model '{model_cfg.model}')"
        )
    return key


def post_json(url: str, *, body: dict, headers: dict, timeout: float) -> dict[str, Any]:
    """요청을 보내고 JSON 응답을 돌려준다. 재시도할 오류는 TransientLLMError로 올린다."""
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.TransportError as e:
        raise TransientLLMError(str(e)) from e
    if r.status_code in TRANSIENT_STATUS:
        raise TransientLLMError(f"HTTP {r.status_code}: {r.text[:300]}")
    if r.status_code != 200:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:1000]}")
    return r.json()
