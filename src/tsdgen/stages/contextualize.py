"""contextualize 스테이지: 유저 입력(한국어 재작성)과 환경 컨텍스트 분리.

모델에게 보여 줄 세계 상태 글(world_state 보기)을 여기서 한 번 만들고, 재작성·지시 해소와 뒤 스테이지가 모두 그 글을 쓴다.

재작성이 instruction_resolvability 검사를 통과하지 못하면 config가 정한 폴백
구체성(contextualize.fallback_specificity)으로 한 번 더 쓴다 (스펙 6.2).
"""

from tsdgen import roles, world_state
from tsdgen.config import Settings
from tsdgen.llm.client import LLMClient
from tsdgen.log import get_logger
from tsdgen.models import ScenarioRecord, dump, to_environment_context
from tsdgen.resources import load_resources
from tsdgen.roles import call_role
from tsdgen.stages.common import stage, stage_output
from tsdgen.stages.parallel import Job, run_jobs
from tsdgen.util import read_jsonl
from tsdgen.verify import judges

log = get_logger("tsdgen.contextualize")


def _rewrite(
    client: LLMClient, settings: Settings, record: ScenarioRecord, world: str, specificity: str
) -> str:
    values = {
        "ORIGINAL_INSTRUCTION": record.source.original_instruction_en,
        "ENVIRONMENT_CONTEXT": world,
    }
    parsed = call_role(client, settings, roles.REWRITE, values, variant=specificity)
    return parsed.user_instruction_ko


def _contextualize_one(client: LLMClient, settings: Settings, raw: dict) -> dict:
    record = ScenarioRecord.model_validate(raw)
    env_ctx = to_environment_context(record.scene_state)
    world = world_state.render(settings, env_ctx, load_resources(settings.path("resources")))
    original = record.source.original_instruction_en
    specificity = settings.contextualize.specificity
    fallback = settings.contextualize.fallback_specificity

    def attempt(level: str) -> tuple[str, object]:
        text = _rewrite(client, settings, record, world.text, level)
        return text, judges.run_resolvability(client, settings, original, text, world.text)

    instruction, resolvability = attempt(specificity)
    fallback_used = False
    if not resolvability.pass_ and specificity != fallback:
        specificity = fallback
        instruction, resolvability = attempt(specificity)
        fallback_used = True

    return {
        "scenario_id": record.scenario_id,
        "scenario": raw,
        "input": {
            "user_instruction": instruction,
            # 원문은 열람·납품 참조용이다. 생성 프롬프트에는 user_instruction만 들어간다.
            "original_instruction_en": original,
            "instruction_specificity": specificity,
            "environment_context": env_ctx,
            "world_state": world.text,
            "entity_labels": world.labels,
        },
        "rewriter": {
            "model": roles.REWRITE.model_for(settings, None),
            "prompt_version": roles.REWRITE.prompt_version(settings, specificity),
        },
        "resolvability": dump(resolvability),
        "fallback_used": fallback_used,
    }


@stage("contextualize", calls_llm=True)
def run(settings: Settings) -> dict:
    in_path = stage_output(settings, "sample")
    if not in_path.exists():
        raise FileNotFoundError("no sampled scenarios; run the sample stage first")

    client = LLMClient(settings, settings.run_dir())
    jobs = [
        Job(key=raw["scenario_id"], record_id=raw["scenario_id"], payload=raw)
        for raw in read_jsonl(in_path)
    ]
    result = run_jobs(
        settings,
        stage="contextualize",
        jobs=jobs,
        work=lambda job: _contextualize_one(client, settings, job.payload),
        out_path=stage_output(settings, "contextualize"),
        key_field="scenario_id",
        workers=settings.model_cfg(roles.REWRITE.model_for(settings, None)).concurrency,
        done_status="contextualized",
    )
    log.info(f"+{result.written} (failed {result.failed}) -> {result.output}")
    return result.as_dict()
