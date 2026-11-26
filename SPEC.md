# tsdgen 설계

이 문서는 현재 코드의 유일한 설명이다. 코드와 이 문서가 어긋나면 어느 쪽이 맞는지 확인해서 둘 중 하나를 고치고, 어긋난 채로 두지 않는다. 사람이 준 요구사항 원문은 [CODEBASE_SPECIFICATION.md](CODEBASE_SPECIFICATION.md)이고, 결정 이력과 되돌리는 방법은 [docs/DECISIONS.md](docs/DECISIONS.md), 스키마 변경 이력은 [docs/SCHEMA_CHANGELOG.md](docs/SCHEMA_CHANGELOG.md), 데이터 사실관계는 [docs/RECON_ROBOCASA365.md](docs/RECON_ROBOCASA365.md)에 있다.

## 목적

로봇 시뮬레이션 데이터셋(RoboCasa365)의 에피소드에서 구조화된 씬 상태를 뽑고, LLM 앙상블로 `{유저 입력(한국어 지시), 환경 컨텍스트(씬 상태)} → {추론, 작업상황설명문 JSON}` 샘플을 만든다. 만든 샘플은 규칙 검사와 LLM 교차 채점으로 검수하고, 통과분은 데이터셋으로, 미달분은 negative로 보존해 내보낸다. 작업상황설명문은 KIST 작업명세 모델의 입력이 되는 인터페이스이며, 실행 단계 목록(분해)은 넣지 않는다.

## 흐름

`TSDGEN_STAGE=<스테이지> python -m tsdgen` 한 번이 스테이지 하나다. 실행 대상 선택은 이 환경변수 하나뿐이고 나머지는 전부 config가 정한다. `run` 스테이지는 `project.pipeline_order`를 차례로 돈다.

1. ingest: 원천 어댑터가 시나리오(씬 상태 + 원 지시문)를 `data/scenarios/<어댑터>.jsonl`로 적재한다. 장면을 늘릴 때는 순서 밖의 `synthesize` 스테이지가 원천의 생성기로 새 장면을 만들어 같은 시나리오 파일에 더한다: RoboCasa는 주방(배치·스타일)을 고정하고 태스크 템플릿과 seed를 바꿔 시뮬레이터를 reset한다(`adapters/robocasa_synth.py`, LLM 호출 없음). 작업을 늘릴 때는 `compose` 스테이지가 바탕 장면마다 그 장면의 물체·가구·칸으로 작업 유형의 빈칸을 채워 지시와 목표 상태(정답)를 만들어 같은 파일에 더한다(`tasks/`, LLM 호출 없음, 같은 상황은 한 번만 고른다).
2. sample: 필터(`sampling.filters`)로 성립하지 않는 시나리오를 거르고(사유와 함께 보존), 선별 전략으로 `project.n_scenarios`건을 고른다. `sampling.same_scenarios_as_run`을 주면 그 실행이 고른 장면을 그대로 고르고(반복 점검), `sampling.exclude_tasks_of_runs`를 주면 그 실행들이 쓴 태스크의 장면을 뺀다(처음 보는 태스크로 확인).
3. contextualize: 모델에게 줄 세계 상태 글을 만들고(`contextualize.world_state` 보기), 원 지시문을 한국어 발화로 재작성한 뒤 그 세계 상태만으로 같은 작업으로 해소되는지 판정한다. 해소 불가면 폴백 구체성으로 한 번 더 쓴다. 뒤 스테이지(생성, 체크리스트 채점)도 이때 만든 글을 그대로 받는다.
4. generate: 시나리오당 `llm.per_scenario_k`개를 생성 모델들이 번갈아 만든다. 출력은 추론과 작업상황설명문 JSON이고, 사람이 읽는 산문은 JSON에서 결정론으로 렌더링한다.
5. verify: `verify.checks`의 항목을 순서대로 돌리고 앞 항목이 실패하면 뒤는 생략한다. judge 호출 자체가 실패하면 미달과 구분해 needs_review로 남긴다.
6. select: 시나리오마다 전달본을 고르고 나머지 통과분은 차순위(alternate)로 둔다.
7. export: `export.formats`의 포맷으로 `deliverables/<run_id>_<날짜>/`에 쓴다.

