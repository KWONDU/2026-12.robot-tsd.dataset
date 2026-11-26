"""레코드 단위 상태 머신 기록 (runs/<run_id>/state.sqlite).

pending → contextualized → generated → verified → selected → exported,
실패 시 failed:<stage>. 스테이지 출력 자체는 append-only JSONL이 원본이고,
이 저장소는 진행 관측과 재개 판단을 위한 보조 기록이다.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from tsdgen.runs import STATE_DB

_SCHEMA = """
CREATE TABLE IF NOT EXISTS record_state (
    record_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    detail TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    ts TEXT NOT NULL
);
"""


class StateStore:
    def __init__(self, run_dir: Path):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(run_dir / STATE_DB)
        self.conn.executescript(_SCHEMA)

    def set(self, record_id: str, status: str, detail: str | None = None) -> None:
        now = datetime.now(UTC).isoformat()
        with self.conn:
            self.conn.execute(
                "INSERT INTO record_state(record_id, status, detail, updated_at) "
                "VALUES(?, ?, ?, ?) ON CONFLICT(record_id) DO UPDATE SET "
                "status=excluded.status, detail=excluded.detail, updated_at=excluded.updated_at",
                (record_id, status, detail, now),
            )
            self.conn.execute(
                "INSERT INTO events(record_id, status, detail, ts) VALUES(?, ?, ?, ?)",
                (record_id, status, detail, now),
            )

    def status_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) FROM record_state GROUP BY status"
        ).fetchall()
        return dict(rows)

    def close(self) -> None:
        self.conn.close()
