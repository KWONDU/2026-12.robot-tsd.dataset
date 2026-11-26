"""원본 tar 추출과 비용 추정의 계약.

fetch는 비디오·궤적을 건너뛰고 메타데이터만 골라내는 것이 전부이므로, 무엇을 남기고
무엇을 버리는지가 계약이다. estimate는 역할 등록이 선언한 호출 횟수를 쓰는데, 그 선언이
실제 실행과 어긋나면 예산 가드의 근거가 무너지므로 실제 호출 기록과 대조한다.
"""

import io
import json
import tarfile

import pytest

from tsdgen.adapters import robocasa_fetch
from tsdgen.registry import STAGES
from tsdgen.stages.estimate import calls_per_scenario
from tsdgen.util import read_jsonl

TAR_MEMBERS = [
    ("lerobot/data/chunk-000/episode_000000.parquet", b"x" * 100),
    ("lerobot/extras/episode_000000/states.npz", b"y" * 100),
    ("lerobot/extras/episode_000000/model.xml.gz", b"z" * 100),
    ("lerobot/extras/episode_000000/ep_meta.json", b'{"layout_id": 1}'),
    ("lerobot/extras/dataset_meta.json", b'{"env": "T"}'),
    ("lerobot/meta/info.json", b'{"robot_type": "PandaOmron"}'),
    ("lerobot/meta/episodes.jsonl", b'{"episode_index": 0, "tasks": ["do it"]}\n'),
    ("lerobot/meta/episodes_stats.jsonl", b"w" * 100),
    ("lerobot/videos/chunk-000/a.mp4", b"v" * 10_000),
]


def _fake_tar() -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, blob in TAR_MEMBERS:
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            tf.addfile(info, io.BytesIO(blob))
    buf.seek(0)
    return buf


def test_fetch_keeps_metadata_and_skips_bulk(settings, monkeypatch):
    monkeypatch.setattr(robocasa_fetch.urllib.request, "urlopen", lambda *a, **k: _fake_tar())
    monkeypatch.setattr(
        robocasa_fetch, "load_box_links",
        lambda _s, _o: {"pretrain/atomic/NewTask/20260101/lerobot.tar": "https://x/s/id"},
    )
    options = settings.adapter_options("robocasa365")
    task_dir = robocasa_fetch.fetch_task_metadata(
        settings, options, "pretrain", "atomic", "NewTask"
    )

    kept = {str(p.relative_to(task_dir)) for p in task_dir.rglob("*") if p.is_file()}
    assert "meta/info.json" in kept and "meta/episodes.jsonl" in kept
    assert "extras/episode_000000/ep_meta.json" in kept
    assert "extras/dataset_meta.json" in kept
    # 용량을 차지하는 것들은 남기지 않는다
    assert not any("states.npz" in k or "model.xml.gz" in k for k in kept)
    assert not any(".parquet" in k or ".mp4" in k for k in kept)
    assert not any("episodes_stats" in k for k in kept)
    assert robocasa_fetch.is_cached(settings, "pretrain", "atomic", "NewTask")


