# robot-tsd.dataset: 구축 파이프라인 (pipeline 브랜치)

[main 브랜치](https://github.com/KWONDU/robot-tsd.dataset)의 데이터셋을 만든 코드, 프롬프트, 도메인 자원, 대시보드예요. 로봇에게 내린 한국어 자연어 지시와 세계 상태를 입력으로, 추론과 작업 상황 설명문(TSD)을 만들고 검수해요. 실행 기록, 캐시, 원천 데이터는 들어 있지 않아요.

## 구성

```
src/tsdgen/
    adapters/        원천마다 장면을 공통 장면 상태로 (RoboCasa365, SimReady Warehouse, 공장 바닥 생성기)
    tasks/           작업 합성: 장면의 물체·설비로 작업 유형의 빈칸을 채워 지시와 목표 상태(정답)를 만든다
    stages/          파이프라인 스테이지 (ingest, compose, sample, contextualize, generate, verify, select, export ...)
    verify/          규칙 검사(구조, 장면 접지, 목표 상태, 지도의 길·기다림·시간 사실)와 LLM 채점
    goals/           목표 상태 단언과 정답 대조
    dashboard/       로컬 대시보드 (실행 기록을 읽어 보여 준다)
prompts/<역할>/<판>.txt    생성(generate), 재작성(rewrite), 채점(checklist), 정보 충분성(probe), 지시 해소(resolvability)
resources/
    domains/         도메인 프로필 (가사, 물류, 제조): 낱말, 프롬프트 칸 값, 작업 합성 규칙
    golden/          생성 프롬프트에 넣는 예시
    adapters/        원천 어댑터 표, ko_lexicon.yaml (한국어 낱말 사전)
config/              config.yaml (실행 설정), models.yaml (모델과 파라미터), sources.yaml (원천)
schema/              장면 상태, 설명문, 샘플의 JSON Schema
scripts/ tests/      보조 스크립트와 테스트 (mock LLM, 오프라인)
SPEC.md              설계
```

## 실행

```bash
PY=.conda/bin/python
cp .env.example .env                       # API 키 (OPENAI_API_KEY, OPENROUTER_API_KEY)
TSDGEN_STAGE=compose $PY -m tsdgen         # 작업 합성 (LLM 호출 없음)
TSDGEN_STAGE=estimate $PY -m tsdgen        # 비용 추정 (LLM 호출 없음)
scripts/run_detached.sh run                # 전체 파이프라인 (sample -> contextualize -> generate -> verify -> select)
scripts/keep_dashboard.sh                  # 로컬 대시보드 http://127.0.0.1:8765/
$PY -m pytest tests                        # 테스트
```

실행 대상은 환경변수 `TSDGEN_STAGE` 하나로 고르고, 나머지는 `config/config.yaml`이 정해요.

## 데이터셋에 쓴 프롬프트 판

| 도메인 | 생성 | 재작성 | 지시 해소 | 채점 | 정보 충분성 |
|---|---|---|---|---|---|
| 가사 | gen_v17 | rewrite_v6 | resolve_v6 | check_v17 | probe_v6 |
| 물류 | gen_v18 | rewrite_v6 | resolve_v6 | check_v18 | probe_v6 |
| 제조 | gen_v19 | rewrite_v7 | resolve_v7 | check_v20 | probe_v6 |

프롬프트는 도메인에 따라 바뀌는 자리(`<<DOMAIN_...>>`, `<<EX_...>>`)를 `resources/domains/<도메인>.yaml`의 값으로 채워요. 가사·물류에서는 같은 역할의 나중 판이 앞 판과 채운 글까지 같아요(tests/test_situation.py).

## 모델

생성: gpt-6-sol, gemini-3.8-flash, qwen3.8-27b-high(reasoning effort high). 재작성·지시 해소·채점·정보 충분성: gpt-6-luna. 모델별 파라미터는 `config/models.yaml`, 역할별 파라미터는 `config/config.yaml`의 `llm.generation`, `llm.judging`이에요. 제조의 gemini-3.8-flash와 qwen3.8-27b-high는 출력 한도 32,000 토큰으로 돌렸어요(가사·물류는 16,000).

## 라이선스

미정. 원천: RoboCasa365 (MIT), NVIDIA SimReady (CC BY 4.0), FloorLayout.jl FLP instances (MIT), RoboCup Logistics League 2025 rulebook.
