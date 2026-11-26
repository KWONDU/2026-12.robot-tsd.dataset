# robot-tsd.dataset

로봇에게 내린 한국어 자연어 지시와 로봇이 인식한 세계 상태를 입력으로, 추론(reasoning)과 작업 상황 설명문(Task Situation Description, TSD)을 출력으로 하는 데이터셋이에요. 가사, 물류, 제조 세 도메인에서 생성 에이전트 셋이 만들고 검수를 통과한 샘플만 담았어요.

- 데이터셋 뷰어: https://kwondu.github.io/robot-tsd.dataset/
- 구축 파이프라인(코드, 프롬프트, 도메인 자원, 대시보드): [`pipeline` 브랜치](https://github.com/KWONDU/robot-tsd.dataset/tree/pipeline)
- 라이선스: 미정 (원천 데이터의 라이선스는 아래 '원천과 출처 표기')

## 구성

| 도메인 | gpt-6-sol | gemini-3.8-flash | qwen3.8-27b-high | 합 |
| --- | --- | --- | --- | --- |
| 가사 (household) | 11,899 | 5,818 | 5,634 | 23,351 |
| 물류 (logistics) | 10,899 | 5,131 | 4,598 | 20,628 |
| 제조 (manufacturing) | 9,453 | 4,850 | 2,129 | 16,432 |
| **합** | 32,251 | 15,799 | 12,361 | **60,411** |

작업 유형은 가사 25, 물류 24, 제조 13으로 모두 62개예요(유형별 수는 맨 아래).

## 폴더 구조

```
<도메인>/<작업 유형>/<샘플 id>/
    sample.json     지시, 세계 상태 글, 추론, 작업 상황 설명문, 에이전트
    scene.json      구조화된 장면 상태 (물체, 설비와 칸, 관계, 로봇, 제조는 지도)
    thumbnail.jpg   장면 썸네일
index.csv           샘플마다 id, domain, task_type, agent, path
```

### sample.json

| 필드 | 내용 |
| --- | --- |
| `id`, `domain`, `task_type`, `agent` | 샘플 id, 도메인, 작업 유형, 생성 에이전트(모델) |
| `source` | 원천 데이터, 장면 id, 원천 과제 이름, 합성 작업인지(`composed`) |
| `input.instruction` | 한국어 자연어 지시 (모델 입력) |
| `input.instruction_en` | 재작성 전 영어 원 지시 |
| `input.world_state` | 모델이 받은 세계 상태 글 (id 없이 이름으로) |
| `input.entity_labels` | 장면의 entity id에서 세계 상태 글의 이름으로 가는 표 |
| `output.reasoning` | 추론 (지시 해석, 씬 접지, 역할 부여, 제약 도출, 목표 상태) |
| `output.task_situation_description` | 작업 상황 설명문 JSON: 작업 목표(요약, 목표 상태), 시간·공간 조건, 대상 물체(필수, 핵심 수단, 보조), 제약 사항 |
| `output.task_situation_description_text` | 같은 설명문을 사람이 읽는 글로 |

### 썸네일

- 가사: RoboCasa365 시뮬레이터가 저장한 장면의 첫 상태를 MuJoCo로 다시 그린 것 (장면 물체들을 로봇 쪽에서 내려다봄)
- 물류: 장면을 이루는 작업 구역 2에서 3개의 NVIDIA SimReady 공식 렌더를 나란히 놓은 것
- 제조: 공장 지도를 위에서 본 그림 (구역별 색의 설비, 통로, 막힌 통로, 한 방향 통로, 출입 제한 구역, 로봇 위치)

## 도메인

- **가사**: RoboCasa365의 주방 장면. RoboCasa365 원 과제와, 장면의 물체·가구로 채운 합성 작업.
- **물류**: NVIDIA SimReady Warehouse의 작업 구역(팔레트, 랙, 카트, 작업대)을 2에서 3개 나란히 놓은 창고 장면. 쌓임, 담김, 같은 물체 여럿을 다루는 합성 작업.
- **제조**: RoboCup Logistics League 2025 규칙과 시설 배치 벤치마크(FloorLayout.jl FLP 인스턴스)로 만든 공장 바닥과 지도. 물건은 NVIDIA SimReady 에셋. 지도 위에서 작업장 사이를 오가는 운반 작업이고, 막힌 통로·출입 제한 구역·한 방향 통로·점검으로 멈춘 설비가 있어요.

## 만든 방법

1. 장면마다 작업을 고르고 영어 지시를 한국어 일상 발화로 다시 써요 (gpt-6-luna). 다시 쓴 지시가 원 작업과 같은 것을 가리키는지 판정해요.
2. 생성 에이전트가 지시와 세계 상태 글을 받아 추론과 작업 상황 설명문을 써요: gpt-6-sol 12K, gemini-3.8-flash 6K, qwen3.8-27b-high(reasoning effort high) 6K씩 도메인마다.
3. 검수: 구조 검사, 장면 접지 검사(장면에 없는 것을 지어내지 않았는지), 목표 상태 규칙 검사와 정답 대조, 제조는 지도로 따진 길·기다림·시간 사실 대조, 그리고 생성과 다른 모델(gpt-6-luna)의 체크리스트 채점과 정보 충분성 판정. 모두 통과한 것만 담았어요.

| 도메인 | 생성 프롬프트 | 재작성 | 체크리스트 |
| --- | --- | --- | --- |
| 가사 | gen_v17 (19개는 gen_v18) | rewrite_v6 | check_v17 (19개는 check_v18) |
| 물류 | gen_v18 | rewrite_v6 | check_v18 |
| 제조 | gen_v19 | rewrite_v7 | check_v20 |

프롬프트 원문과 도메인별로 채운 값, 파이프라인 코드는 `pipeline` 브랜치에 있어요. 제조의 gemini-3.8-flash와 qwen3.8-27b-high는 출력 한도를 32,000 토큰으로 두었고(가사·물류는 16,000), 그 밖의 하이퍼파라미터는 도메인 모두 같아요.

## 원천과 출처 표기

- RoboCasa365 (MIT License, https://github.com/robocasa/robocasa): 가사 장면과 원 과제. 썸네일은 그 장면을 다시 그린 것.
- NVIDIA PhysicalAI-SimReady-Warehouse-01 (CC BY 4.0): 물류 작업 구역과 썸네일(공식 렌더).
- NVIDIA SimReady assets (CC BY 4.0): 제조의 물건.
- RoboCup Logistics League 2025 rulebook: 제조 공장 배치 규칙.
- FloorLayout.jl FLP instances (MIT License): 제조 작업장 수와 물동량.

## 작업 유형 × 에이전트

<details><summary>가사 (household): 25개 유형</summary>

| 유형 | gpt-6-sol | gemini-3.8-flash | qwen3.8-27b-high | 합 |
| --- | --- | --- | --- | --- |
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

| 유형 | gpt-6-sol | gemini-3.8-flash | qwen3.8-27b-high | 합 |
| --- | --- | --- | --- | --- |
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

| 유형 | gpt-6-sol | gemini-3.8-flash | qwen3.8-27b-high | 합 |
| --- | --- | --- | --- | --- |
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

