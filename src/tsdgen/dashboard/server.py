"""대시보드 HTTP 서버 (표준 라이브러리만 사용). 조회 전용이다.

규칙: config의 주소(기본 127.0.0.1)에만 묶는다. GET만 받는다(다른 메서드는 501). 파일을 쓰지 않는다.
정적 파일은 허용 목록의 세 개만 준다. 실행 이름은 규칙(data.RUN_NAME)과 실제 폴더 존재를 둘 다 확인한다.
파이프라인 실행과 LLM 분석은 대시보드가 하지 않는다. 그런 일은 채팅(에이전트)으로 시키는 것이 이 프로젝트의
운용 방식이고, 화면은 산출물을 읽기만 한다.

경로 하나가 ROUTES에 등록된 핸들러 하나다. 핸들러는 (요청 컨텍스트) -> 응답 dict를 돌려주고,
직렬화·오류 변환은 여기 한 곳에서 한다.
"""

import json
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from tsdgen.config import Settings
from tsdgen.dashboard.data import RunData, default_run, list_runs
from tsdgen.dashboard.dataset import DATASET_ID, dataset_rows, overview_blocks, warm
from tsdgen.dashboard.progress import run_progress
from tsdgen.dashboard.samples import sample_detail, sample_rows
from tsdgen.dashboard.tasks import task_detail, task_rows
from tsdgen.dashboard.views import VIEWS, catalog, render
from tsdgen.log import get_logger
from tsdgen.registry import Registry
from tsdgen.resources import load_resources

log = get_logger("tsdgen.dashboard")

ROUTES = Registry("dashboard route")
"""'/api/경/로/{이름}' -> (RequestContext) -> 응답 dict. 등록이 곧 API 목록이다 (전부 GET)."""

ASSET_DIR = Path(__file__).parent / "assets"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
FILE_KEY = "__file__"  # 핸들러가 JSON 대신 파일을 내려 달라고 할 때 쓰는 키
# 화면이 쓰는 표시명 묶음 (공용 사전의 키). 화면에 따로 적지 않고 여기서 내려 준다.
LABEL_KEYS = {"checklist": "checklist_items", "status": "status_ko", "relation": "relation_ko",
              "run_status": "run_status_ko", "run_status_hint": "run_status_hint_ko",
              "check": "verify_check_ko", "stage": "stage_ko", "role": "role_ko",
              "goal_status": "goal_status_ko", "schema_type": "schema_type_ko",
              "family": "task_family_ko", "target_role": "target_role_ko", "slot": "prompt_slot_ko",
              "constraint": "constraint_type_ko", "domain": "domain_ko"}


class NotFound(ValueError):
    """없는 실행·샘플·화면. 404로 돌려준다."""


@dataclass
class RequestContext:
    settings: Settings
    params: dict[str, str]


def _run(ctx: RequestContext) -> RunData:
    data = RunData(ctx.settings, ctx.params["run"])
    if not data.exists():
        raise NotFound(f"unknown run '{ctx.params['run']}'")
    return data


def _sample(data: RunData, sample_id: str) -> dict:
    sample = data.sample_by_id(sample_id)
    if sample is None:
        raise NotFound(f"unknown sample '{sample_id}'")
    return sample


def _with_progress(settings: Settings, run: dict) -> dict:
    """사이드바 실행 목록에 보일 진행률과 API 지출을 붙인다 (모든 실행)."""
    progress = run_progress(RunData(settings, run["run_id"]))
    return {**run, "percent": progress["percent"], "spent_usd": progress["cost"]["spent_usd"],
            "elapsed_s": progress["elapsed_s"], "eta_s": progress["eta_s"]}


_META_LOCK = threading.Lock()
_META: dict[str, Any] = {}  # 마지막으로 만든 실행 목록과 그 시각


def _runs_listing(settings: Settings) -> list[dict]:
    """실행 목록(진행률 포함). 화면이 15초마다 묻는데 만드는 데 그보다 오래 걸리면 요청이 쌓여 서버가 멈췄다
    (실행 중 파일이 수백 MB). 그래서 한 번에 하나만 만들고, 기다린 요청은 그사이 만든 결과를 쓴다."""
    asked = time.monotonic()
    with _META_LOCK:
        if _META.get("at", -1.0) >= asked:
            return _META["runs"]
        runs = [_with_progress(settings, run) for run in list_runs(settings)]
        _META.update(runs=runs, at=time.monotonic())
        return runs


@ROUTES.register("/api/meta")
def _meta(ctx: RequestContext) -> dict:
    lexicon = load_resources(ctx.settings.path("resources"))
    return {
        "runs": _runs_listing(ctx.settings),
        "default_run": ctx.settings.dashboard.run_id or default_run(ctx.settings),
        "views": catalog(),
        "labels": {name: lexicon[key] for name, key in LABEL_KEYS.items()},
        "score_max": ctx.settings.verify.score_max,
        "poll_seconds": ctx.settings.dashboard.poll_seconds,
        "dataset": {"id": DATASET_ID, "domains": list(ctx.settings.dashboard.dataset)}
        if ctx.settings.dashboard.dataset else None,
    }


