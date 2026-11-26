# 작업상황설명문 샘플 v1

가정용 로봇에게 내린 자연어 지시와 주방 환경 정보를 입력으로, 로봇이 작업 계획을 세우기 전에 알아야 할 정보를 정리한 문서인 "작업상황설명문"을 생성한 샘플 {n_samples}건입니다.

## 파일 구성

- `samples/`: 샘플 {n_samples}건. 파일 하나가 샘플 한 건이며, 파일 이름은 순번과 태스크 이름입니다 (예: 05_BlendMarinade.json).
- `schema/task_situation_description.schema.json`: 작업상황설명문 JSON 구조의 형식 명세입니다.
- `generation_prompt.txt`: 생성에 실제로 사용한 프롬프트입니다.

## 샘플이 만들어진 과정

1. {source_description}에서 영어 작업 지시와 씬 안의 {fixture_word}·물체 정보를 추출했습니다.
2. 영어 지시를 한국어 사용자가 실제로 말할 법한 발화로 다시 썼습니다. 원문도 샘플에 함께 있습니다.
3. 언어모델({generator})에 한국어 지시와 환경 정보를 주고 작업상황설명문을 JSON으로 직접 출력하게 했습니다. 출력 구조는 스키마로 강제됩니다.
4. 생성 결과는 자동 검증을 거쳤습니다: 형식 검사, 씬에 없는 물체·상태를 언급하지 않는지 검사, 그리고 별도 검증 언어모델({verifier})이 품질(목표 반영, 물체·위치 충분성, 이 문서만으로 계획 수립 가능한지 등)을 채점했습니다. 검증을 통과한 샘플만 담았습니다.

## 샘플 한 건의 구조

만든 방법(meta), 입력(input), 출력(output)이 한 파일에 있습니다.

- `meta.generator_model`: 이 설명문을 생성한 언어모델
- `meta.task_template`: 이 샘플의 장면과 지시를 낳은 태스크 템플릿 (원천 데이터셋/태스크 이름, 예: robocasa365/ArrangeBuffetDessert). 원천의 태스크 하나가 물체·가구 자리를 비워 둔 틀이고, 에피소드마다 그 자리를 다른 물체와 배치로 채운 것이 장면입니다

- `input.user_instruction`: 로봇에게 내리는 한국어 지시
- `input.original_instruction_en`: 원본 데이터셋의 영어 지시
- `input.environment_context`: {input_environment}
- `output.reasoning`: 설명문을 쓰기 전에 모델이 정리한 추론 과정
- `output.task_situation_description`: 작업상황설명문 본문

## 작업상황설명문의 4개 필드

- `task_goal`: 이 작업이 무엇을 이루려는 것인지. 두 부분으로 되어 있습니다.
  - `summary`: 사람이 읽는 한 문장 요약
  - `goal_state`: 작업을 마쳤을 때 성립해야 하는 상태 (행동이나 순서가 아니라 최종 상태). "경우"의 목록이고 경우 하나는 단언 목록입니다. 한 경우 안의 단언은 모두 성립해야 하고, 경우들 중 하나만 성립하면 됩니다. 지시를 만족하는 최종 상태가 여러 가지면(예: 어느 그릇에 담든 되는 지시) 경우마다 하나씩 모두 적고, 하나뿐이면 경우 하나입니다. 단언 하나는 `kind`(relation: 물체·가구 사이 관계, state: 물체나 가구의 상태), {pointer_note}, `predicate`(관계 이름 또는 상태 키), `value`(true면 성립해야 함, false면 성립하지 않아야 함)입니다. 적혀 있지 않은 상태는 바뀌어도 상관없다는 뜻입니다.
- `spatiotemporal_conditions`: 지시나 씬에 있는 시간·공간 조건 (없으면 null)
- `target_objects`: 작업과 관련된 물체를 세 부류로 나누고 각 물체의 위치를 함께 기록
  - `required`: 반드시 조작해야 하는 물체
  - `key_means`: 목표를 이루는 수단이 되는 물체 (대안이 있으면 alternatives에 함께)
  - `auxiliary`: 위치 기준 등 참조용 물체
- `constraints`: 지켜야 할 제약. 종류는 순서(precedence), 자원(resource), 물리(physical), 안전(safety), 공간(spatial), 기타(other)입니다.

씬 데이터에 없는 정보는 지어내지 않는 것이 생성 규칙입니다. 그런 칸은 비워 둡니다(null). 예를 들어 가구처럼 놓인 곳이 따로 없는 대상은 location이 null이고, 상태가 확인되지 않은 대상은 state가 null입니다.

## 프롬프트 읽는 법

`generation_prompt.txt`에서 `---USER---` 윗부분이 시스템 메시지(작성 규칙과 예시 1건 포함)이고, 아랫부분이 사용자 메시지 틀입니다. `<<USER_INSTRUCTION>>`과 `<<ENVIRONMENT_CONTEXT>>` 자리에 샘플의 input이 그대로 들어갑니다.
