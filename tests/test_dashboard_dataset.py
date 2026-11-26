"""대시보드 최종 데이터셋: config(dashboard.dataset)가 정한 실행들의 검수 통과 샘플만, 같은 장면은 뒤 실행 것으로."""

import threading

import httpx
import pytest

from tsdgen.dashboard.data import RunData
from tsdgen.dashboard.dataset import DATASET_ID, dataset_rows, overview_blocks
from tsdgen.dashboard.records import status_of
from tsdgen.dashboard.server import make_server
from tsdgen.registry import STAGES


@pytest.fixture()
def finished(settings):
    settings.dashboard.model = "mock"
    STAGES.get("run")(settings)
    settings.dashboard.dataset = {"household": [settings.project.run_id]}
    return settings


def test_only_accepted_samples_and_later_run_wins(finished):
    run = finished.project.run_id
    accepted = [s for s in RunData(finished, run).samples() if status_of(s) == "accepted"]
    rows = dataset_rows(finished)
    assert len(rows) == len(accepted) and {r["run"] for r in rows} == {run}
    finished.dashboard.dataset = {"household": [run, run]}  # 같은 실행을 두 번 적어도 장면마다 하나
    assert len(dataset_rows(finished)) == len(accepted)


def test_overview_counts_add_up(finished):
    blocks = overview_blocks(finished)
    total = next(b for b in blocks if b["kind"] == "stats")["data"][0]["value"]
    agent_table = next(b for b in blocks if b["label"] == "도메인 × 에이전트")["data"]
    assert agent_table["rows"][-1][-1] == total == len(dataset_rows(finished))


def test_dataset_api_and_meta(finished):
    server = make_server(finished, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=30)
    try:
        assert client.get("/api/meta").json()["dataset"]["id"] == DATASET_ID
        assert client.get("/api/dataset/overview").status_code == 200
        tasks = client.get("/api/dataset/tasks").json()["tasks"]
        assert all({"run", "domain", "scenario_id", "sample_id", "model", "task"} <= set(t) for t in tasks)
    finally:
        server.shutdown()
        server.server_close()
