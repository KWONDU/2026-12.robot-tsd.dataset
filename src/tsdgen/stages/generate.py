"""generate 스테이지: 추론 + TSD 생성 (시나리오당 k개, generator 라운드로빈)."""

from datetime import UTC, datetime

from tsdgen import roles, world_state
from tsdgen.config import Settings
from tsdgen.llm.client import LLMClient
from tsdgen.log import get_logger
from tsdgen.models import (
    GeneratorMeta,
    RewriterMeta,
    Sample,
    SampleInput,
    SampleIntermediate,
    SampleMeta,
    SampleOutput,
    dump,
)
from tsdgen.render import render_tsd_text
from tsdgen.resources import load_resources
from tsdgen.roles import call_role
from tsdgen.stages.common import stage, stage_output
from tsdgen.stages.parallel import Job, max_concurrency, run_jobs
from tsdgen.util import derive_seed, read_jsonl, source_digest

log = get_logger("tsdgen.generate")


def sample_id_of(scenario_id: str, slot: int, model_key: str) -> str:
    return f"{scenario_id}--v{slot}--{model_key}"


def _generate_one(
    client: LLMClient, settings: Settings, ctx_record: dict, slot: int, model_key: str
) -> Sample:
    scenario_id = ctx_record["scenario_id"]
    inp = ctx_record["input"]
    role = roles.GENERATE
    seed = derive_seed(settings.project.seed, scenario_id, slot)
    values = {
        "USER_INSTRUCTION": inp["user_instruction"],
        "ENVIRONMENT_CONTEXT": world_state.model_input(inp),
    }
    parsed = call_role(client, settings, role, values, generator=model_key, seed=seed)

    lexicon = load_resources(settings.path("resources"))
    env = inp["environment_context"]
    names = {} if world_state.is_named(inp.get("entity_labels") or {}) else {
        e["id"]: e.get("name_ko", e["id"]) for e in [*env.get("objects", []), *env.get("fixtures", [])]}
    tsd_text = render_tsd_text(parsed.task_situation_description, lexicon, names)
    params = role.params(settings)
    return Sample(
        sample_id=sample_id_of(scenario_id, slot, model_key),
        scenario_id=scenario_id,
        domain=settings.domain,
        lang=settings.project.lang,
        input=SampleInput.model_validate(inp),
        intermediate=SampleIntermediate(reasoning=parsed.reasoning),
        output=SampleOutput(
            task_situation_description=parsed.task_situation_description,
            task_situation_description_text=tsd_text,
        ),
        meta=SampleMeta(
            generator=GeneratorMeta(
                model=model_key,
                provider=settings.model_cfg(model_key).backend,
                prompt_version=role.prompt_version(settings),
                temperature=params.temperature,
                seed=seed,
            ),
            rewriter=RewriterMeta.model_validate(ctx_record["rewriter"]),
            sampling=(ctx_record["scenario"].get("meta") or {}).get("sampling"),
            task_template=_task_template(ctx_record["scenario"]),
            status="pending",
            created_at=datetime.now(UTC).isoformat(),
            pipeline_version=source_digest(settings.root / "src"),
            schema_version=settings.schema_versions["sample"],
        ),
    )


def _task_template(scenario: dict) -> str | None:
    """시나리오를 낳은 태스크 템플릿 이름: '원천/태스크' (예: robocasa365/ArrangeBuffetDessert)."""
    source = scenario.get("source") or {}
    return f"{source.get('simulator')}/{source['task_name']}" if source.get("task_name") else None


def _jobs(settings: Settings, ctx_records: list[dict]) -> list[Job]:
    jobs = []
    for ctx in ctx_records:
        for slot in range(1, roles.GENERATE.calls_per_scenario(settings) + 1):
            model_key = roles.generator_for_slot(settings, slot - 1)
            jobs.append(
                Job(
                    key=sample_id_of(ctx["scenario_id"], slot, model_key),
                    record_id=ctx["scenario_id"],
                    payload=(ctx, slot, model_key),
                )
            )
    return jobs


@stage("generate", calls_llm=True)
def run(settings: Settings) -> dict:
    ctx_records = list(read_jsonl(stage_output(settings, "contextualize")))
    if not ctx_records:
        raise FileNotFoundError("no contextualized records; run the contextualize stage first")

    client = LLMClient(settings, settings.run_dir())
    result = run_jobs(
        settings,
        stage="generate",
        jobs=_jobs(settings, ctx_records),
        work=lambda job: dump(_generate_one(client, settings, *job.payload)),
        out_path=stage_output(settings, "generate"),
        key_field="sample_id",
        workers=max_concurrency(settings, settings.llm.roles.generators),
        done_status="generated",
    )
    log.info(f"+{result.written} samples (failed {result.failed}) -> {result.output}")
    return result.as_dict()
