"""Hugging Face 로컬 추론 백엔드 자리.

P0에서는 상용 API만 쓴다(스펙 7절). 오픈웨이트 모델은 vLLM 서버로 서빙해
openai_compat 백엔드로 붙이는 것이 기본 경로이며, 이 백엔드는 transformers
직접 로드가 필요해질 때를 위한 등록 지점이다.
"""

from tsdgen.config import ModelCfg, Settings
from tsdgen.llm.base import Backend, LLMError, LLMRequest, LLMResponse
from tsdgen.registry import LLM_BACKENDS


@LLM_BACKENDS.register("hf_local")
class HFLocalBackend(Backend):
    needs_api_key = False  # 로컬 가중치를 직접 로드하므로 키가 없다

    def complete(self, req: LLMRequest, model_cfg: ModelCfg, settings: Settings) -> LLMResponse:
        raise LLMError(
            "hf_local backend is a placeholder; serve the model with vLLM and use "
            "backend 'openai_compat' instead (see config llm.models)"
        )