예산 가드는 LLM을 부르는 첫 스테이지(스테이지가 `calls_llm`으로 선언) 직전에 비용 추정으로 건다. 스테이지는 레코드 단위로 재개된다(완료 레코드는 건너뜀, select는 매번 전량 재계산).

## 데이터 모델

정의는 `src/tsdgen/models.py`, JSON Schema는 `TSDGEN_STAGE=schemas`가 `schema/`에 만든다. 버전 값은 config의 `schema_versions`가 단일 출처다.

- SceneState: 시뮬레이터 독립 씬 상태(layout, fixtures, objects, relations, agent, constraints, provenance). 모델 입력으로 내보낼 때는 `to_environment_context`가 정답 단서(provenance, 물체 역할, 태스크명이 든 scene_id)를 걷어낸다. 추출 단계는 둘이다. Tier A(기본)는 ep_meta와 태스크 소스 정적 분석만 쓰고 pose가 비어 있다. Tier B(`source.options.robocasa365.sim_state: true`)는 시연 첫 프레임의 MuJoCo 상태에서 물체 pose(월드 좌표 pos, 쿼터니언 quat)와 가구의 문·손잡이·수전 상태를 RoboCasa 원본 판정 기준대로 채우고, 어떤 키를 sim에서 정했는지 `provenance.sim_state_keys`에 남긴다. 관계 어휘는 두 단계 모두 on, in, near, held_by이고 지금 원천에서 실제로 나오는 것은 가구 배치(on, in)다. 메시가 배포에 없어 접촉 판정을 할 수 없으므로 협의안의 in_contact는 만들지 않는다.
- 세계 상태 보기(`world_state.py`): 씬 상태는 내부 정본으로 두고 LLM에게는 보기가 만든 글만 준다. `json`은 환경 컨텍스트 JSON과 id를 그대로 보이는 예전 방식이다. `text`는 실제 로봇이 인식한 세계 상태를 자연어로 넘기는 상황에 맞춰, id 없이 한국어 이름으로 쓰고 같은 이름이 여럿이면 '조리대 2'처럼 번호를 붙인다(좌표 같은 시뮬레이터 전용 값은 빼고, 같은 상태의 같은 가구는 한 줄로 묶는다). 세계 상태는 태스크와 무관하게 장면에 있는 것을 전부 적는다: 물체는 놓인 곳을 원천이 알려 주는 만큼 자세히(가구 안의 칸 '냉장고 냉동실 선반 위', 배치 기준이 된 옆 가구 '조리대 3 위, 싱크대 옆', 기준 물체 '접시 위'), 가구는 상태가 있으면 상태와 함께 이름을 적는다. 데이터에 없는 정보는 '모름' 같은 말로 채우지 않고 적지 않으며, 설명문도 그 칸을 비운다(null). 모델은 설명문의 `ref`·`subject`·`object`에 그 이름을 쓰고, 검수는 샘플에 남긴 이름 대응표(`input.entity_labels`)로 id에 되짚어 규칙 검사를 돌린다. 채점 모델은 생성 모델이 받은 글과 쓴 그대로의 설명문을 본다. 보기를 바꾸면 프롬프트도 그 보기용 판을 고른다. 씬 0.5부터는 가구의 칸과 칸별 상태(켜진 화구, 빼 놓은 랙, 열린 서랍), 같은 종류 물체의 크기 순서, 일부만 열린 문, 뚜껑, 가구 옆의 조리대('조리대 2: 싱크대 옆'), 원천이 식사용으로 치는 조리대도 적는다. 모두 시뮬레이터 첫 프레임과 배치 파일에서 읽고, 읽는 규칙과 기준값은 어댑터 자원(`resources/adapters/robocasa365.yaml`)에 있다.
- TaskSituationDescription: 최상위 4요소는 작업 목표(`task_goal`), 시간·공간 조건, 대상 물체(필수·핵심 수단·보조, 각 위치 병기), 제약(유형 포함)이다. 공간 조건(`space`)은 작업이 시작되는 곳(`start`)과 끝나는 곳(`end`)이고, 지시가 순서를 정하지 않아 후보가 여럿이면 후보를 모두 '혹은'으로 잇는다(킥오프 자료의 '시간·공간 조건(시작/종료)', 협의자료 6쪽 예시). 작업 목표는 요약(`summary`)과 목표 상태(`goal_state`)로 이루어진다.
- Sample: 입력(지시, 원문, 구체성, 환경 컨텍스트, 모델이 받은 세계 상태 글과 이름 대응표), 추론, 출력(설명문 JSON과 렌더링 산문), 메타(생성·재작성 정보, 검수 결과, 상태, 스키마 버전).

