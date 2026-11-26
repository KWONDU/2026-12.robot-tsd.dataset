"""LLM 없이 CPU만 쓰는 일(규칙으로 장면 만들기, 작업 합성)을 여러 프로세스에 나눠 돌리고 넣은 순서대로 내놓는다.

일 하나하나가 서로 독립이고 각자 seed를 가진 경우에만 쓴다. 그래야 작업자 수와 상관없이 결과가 같다.
fork로 띄우므로 make는 피클로 넘기지 않는다 (클로저도 된다). 결과만 피클로 돌아온다.
필요한 만큼 받고 그만 읽으면 아직 시작하지 않은 일은 취소한다.
"""

from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from typing import Any

AHEAD = 4  # 작업자 하나당 미리 맡겨 두는 일 수. 결과가 쌓여 메모리를 다 쓰지 않게 하는 상한
_JOB: dict[str, Callable[[Any], Any]] = {}


def _start(make: Callable[[Any], Any]) -> None:
    _JOB["make"] = make


def _one(item: Any) -> Any:
    return _JOB["make"](item)


def ordered_map(make: Callable[[Any], Any], items: Iterable, workers: int = 1) -> Iterator[Any]:
    if workers <= 1:
        yield from map(make, items)
        return
    pool = ProcessPoolExecutor(
        workers, mp_context=get_context("fork"), initializer=_start, initargs=(make,)
    )
    pending: deque = deque()
    try:
        for item in items:
            pending.append(pool.submit(_one, item))
            if len(pending) >= workers * AHEAD:
                yield pending.popleft().result()
        while pending:
            yield pending.popleft().result()
    finally:
        for future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
