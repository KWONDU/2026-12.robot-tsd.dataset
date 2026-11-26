"""대시보드: 조회 전용 서버와 화면(view) 분석.

서버는 임시 포트(0)에 실제로 띄워 HTTP로 검증한다. 파이프라인 실행과 LLM 분석은 대시보드에 없고(채팅으로
요청하는 운용), LLM 분석 라이브러리는 mock 모델로 직접 호출해 프롬프트 조립부터 스키마 검증까지의 배관만
확인한다.
"""

import threading

import httpx
import pytest

from tsdgen.dashboard.data import RunData
from tsdgen.dashboard.server import make_server
from tsdgen.dashboard.views import catalog, render
from tsdgen.registry import STAGES


@pytest.fixture()
def finished_run(settings):
    """mock 파이프라인을 끝까지 돌려 산출물이 있는 실행을 만든다."""
    settings.dashboard.model = "mock"
    STAGES.get("run")(settings)
    return settings


@pytest.fixture()
def client(finished_run):
    server = make_server(finished_run, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield httpx.Client(base_url=base, timeout=30)
    server.shutdown()
    server.server_close()


def test_every_view_renders_without_error_blocks(finished_run):
    data = RunData(finished_run, finished_run.project.run_id)
    for page in catalog():
        blocks = render(data, page["name"])
        errors = [b for b in blocks if b["kind"] == "error"]
        assert not errors, (page["name"], errors)
        assert all({"kind", "label", "data"} <= set(b) for b in blocks), page["name"]


def test_overview_and_goal_views_have_content(finished_run):
    """개요: 흐름도의 최종 판정과 모델별 성적표 (목표 검사 줄은 목표 상태가 있는 실행에서만 보인다)."""
    data = RunData(finished_run, finished_run.project.run_id)
    blocks = render(data, "overview")
    flow = next(b for b in blocks if b["kind"] == "flow")
    assert any(o["label"].startswith("채택") and o["value"] > 0 for o in flow["data"]["outcomes"])
    scorecard = next(b for b in blocks if b["label"] == "생성 모델별 성적표")
    metrics = [row[0] for row in scorecard["data"]["rows"]]
    assert "검수 통과" in metrics and "목표 검사 통과" in metrics


def test_flow_and_funnel_counts_agree(finished_run):
    """흐름도는 파이프라인 순서대로 단계를 잇고, 깔때기 끝에 남은 수가 검수 통과 수와 같다."""
    from tsdgen.dashboard.data import PROGRESS_STAGES

    data = RunData(finished_run, finished_run.project.run_id)
    blocks = {b["kind"]: b for b in render(data, "overview")}
    steps = blocks["flow"]["data"]["steps"]
    assert [s["key"] for s in steps] == list(PROGRESS_STAGES)
    assert all(s["state"] == "done" for s in steps)
    passed = sum(1 for s in data.samples() if (s["meta"].get("verification") or {}).get("final_pass"))
    verify = next(s for s in steps if s["key"] == "verify")
    assert verify["value"] == passed
    funnel = blocks["funnel"]["data"]
    assert funnel["rows"][-1]["remaining"] == passed
    assert [r["key"] for r in funnel["rows"]][: len(data.verify_checks())] == data.verify_checks()
    outcomes = blocks["flow"]["data"]["outcomes"]
    assert sum(o["value"] for o in outcomes) == len(data.samples())


def test_verify_order_comes_from_the_run(finished_run, monkeypatch):
    """검수 순서는 지금 설정이 아니라 실행이 남긴 설정 사본을 따른다 (예전 실행은 항목이 다르다)."""
    data = RunData(finished_run, finished_run.project.run_id)
    recorded = data.verify_checks()
    monkeypatch.setattr(finished_run.verify, "checks", ["schema"])
    assert data.verify_checks() == recorded


def test_run_status_comes_from_files(finished_run):
    """끝난 실행은 manifest가 가장 최근 파일이라 완료로 보인다."""
    assert RunData(finished_run, finished_run.project.run_id).status() == "done"


def test_meta_lists_runs_views_and_labels(client, finished_run):
    from tsdgen.resources import load_resources

    meta = client.get("/api/meta").json()
    assert any(r["run_id"] == finished_run.project.run_id for r in meta["runs"])
    assert [v["name"] for v in meta["views"]][0] == "overview"
    lexicon = load_resources(finished_run.path("resources"))
    assert meta["labels"]["checklist"] == lexicon["checklist_items"]
    assert meta["labels"]["status"] == lexicon["status_ko"]
    assert meta["score_max"] == finished_run.verify.score_max


def test_sample_endpoints(client, finished_run):
    run_id = finished_run.project.run_id
    samples = client.get(f"/api/runs/{run_id}/samples").json()["samples"]
    assert samples and all("sample_id" in s and "status" in s for s in samples)
    detail = client.get(f"/api/runs/{run_id}/samples/{samples[0]['sample_id']}").json()["sample"]
    assert detail["sample_id"] == samples[0]["sample_id"]
    # 목표 상태·검수 결과는 서버가 사람이 읽는 줄로 만들어 붙인다
    assert detail["view"]["goal_cases"] and "condition_lines" in detail["view"]
    assert [c["check"] for c in detail["view"]["checks"]] == finished_run.verify.checks
    # 검수 탭: 체크리스트 항목마다 점수와 채점 기준, 설명문 탭: JSON 나무
    assert detail["view"]["checklist"] and all(i["criteria"] for i in detail["view"]["checklist"])
    assert detail["view"]["tsd_json"]["children"]


def test_unknown_run_view_and_route_are_404(client, finished_run):
    run_id = finished_run.project.run_id
    assert client.get("/api/runs/no_such_run/views/overview").status_code == 404
    assert client.get(f"/api/runs/{run_id}/views/no_such_view").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fconfig/views/overview").status_code == 404
    assert client.get("/api/no/such/route").status_code == 404


def test_llm_analysis_library_via_mock(finished_run):
    """채팅에서 요청받아 돌리는 LLM 분석: 프롬프트 조립 -> 호출 -> 스키마 검증."""
    from tsdgen.dashboard.llm_actions import run_action
    from tsdgen.llm.client import LLMClient

    data = RunData(finished_run, finished_run.project.run_id)
    client = LLMClient(finished_run, finished_run.run_dir())
    sid = data.samples()[0]["sample_id"]
    result = run_action(client, finished_run, data, "sample_diagnosis", sid)
    assert "verdict_opinion" in result and "issues" in result
    advice = run_action(client, finished_run, data, "improvement_advice", None)
    assert "suggestions" in advice
    with pytest.raises(ValueError):
        run_action(client, finished_run, data, "sample_diagnosis", "없는샘플")


def test_dashboard_is_read_only(client):
    """GET 말고는 받지 않는다. 실행·분석 트리거는 채팅으로 한다."""
    assert client.post("/api/meta", json={}).status_code == 501
    assert client.delete("/api/meta").status_code == 501


def test_only_allowlisted_assets_are_served(client):
    assert client.get("/").status_code == 200
    assert "text/html" in client.get("/").headers["content-type"]
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200
    assert client.get("/../config/config.yaml").status_code == 404
    assert client.get("/index.html").status_code == 404


def test_frontend_never_injects_html():
    """데이터는 textContent로만 넣는다 (innerHTML·외부 CDN 금지)."""
    from pathlib import Path

    assets = Path(__file__).resolve().parents[1] / "src" / "tsdgen" / "dashboard" / "assets"
    source = "".join(p.read_text(encoding="utf-8") for p in assets.glob("*"))
    assert ".innerHTML" not in source and ".insertAdjacentHTML" not in source
    assert "https://" not in source


def test_sample_frame_absent_is_reported(client, finished_run):
    run_id = finished_run.project.run_id
    sid = client.get(f"/api/runs/{run_id}/samples").json()["samples"][0]["sample_id"]
    detail = client.get(f"/api/runs/{run_id}/samples/{sid}").json()["sample"]
    assert detail["has_frame"] is False
    res = client.get(f"/api/runs/{run_id}/samples/{sid}/frame")
    assert res.status_code == 404 and "캡처" in res.json()["error"]


def test_progress_reports_stages_calls_and_cost(client, finished_run):
    """진행 카드: 스테이지별 레코드와 기대치, 호출 수, API 비용. 끝난 실행은 100%다."""
    run_id = finished_run.project.run_id
    progress = client.get(f"/api/runs/{run_id}/progress").json()["progress"]
    assert progress["status"] == "done" and progress["percent"] == 100.0
    stages = {s["stage"]: s for s in progress["stages"]}
    n, k = finished_run.project.n_scenarios, finished_run.llm.per_scenario_k
    assert stages["sample"]["expected"] == n and stages["verify"]["expected"] == n * k
    assert stages["verify"]["records"] == n * k and "(verify)" in stages["verify"]["label"]
    assert progress["calls"]["done"] > 0 and progress["cost"]["spent_usd"] == 0.0  # mock은 무료


def test_rejection_stages_use_one_labeling(finished_run):
    """미달 단계 표기는 전부 "한국어 (등록 이름)" 한 가지 모양이다."""
    from tsdgen.dashboard.verdicts import check_label, rejection_reason

    data = RunData(finished_run, finished_run.project.run_id)
    assert check_label(data, "resolvability") == "지시 해소 (resolvability)"
    failed = {"skipped_reason": "structure failed: has_step_list"}
    assert rejection_reason(failed, {}, 2)[0] == "structure"


def _walk(node):
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)