## 목표 상태

목표 상태는 작업을 마쳤을 때 성립해야 하는 상태 단언 목록이고, 행동이나 순서가 아니다. 현재 씬과 같은 어휘(관계 술어, 사전의 상태 키, entity id)로 쓰며, 적지 않은 상태는 상관없음이다. 가열 시간처럼 상태로 표현할 수 없는 기준은 목표 상태에 억지로 넣지 않는다: 정해진 시간은 시간 조건(`time`)에, 정도·과정 기준은 `other` 제약에 쓴다(3.2까지 있던 완료 판정 칸은 3.3에서 뺐다. 협의자료와 KIST 요청에 없던 칸이다).

- 단언·판정 종류는 `goals/kinds.py`의 등록부이고, 종류마다 형식 검사·씬 판정·한 줄 표기를 전략 하나가 맡는다.
- 판정기(`goals/verifier.py`)는 씬 상태 하나에 대 만족·불만족·판단 불가를 돌려준다. 확인했는데 아닌 것과 정보가 없는 것을 섞지 않는다.
- 검수 `goal`은 형식·접지, 대상 물체와의 일관성, 목표 상태가 비었을 때 상태로 쓸 수 없는 기준(시간 조건, other 제약)이 있는지, 초기 씬에서 이미 성립하는 무의미한 목표를 규칙으로 본다.
- 검수 `goal_oracle`은 원천의 성공 판정과 대조한다. RoboCasa는 prepare 스테이지가 태스크 클래스의 `_check_success()`를 정적 분석해 정답(`success_map.yaml`)을 만들고, 어댑터의 `goal_oracle()`이 원본 이름을 씬의 id로 바꿔 준다. 초기 씬에서 이미 성립하는 정답 조건은 재현율에서 뺀다. 기본은 기록만 하고, `verify.thresholds.goal_oracle_min_recall`에 값을 넣으면 그 미만을 탈락시킨다.

## 목표 상태의 종류 (kind)

`task_goal.goal_state`는 경우 목록이다. 경우 하나는 단언 목록이고 그 안의 단언은 모두 성립해야 하며, 경우들 중 하나만 성립하면 된다. 지시를 만족하는 최종 상태가 여러 가지면(어느 그릇에 담든 되는 지시) 경우마다 하나씩 모두 적는다. 단언의 `kind`는 이 과제에서 정한 분류다(TSD 2.0, KIST의 goal_state 제안 회신 후속). RoboCasa의 분류가 아니고, RoboCasa 성공 판정 함수(`check_obj_in_receptacle`, `is_open` 등)는 정답 대조 때 이 분류로 옮긴다. 등록부는 `goals/kinds.py`(GOAL_KINDS, COMPLETION_KINDS)이고 새 종류는 등록 한 줄로 더한다.

가리키는 이름(ref, subject, object)은 세계 상태 글의 이름이다. 물체·가구 이름('조리대 2') 말고도 가구의 칸('냉장고 냉장실 선반 2', '스토브 뒤 왼쪽 화구', 칸 id는 `<가구 id>/<칸 key>`)을 쓸 수 있고, 여럿 중 아무 것이면 번호 뗀 이름('조리대'), 칸 이름의 앞부분('냉장고 냉동실'), 여럿이 나눠 가진 위치 설명('아일랜드 식탁')으로 쓴다. 검수는 이런 이름을 id를 `|`로 이은 참조로 되짚어(`world_names.py`) 하나라도 성립하면 성립으로 본다. 받는 곳은 지시가 말한 만큼만 구체적으로 쓰고, 경우가 여섯을 넘게 되는 지시는 나열하지 않고 번호 뗀 이름과 제약으로 쓴다.

