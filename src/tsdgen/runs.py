"""실행 폴더(runs/<run_id>/) 계약: 무엇이 어떤 이름으로 남는지 한 곳에 둔다.

파이프라인이 쓰고 대시보드·내보내기가 읽는다. 이름을 바꾸면 이미 있는 실행 폴더를 못 읽게 되므로, 바꿀 때는
기존 이름도 읽을 수 있게 하고 docs/DECISIONS.md에 적는다.
"""

STAGE_SUFFIX = ".jsonl"  # 스테이지 출력: <스테이지 이름>.jsonl (append 전용, 레코드 단위 재개의 근거)
EXCLUDED = "sample_excluded.jsonl"  # 선별 필터가 뺀 시나리오와 사유
USAGE = "usage.jsonl"  # LLM 호출마다 한 줄: 모델, 역할, 토큰, 비용, 캐시 여부
ESTIMATE = "estimate.json"  # 실행 전 비용 추정
AUDIT = "audit.json"  # audit 스테이지의 점검표 (필드 채움, 모델 간 일치, 정답 대조)
MANIFEST = "manifest.json"  # run 스테이지가 끝날 때: 소스 hash, 설정, 프롬프트 hash, 스테이지 결과
STATE_DB = "state.sqlite"  # 레코드 단위 상태 기록 (진행 관측용 보조 기록)
CONFIG_SNAPSHOT_DIR = "_config"  # 실행에 쓴 설정 파일 사본
RESOLVED_CONFIG = "resolved.yaml"  # 세 설정 파일을 합쳐 검증한 최종 설정
CONFIG_HASH = "hash.txt"  # 최종 설정의 hash
PROMPT_SNAPSHOT_DIR = "_prompts"  # 실행에 쓴 프롬프트 사본


def stage_file(stage: str) -> str:
    return f"{stage}{STAGE_SUFFIX}"