def test_fetch_is_skipped_when_already_cached(settings, monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("이미 캐시된 태스크는 다시 받지 않아야 한다")

    monkeypatch.setattr(robocasa_fetch.urllib.request, "urlopen", explode)
    robocasa_fetch.fetch_task_metadata(
        settings, settings.adapter_options("robocasa365"), "pretrain", "atomic", "FakeTask"
    )


def test_direct_download_url_matches_official_rule():
    url = robocasa_fetch.direct_download_url("https://utexas.box.com/s/abc123")
    assert url == "https://utexas.box.com/shared/static/abc123.tar"


def test_missing_link_is_reported(settings, monkeypatch):
    monkeypatch.setattr(robocasa_fetch, "load_box_links", lambda _s, _o: {})
    with pytest.raises(FileNotFoundError, match="no human Box link"):
        robocasa_fetch.fetch_task_metadata(
            settings, settings.adapter_options("robocasa365"), "pretrain", "atomic", "Nope"
        )


def test_declared_call_count_matches_real_run(settings):
    """역할이 선언한 시나리오당 호출 수가 실제 실행과 일치하는지 확인한다.

    비용 추정과 예산 가드가 이 선언을 근거로 삼는다. 스테이지가 호출을 늘리거나 줄였는데
    선언이 그대로면 추정치가 조용히 틀어지므로, 여기서 실제 사용량 기록과 대조한다.
    """
    for stage in ("ingest", "sample", "contextualize", "generate", "verify"):
        STAGES.get(stage)(settings)

    usage = list(read_jsonl(settings.run_dir() / "usage.jsonl"))
    actual = len(usage)
    declared = calls_per_scenario(settings) * settings.project.n_scenarios
    assert actual == declared, (
        f"실제 호출 {actual}회 vs 선언 {declared}회. "
        f"역할별 실제: {json.dumps(_by_role(usage), ensure_ascii=False)}"
    )


def _by_role(usage: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in usage:
        counts[entry["role"]] = counts.get(entry["role"], 0) + 1
    return counts


def test_human_and_mimicgen_tars_are_distinguished():
    """배포에는 사람 시연본과 mimicgen 합성본이 섞여 있고 후자에는 씬 메타데이터가 없다.

    키를 정렬해 마지막을 집으면 항상 mimicgen이 뽑히고, config의 dataset_source가
    무시된 채 씬이 하나도 없는 캐시가 만들어진다. 실제로 60개 태스크가 그렇게 받아졌다.
    """
    links = {
        "pretrain/atomic/T/20250820/lerobot.tar": "https://x/s/human",
        "pretrain/atomic/T/20250820/mg/demo/2025-08-20-21-53-08/lerobot.tar": "https://x/s/mg",
    }
    human = robocasa_fetch.find_tar_key(links, "pretrain", "atomic", "T", "human")
    mimicgen = robocasa_fetch.find_tar_key(links, "pretrain", "atomic", "T", "mimicgen")
    assert human == "pretrain/atomic/T/20250820/lerobot.tar"
    assert "/mg/" in mimicgen
    assert robocasa_fetch.find_tar_key(links, "pretrain", "atomic", "없음", "human") is None


def test_fetch_refuses_to_mark_complete_without_scene_metadata(settings, monkeypatch):
    """meta/만 있고 씬 메타데이터가 없으면 완료로 표시하면 안 된다.

    완료로 표시해 버리면 시나리오가 조용히 0건이 되고, 그 사실은 최종 데이터 분포가
    한쪽으로 쏠린 뒤에야 드러난다.
    """
    import io
    import tarfile

    def meta_only_tar(*_a, **_k):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for name, blob in [
                ("lerobot/meta/info.json", b"{}"),
                ("lerobot/meta/episodes.jsonl", b'{"episode_index": 0, "tasks": ["x"]}\n'),
                ("lerobot/videos/a.mp4", b"v" * 100),
            ]:
                info = tarfile.TarInfo(name)
                info.size = len(blob)
                tf.addfile(info, io.BytesIO(blob))
        buf.seek(0)
        return buf

    monkeypatch.setattr(robocasa_fetch.urllib.request, "urlopen", meta_only_tar)
    monkeypatch.setattr(
        robocasa_fetch, "load_box_links",
        lambda _s, _o: {"pretrain/atomic/Empty/20260101/lerobot.tar": "https://x/s/id"},
    )
    with pytest.raises(FileNotFoundError, match="씬 메타데이터가 없다"):
        robocasa_fetch.fetch_task_metadata(
            settings, settings.adapter_options("robocasa365"), "pretrain", "atomic", "Empty"
        )
    assert not robocasa_fetch.is_cached(settings, "pretrain", "atomic", "Empty")


VIDEO_TAR_MEMBERS = [m for m in TAR_MEMBERS if "videos" not in m[0]] + [
    ("lerobot/videos/chunk-000/cam_a/episode_000000.mp4", b"v" * 500),
    ("lerobot/videos/chunk-000/cam_a/episode_000001.mp4", b"v" * 500),
    ("lerobot/videos/chunk-000/cam_b/episode_000000.mp4", b"v" * 500),
]


def _video_tar() -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, blob in VIDEO_TAR_MEMBERS:
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            tf.addfile(info, io.BytesIO(blob))
    buf.seek(0)
    return buf


def test_fetch_task_videos_extracts_only_requested(settings, monkeypatch):
    monkeypatch.setattr(robocasa_fetch.urllib.request, "urlopen", lambda *a, **k: _fake_tar())
    monkeypatch.setattr(
        robocasa_fetch, "load_box_links",
        lambda _s, _o: {"pretrain/atomic/NewTask/20260101/lerobot.tar": "https://x/s/id"},
    )
    options = settings.adapter_options("robocasa365").model_copy(
        update={"video_camera": "cam_a"})
    robocasa_fetch.fetch_task_metadata(settings, options, "pretrain", "atomic", "NewTask")

    monkeypatch.setattr(robocasa_fetch.urllib.request, "urlopen", lambda *a, **k: _video_tar())
    result = robocasa_fetch.fetch_task_videos(
        settings, options, "pretrain", "atomic", "NewTask", [0])
    assert result == {"fetched": 1, "already": 0, "missing": 0}
    task_dir = robocasa_fetch.task_cache_dir(settings, "pretrain", "atomic", "NewTask")
    kept = {str(p.relative_to(task_dir)) for p in task_dir.rglob("*.mp4")}
    assert kept == {"videos/chunk-000/cam_a/episode_000000.mp4"}

    # 이미 있으면 네트워크를 건드리지 않는다
    def _boom(*a, **k):
        raise AssertionError("cached video must not refetch")
    monkeypatch.setattr(robocasa_fetch.urllib.request, "urlopen", _boom)
    again = robocasa_fetch.fetch_task_videos(
        settings, options, "pretrain", "atomic", "NewTask", [0])
    assert again == {"fetched": 0, "already": 1, "missing": 0}
