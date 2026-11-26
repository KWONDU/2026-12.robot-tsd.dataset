"""파이프라인 스테이지 등록 모듈.

각 스테이지는 `stage(settings) -> dict` 서명의 순수 실행 단위이며,
입력 JSONL → 출력 JSONL 변환으로 독립 실행·재개가 가능하다.
"""

from tsdgen.stages import (  # noqa: F401
    audit_stage,
    contextualize,
    dashboard_stage,
    estimate,
    export_stage,
    fetch_videos_stage,
    generate,
    ingest,
    prefetch_stage,
    prepare_stage,
    run_stage,
    sample_stage,
    schemas_stage,
    select_stage,
    verify_stage,
)
