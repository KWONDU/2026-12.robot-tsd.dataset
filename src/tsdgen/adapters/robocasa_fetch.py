"""RoboCasa365 공식 배포(UT Box의 태스크별 tar)에서 메타데이터만 선택 수신.

정찰로 확인한 사실(docs/RECON_ROBOCASA365.md): tar 멤버 순서가
data → extras → meta → videos 이므로, 스트리밍으로 meta/와 extras/의
ep_meta.json·dataset_meta.json만 추출하고 videos에서 끊으면 태스크당
수십 MB 수신으로 끝난다. 전체 tar(비디오 포함)를 받을 필요가 없다.
"""

import json
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

from tsdgen.adapters.base import CACHE_COMPLETE_MARKER as METADATA_MARKER
from tsdgen.adapters.robocasa_sim import SIM_FILES
from tsdgen.config import Settings
from tsdgen.registry import Registry

KEEP_META_FILES = {"info.json", "tasks.jsonl", "episodes.jsonl", "modality.json", "embodiment.json"}
KEEP_EXTRAS_FILES = {"dataset_meta.json", "ep_meta.json"}
VIDEOS_SECTION = "videos"  # tar 안에서 시연 영상이 모인 최상위 구간 (멤버 순서상 맨 뒤)
SIM_MARKER = ".sim_state_complete"  # Tier B 파일까지 받은 캐시 표시 (메타데이터 표시와 별개)
EPISODE_DIR_PREFIX = "episode_"


def load_box_links(settings: Settings, options: Any) -> dict[str, str]:
    cache_path = settings.path("raw_cache") / "box_links_ds.json"
    if not cache_path.exists():
        with urllib.request.urlopen(
            options.box_links_url, timeout=options.download_timeout_s
        ) as r:
            payload = r.read().decode("utf-8")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(payload, encoding="utf-8")
    return json.loads(cache_path.read_text(encoding="utf-8"))


def direct_download_url(shared_url: str, ext: str = "tar") -> str:
    """Box 공유 링크를 직접 다운로드 URL로 바꾼다 (공식 스크립트와 동일 규칙)."""
    shared_id = shared_url.rstrip("/").split("/")[-1]
    base = shared_url.split("/s/")[0]
    return f"{base}/shared/static/{shared_id}.{ext}"


# 사람 시연은 <split>/<family>/<task>/<날짜>/lerobot.tar 한 단계이고,
# mimicgen 합성본은 .../<날짜>/mg/demo/<시각>/lerobot.tar 로 더 깊다.
MIMICGEN_SEGMENT = "/mg/"
HUMAN_KEY_DEPTH = 5  # split/family/task/date/lerobot.tar

DATASET_SOURCES = Registry("robocasa dataset source", axes=())
"""이름 -> (tar 키) -> 이 종류에 속하는가.

한 태스크에 사람 시연본과 mimicgen 합성본이 함께 있고 키 모양이 다르다. 어느 쪽을
받을지는 config가 고르므로 이름으로 분기하지 않고 등록으로 표현한다. 종류가 늘어도
선택 코드는 바뀌지 않는다."""


@DATASET_SOURCES.register("human")
def _is_human(key: str) -> bool:
    """사람 시연본. mimicgen 경로가 없고 키 깊이가 한 단계다."""
    return MIMICGEN_SEGMENT not in key and len(key.split("/")) == HUMAN_KEY_DEPTH


@DATASET_SOURCES.register("mimicgen")
def _is_mimicgen(key: str) -> bool:
    """mimicgen 합성본. 씬 메타데이터(extras)가 없어 우리 용도로는 쓸 수 없다."""
    return MIMICGEN_SEGMENT in key


def find_tar_key(
    links: dict[str, str], split: str, family: str, task: str, dataset_source: str = "human"
) -> str | None:
    """이 태스크의 tar 키. 날짜가 여럿이면 최신을 고른다."""
    belongs = DATASET_SOURCES.get(dataset_source)
    prefix = f"{split}/{family}/{task}/"
    matches = sorted(k for k in links if k.startswith(prefix) and belongs(k))
    return matches[-1] if matches else None


