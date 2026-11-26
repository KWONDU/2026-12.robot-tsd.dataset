"""Isaac Lab-Arena 환경 스펙 어댑터.

Arena 저장소를 캐시에 받아 두고(prefetch), 거기 있는 환경 스펙 YAML을 우리 씬
상태로 읽어들인다(iter_scenarios). ETRI가 전달할 시뮬레이션 패키지가 Arena
기반일 경우 스펙 파일만 캐시에 넣으면 같은 경로로 파이프라인에 들어온다.

반대 방향(우리 씬 -> Arena 스펙)은 export 포맷 arena_yaml이 맡는다.
"""

import tarfile
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import yaml

from tsdgen.adapters import arena_convert, arena_spec
from tsdgen.adapters.base import CACHE_COMPLETE_MARKER, AdapterOptions, SourceAdapter
from tsdgen.log import get_logger
from tsdgen.models import ScenarioRecord, SourceInfo
from tsdgen.registry import SOURCE_ADAPTERS
from tsdgen.resources import load_arena_mapping, load_resources

log = get_logger("tsdgen.adapters.arena")

CACHE_SUBDIR = "isaaclab_arena"
SPEC_DIR = "isaaclab_arena_environments"
SIMULATOR = arena_convert.SIMULATOR


class ArenaOptions(AdapterOptions):
    """Arena 어댑터가 config에서 받는 설정."""

    domain: str  # 장면이 속한 도메인 (sources.yaml)
    description_ko: str  # 전달 문서에 쓰는 원천 소개
    repo_tarball_url: str
    download_timeout_s: int = 600


@SOURCE_ADAPTERS.register("isaaclab_arena")
class ArenaSpecAdapter(SourceAdapter):
    options_model = ArenaOptions

    def cache_dir(self) -> Path:
        return self.settings.path("raw_cache") / CACHE_SUBDIR

    def spec_paths(self) -> list[Path]:
        return sorted((self.cache_dir() / SPEC_DIR).rglob("*.yaml"))

    def prefetch(self) -> dict:
        cfg = self.options
        dest = self.cache_dir()
        marker = dest / CACHE_COMPLETE_MARKER
        if marker.exists():
            log.info(f"isaaclab_arena: already cached -> {dest}")
            return {"source": SIMULATOR, "cached": True, "path": str(dest)}

        dest.mkdir(parents=True, exist_ok=True)
        extracted = 0
        with (
            urllib.request.urlopen(cfg.repo_tarball_url, timeout=cfg.download_timeout_s) as stream,
            tarfile.open(fileobj=stream, mode="r|gz") as tf,
        ):
            for member in tf:
                if not member.isfile():
                    continue
                parts = member.name.split("/")
                if len(parts) < 2:
                    continue
                member.name = "/".join(parts[1:])  # 최상위 <repo>-main/ 제거
                tf.extract(member, path=dest)
                extracted += 1
        env_specs = self.spec_paths()
        if not env_specs:
            raise FileNotFoundError(
                f"no env spec YAML under {dest}; Arena repo layout may have changed"
            )
        marker.touch()
        log.info(f"isaaclab_arena: {extracted} files, {len(env_specs)} env specs -> {dest}")
        return {
            "source": SIMULATOR,
            "files": extracted,
            "env_specs": len(env_specs),
            "path": str(dest),
        }

    def iter_scenarios(self) -> Iterator[ScenarioRecord]:
        mapping = dict(load_arena_mapping(self.settings.path("resources")))
        mapping["category_names_ko"] = load_resources(self.settings.path("resources"))[
            "category_names_ko"
        ]
        cache_root = self.settings.path("raw_cache")
        skipped = 0
        for path in self.spec_paths():
            try:
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as e:
                log.warning(f"skip unreadable spec {path.name}: {e}")
                continue
            if not arena_spec.is_env_spec(document):
                skipped += 1  # 실험 설정 등 env spec이 아닌 YAML
                continue
            env = arena_spec.ArenaEnvSpec.model_validate(document)
            scene = arena_convert.scene_from_spec(
                env,
                mapping,
                domain=self.options.domain,
                schema_version=self.settings.schema_versions["scene_state"],
                source_path=str(path.relative_to(cache_root)),
            )
            yield ScenarioRecord(
                scenario_id=f"arena-{env.env_name}",
                source=SourceInfo(
                    simulator=SIMULATOR,
                    task_name=env.env_name,
                    task_family=env.task.composition,
                    split="arena",
                    dataset_source="env_spec",
                    episode_index=0,
                    original_instruction_en=env.task.description,
                ),
                task_type_id=f"arena:{env.env_name}",
                scene_state=scene,
            )
        if skipped:
            log.info(f"skipped {skipped} YAML files that are not env specs")
