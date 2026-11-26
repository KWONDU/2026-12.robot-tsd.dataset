"""RoboCasa365 Tier A 어댑터: 메타데이터만으로 ScenarioRecord를 만든다."""

import json
import random
import tarfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml
from pydantic import Field

from tsdgen.adapters import robocasa_fetch, robocasa_synth
from tsdgen.adapters import robocasa_staticmap as staticmap
from tsdgen.adapters.base import AdapterOptions, SourceAdapter
from tsdgen.adapters.robocasa_scene import SIMULATOR, TASK_TYPE_PREFIX, SceneBuilder
from tsdgen.adapters.robocasa_sim import SimSnapshot
from tsdgen.adapters.robocasa_success import oracle_alternatives
from tsdgen.adapters.robocasa_synth import Episode, RoboCasaSynthesisOptions
from tsdgen.config import Settings
from tsdgen.log import get_logger
from tsdgen.models import (
    GoalAssertion,
    ScenarioRecord,
    SourceInfo,
)
from tsdgen.registry import SOURCE_ADAPTERS
from tsdgen.resources import load_adapter_resource, load_resources

log = get_logger("tsdgen.adapters.robocasa365")


class RoboCasaPrefetchOptions(AdapterOptions):
    """캐시에 미리 확보할 범위. 실행 대상 범위와 별개로 넓게 잡을 수 있다."""

    splits: list[str]
    families: list[str]
    workers: int
    retries: int
    retry_base_delay_s: float = 2.0  # 재시도 대기 = 이 값 × 2^시도 번호
    log_every: int = 10  # 진행 상황을 이 건수마다 로그로 남긴다


class RoboCasaSimulatorOptions(AdapterOptions):
    """장면 합성용 RoboCasa 시뮬레이터 설치 정보 (scripts/setup_robocasa_sim.sh가 읽는다)."""

    venv: str
    root: str
    repos: dict[str, dict[str, str]]
    packages: list[str]
    # 장면 합성(reset)에 쓰는 값: 고를 수 있는 배치·스타일 번호 범위, 로봇, 물체 인스턴스 묶음, 작업자 스크립트
    layout_ids: tuple[int, int]
    style_ids: tuple[int, int]
    robot: str
    obj_instance_split: str
    reset_worker: str


class RoboCasaOptions(AdapterOptions):
    """RoboCasa365 어댑터가 config에서 받는 설정 전부."""

    domain: str  # 장면이 속한 도메인 (sources.yaml). 도메인 프로필과 프롬프트 낱말을 고른다
    description_ko: str  # 전달 문서에 쓰는 원천 소개
    simulator: RoboCasaSimulatorOptions | None = None  # 장면 합성용 시뮬레이터 (없으면 합성 안 함)
    synthesis: RoboCasaSynthesisOptions | None = None  # 장면 합성 범위 (config의 실행 설정)
    box_links_url: str
    repo_tarball_url: str
    dataset_source: str
    download_timeout_s: int
    prefetch: RoboCasaPrefetchOptions
    splits: list[str]
    families: list[str]
    tasks: list[str] | None = None
    episodes_per_task: int = 2
    task_oversample_factor: float = 2.0
    assume_defaults: bool = False
    # 태스크를 받을 때의 family 간 배분. 선별 단계의 층화와는 별개로,
    # 이 어댑터가 자기 원본에서 무엇을 얼마나 가져올지 스스로 정한다.
    family_weights: dict[str, float] = Field(default_factory=dict)
    # 대시보드 열람용 시연 영상의 카메라 키 (meta/info.json의 video feature 이름).
    # null이면 영상 수신 기능을 쓰지 않는다. LLM 입력에는 어떤 경우에도 안 들어간다.
    video_camera: str | None = None
    # Tier B (opt-in): 시연 첫 프레임의 MuJoCo 상태로 물체 pose와 가구 관절 상태를 채운다.
    # 수신 시 앞쪽 episodes_per_task개 에피소드의 model.xml.gz·states.npz를 더 받는다.
    # 선택 의존성 numpy·mujoco가 필요하다 (pip install -e .[sim]).
    sim_state: bool = False
    sim_pose_decimals: int = 3


