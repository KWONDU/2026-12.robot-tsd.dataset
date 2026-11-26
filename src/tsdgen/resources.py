"""자원 파일 로더.

한국어 어휘 사전과 전달물 문서 템플릿처럼 코드가 아닌 내용은 파일에 두고
여기서만 읽는다. 설정(config)과 분리해 두는 이유는, 설정은 실행마다 바뀌는
조정값이고 이쪽은 실행과 무관한 어휘·문안이기 때문이다.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

LEXICON_FILE = "ko_lexicon.yaml"
TEMPLATE_DIR = "templates"
ARENA_MAPPING_FILE = "arena_mapping.yaml"
ADAPTER_DIR = "adapters"
GOLDEN_FILE = "golden/heatmug_sample.json"
DOMAIN_DIR = "domains"  # 도메인 프로필 (파일 이름 = 도메인 이름)


@lru_cache(maxsize=8)
def load_resources(resources_dir: Path) -> dict[str, Any]:
    """한국어 명칭·fixture 분류·검수 항목 등 어휘 사전을 읽는다. 도메인 프로필은 domains 아래에 함께 싣는다
    (세계 상태 글이 장면의 도메인으로 장소 이름과 가구 낱말을 찾는다)."""
    lexicon = yaml.safe_load((resources_dir / LEXICON_FILE).read_text(encoding="utf-8"))
    lexicon["domains"] = {p.stem: yaml.safe_load(p.read_text(encoding="utf-8"))
                          for p in sorted((resources_dir / DOMAIN_DIR).glob("*.yaml"))}
    return lexicon


def load_template(resources_dir: Path, name: str) -> str:
    """전달물 문서 템플릿을 읽는다.

    산출물에 그대로 실리는 산문은 코드가 아니라 파일에 둔다. 코드 컨벤션(줄 길이 등)이
    사람이 읽을 문서의 문장을 자르지 않게 하기 위함이다.
    """
    return (resources_dir / TEMPLATE_DIR / name).read_text(encoding="utf-8")


@lru_cache(maxsize=8)
def load_arena_mapping(resources_dir: Path) -> dict[str, Any]:
    """Arena 상호운용 매핑 (물체 registry, prim 경로, 배경·embodiment 이름)."""
    return yaml.safe_load((resources_dir / ARENA_MAPPING_FILE).read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def load_adapter_resource(resources_dir: Path, adapter_name: str) -> dict[str, Any]:
    """어댑터 전용 어휘. 원본 표기를 우리 규격으로 옮기는 표라 원천마다 다르다."""
    path = resources_dir / ADAPTER_DIR / f"{adapter_name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_golden(resources_dir: Path, name: str = GOLDEN_FILE) -> dict[str, Any]:
    """골든 예시 한 건 (생성 프롬프트의 few-shot 겸 테스트 픽스처). 도메인마다 자기 예시를 고른다."""
    return json.loads((resources_dir / name).read_text(encoding="utf-8"))


def load_domain(resources_dir: Path, name: str) -> dict[str, Any]:
    """도메인 프로필 하나 (resources/domains/<이름>.yaml). 없는 도메인이면 있는 이름을 알려 준다."""
    path = resources_dir / DOMAIN_DIR / f"{name}.yaml"
    if not path.exists():
        known = sorted(p.stem for p in (resources_dir / DOMAIN_DIR).glob("*.yaml"))
        raise KeyError(f"unknown domain '{name}'; allowed (resources/domains): {known}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))
