# TSD 샘플 패키지 ({date})

## 구성

- `samples.jsonl`: 정식 편입 샘플 (시나리오당 1건, 검수 통과)
- `alternates.jsonl`: 동일 시나리오의 차순위 생성본 (검수는 통과했으나 best-of-k에서 탈락)
- `rejected.jsonl`: 검수 미달 샘플 (negative 보존, 삭제하지 않음)
- `scenarios.jsonl`: 원 시나리오 (씬 상태 + 출처 provenance)
- `stats.json`: 통과율·항목별 평균·사용량 집계
- 채택 샘플 전건은 이 문서 아래에 사람이 읽을 수 있는 형태로 실려 있습니다

{report_line}## 생성 설정

- 시나리오 소스: {adapter} (Tier A, 메타데이터 전용 추출)
- 생성 모델: {generators} (시나리오당 {k}건 생성, 선별 방식 {selection})
- 재작성: {rewriter} / 체크리스트 judge: 교차 검수({judges}) / probe judge: {probe_judge}
- 프롬프트 버전: {prompt_versions} (원문 템플릿은 동봉된 prompts/ 폴더)
- 유저 입력 구체성: {specificity}