class TaskSelection:
    """box 링크 목록에서 받을 태스크를 seed 기반으로 고른다."""

    def __init__(self, settings: Settings, options: "RoboCasaOptions", links: dict[str, str]):
        self.settings = settings
        self.options = options
        self.links = links

    def _available(self) -> dict[str, list[tuple[str, str, str]]]:
        """받을 수 있는 태스크를 family별로. 한 태스크가 tar 키를 여럿 가질 수 있으므로
        (날짜가 다르거나 사람 시연본·mimicgen 합성본이 함께 있다) 이름으로 중복을 없앤
        목록(list_tasks)을 쓴다. 중복이 남으면 같은 시나리오가 두 벌 적재된다."""
        cfg = self.options
        by_family: dict[str, list[tuple[str, str, str]]] = {f: [] for f in cfg.families}
        for split, family, task in robocasa_fetch.list_tasks(self.links, cfg.splits, cfg.families):
            by_family[family].append((split, family, task))
        return by_family

    def select(self) -> list[tuple[str, str, str]]:
        cfg = self.options
        by_family = self._available()
        if cfg.tasks is not None:
            wanted = set(cfg.tasks)
            chosen = [t for lst in by_family.values() for t in lst if t[2] in wanted]
            missing = wanted - {t[2] for t in chosen}
            if missing:
                raise KeyError(f"tasks not found in box links: {sorted(missing)}")
            return chosen

        weights = cfg.family_weights
        need_scenarios = self.settings.project.n_scenarios
        per_task = cfg.episodes_per_task
        rng = random.Random(self.settings.project.seed)
        chosen: list[tuple[str, str, str]] = []
        for family, candidates in by_family.items():
            frac = weights.get(family, 0)
            n_tasks = int(need_scenarios * frac / per_task * cfg.task_oversample_factor) + 1
            picked = rng.sample(candidates, min(n_tasks, len(candidates)))
            chosen.extend(sorted(picked))
        return chosen


def _read_static_map(settings: Settings, file_name: str) -> dict:
    """prepare 산출물 하나 (없으면 빈 dict: prepare 없이도 적재는 되고 해당 정보만 빠진다)."""
    path = settings.path("static_map_dir") / file_name
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def _load_static_maps(settings: Settings) -> tuple[dict, dict]:
    """태스크 색인과 fixture 초기 상태 표."""
    return (_read_static_map(settings, staticmap.TASK_INDEX_FILE),
            _read_static_map(settings, staticmap.STATE_MAP_FILE))


