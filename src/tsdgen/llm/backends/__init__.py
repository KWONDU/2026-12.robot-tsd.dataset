"""LLM 백엔드 등록 모듈. 임포트만으로 registry에 등록된다."""

from tsdgen.llm.backends import gemini, hf_local, mock, openai_compat  # noqa: F401