역할은 목표 상태에서 따라 나온다: 목표 상태의 주어와 상태로 쓸 수 없는 목표의 조작 대상은 필수, 물체를 받는 곳과 도구는 핵심 수단, 보조는 닫혀 있어 열어야 하는 출발지 가구, 근처 관계의 기준, 옮길 물체가 처음 놓여 있는 곳(초기 위치 기준, 협의자료 6쪽 예시) 셋이다. 그 밖의 것(가리키는 데만 쓴 물체)은 올리지 않는다. 목표 상태로 쓸 수 없는 기준은 시간 조건(정해진 시간)과 other 제약(과정·정도)에 쓴다. 이 규칙은 goal 검사(`goals/validation.py`)가 확인한다.

| 자리 | kind | 뜻 | 칸 | 예 |
|---|---|---|---|---|
| 목표 단언 | relation | 물체·가구 사이 관계 | subject, predicate(on 위, in 안, near 근처, held_by 로봇이 들고 있음), object(held_by면 null), value | 레몬 조각 1 in 그릇 1 = true |
| 목표 단언 | state | 물체나 가구(가구의 칸 포함)의 상태 | subject, predicate(상태 키, 도메인 프로필의 goal_state_keys), object null, value | 싱크대 turned_on = false, 머그 filled = true |

value가 true면 성립해야 하고 false면 성립하지 않아야 한다. 적지 않은 상태는 바뀌어도 상관없다는 뜻이다.

## 도메인

도메인(가정, 제조, 물류 등)은 원천이 정한다: `config/sources.yaml`의 원천 블록마다 `domain`이 있고 `settings.domain`이 지금 원천의 값을 돌려준다. 도메인마다 달라지는 것은 프로필 파일 하나(`resources/domains/<도메인>.yaml`)에 모은다: 세계 상태 글의 장소 이름과 가구 절 제목, 프롬프트의 로봇 종류·장소·가구 낱말, 목표 상태에 쓸 수 있는 상태 키, 프롬프트 지침 속 예시, 골든 예시 파일. 프롬프트는 이 값들을 자리(`<<DOMAIN_ROBOT>>`, `<<DOMAIN_PLACE>>`, `<<DOMAIN_FIXTURE>>`, `<<DOMAIN_STATE_KEYS>>`, `<<DOMAIN_RELATIONS>>`, `<<DOMAIN_KNOWLEDGE>>`, `<<EX_...>>`)로 받고 `domains.prompt_values`가 채운다. 프롬프트 본문에는 도메인 낱말이 없다(tests/test_domains.py). 새 도메인은 프로필 하나와 그 도메인 장면을 내는 원천 어댑터를 더하면 되고, 상태 키를 새로 쓰면 사전 `state_phrases`에 풀이를 더한다.

## 교체 가능한 부분 (Strategy)

config가 고르는 축은 등록부 이름이 곧 허용값이고, 미등록 값은 config 로드 시점에 허용 목록과 함께 거부된다(`registry.validate_all`). 새 구현은 등록 한 줄로 추가하고 호출하는 쪽에 이름 분기를 만들지 않는다. 등록된 구현 목록은 등록부가 단일 출처이므로 여기 옮겨 적지 않는다.