@SOURCE_ADAPTERS.register("robocasa365")
class RoboCasa365Adapter(SourceAdapter):
    options_model = RoboCasaOptions

    def __init__(self, settings: Settings, options: RoboCasaOptions | None = None):
        super().__init__(settings, options)
        self.lexicon = load_resources(settings.path("resources"))
        self.vocabulary = load_adapter_resource(settings.path("resources"), SIMULATOR)
        self.unmapped_categories: set[str] = set()
        self.unmapped_fixture_classes: set[str] = set()
        self.sim_conflicts: list[tuple[str, str, object, object]] = []
        self._success_map: dict | None = None
        self._layout_map: dict | None = None

    @staticmethod
    def _read_instructions(task_dir: Path) -> dict[int, str]:
        """에피소드별 지시문. 같은 태스크라도 물체가 달라 지시문이 다르다."""
        instructions: dict[int, str] = {}
        with (task_dir / "meta" / "episodes.jsonl").open(encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                if entry.get("tasks"):
                    instructions[entry["episode_index"]] = entry["tasks"][0]
        return instructions

    @staticmethod
    def _available_episodes(task_dir: Path) -> list[int]:
        """씬 메타데이터가 실제로 있는 에피소드 번호."""
        return sorted(
            int(p.name.removeprefix("episode_"))
            for p in (task_dir / "extras").glob("episode_*")
            if (p / "ep_meta.json").exists()
        )

    def _builder(self) -> SceneBuilder:
        builder = SceneBuilder(self.settings, self.lexicon, self.vocabulary, self.options)
        builder.unmapped_categories = self.unmapped_categories
        builder.unmapped_fixture_classes = self.unmapped_fixture_classes
        builder.sim_conflicts = self.sim_conflicts
        if self._layout_map is None:
            self._layout_map = _read_static_map(self.settings, staticmap.LAYOUT_MAP_FILE)
        builder.layout_map = self._layout_map
        return builder

    def _record(self, episode: "Episode", maps: tuple[dict, dict]) -> ScenarioRecord:
        """에피소드 폴더 하나(ep_meta.json과, 있으면 시뮬레이터 첫 프레임)를 시나리오로. 받은 시연이든 합성한
        장면이든 같은 길로 만든다."""
        task_index, state_map = maps
        folder = episode.task_dir / "extras" / f"episode_{episode.index:06d}"
        ep_meta = json.loads((folder / "ep_meta.json").read_text(encoding="utf-8"))
        sim = SimSnapshot.load(folder) if self.options.sim_state else None
        if self.options.sim_state and sim is None:
            log.warning(f"{episode.task} ep{episode.index}: no sim state files, falling back to tier A")
        scene = self._builder().build(
            ep_meta, episode.index, episode.task, episode.split, episode.task_dir, episode.robot,
            state_map.get(episode.task, {}), sim, episode.dataset_source,
        )
        activity = (task_index.get(episode.task) or {}).get("activity")
        return ScenarioRecord(
            scenario_id=(f"{TASK_TYPE_PREFIX}-{episode.task}-{episode.split}-"
                         f"{episode.dataset_source}-ep{episode.index:04d}"),
            source=SourceInfo(
                simulator=SIMULATOR,
                task_name=episode.task,
                task_family=episode.family,
                activity=activity,
                split=episode.split,
                dataset_source=episode.dataset_source,
                episode_index=episode.index,
                original_instruction_en=episode.instruction or ep_meta.get("lang") or "",
            ),
            task_type_id=f"{TASK_TYPE_PREFIX}:{episode.task}",
            scene_state=scene,
            # 층화 전략이 이 원천의 어휘를 알 필요가 없도록, 어떤 축으로 나눌지를
            # 어댑터가 이름표로 붙여 준다. 전략은 이름표만 보고 배분한다.
            strata={"family": episode.family, "activity": activity or episode.task},
        )

    def _iter_task_scenarios(
        self, split: str, family: str, task: str, task_index: dict, state_map: dict
    ) -> Iterator[ScenarioRecord]:
        cfg = self.options
        task_dir = robocasa_fetch.fetch_task_metadata(
            self.settings, self.options, split, family, task
        )
        info = json.loads((task_dir / "meta" / "info.json").read_text(encoding="utf-8"))
        instructions = self._read_instructions(task_dir)
        for episode_index in self._available_episodes(task_dir)[: cfg.episodes_per_task]:
            episode = Episode(task_dir, split, family, task, episode_index, cfg.dataset_source,
                              info.get("robot_type", "unknown"), instructions.get(episode_index))
            yield self._record(episode, (task_index, state_map))

    def synthesize(self) -> Iterator[ScenarioRecord]:
        """주방을 고정하고 태스크 템플릿과 seed를 바꿔 시뮬레이터를 reset한 장면들 (robocasa_synth)."""
        links = robocasa_fetch.load_box_links(self.settings, self.options)
        tasks = robocasa_fetch.list_tasks(links, self.options.splits, self.options.families)
        maps = _load_static_maps(self.settings)
        for episode in robocasa_synth.run(self.settings, self.options, tasks):
            yield self._record(episode, maps)

    def iter_scenarios(self) -> Iterator[ScenarioRecord]:
        links = robocasa_fetch.load_box_links(self.settings, self.options)
        task_index, state_map = _load_static_maps(self.settings)
        self.unmapped_categories = set()
        self.unmapped_fixture_classes = set()
        self.sim_conflicts = []
        for split, family, task in TaskSelection(self.settings, self.options, links).select():
            try:
                yield from self._iter_task_scenarios(split, family, task, task_index, state_map)
            except (OSError, tarfile.TarError, json.JSONDecodeError) as e:
                # 실패 격리: 한 태스크의 수신 실패가 전체 적재를 멈추지 않게 한다
                log.warning(f"skip task {task}: {e}")
        if self.unmapped_categories:
            log.warning(
                "한국어 명칭이 없어 영어 식별자를 그대로 쓴 카테고리 "
                f"{len(self.unmapped_categories)}개: {sorted(self.unmapped_categories)} "
                "(resources/ko_lexicon.yaml의 category_names_ko에 추가할 것)"
            )
        if self.unmapped_fixture_classes:
            log.warning(
                "어휘표에 없어 이름에서 분류를 추정한 fixture 클래스 "
                f"{len(self.unmapped_fixture_classes)}개: {sorted(self.unmapped_fixture_classes)} "
                f"(resources/adapters/{SIMULATOR}.yaml의 fixture_classes에 추가할 것)"
            )
        if self.sim_conflicts:
            log.warning(
                f"정적 분석 상태와 시뮬레이터 첫 프레임이 다른 가구 {len(self.sim_conflicts)}건 "
                f"(sim 값을 씀), 예: {self.sim_conflicts[:5]}"
            )

    def object_traits(self) -> dict[str, list[str]]:
        """물체 종류 -> 성질 이름들 (prepare가 시뮬레이터 물체 등록부에서 뽑아 둔 표)."""
        return _read_static_map(self.settings, staticmap.TRAITS_FILE)

    def prepare(self) -> dict:
        """원본에 없는 가구 초기 상태를 시뮬레이터 소스에서 역산해 둔다.

        RoboCasa ep_meta.json에는 문 개폐 같은 의미 상태가 없다. 태스크 클래스의
        상태 설정 호출을 정적 분석해 표로 만들고, 시나리오를 만들 때 참조한다.
        """
        return {"source": self.name(), **staticmap.build_static_maps(self.settings, self.options)}

    def goal_oracle(self, scenario: dict) -> list[list[list[GoalAssertion]]] | None:
        """태스크 성공 판정에서 뽑은 목표 상태 정답 대안들 (prepare가 만든 success_map.yaml)."""
        composed = super().goal_oracle(scenario)  # 작업을 합성한 시나리오는 만들 때 정한 목표 상태가 정답이다
        if composed:
            return composed
        if self._success_map is None:
            self._success_map = _read_static_map(self.settings, staticmap.SUCCESS_MAP_FILE)
        scene = scenario.get("scene_state") or {}
        task = (scene.get("provenance") or {}).get("original_task_name")
        aliases = {spec["category"]: spec["state_key_aliases"]
                   for spec in self.vocabulary["fixture_classes"].values()
                   if spec.get("state_key_aliases")}
        return oracle_alternatives(self._success_map.get(task) or {}, scene, aliases) or None

    def _video_locator(self, provenance: dict) -> tuple[tuple[str, str, str], int] | None:
        """provenance -> ((split, family, task), episode_index). 경로가 예상 밖이면 None."""
        parts = Path(provenance.get("dataset_path", "")).parts
        if len(parts) < 3:
            return None
        return (parts[-3], parts[-2], parts[-1]), provenance.get("episode_index", 0)

    def fetch_videos(self, records: list) -> dict:
        """선별된 시나리오들의 시연 영상(설정된 카메라 1대 분)만 골라 받는다."""
        if not self.options.video_camera:
            raise ValueError("source.options.robocasa365.video_camera가 설정되어야 한다")
        by_task: dict[tuple[str, str, str], set[int]] = {}
        for record in records:
            located = self._video_locator(record.scene_state.provenance.model_dump(mode="json"))
            if located:
                by_task.setdefault(located[0], set()).add(located[1])
        counts = {"fetched": 0, "already": 0, "missing": 0}
        for (split, family, task), episodes in sorted(by_task.items()):
            result = robocasa_fetch.fetch_task_videos(
                self.settings, self.options, split, family, task, sorted(episodes)
            )
            log.info(f"videos {split}/{family}/{task}: {result}")
            for key in counts:
                counts[key] += result[key]
        return {"tasks": len(by_task), **counts}

    def video_file(self, scenario: dict) -> Path | None:
        """수신되어 있는 시연 영상 파일 경로. 없으면 None (수신은 fetch_videos 스테이지가 한다)."""
        if not self.options.video_camera:
            return None
        provenance = (scenario.get("scene_state") or {}).get("provenance") or {}
        located = self._video_locator(provenance)
        if located is None:
            return None
        (split, family, task), episode = located
        task_dir = robocasa_fetch.task_cache_dir(self.settings, split, family, task)
        if not (task_dir / "meta" / "info.json").exists():
            return None
        path = task_dir / robocasa_fetch.video_relpath(
            self.options.video_camera, robocasa_fetch.episode_chunk(task_dir, episode), episode
        )
        return path if path.exists() else None

    def _prefetch_task(self, split: str, family: str, task: str) -> str:
        """한 태스크 메타데이터를 확보한다. 일시적 오류는 설정된 횟수만큼 재시도."""
        cfg = self.options.prefetch
        last_error: Exception | None = None
        for attempt in range(cfg.retries):
            try:
                robocasa_fetch.fetch_task_metadata(self.settings, self.options, split, family, task)
                return "ok"
            except (OSError, tarfile.TarError) as e:
                last_error = e
                if attempt + 1 < cfg.retries:  # 마지막 시도 뒤에는 기다릴 이유가 없다
                    time.sleep(cfg.retry_base_delay_s * (2**attempt))
        log.warning(f"fail {split}/{family}/{task}: {last_error}")
        return "failed"

    def prefetch(self) -> dict:
        """설정된 범위의 태스크 메타데이터를 캐시에 확보한다 (비디오·궤적 제외)."""
        cfg = self.options.prefetch
        links = robocasa_fetch.load_box_links(self.settings, self.options)
        tasks = robocasa_fetch.list_tasks(links, cfg.splits, cfg.families)
        with_sim = self.options.sim_state
        pending = [t for t in tasks if not robocasa_fetch.is_cached(self.settings, *t, with_sim)]
        log.info(f"{SIMULATOR}: {len(tasks)} tasks in scope, {len(pending)} to fetch")

        counts = {"ok": 0, "failed": 0, "cached": len(tasks) - len(pending)}
        done = 0
        with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
            futures = {pool.submit(self._prefetch_task, *t): t for t in pending}
            for future in as_completed(futures):
                counts[future.result()] += 1
                done += 1
                if done % cfg.log_every == 0 or done == len(pending):
                    log.info(f"{SIMULATOR} {done}/{len(pending)} ({counts})")
        return {"source": self.name(), "tasks_in_scope": len(tasks), **counts}
