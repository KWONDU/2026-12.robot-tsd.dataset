"""레코드 단위 병렬 스테이지 실행기.

contextualize, generate, verify가 똑같이 반복하던 것을 한 곳에 모았다. 완료
레코드 건너뛰기, 스레드풀, append 저장, 상태 기록, 실패 격리가 여기 있다.
실패를 어떻게 다룰지는 스테이지마다 다르므로 on_error가 정한다. None을
돌려주면 그 레코드는 버려지고, 레코드를 돌려주면 그대로 저장된다.
"""

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tsdgen.config import Settings
from tsdgen.log import get_logger
from tsdgen.state import StateStore
from tsdgen.util import append_jsonl, existing_keys

log = get_logger("tsdgen.parallel")


@dataclass
class Job:
    """작업 하나. key는 재개 판정에, record_id는 상태 기록에 쓴다."""

    key: str
    record_id: str
    payload: Any


@dataclass
class ParallelResult:
    written: int
    failed: int
    output: Path

    def as_dict(self, failure_field: str = "failed") -> dict:
        return {"written": self.written, failure_field: self.failed, "output": str(self.output)}


def run_jobs(
    settings: Settings,
    *,
    stage: str,
    jobs: Iterable[Job],
    work: Callable[[Job], dict],
    out_path: Path,
    key_field: str,
    workers: int,
    done_status: str,
    on_error: Callable[[Job, Exception], dict | None] | None = None,
) -> ParallelResult:
    done = existing_keys(out_path, key_field)  # 한 번만 읽는다 (작업마다 읽으면 재개할 때 출력 크기 × 작업 수가 된다)
    todo = [job for job in jobs if job.key not in done]
    store = StateStore(settings.run_dir())
    written = failed = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(work, job): job for job in todo}
            for future, job in futures.items():
                try:
                    append_jsonl(out_path, [future.result()])
                    store.set(job.record_id, done_status)
                    written += 1
                except Exception as e:  # 한 레코드 실패가 배치를 멈추지 않게 한다
                    failed += 1
                    store.set(job.record_id, f"failed:{stage}", str(e)[:500])
                    log.warning(f"fail {job.key}: {e}")
                    fallback = on_error(job, e) if on_error else None
                    if fallback is not None:
                        append_jsonl(out_path, [fallback])
    finally:
        store.close()
    return ParallelResult(written=written, failed=failed, output=out_path)


def max_concurrency(settings: Settings, model_keys: Iterable[str]) -> int:
    """이 스테이지가 쓰는 모델들이 허용하는 동시 실행 수."""
    limits = [settings.model_cfg(key).concurrency for key in model_keys]
    return max(limits) if limits else 1
