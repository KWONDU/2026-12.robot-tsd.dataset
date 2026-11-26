"""ingest·synthesize 스테이지: 소스 어댑터 → data/scenarios/<adapter>.jsonl."""

import random
from collections.abc import Iterable

from tsdgen import domains
from tsdgen.adapters.base import make_adapter
from tsdgen.config import Settings
from tsdgen.cpu_pool import ordered_map
from tsdgen.log import get_logger
from tsdgen.models import ScenarioRecord, dump
from tsdgen.stages.common import scenarios_path, stage
from tsdgen.tasks.base import Words
from tsdgen.tasks.compose import SELECTIONS, tasks_of, to_scenario
from tsdgen.tasks.options import compose_options
from tsdgen.util import append_jsonl, read_jsonl, write_jsonl

log = get_logger("tsdgen.ingest")


def _built_as(record: dict) -> tuple[str | None, str | None]:
    """기록이 어떻게 만들어졌는지: 씬 추출 단계(A: 메타데이터만, B: 시뮬레이터 첫 프레임까지)와 씬 상태 스키마 판."""
    scene = record.get("scene_state") or {}
    return (scene.get("provenance") or {}).get("extraction_tier"), scene.get("schema_version")


def store(settings: Settings, records: Iterable[ScenarioRecord]) -> dict:
    """시나리오를 data/scenarios/<adapter>.jsonl에 넣는다. 이미 있는 것은 두 번 쓰지 않고, 추출 단계나 씬 상태
    스키마 판이 다르게 만들어진 기록만 새 것으로 바꾼다. ingest와 synthesize가 함께 쓴다."""
    out_path = scenarios_path(settings)
    existing = {r["scenario_id"]: r for r in read_jsonl(out_path) if "scenario_id" in r}
    # 파일에 이미 있는 것뿐 아니라 이번 실행에서 이미 쓴 것도 걸러야 한다.
    # 원천이 같은 시나리오를 두 번 내놓아도 데이터가 두 벌이 되지 않게 하는 마지막 방어선이다.
    seen: set[str] = set()
    fresh: list[dict] = []
    replaced = 0
    for record in records:
        if record.scenario_id in seen:
            continue
        seen.add(record.scenario_id)
        data = dump(record)
        if record.scenario_id not in existing:
            fresh.append(data)
        elif _built_as(existing[record.scenario_id]) != _built_as(data):
            # 같은 시나리오라도 설정한 추출 단계(source.options.<어댑터>.sim_state)나 씬 상태 스키마 판
            # (schema_versions.scene_state)과 다르게 만들어진 기록은 새로 만든 것으로 바꾼다.
            existing[record.scenario_id] = data
            replaced += 1
    if replaced:
        staging = settings.path("tmp") / out_path.name
        write_jsonl(staging, existing.values())
        staging.replace(out_path)
    written = append_jsonl(out_path, fresh)
    total = len(existing) + written
    log.info(f"+{written} scenarios, {replaced} rebuilt for extraction tier or schema (total {total}) "
             f"-> {out_path}")
    return {"written": written, "replaced": replaced, "total": total, "output": str(out_path)}


@stage("ingest")
def run(settings: Settings) -> dict:
    return store(settings, make_adapter(settings).iter_scenarios())


@stage("synthesize")
def synthesize(settings: Settings) -> dict:
    """원천의 생성기로 새 장면을 만들어 같은 시나리오 파일에 더한다 (LLM 호출 없음)."""
    return store(settings, make_adapter(settings).synthesize())



@stage("compose")
def compose(settings: Settings) -> dict:
    """바탕 장면마다 작업 유형을 돌려 서로 다른 작업 상황만 골라 시나리오 파일에 더한다 (LLM 호출 없음)."""
    options = compose_options(settings)
    words = Words(domains.profile(settings)["task_synthesis"])
    traits = make_adapter(settings).object_traits()
    bases = [r for r in read_jsonl(scenarios_path(settings))
             if r.get("source", {}).get("split") == options.base_split]
    random.Random(settings.project.seed).shuffle(bases)
    # 장면마다 작업 찾기는 서로 독립이라 나눠 돌려도 순서와 결과가 같다 (compose.<도메인>.workers)
    per_base = ordered_map(
        lambda i: list(tasks_of(bases[i]["scene_state"], traits, words, options.types)),
        range(len(bases)),  # 장면은 작업자가 fork로 이미 가지고 있어 번호만 넘긴다
        options.workers,
    )
    found = ((base, task) for base, tasks in zip(bases, per_base, strict=True) for task in tasks)
    chosen, counts = SELECTIONS.get(options.selection)(found, options, settings.project.seed, words)
    for type_id, count in counts.items():
        log.info(f"compose {type_id}: {count}")
    result = store(settings, (to_scenario(base, task, settings.domain) for base, task in chosen))
    return {**result, "bases": len(bases), "types": counts}
