# 2026-12.robot-tsd.dataset: 구축 파이프라인

[main 브랜치](https://github.com/KWONDU/2026-12.robot-tsd.dataset)에 수록된 작업상황설명문(TSD) 데이터셋의 구축 파이프라인이다. 코드, 프롬프트, 도메인 리소스, 설정, 로컬 대시보드를 포함하며, 실행 기록과 캐시, 원천 데이터는 포함하지 않는다.

## 디렉터리 구조

```
src/tsdgen/
    adapters/        원천 데이터의 장면을 공통 scene state로 변환 (RoboCasa365, SimReady Warehouse, 공장 레이아웃 생성기)
    tasks/           작업 합성: 장면의 객체·설비로 작업 템플릿을 채워 지시와 정답 goal state를 생성
    stages/          파이프라인 스테이지 (ingest, compose, sample, contextualize, generate, verify, select, export)
    verify/          규칙 기반 검사(구조, scene grounding, goal state, 맵 기반 경로·대기·시간 조건)와 모델 기반 채점
    goals/           goal state 단언의 형식 검사와 정답 대조
    dashboard/       실행 기록을 조회하는 로컬 대시보드
prompts/             역할별 프롬프트 (generate, rewrite, resolvability, checklist, probe)
resources/
    domains/         도메인 프로필 (가사, 물류, 제조): 어휘, 프롬프트 자리 값, 작업 합성 규칙
    golden/          생성 프롬프트의 예시
    adapters/        원천 어댑터 설정
    ko_lexicon.yaml  한국어 어휘 사전
config/              config.yaml (실행 설정), models.yaml (모델과 파라미터), sources.yaml (원천 데이터)
schema/              scene state, 작업상황설명문, 샘플의 JSON Schema
scripts/, tests/     보조 스크립트와 테스트 (mock LLM, 오프라인)
SPEC.md              설계 문서
```

## 실행

```bash
PY=.conda/bin/python
cp .env.example .env                       # API 키 (OPENAI_API_KEY, OPENROUTER_API_KEY)
TSDGEN_STAGE=compose $PY -m tsdgen         # 작업 합성 (LLM 호출 없음)
TSDGEN_STAGE=estimate $PY -m tsdgen        # 비용 추정 (LLM 호출 없음)
scripts/run_detached.sh run                # 전체 파이프라인 (sample, contextualize, generate, verify, select)
scripts/keep_dashboard.sh                  # 로컬 대시보드 http://127.0.0.1:8765/
$PY -m pytest tests                        # 테스트
```

실행할 스테이지는 환경변수 `TSDGEN_STAGE`로 지정하며, 그 밖의 설정은 모두 `config/config.yaml`에서 관리한다.

## 프롬프트

| 역할 | 파일 | 모델 |
|---|---|---|
| 지시 재작성 | `prompts/rewrite/rewrite_<구체성>.txt` | gpt-6-luna |
| 지시 해소 판정 | `prompts/resolvability/resolvability.txt` | gpt-6-luna |
| 추론·작업상황설명문 생성 | `prompts/generate/generate.txt` | gpt-6-sol, gemini-3.8-flash, qwen3.8-27b-high |
| 체크리스트 채점 | `prompts/checklist/checklist.txt` | gpt-6-luna |
| 정보 충분성 판정 | `prompts/probe/probe.txt` | gpt-6-luna |

프롬프트는 도메인 공통이며, 도메인에 따라 달라지는 어휘·예시·규칙 문장은 자리 표시자(`<<DOMAIN_...>>`, `<<EX_...>>`)로 두고 `resources/domains/<도메인>.yaml`의 값으로 채운다. 가사 도메인 데이터의 생성과 채점에는 작업 순서(precedence) 조건 문장 하나가 빠진 직전 프롬프트를 사용하였다.

## 모델 설정

모델별 파라미터는 `config/models.yaml`, 역할별 파라미터는 `config/config.yaml`의 `llm.generation`, `llm.judging`에 있다. qwen3.8-27b-high는 reasoning effort를 high로 설정하였다. 제조 도메인의 gemini-3.8-flash와 qwen3.8-27b-high는 최대 출력 토큰을 32,000으로 설정하였으며(가사·물류 16,000), 그 외 하이퍼파라미터는 모든 도메인에서 동일하다.

## 라이선스

미정. 원천 데이터: RoboCasa365 (MIT), NVIDIA SimReady (CC BY 4.0), FloorLayout.jl FLP instances (MIT), RoboCup Logistics League 2025 rulebook.