| 축 | 등록부 | config 키 |
|---|---|---|
| 스테이지 | `registry.STAGES` (`stages/common.stage`로 등록, `calls_llm`·`orchestrates` 선언) | 환경변수 `TSDGEN_STAGE`, `project.pipeline_order` |
| 원천 어댑터 | `registry.SOURCE_ADAPTERS` (계약은 `adapters/base.SourceAdapter`, 설정 스키마는 어댑터의 `options_model`) | `source.adapter`, `source.prefetch_sources`, `source.options.<어댑터>` |
| 시나리오 필터 | `sampling/filters.SCENARIO_FILTERS` | `sampling.filters` |
| 작업 유형 (작업 합성) | `tasks/base.TASK_TYPES` (낱말과 규칙은 도메인 프로필의 `task_synthesis`) | `compose.<도메인>.types` |
| 작업 합성의 고르기 | `tasks/compose.SELECTIONS` (distinct: 유형 안에서 무작위, balanced: 난이도 단계를 고르게, 덜 나온 물체 종류·장면부터, `tasks/balance.py`) | `compose.<도메인>.selection`, `per_type`, `pool_factor`, `level_max`, `candidates`, `scene_cap` |
| 세계 상태 보기 | `world_state.WORLD_STATE_VIEWS` | `contextualize.world_state` |
| 선별 전략 | `registry.SAMPLING_STRATEGIES` (설정 스키마는 전략의 `options_model`) | `sampling.strategy`, `sampling.<전략>` |
| 전달본 선택 | `registry.SELECTION_STRATEGIES` | `llm.selection` |
| LLM 백엔드 | `registry.LLM_BACKENDS` (키 필요 여부를 백엔드가 선언) | `config/models.yaml`의 `backend` |
| LLM 역할 | `registry.ROLES` (`roles.py`: 프롬프트 버전·변형, 출력 스키마, 파라미터, 모델 라우팅, 호출 횟수) | `llm.roles`, `prompts_versions`, `estimate.assumed_output_tokens` |
| 검수 항목 | `verify/checks.VERIFY_CHECKS` (`verify_check`로 등록, 결과 필드 선언) | `verify.checks` |
| 목표 단언 종류 | `goals/kinds.GOAL_KINDS` | 생성 출력의 `kind` |
| 내보내기 포맷 | `registry.EXPORT_FORMATS` | `export.formats` |
| 대시보드 화면 | `dashboard/views/base.VIEWS` (등록 순서가 화면 순서) | 없음 |
| 대시보드 API | `dashboard/server.ROUTES` (GET 전용) | 없음 |
| 대시보드 LLM 분석 | `dashboard/llm_actions.LLM_ACTIONS` (채팅으로 요청받아 실행) | `dashboard.model`, `dashboard.prompt_version` |
| mock 응답 | `registry.MOCK_RESPONSES` (역할·분석마다 하나) | `llm.roles`에 mock 모델 바인딩 |
| 원천 전용 해석표 | RoboCasa 데이터 종류(`robocasa_fetch.DATASET_SOURCES`), 초기 상태 호출(`robocasa_staticmap.STATE_CALLS`), 성공 판정 호출(`robocasa_success.SUCCESS_CALLS`), Arena 관계(`arena_convert.RELATION_EXPORT`), 시뮬레이터 가구 상태 판정(`robocasa_sim.SIM_STATE_RULES`) | 해당 어댑터 설정, `resources/adapters/robocasa365.yaml`의 `sim_states` |

프롬프트 파일은 실험 조건이다. `prompts/<역할>/<버전>.txt`로 두고 config가 버전을 고르며, 기존 버전 파일은 고치지 않고 새 버전을 만든다. 실행 폴더에 사용한 프롬프트 사본과 hash가 남는다.

## config

`config/config.yaml`은 이번 실행의 조정값, `registries`가 가리키는 `config/models.yaml`(모델 속성·단가·키 환경변수 이름)과 `config/sources.yaml`(원천 속성)은 실행마다 바뀌지 않는 속성이다. 로드 시점에 하나의 `Settings`로 합쳐지고, 값 하나의 출처는 한 곳이다. 비밀값은 `.env`(셸에 이미 있는 값이 우선)에만 있다. 코드에는 숫자·경로·이름을 두지 않는다. 한국어 표시명과 분류 어휘는 `resources/ko_lexicon.yaml`, 원천 전용 해석표는 `resources/adapters/<어댑터>.yaml`이 단일 출처다.

## 산출물

