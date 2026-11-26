"""실행(run) 폴더에서 대시보드가 쓸 데이터를 읽는다. 읽기만 하고 아무것도 쓰지 않는다.

원본은 파이프라인이 남긴 파일들이다. 파일이 없으면 그 부분만 빈 값으로 둔다(실행 중간에도 열 수 있어야
하므로). 진행 상태는 프로세스를 조회하지 않고 파일로만 판단한다: manifest가 가장 최근이면 완료, 산출물이
최근에 바뀌었으면 진행 중, 오래 안 바뀌었으면 미완료(멈췄거나 일부만 돌림)다.
"""

import json
import re
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from tsdgen.adapters.base import make_adapter
from tsdgen.config import Settings
from tsdgen.resources import load_resources
from tsdgen.runs import (
    AUDIT,
    CONFIG_SNAPSHOT_DIR,
    EXCLUDED,
    MANIFEST,
    RESOLVED_CONFIG,
    USAGE,
    stage_file,
)
from tsdgen.util import read_jsonl

RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# 진행 표에 보이는 스테이지 산출물 순서 (파이프라인 순서 중 레코드를 남기는 것)
PROGRESS_STAGES = ("sample", "contextualize", "generate", "verify", "select")
FRAME_TIMEOUT_S = 60


# 파일 경로 -> ((수정 시각, 크기), 읽은 내용). 파일이 바뀌면 그 파일만 다시 읽는다
_ROWS: dict[Path, tuple[tuple[int, int], list[dict]]] = {}
_COUNTS: dict[Path, tuple[tuple[int, int], int]] = {}
_LOCKS: dict[Path, threading.Lock] = defaultdict(threading.Lock)  # 파일마다 읽기 차례
_STATUS: dict[Path, tuple[tuple[int, int], dict[str, int]]] = {}
STATUS_FIELD = re.compile(rb'"status": "([a-z_]+)"')  # 샘플 meta의 판정 (기록마다 마지막 것)