def test_structure_view_documents(finished_run):
    """데이터 구조: 문서마다 JSON 나무가 스키마의 키를 빠짐없이 갖고, 체크리스트 항목 키에는 채점 기준이 붙는다."""
    from tsdgen.models import TaskSituationDescription
    from tsdgen.resources import load_resources

    data = RunData(finished_run, finished_run.project.run_id)
    (block,) = render(data, "structure")
    docs = {d["key"]: d for d in block["data"]["docs"]}
    assert set(docs) == {"sample", "input", "output", "verification"}
    output_keys = [n["key"] for n in docs["output"]["tree"]["children"]]
    assert output_keys == list(TaskSituationDescription.model_json_schema()["properties"])
    assert all(n["desc"] for n in docs["output"]["tree"]["children"])
    # 입력은 골든 예시가 아니라 이 실행 샘플의 기록 그대로다
    sample = next(s for s in data.samples() if s["sample_id"] in docs["input"]["source"])
    inputs = {n["key"]: n for n in docs["input"]["tree"]["children"]}
    assert inputs["user_instruction"]["value"] == sample["input"]["user_instruction"]
    lexicon = load_resources(finished_run.path("resources"))
    items = {n["key"]: n for n in _walk(docs["verification"]["tree"])
             if n["key"] in lexicon["checklist_items"]}
    assert items and all("채점 기준" in n["desc"] for n in items.values())
    assert docs["verification"]["rubric"]["judges"]