- `data/scenarios/<어댑터>.jsonl`: 적재한 시나리오. `data/raw/`는 원천 캐시, `data/llm_cache/`는 요청 전체를 키로 한 LLM 응답 캐시(실행 사이에 공유), `data/robocasa_static/`은 prepare 산출물.
- `runs/<run_id>/`: 스테이지별 JSONL(`sample`, `contextualize`, `generate`, `verify`, `select`), `sample_excluded.jsonl`, `usage.jsonl`(호출별 토큰·비용), `estimate.json`, `state.sqlite`(레코드 상태), `manifest.json`(소스 hash, 설정 스냅샷, 프롬프트 hash, 스테이지 결과), `_config/`(설정 파일 사본, 해석된 설정과 그 hash), `_prompts/`(프롬프트 사본).
- `deliverables/<run_id>_<날짜>/`: `samples.jsonl`(채택), `alternates.jsonl`, `rejected.jsonl`(미달·재검토 negative), `scenarios.jsonl`, `README.md`, `stats.json`, `kist_package/`(KIST 전달본: 샘플별 JSON, 전달 스키마, 예시를 채운 생성 프롬프트, README). 전달본에는 검수·모델·비용 같은 내부 정보를 싣지 않고, id는 원본 이름으로, 관계 술어는 협의 어휘로 바꾼다.

## 대시보드

`TSDGEN_STAGE=dashboard`가 `dashboard.host:dashboard.port`에서 `runs/`를 읽어 보여 준다. 조회만 하고(GET 전용, 그 외 501) 아무것도 쓰지 않는다. 화면은 다섯이고 하나가 질문 하나에 답한다. 개요는 결과가 어떤지(가로 흐름도, 검수 깔때기, 생성 모델별 성적표, 체크리스트 항목 점수). 태스크는 시뮬레이션 장면마다 무엇을 받아 무엇을 냈는지다: 장면 갤러리(시작 화면, 지시, 모델별 판정)에서 장면을 고르면 시연 첫 프레임과 모델이 받은 세계 상태, 원문과 다시 쓴 지시, 모델별 출력(설명문·검수·추론), 그 장면으로 채운 생성 프롬프트가 나온다(`dashboard/tasks.py`). 프롬프트는 파이프라인 순서의 LLM 역할마다 프롬프트 섹션 구조, 샘플마다 채워지는 자리, 돌려받는 출력 JSON, 다른 버전과의 차이다(`dashboard/prompt_text.py`, `views/prompts.py`). 데이터 구조는 이 실행의 채택 샘플 한 건과 그 입력·출력·검수 결과가 어떤 JSON인지(접고 펴는 나무, 키에 마우스를 올리면 뜻·종류·필수 여부·허용 값), 실행 정보는 어떤 설정으로 얼마 들었는지다. 화면에는 파이프라인이 남긴 기록과 그 개수만 싣고, 화면이 따로 묶거나 추정한 정보(씬 지도, 입력 채움 분석, 모델 간 비교 지표)는 만들지 않는다. 개요의 빠진 수와 판정을 누르면 그 조건으로 걸러진 장면 갤러리로 간다. 블록 계약은 `views/base.py`에 있고, 집계의 근거 표(fold)는 API 응답에만 있고 화면에는 그리지 않는다. JSON 나무는 실제 값을 따라 그리고 키 설명은 코드의 스키마와 화면용 사전(`schema_field_ko`)에서 붙인다(`dashboard/json_tree.py`). 검수 순서·통과 기준·채점 기준·채점 모델·프롬프트는 실행이 남긴 설정·프롬프트 사본과 호출 기록을 따른다(`dashboard/rubric.py`).

## Isaac Lab-Arena 연동

씬 상태는 시뮬레이터 독립 규격이고 Arena는 그 규격에 붙는 소스(`source.adapter: isaaclab_arena`)이자 목적지(`export.formats`의 `arena_yaml`)다. 표현력 차이로 옮기지 못하는 항목은 숫자로 보고된다. 대응표와 한계는 [docs/ARENA_INTEROP.md](docs/ARENA_INTEROP.md)에 있다.

## 규약

`tests/test_conventions.py`가 강제한다: ruff(규칙은 `pyproject.toml`), 코드 구문 폭 100자(문자열·주석 안의 글자는 세지 않음), 함수 60줄·파일 400줄, print·argparse·박아 둔 절대경로·같은 대상의 문자열 분기 사슬 금지, 사람이 읽는 문구의 em dash와 물결표 금지. 마크다운은 문단·항목마다 한 줄로 쓴다(`tests/test_markdown_convention.py`). 테스트는 전부 오프라인이고 LLM은 mock이다.