def _tar_url(settings: Settings, options: Any, split: str, family: str, task: str) -> str:
    """이 태스크의 설정된 종류(dataset_source) tar를 직접 받는 URL. 링크가 없으면 오류."""
    links = load_box_links(settings, options)
    tar_key = find_tar_key(links, split, family, task, options.dataset_source)
    if tar_key is None:
        raise FileNotFoundError(
            f"no {options.dataset_source} Box link for {split}/{family}/{task}"
        )
    return direct_download_url(links[tar_key])


def list_tasks(
    links: dict[str, str], splits: list[str], families: list[str]
) -> list[tuple[str, str, str]]:
    """링크 목록에서 (split, family, task) 조합을 중복 없이 뽑는다."""
    found = set()
    for key in links:
        split, family, task = key.split("/")[:3]
        if split in splits and family in families:
            found.add((split, family, task))
    return sorted(found)


def _want_member(parts: list[str], sim_episodes: int = 0) -> bool:
    """받을 tar 멤버. sim_episodes > 0이면 앞쪽 그 수만큼의 에피소드에서 시뮬레이터 상태 파일도 받는다
    (에피소드 전부를 받으면 태스크당 수십 MB가 쌓이는데, 시나리오로 쓰는 것은 앞쪽 몇 개뿐이다)."""
    if len(parts) < 3 or parts[1] not in ("meta", "extras"):
        return False
    if parts[1] == "meta":
        return parts[-1] in KEEP_META_FILES
    if parts[-1] in KEEP_EXTRAS_FILES:
        return True
    episode = parts[2].removeprefix(EPISODE_DIR_PREFIX)
    return parts[-1] in SIM_FILES and episode.isdigit() and int(episode) < sim_episodes


def task_cache_dir(settings: Settings, split: str, family: str, task: str) -> Path:
    return settings.path("raw_cache") / split / family / task


def is_cached(
    settings: Settings, split: str, family: str, task: str, with_sim: bool = False
) -> bool:
    task_dir = task_cache_dir(settings, split, family, task)
    markers = (METADATA_MARKER, SIM_MARKER) if with_sim else (METADATA_MARKER,)
    return all((task_dir / name).exists() for name in markers)


def fetch_task_metadata(
    settings: Settings, options: Any, split: str, family: str, task: str
) -> Path:
    """한 태스크의 meta/·extras/ 메타데이터를 캐시에 확보하고 경로를 돌려준다.
    sim_state가 켜져 있으면 시뮬레이터 상태 파일까지 받았다는 표시가 있어야 캐시로 인정한다."""
    task_dir = task_cache_dir(settings, split, family, task)
    marker = task_dir / METADATA_MARKER
    sim_episodes = options.episodes_per_task if options.sim_state else 0
    sim_marker = task_dir / SIM_MARKER
    if marker.exists() and (not sim_episodes or sim_marker.exists()):
        return task_dir

    url = _tar_url(settings, options, split, family, task)
    task_dir.mkdir(parents=True, exist_ok=True)
    with (
        urllib.request.urlopen(url, timeout=options.download_timeout_s) as stream,
        tarfile.open(fileobj=stream, mode="r|") as tf,
    ):
        for member in tf:
            parts = member.name.split("/")
            if len(parts) > 1 and parts[1] == VIDEOS_SECTION:
                break  # 멤버 순서상 이후는 전부 비디오
            if member.isfile() and _want_member(parts, sim_episodes):
                member.name = "/".join(parts[1:])  # 최상위 'lerobot/' 제거
                tf.extract(member, path=task_dir)
    # 씬 메타데이터가 실제로 들어왔는지 확인하고 나서 완료 표시를 찍는다.
    # meta/만 받고 extras/가 비어도 완료로 표시하면, 시나리오가 조용히 0건이 되고
    # 그 사실이 최종 데이터 분포에 가서야 드러난다.
    episodes = list((task_dir / "extras").glob("episode_*/ep_meta.json"))
    if not (task_dir / "meta" / "episodes.jsonl").exists() or not episodes:
        raise FileNotFoundError(
            f"{split}/{family}/{task}: 받은 tar에 씬 메타데이터가 없다 "
            f"(episodes.jsonl={bool((task_dir / 'meta' / 'episodes.jsonl').exists())}, "
            f"ep_meta={len(episodes)}건, url={url}). tar 구성이 바뀌었을 수 있다."
        )
    marker.touch()
    if sim_episodes:
        sim_marker.touch()
    return task_dir