def test_analysis_views_do_not_list_samples(finished_run):
    """샘플 하나하나는 샘플 화면에만: 다른 화면의 블록에는 샘플 id가 보이지 않는다 (근거 표는 API에만)."""
    data = RunData(finished_run, finished_run.project.run_id)
    ids = {s["sample_id"] for s in data.samples()}
    for page in ("overview", "run"):
        for block in render(data, page):
            shown = str({k: v for k, v in block.items() if k != "fold"})
            assert not any(i in shown for i in ids), (page, block["label"])


def test_task_endpoints(client, finished_run):
    """태스크: 장면 목록과 장면 상세(모델이 받은 세계 상태, 모델별 샘플, 그 장면으로 채운 생성 프롬프트)."""
    run_id = finished_run.project.run_id
    tasks = client.get(f"/api/runs/{run_id}/tasks").json()["tasks"]
    assert len(tasks) == finished_run.project.n_scenarios
    assert all(t["samples"] and {"status", "model"} <= set(t["samples"][0]) for t in tasks)
    first = tasks[0]["scenario_id"]
    task = client.get(f"/api/runs/{run_id}/tasks/{first}").json()["task"]
    assert task["world_state"] and task["samples"][0]["detail"]["view"]["checks"]
    # 유저 메시지는 모델이 받은 지시와 환경 설명으로 채워지고 자리 표시가 남지 않는다
    prompt = task["prompt"]
    assert task["rewrite"]["instruction"] in prompt["user"] and "<<" not in prompt["user"]
    assert task["world_state"] in prompt["user"]
    assert client.get(f"/api/runs/{run_id}/tasks/no_such_scenario").status_code == 404


def test_task_detail_shows_records_only(finished_run):
    """장면 상세는 파이프라인 기록만 싣는다: 세계 상태는 모델 입력 그대로이고, 화면이 따로 묶은 씬 정보는 없다."""
    from tsdgen import world_state
    from tsdgen.dashboard.tasks import task_detail, task_rows

    data = RunData(finished_run, finished_run.project.run_id)
    task = task_detail(data, task_rows(data)[0]["scenario_id"])
    assert task["world_state"] == world_state.model_input(task["samples"][0]["detail"]["input"])
    assert not {"scene", "env_counts"} & set(task)


def test_prompt_view_reads_run_prompts(finished_run):
    """프롬프트: 파이프라인 순서의 역할마다 실행이 쓴 버전, 섹션 구조, 유저 메시지 자리, 출력 나무."""
    from tsdgen.resources import load_resources

    data = RunData(finished_run, finished_run.project.run_id)
    (block,) = render(data, "prompts")
    cards = block["data"]["roles"]
    lexicon = load_resources(finished_run.path("resources"))
    assert [c["key"] for c in cards] == list(lexicon["role_ko"])
    generate = next(c for c in cards if c["key"] == "generate")
    assert generate["version"] == finished_run.prompts_versions["generate"]
    assert "지침" in [s["title"] for s in generate["system"]]
    user_slots = {slot for s in generate["user"] for slot in s["slots"]}
    assert {"USER_INSTRUCTION", "ENVIRONMENT_CONTEXT"} <= user_slots
    assert generate["output"]["children"]
