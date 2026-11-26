"""공용 유틸: JSONL 입출력, 해시, 시드 파생."""

import hashlib
import json
from collections.abc import Iterable, Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def append_jsonl(path: Path, records: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return n


def write_jsonl(path: Path, records: Iterable[dict]) -> int:
    """파일을 새로 쓴다. 재계산 결과처럼 이전 내용을 이어 붙이면 안 되는 출력용이다."""
    path.unlink(missing_ok=True)
    return append_jsonl(path, records)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def stable_hash(obj: Any, length: int = 16) -> str:
    """JSON 직렬화 기반 결정론 해시 (캐시 키·중립 id용)."""
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:length]


def derive_seed(base_seed: int, *parts: Any) -> int:
    """전역 seed에서 레코드별 seed를 결정론적으로 파생한다."""
    digest = stable_hash([base_seed, *parts], length=8)
    return int(digest, 16)


def existing_keys(path: Path, key_field: str) -> set[str]:
    """append-only 출력 파일에서 이미 처리된 키 집합을 읽어 재개(resume)에 쓴다."""
    return {rec[key_field] for rec in read_jsonl(path) if key_field in rec}


@lru_cache(maxsize=4)
def source_digest(src_root: Path) -> str:
    """git 저장소가 아니어도 재현성 기록이 가능하도록 소스 트리 해시를 만든다.

    실행 중에는 소스가 바뀌지 않으므로 한 번만 계산한다 (샘플마다 트리를 다시 읽지 않는다).
    """
    hasher = hashlib.sha256()
    for p in sorted(src_root.rglob("*.py")):
        hasher.update(p.relative_to(src_root).as_posix().encode())
        hasher.update(p.read_bytes())
    return hasher.hexdigest()[:12]