def _stamp(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


class RunData:
    """실행 하나의 산출물 묶음."""

    def __init__(self, settings: Settings, run_id: str):
        self.settings = settings
        self.run_id = run_id
        self.run_dir = settings.path("runs") / run_id

    def exists(self) -> bool:
        return bool(RUN_NAME.fullmatch(self.run_id)) and self.run_dir.is_dir()

    def rows(self, stage: str) -> list[dict]:
        """스테이지 산출물 전체. 파일이 바뀌지 않았으면 앞서 읽은 것을 다시 쓴다(만 건짜리 실행은 한 번 읽는
        데 수십 초가 걸린다). 돌려준 목록과 그 안의 기록은 읽기만 한다."""
        path = self.run_dir / stage_file(stage)
        stamp = _stamp(path)
        if stamp is None:
            return []
        # 같은 파일을 여러 요청이 한꺼번에 처음부터 읽지 않게 한다 (재시작 직후 화면이 15초마다 묻는 요청이 겹쳐
        # 수백 MB 파일을 동시에 읽다가 서로 막혔다). 먼저 온 요청이 읽고 나머지는 그 결과를 쓴다
        with _LOCKS[path]:
            cached = _ROWS.get(path)
            if cached is None or cached[0] != stamp:
                cached = _ROWS[path] = (stamp, list(read_jsonl(path)))
        return cached[1]

    def count(self, stage: str) -> int:
        """스테이지 산출물의 기록 수. 내용을 풀지 않고 줄만 센다(돌고 있는 실행의 진행 표시용)."""
        path = self.run_dir / stage_file(stage)
        stamp = _stamp(path)
        if stamp is None:
            return 0
        cached = _COUNTS.get(path)
        if cached is None or cached[0] != stamp:
            # 산출물은 뒤에 덧붙기만 하므로 커졌으면 늘어난 부분만 센다 (돌고 있는 실행의 파일은 수백 MB다)
            grown = cached is not None and stamp[1] >= cached[0][1]
            start, base = (cached[0][1], cached[1]) if grown else (0, 0)
            with path.open("rb") as f:
                f.seek(start)
                cached = _COUNTS[path] = (stamp, base + sum(1 for line in f if line.strip()))
        return cached[1]

    def samples(self) -> list[dict]:
        """검수까지 끝난 샘플. 선별이 끝났으면 그 상태(alternate 포함)를 쓴다."""
        return self.rows("select") or self.rows("verify")

    def sample_by_id(self, sample_id: str) -> dict | None:
        return next((s for s in self.samples() if s.get("sample_id") == sample_id), None)

    def scenarios(self) -> list[dict]:
        return self.rows("sample")

    def scenario_of(self, sample: dict) -> dict | None:
        return next((sc for sc in self.scenarios()
                     if sc.get("scenario_id") == sample.get("scenario_id")), None)

    def contextualized(self) -> list[dict]:
        return self.rows("contextualize")

    def excluded(self) -> list[dict]:
        return list(read_jsonl(self.run_dir / EXCLUDED))

    def usage(self) -> list[dict]:
        return list(read_jsonl(self.run_dir / USAGE))

    def lexicon(self) -> dict:
        """표시용 한국어 명칭 (체크리스트 항목·판정 상태 등). 공용 사전이 단일 출처다."""
        return load_resources(self.settings.path("resources"))

    def json_file(self, name: str) -> dict[str, Any]:
        path = self.run_dir / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def config_snapshot(self) -> dict[str, Any]:
        """이 실행이 실제로 쓴 설정. manifest(끝난 실행)가 우선이고, 없으면 시작할 때 남긴 해석본을 읽는다."""
        snapshot = self.json_file(MANIFEST).get("config_snapshot")
        if snapshot:
            return snapshot
        resolved = self.run_dir / CONFIG_SNAPSHOT_DIR / RESOLVED_CONFIG
        return yaml.safe_load(resolved.read_text(encoding="utf-8")) if resolved.exists() else {}

    def verify_checks(self) -> list[str]:
        """이 실행의 검수 항목 순서. 사본이 없으면 지금 설정을 쓴다."""
        checks = (self.config_snapshot().get("verify") or {}).get("checks")
        return list(checks) if checks else list(self.settings.verify.checks)

    def snapshot_files(self, subdir: str) -> list[Path]:
        """실행에 쓴 설정·프롬프트 사본 (run 스테이지가 남긴다). 없으면 빈 목록."""
        return sorted(p for p in (self.run_dir / subdir).glob("*") if p.is_file())

    def last_modified(self) -> float:
        """실행이 남긴 파일 가운데 가장 나중 시각. 점검표(audit.json)는 실행이 끝난 뒤에 따로 내는 것이라 뺀다."""
        stamps = []
        for path in self.run_dir.glob("*"):
            try:  # 실행 중에는 임시 파일(sqlite 저널 등)이 읽는 사이에 사라진다
                if path.is_file() and path.name != AUDIT:
                    stamps.append(path.stat().st_mtime)
            except FileNotFoundError:
                continue
        return max(stamps, default=0.0)

    def status(self) -> str:
        """done / running / incomplete / empty. 파일 시각만 본다."""
        latest = self.last_modified()
        if not latest:
            return "empty"
        manifest = self.run_dir / MANIFEST
        if manifest.exists() and manifest.stat().st_mtime >= latest:
            return "done"
        stale_after = self.settings.dashboard.stale_after_min * 60
        return "running" if time.time() - latest < stale_after else "incomplete"

    def status_counts(self) -> dict[str, int]:
        """최종 샘플 파일의 판정별 수. 실행 목록은 모든 실행에 대해 15초마다 묻는데 샘플을 다 풀면(실행 하나에
        수백 MB) 재시작 직후 몇 분이 걸려, 기록마다 하나뿐인 판정 칸만 글자로 센다. 파일이 바뀌지 않았으면 다시 쓴다."""
        path = next((p for p in (self.run_dir / stage_file(st) for st in ("select", "verify"))
                     if p.exists() and p.stat().st_size), None)
        if path is None:
            return {}
        stamp = _stamp(path)
        with _LOCKS[path]:
            cached = _STATUS.get(path)
            if cached is None or cached[0] != stamp:
                counts: dict[str, int] = {}
                with path.open("rb") as f:
                    for line in f:
                        found = STATUS_FIELD.findall(line)
                        if found:
                            key = found[-1].decode()
                            counts[key] = counts.get(key, 0) + 1
                cached = _STATUS[path] = (stamp, counts)
        return cached[1]

    def summary(self) -> dict[str, Any]:
        statuses = self.status_counts()
        return {
            "run_id": self.run_id,
            "status": self.status(),
            "updated": self.last_modified(),
            "n_samples": sum(statuses.values()),
            "n_scenarios": self.count("sample"),
            "statuses": statuses,
        }

    def video_path(self, sample: dict) -> Path | None:
        """샘플 시나리오의 시연 영상 파일. 위치 규약은 소스 어댑터가 안다."""
        scenario = self.scenario_of(sample)
        return None if scenario is None else make_adapter(self.settings).video_file(scenario)

    def original_instruction(self, sample: dict) -> str | None:
        """재작성 전 원 지시문. v1.1부터 샘플에 있고, 그 전 산출물은 시나리오 기록에서 찾는다."""
        stored = (sample.get("input") or {}).get("original_instruction_en")
        if stored:
            return stored
        scenario = self.scenario_of(sample) or {}
        return (scenario.get("source") or {}).get("original_instruction_en")

    def thumbnail_path(self, sample: dict) -> Path | None:
        """장면 썸네일(가사: 시뮬레이터 첫 상태 렌더, 물류: 작업 구역 공식 렌더, 제조: 공장 지도). 파일 이름은
        장면 id의 /를 _로 바꾼 것이고 어댑터별 하위 폴더에 있다. 없으면 None."""
        scenario = self.scenario_of(sample) or {}  # 샘플 입력의 장면 id는 가린 값이라 원 시나리오의 것을 쓴다
        scene_id = (scenario.get("scene_state") or {}).get("scene_id")
        if not scene_id:
            return None
        name = f"{scene_id.replace('/', '_')}.png"
        return next(self.settings.path("thumbnails").glob(f"*/{name}"), None)

    def frame_path(self, sample: dict) -> Path | None:
        """장면 이미지. 장면 썸네일이 있으면 그것을, 없으면 시연 영상의 첫 프레임 캡처(입력 시점의 모습)를 쓴다.
        캡처는 영상 옆에 .jpg로 캐시하고 없으면 ffmpeg로 한 번만 만든다. ffmpeg이 없거나 영상이 없으면 None."""
        thumbnail = self.thumbnail_path(sample)
        if thumbnail is not None:
            return thumbnail
        video = self.video_path(sample)
        if video is None:
            return None
        frame = video.with_suffix(".jpg")
        ffmpeg = shutil.which("ffmpeg")
        if not frame.exists() and ffmpeg:
            subprocess.run(
                [ffmpeg, "-y", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(frame)],
                capture_output=True, timeout=FRAME_TIMEOUT_S,
            )
        return frame if frame.exists() else None


def list_runs(settings: Settings) -> list[dict]:
    """열람 가능한 실행 목록. 산출물이 하나라도 있는 폴더만, 샘플이 있는 실행을 위로·최근 순."""
    runs_dir = settings.path("runs")
    if not runs_dir.is_dir():
        return []
    summaries = []
    for path in sorted(runs_dir.iterdir()):
        data = RunData(settings, path.name)
        if not data.exists():
            continue
        summary = data.summary()
        if summary["n_samples"] or summary["n_scenarios"]:
            summaries.append(summary)
    return sorted(summaries, key=lambda r: (-(r["n_samples"] > 0), -r["updated"]))


def default_run(settings: Settings) -> str | None:
    """기본으로 보여줄 실행. config의 run_id가 샘플을 가졌으면 그것, 아니면 샘플이 있는 최근 실행."""
    runs = list_runs(settings)
    configured = next((r for r in runs if r["run_id"] == settings.project.run_id), None)
    if configured and configured["n_samples"] > 0:
        return configured["run_id"]
    with_samples = [r for r in runs if r["n_samples"] > 0]
    if with_samples:
        return with_samples[0]["run_id"]
    return runs[0]["run_id"] if runs else None
