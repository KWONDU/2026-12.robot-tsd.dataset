# 2026-12.robot-tsd.dataset

자율행동체의 작업 상황 이해를 위한 작업상황설명문(Task Situation Description, TSD) 데이터셋이다. 한국어 자연어 지시(instruction)와 세계 상태(world state)를 입력으로, 추론(reasoning)과 작업상황설명문을 출력으로 한다. 가사(household), 물류(logistics), 제조(manufacturing) 3개 도메인의 62개 작업 유형(task type)에 대해 3종의 생성 모델로 샘플을 생성하고, 자동 검수를 통과한 60,411개 샘플을 수록하였다.

| 항목 | 내용 |
| --- | --- |
| 데이터셋 뷰어 | https://kwondu.github.io/2026-12.robot-tsd.dataset/ |
| 구축 파이프라인 | [`pipeline` 브랜치](https://github.com/KWONDU/2026-12.robot-tsd.dataset/tree/pipeline) (코드, 프롬프트, 도메인 리소스, 대시보드) |
| 라이선스 | 미정 (원천 데이터의 라이선스는 [원천 데이터 및 라이선스](#원천-데이터-및-라이선스) 참조) |

## 데이터셋 구성

| 도메인 | gpt-6-sol | gemini-3.8-flash | qwen3.8-27b-high | 합계 |
| --- | ---: | ---: | ---: | ---: |
| 가사 (household) | 11,899 | 5,818 | 5,634 | 23,351 |
| 물류 (logistics) | 10,899 | 5,131 | 4,598 | 20,628 |
| 제조 (manufacturing) | 9,453 | 4,850 | 2,129 | 16,432 |
| **합계** | 32,251 | 15,799 | 12,361 | **60,411** |

작업 유형은 가사 25개, 물류 24개, 제조 13개이며, 유형별 샘플 수는 [부록](#부록-작업-유형별-샘플-수)에 수록하였다.

## 디렉터리 구조

```
<domain>/<task_type>/<sample_id>/
    sample.json     입력(instruction, world state), 출력(reasoning, TSD), 메타데이터
    scene.json      구조화된 scene state (objects, fixtures, relations, robot; 제조는 map 포함)
    thumbnail.jpg   장면 썸네일
index.csv           전체 샘플 색인 (id, domain, task_type, agent, path)
```

## 데이터 형식

### sample.json

| 필드 | 설명 |
| --- | --- |
| `id` | 샘플 식별자 |
| `domain` | 도메인 (`household`, `logistics`, `manufacturing`) |
| `task_type` | 작업 유형 |
| `agent` | 생성 모델 |
| `source.data` | 원천 데이터 |
| `source.scene_id` | 장면 식별자 |
| `source.task` | 원천 작업 이름 |
| `source.composed` | 합성 작업 여부 (`true`: 장면의 객체로 작업 템플릿을 채워 합성, `false`: 원천 데이터의 작업) |
| `input.instruction` | 한국어 자연어 지시 |
| `input.instruction_en` | 한국어로 재작성하기 전의 영어 지시 |
| `input.world_state` | 모델에 입력된 세계 상태 서술 (entity ID 대신 이름 사용) |
| `input.entity_labels` | entity ID와 세계 상태 서술의 이름 간 대응 |
| `output.reasoning` | 추론 과정 (지시 해석, scene grounding, 역할 부여, 제약 도출, goal state 도출) |
| `output.task_situation_description` | 작업상황설명문 (JSON, 아래 표) |
| `output.task_situation_description_text` | 작업상황설명문의 자연어 서술 |

### 작업상황설명문

| 필드 | 설명 |
| --- | --- |
| `task_goal.summary` | 작업 목표 요약 |
| `task_goal.goal_state` | 작업 완료 시 성립해야 하는 상태 단언(assertion)의 목록. 허용되는 최종 상태가 여럿이면 경우(case)별 목록 |
| `spatiotemporal_conditions.time` | 시간 조건 (지속 시간, 대기 시간, 기한). 없으면 `null` |
| `spatiotemporal_conditions.space` | 공간 조건: 작업 시작 위치(`start`)와 종료 위치(`end`) |
| `target_objects.required` | 목표 상태에 직접 등장하는 필수 대상. 대상마다 `ref`(세계 상태의 이름), `location`, `state` |
| `target_objects.key_means` | 작업 수행의 핵심 수단 (예: 물체를 넣을 칸, 사용할 설비) |
| `target_objects.auxiliary` | 작업 수행을 위해 먼저 상태를 바꿔야 하는 보조 대상 (예: 열어야 하는 문) |
| `constraints` | 제약 사항 목록. 각 항목은 `type`(precedence, spatial, physical, safety, resource, other)과 `description` |

### 썸네일

| 도메인 | 생성 방법 |
| --- | --- |
| 가사 | RoboCasa365 장면의 초기 상태를 MuJoCo로 렌더링. 로봇 쪽에서 작업 대상 물체를 내려다보는 시점 |
| 물류 | 장면을 구성하는 작업 구역(2-3개)의 NVIDIA SimReady 공식 렌더를 합성 |
| 제조 | 공장 맵 평면도 (구역별 설비, 통로, 폐쇄 통로, 일방통행 통로, 출입 제한 구역, 로봇 위치) |

## 도메인

| 도메인 | 장면 | 작업 |
| --- | --- | --- |
| 가사 | RoboCasa365 주방 장면 | RoboCasa365 원천 작업과, 장면의 객체·가구로 작업 템플릿을 채운 합성 작업 |
| 물류 | NVIDIA PhysicalAI-SimReady-Warehouse-01의 작업 구역(팔레트, 랙, 카트, 작업대) 2-3개를 결합한 창고 장면 | 적재(stacking), 수납(containment), 동일 물품 다수 처리를 포함한 합성 작업 |
| 제조 | RoboCup Logistics League 2025 규칙과 시설 배치 벤치마크(FloorLayout.jl FLP instances)로 생성한 공장 레이아웃과 맵. 물품은 NVIDIA SimReady 에셋 | 작업장 간 운반 작업. 폐쇄 통로, 출입 제한 구역, 일방통행 통로, 점검으로 정지된 설비, 시간 제약(time window) 등의 상황 조건 포함 |

## 구축 절차

1. **지시 재작성**: 작업마다 영어 지시를 한국어 자연 발화로 재작성하고(gpt-6-luna), 재작성된 지시가 세계 상태만으로 원 작업을 특정할 수 있는지 판정한다.
2. **생성**: 생성 모델(gpt-6-sol, gemini-3.8-flash, qwen3.8-27b-high)이 지시와 세계 상태를 입력받아 추론과 작업상황설명문을 생성한다. qwen3.8-27b-high는 reasoning effort를 high로 설정하였다.
3. **검수**: 아래 검사를 모두 통과한 샘플만 수록한다.
    - 규칙 기반: 스키마 및 구조 검사, scene grounding 검사(장면에 없는 객체·상태 참조 여부), goal state 규칙 검사 및 정답 goal state와의 대조, 제조 도메인의 맵 기반 경로·대기·시간 조건 대조
    - 모델 기반: 생성 모델과 별도의 모델(gpt-6-luna)에 의한 체크리스트 채점과 정보 충분성(information sufficiency) 판정

프롬프트 원문과 도메인별 설정값은 `pipeline` 브랜치에 있다. 제조 도메인의 gemini-3.8-flash와 qwen3.8-27b-high는 최대 출력 토큰을 32,000으로 설정하였으며(가사·물류 16,000), 그 외 하이퍼파라미터는 모든 도메인에서 동일하다.

## 원천 데이터 및 라이선스

| 원천 | 라이선스 | 사용 범위 |
| --- | --- | --- |
| [RoboCasa365](https://github.com/robocasa/robocasa) | MIT | 가사 장면, 원천 작업, 썸네일 렌더링 |
| NVIDIA PhysicalAI-SimReady-Warehouse-01 | CC BY 4.0 | 물류 작업 구역, 썸네일(공식 렌더) |
| NVIDIA SimReady assets | CC BY 4.0 | 제조 물품 |
| RoboCup Logistics League 2025 rulebook | - | 제조 공장 배치 규칙 |
| FloorLayout.jl FLP instances | MIT | 제조 작업장 수와 물동량 |

본 데이터셋의 라이선스는 미정이다.

## 부록: 작업 유형별 샘플 수

<details><summary>가사 (household): 25개 유형</summary>

| 작업 유형 | gpt-6-sol | gemini-3.8-flash | qwen3.8-27b-high | 합계 |
| --- | ---: | ---: | ---: | ---: |
| `relocate` | 1,523 | 560 | 557 | 2,640 |
| `relocate_two` | 1,172 | 561 | 543 | 2,276 |
| `sort_two` | 1,079 | 551 | 523 | 2,153 |
| `wash` | 901 | 540 | 545 | 1,986 |
| `next_to` | 698 | 561 | 548 | 1,807 |
| `by_size` | 596 | 610 | 591 | 1,797 |
| `fetch_and_close` | 684 | 531 | 520 | 1,735 |
| `store_and_close` | 686 | 552 | 491 | 1,729 |
| `load_and_move` | 688 | 541 | 489 | 1,718 |
| `sort_groups` | 771 | 150 | 152 | 1,073 |
| `either` | 518 | 7 | 0 | 525 |
| `clear_spot` | 222 | 146 | 155 | 523 |
| `gather` | 189 | 151 | 160 | 500 |
| `pack_items` | 171 | 153 | 161 | 485 |
| `set_state` | 475 | 2 | 2 | 479 |
| `heat` | 448 | 5 | 2 | 455 |
| `take_out` | 373 | 42 | 39 | 454 |
| `move_all` | 76 | 152 | 153 | 381 |
| `cook` | 256 | 3 | 3 | 262 |
| `store_many_and_close` | 116 | 0 | 0 | 116 |
| `pour_fill_mix` | 93 | 0 | 0 | 93 |
| `portion_and_match` | 59 | 0 | 0 | 59 |
| `stir_press_move` | 48 | 0 | 0 | 48 |
| `insert_and_turn_on` | 44 | 0 | 0 | 44 |
| `stack_on` | 13 | 0 | 0 | 13 |

</details>

<details><summary>물류 (logistics): 24개 유형</summary>

| 작업 유형 | gpt-6-sol | gemini-3.8-flash | qwen3.8-27b-high | 합계 |
| --- | ---: | ---: | ---: | ---: |
| `sort_groups` | 4,927 | 2,264 | 2,047 | 9,238 |
| `gather` | 2,679 | 1,277 | 1,138 | 5,094 |
| `sort_two` | 751 | 360 | 306 | 1,417 |
| `relocate_two` | 435 | 207 | 182 | 824 |
| `move_one` | 363 | 168 | 167 | 698 |
| `next_to` | 255 | 131 | 123 | 509 |
| `move_all` | 229 | 109 | 105 | 443 |
| `unstack_top` | 173 | 87 | 71 | 331 |
| `move_then_stack` | 157 | 78 | 56 | 291 |
| `by_size` | 127 | 63 | 56 | 246 |
| `move_from_place` | 119 | 61 | 58 | 238 |
| `empty_and_move` | 106 | 54 | 47 | 207 |
| `relocate` | 91 | 48 | 42 | 181 |
| `stack_on` | 90 | 45 | 39 | 174 |
| `unstack_all` | 69 | 32 | 24 | 125 |
| `restack` | 70 | 28 | 22 | 120 |
| `pack_and_move` | 58 | 28 | 28 | 114 |
| `swap_places` | 56 | 22 | 25 | 103 |
| `clear_spot` | 51 | 23 | 20 | 94 |
| `load_and_move` | 30 | 15 | 15 | 60 |
| `uncover` | 27 | 13 | 12 | 52 |
| `empty_container` | 20 | 11 | 9 | 40 |
| `pack_items` | 9 | 4 | 3 | 16 |
| `stack_pair` | 7 | 3 | 3 | 13 |

</details>

<details><summary>제조 (manufacturing): 13개 유형</summary>

| 작업 유형 | gpt-6-sol | gemini-3.8-flash | qwen3.8-27b-high | 합계 |
| --- | ---: | ---: | ---: | ---: |
| `deliver_two` | 1,300 | 663 | 657 | 2,620 |
| `store` | 1,123 | 576 | 253 | 1,952 |
| `deliver` | 1,117 | 559 | 156 | 1,832 |
| `retrieve` | 1,107 | 544 | 158 | 1,809 |
| `ship_window` | 1,067 | 567 | 173 | 1,807 |
| `chain` | 904 | 472 | 164 | 1,540 |
| `clear_output` | 730 | 379 | 169 | 1,278 |
| `tool_fetch` | 582 | 285 | 78 | 945 |
| `deliver_to_stopped` | 508 | 268 | 103 | 879 |
| `deliver_any_running` | 431 | 237 | 71 | 739 |
| `deliver_from_here` | 394 | 200 | 120 | 714 |
| `collect_waste` | 140 | 73 | 16 | 229 |
| `go_to` | 50 | 27 | 11 | 88 |

</details>

