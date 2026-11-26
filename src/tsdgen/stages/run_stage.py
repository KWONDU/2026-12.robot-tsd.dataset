"""run 스테이지: 전체 파이프라인 오케스트레이션 + 예산 가드 + run manifest.

예산 가드는 LLM을 부르는 첫 스테이지(Stage.calls_llm) 직전에 한 번 건다. 어느 스테이지가
비용을 쓰는지는 스테이지가 스스로 선언하므로, 여기서 스테이지 이름을 알 필요가 없다.
"""

import hashlib
import shutil
from datetime import UTC, datetime

import yaml

from tsdgen import roles
from tsdgen.config import CONFIG_PATH, Settings
from tsdgen.llm.base import BudgetExceededError
from tsdgen.log import get_logger
from tsdgen.registry import STAGES
from tsdgen.runs import (
    CONFIG_HASH,
    CONFIG_SNAPSHOT_DIR,
    ESTIMATE,
    MANIFEST,
    PROMPT_SNAPSHOT_DIR,
    RESOLVED_CONFIG,
)
from tsdgen.stages.common import stage
from tsdgen.stages.estimate import build_estimate, print_estimate
from tsdgen.util import source_digest, stable_hash, write_json

log = get_logger("tsdgen.run")


def _budget_gate(settings: Settings) -> None:
    estimate = build_estimate(settings)
    write_json(settings.run_dir() / ESTIMATE, estimate)
    print_estimate(estimate)
    budget = settings.estimate.budget_usd
    if budget is None:
        return
    if settings.estimate.confirm_over_budget:
        log.info("[run] budget gate bypassed (estimate.confirm_over_budget=true)")
        return
    cost = estimate["total_cost_usd"]
    if cost is None:
        raise BudgetExceededError(
            "budget_usd is set but cost is unknown; fill estimate.prices_per_1m_tokens "
            "or set estimate.confirm_over_budget=true"
        )
    if cost > budget:
        raise BudgetExceededError(
            f"estimated cost {cost:.2f} USD exceeds budget {budget:.2f} USD; "
            "reduce project.n_scenarios (예: 20) or set estimate.confirm_over_budget=true"
        )


def resolved_config(settings: Settings) -> dict:
    """세 설정 파일을 합쳐 검증을 마친 최종 설정 (실행을 재현하는 데 필요한 값 전부)."""
    return settings.model_dump(mode="json", exclude={"root"})


def _snapshot_configs(settings: Settings) -> list[str]:
    """이번 실행에 쓴 설정 파일 사본, 합쳐진 최종 설정과 그 hash를 남긴다.

    설정이 여러 파일로 나뉘어 있으므로, 산출물만 보고 어떤 조합으로 만든 것인지 되짚으려면 사본이 있어야 한다. hash는 두 실행의 설정이 같은지 한눈에 비교하는 데 쓴다.
    """
    out_dir = settings.run_dir() / CONFIG_SNAPSHOT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    sources = [CONFIG_PATH, settings.root / settings.registries.models,
               settings.root / settings.registries.sources]
    for path in sources:
        shutil.copy2(path, out_dir / path.name)
        written.append(path.name)
    resolved = resolved_config(settings)
    (out_dir / RESOLVED_CONFIG).write_text(
        yaml.safe_dump(resolved, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (out_dir / CONFIG_HASH).write_text(stable_hash(resolved) + "\n", encoding="utf-8")
    return [*written, RESOLVED_CONFIG, CONFIG_HASH]


def _snapshot_prompts(settings: Settings) -> dict[str, str]:
    """실제로 쓴 프롬프트 파일의 해시와 사본을 남긴다.

    프롬프트가 바뀌면 결과 비교가 무의미해지므로, 산출물만 보고도 어떤 문안으로 만든
    것인지 되짚을 수 있어야 한다. 역할별로 config가 가리키는 버전 파일만 대상으로 한다.
    """
    out_dir = settings.run_dir() / PROMPT_SNAPSHOT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for role in roles.all_roles():
        version = role.prompt_version(settings)
        path = settings.path("prompts") / role.name / f"{version}.txt"
        if not path.exists():
            raise FileNotFoundError(f"prompt file not found for role '{role.name}': {path}")
        blob = path.read_bytes()
        hashes[role.name] = f"{version}:{hashlib.sha256(blob).hexdigest()[:12]}"
        (out_dir / f"{role.name}_{version}.txt").write_bytes(blob)
    return hashes


def _write_manifest(settings: Settings, results: dict) -> None:
    manifest = {
        "run_id": settings.project.run_id,
        "finished_at": datetime.now(UTC).isoformat(),
        "pipeline_version": source_digest(settings.root / "src"),
        "seed": settings.project.seed,
        "n_scenarios": settings.project.n_scenarios,
        "prompts_versions": settings.prompts_versions,
        "prompt_hashes": _snapshot_prompts(settings),
        "config_files": _snapshot_configs(settings),
        "roles": settings.llm.roles.model_dump(),
        "config_snapshot": resolved_config(settings),
        "config_hash": stable_hash(resolved_config(settings)),
        "stage_results": results,
    }
    write_json(settings.run_dir() / MANIFEST, manifest)


@stage("run", orchestrates=True)
def run(settings: Settings) -> dict:
    results: dict[str, dict] = {}
    gated = False
    for stage_name in settings.project.pipeline_order:
        step = STAGES.get(stage_name)
        if not gated and step.calls_llm:
            _budget_gate(settings)
            gated = True
        results[stage_name] = step(settings)
    _write_manifest(settings, results)
    log.info(f"done; manifest -> {settings.run_dir() / MANIFEST}")
    return results