def episode_chunk(task_dir: Path, episode_index: int) -> int:
    """에피소드가 속한 비디오 chunk 번호. 규칙은 meta/info.json의 chunks_size가 정한다."""
    info = json.loads((task_dir / "meta" / "info.json").read_text(encoding="utf-8"))
    size = info.get("chunks_size")
    return episode_index // size if size else 0


def check_video_camera(task_dir: Path, camera: str) -> None:
    """카메라 키가 실제 video feature인지 확인한다. 키가 틀리면 tar 전체를 헛수신하게 되므로
    스트리밍 전에 잡는다. features가 없는(테스트용 등) info.json은 검사하지 않는다."""
    info = json.loads((task_dir / "meta" / "info.json").read_text(encoding="utf-8"))
    features = info.get("features") or {}
    video_keys = [k for k, v in features.items() if v.get("dtype") == "video"]
    if video_keys and camera not in video_keys:
        raise ValueError(f"video_camera '{camera}'가 없다. 가용 키: {video_keys}")


def video_relpath(camera: str, chunk: int, episode_index: int) -> str:
    """태스크 캐시 안에서 시연 영상의 상대 경로 (tar 구조를 그대로 따른다)."""
    return f"{VIDEOS_SECTION}/chunk-{chunk:03d}/{camera}/episode_{episode_index:06d}.mp4"


def fetch_task_videos(
    settings: Settings, options: Any, split: str, family: str, task: str,
    episode_indices: list[int],
) -> dict:
    """한 태스크에서 지정 에피소드들의 시연 영상만 추출해 캐시한다.

    tar 순서상 videos가 맨 뒤라 앞 구간(data·extras·meta)을 흘려보내야 하지만,
    비디오 멤버는 카메라·에피소드 순으로 정렬되어 있어 원하는 파일을 다 얻는
    즉시(또는 정렬상 더는 없을 때) 끊는다. 열람용이며 LLM 입력이 아니다.
    """
    task_dir = task_cache_dir(settings, split, family, task)
    if not (task_dir / METADATA_MARKER).exists():
        raise FileNotFoundError(f"{split}/{family}/{task}: 메타데이터가 먼저 수신되어야 한다")
    camera = options.video_camera
    check_video_camera(task_dir, camera)
    wanted = {video_relpath(camera, episode_chunk(task_dir, i), i) for i in episode_indices}
    missing = {w for w in wanted if not (task_dir / w).exists()}
    if not missing:
        return {"fetched": 0, "already": len(wanted), "missing": 0}

    url = _tar_url(settings, options, split, family, task)
    fetched = 0
    with (
        urllib.request.urlopen(url, timeout=options.download_timeout_s) as stream,
        tarfile.open(fileobj=stream, mode="r|") as tf,
    ):
        for member in tf:
            parts = member.name.split("/")
            if len(parts) < 2 or parts[1] != VIDEOS_SECTION or not member.isfile():
                continue
            rel = "/".join(parts[1:])
            if rel in missing:
                member.name = rel
                tf.extract(member, path=task_dir)
                missing.discard(rel)
                fetched += 1
                if not missing:
                    break
            elif rel > max(missing):
                break  # 멤버가 정렬되어 있으므로 남은 파일은 이 tar에 없다
    return {"fetched": fetched, "already": len(wanted) - fetched - len(missing),
            "missing": len(missing)}


def fetch_task_source_tree(settings: Settings, options: Any) -> Path:
    """robocasa 저장소의 태스크 소스(environments/kitchen)를 정적 분석용으로 캐시한다."""
    dest = settings.path("static_map_dir") / "task_src"
    marker = dest / METADATA_MARKER
    if marker.exists():
        return dest

    dest.mkdir(parents=True, exist_ok=True)
    needle = "/robocasa/environments/kitchen/"
    with urllib.request.urlopen(
        options.repo_tarball_url, timeout=options.download_timeout_s
    ) as stream, tarfile.open(fileobj=stream, mode="r|gz") as tf:
        for member in tf:
            if member.isfile() and needle in member.name and member.name.endswith(".py"):
                member.name = member.name.split(needle, 1)[1]
                tf.extract(member, path=dest)
    marker.touch()
    return dest