@ROUTES.register("/api/dataset/overview")
def _dataset_overview(ctx: RequestContext) -> dict:
    return {"blocks": overview_blocks(ctx.settings)}


@ROUTES.register("/api/dataset/tasks")
def _dataset_tasks(ctx: RequestContext) -> dict:
    return {"tasks": dataset_rows(ctx.settings)}


@ROUTES.register("/api/runs/{run}/views/{view}")
def _view(ctx: RequestContext) -> dict:
    data = _run(ctx)
    if ctx.params["view"] not in VIEWS.names():
        raise NotFound(f"unknown view '{ctx.params['view']}'")
    return {"blocks": render(data, ctx.params["view"])}


@ROUTES.register("/api/runs/{run}/progress")
def _progress(ctx: RequestContext) -> dict:
    return {"progress": run_progress(_run(ctx))}


@ROUTES.register("/api/runs/{run}/samples")
def _samples(ctx: RequestContext) -> dict:
    return {"samples": sample_rows(_run(ctx))}


@ROUTES.register("/api/runs/{run}/samples/{sample_id}")
def _sample_detail(ctx: RequestContext) -> dict:
    data = _run(ctx)
    return {"sample": sample_detail(data, _sample(data, ctx.params["sample_id"]))}


@ROUTES.register("/api/runs/{run}/tasks")
def _tasks(ctx: RequestContext) -> dict:
    return {"tasks": task_rows(_run(ctx))}


@ROUTES.register("/api/runs/{run}/tasks/{scenario_id}")
def _task_detail(ctx: RequestContext) -> dict:
    detail = task_detail(_run(ctx), ctx.params["scenario_id"])
    if detail is None:
        raise NotFound(f"unknown scenario '{ctx.params['scenario_id']}'")
    return {"task": detail}


IMAGE_TYPES = {".jpg": "image/jpeg", ".png": "image/png"}  # 장면 이미지: 영상 캡처 jpg, 썸네일 png


@ROUTES.register("/api/runs/{run}/samples/{sample_id}/frame")
def _sample_frame(ctx: RequestContext) -> dict:
    data = _run(ctx)
    path = data.frame_path(_sample(data, ctx.params["sample_id"]))
    if path is None:
        raise NotFound("시작 장면 캡처를 만들 수 없다 (영상 미수신이거나 ffmpeg 없음)")
    return {FILE_KEY: str(path), "content_type": IMAGE_TYPES[path.suffix.lower()]}


def _compile_routes() -> list[tuple[re.Pattern, Any]]:
    compiled = []
    for path in ROUTES.names():
        pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path)
        compiled.append((re.compile(f"^{pattern}$"), ROUTES.get(path)))
    return compiled


class Handler(BaseHTTPRequestHandler):
    settings: Settings
    routes: list[tuple[re.Pattern, Any]]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 표준 시그니처
        log.info(f"{self.address_string()} {format % args}")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # css와 js가 서로 다른 버전으로 캐시되면 화면이 깨진다. 로컬 도구라 캐시 이득도 없다.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _dispatch_api(self, path: str) -> None:
        for pattern, handler in self.routes:
            match = pattern.match(path)
            if not match:
                continue
            params = {k: urllib.parse.unquote(v) for k, v in match.groupdict().items()}
            try:
                result = handler(RequestContext(self.settings, params))
            except NotFound as e:
                self._send_json({"error": str(e)}, 404)
            except Exception as e:  # 응답은 항상 JSON으로 돌려준다 (서버가 죽지 않게)
                log.exception(f"GET {path} failed")
                self._send_json({"error": str(e)[:500]}, 500)
            else:
                if FILE_KEY in result:
                    self._send(200, Path(result[FILE_KEY]).read_bytes(), result["content_type"])
                else:
                    self._send_json(result)
            return
        self._send_json({"error": f"unknown endpoint {path}"}, 404)

    def do_GET(self) -> None:  # noqa: N802 - http.server 규약
        path = urllib.parse.urlparse(self.path).path
        if path in STATIC_FILES:
            file_name, content_type = STATIC_FILES[path]
            self._send(200, (ASSET_DIR / file_name).read_bytes(), content_type)
        elif path.startswith("/api/"):
            self._dispatch_api(path)
        else:
            self._send_json({"error": "not found"}, 404)

    def _read_only(self) -> None:
        self.send_error(501, "dashboard is read-only (GET only)")

    do_POST = do_PUT = do_DELETE = do_PATCH = _read_only  # noqa: N815 - http.server 규약


def make_server(settings: Settings, port: int | None = None) -> ThreadingHTTPServer:
    """서버를 만든다. port=0이면 테스트용 임시 포트를 받는다."""
    Handler.settings = settings
    Handler.routes = _compile_routes()
    address = (settings.dashboard.host, settings.dashboard.port if port is None else port)
    return ThreadingHTTPServer(address, Handler)


def serve(settings: Settings) -> None:
    server = make_server(settings)
    host, port = server.server_address[:2]
    log.info(f"dashboard: http://{host}:{port}/ (Ctrl+C to stop)")
    warm(settings)  # 최종 데이터셋 색인을 미리 만든다
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("dashboard stopped")
    finally:
        server.server_close()
