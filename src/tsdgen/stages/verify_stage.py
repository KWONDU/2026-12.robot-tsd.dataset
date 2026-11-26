"""verify 스테이지: 등록된 검수 항목을 config가 정한 순서로 실행한다 (스펙 6.4).

앞 항목이 실패하면 뒤는 생략하고 사유를 남긴다. 실패 샘플은 삭제하지 않고
status=rejected로 보존한다. judge 호출 자체가 터진 경우는 "판정 결과 미달"과
구분해 needs_review로 남긴다. 둘을 섞으면 통과율 통계가 오염되기 때문이다.
"""

from tsdgen import roles, world_state
from tsdgen.adapters.base import SourceAdapter, make_adapter
from tsdgen.config import Settings
from tsdgen.llm.client import LLMClient
from tsdgen.log import get_logger
from tsdgen.models import ResolvabilityResult, Sample, VerificationResult, dump
from tsdgen.resources import load_resources
from tsdgen.stages.common import stage, stage_output
from tsdgen.stages.parallel import Job, max_concurrency, run_jobs
from tsdgen.tasks.compose import SITUATION_KEY
from tsdgen.util import read_jsonl
from tsdgen.verify.checks import VERIFY_CHECKS, CheckContext

log = get_logger("tsdgen.verify")


def _verify_one(
    client: LLMClient, settings: Settings, raw: dict, context: dict, adapter: SourceAdapter
) -> dict:
    sample = Sample.model_validate(raw)
    resolvability = context.get("resolvability")
    written = (raw.get("output") or {}).get("task_situation_description") or {}
    lexicon = load_resources(settings.path("resources"))
    labels = sample.input.entity_labels
    places = world_state.place_names(sample.input.environment_context, labels, lexicon)
    ctx = CheckContext(
        settings=settings,
        client=client,
        sample=sample,
        # 모델이 쓴 이름표(텍스트 보기)를 id로 되짚어 규칙 검사가 씬과 맞춰 보게 한다
        raw_tsd=world_state.resolve(written, labels, places),
        written_tsd=written,
        lexicon=lexicon,
        goal_oracle=adapter.goal_oracle(context.get("scenario") or {}),
        situation=((context.get("scenario") or {}).get("meta") or {}).get(SITUATION_KEY),
    )
    fields: dict = {
        "schema_valid": False,
        "instruction_resolvability": (
            ResolvabilityResult.model_validate(resolvability) if resolvability else None
        ),
    }

    all_passed = True
    for name in settings.verify.checks:
        check = VERIFY_CHECKS.get(name)
        outcome = check.run(ctx)
        fields[check.field] = outcome.value
        if not outcome.passed:
            all_passed = False
            fields["skipped_reason"] = f"{name} failed" + (
                f": {outcome.reason}" if outcome.reason else ""
            )
            break

    resolvability_ok = resolvability is None or resolvability.get("pass", False)
    final_pass = all_passed and resolvability_ok
    sample.meta.verification = VerificationResult(final_pass=final_pass, **fields)
    sample.meta.status = "accepted" if final_pass else "rejected"
    return dump(sample)


def _needs_review(job: Job, _error: Exception) -> dict:
    """judge 호출 실패. 샘플을 잃지 않도록 보존하되 rejected와 구분한다."""
    sample = Sample.model_validate(job.payload[0])
    sample.meta.status = "needs_review"
    return dump(sample)


@stage("verify", calls_llm=True)
def run(settings: Settings) -> dict:
    raws = list(read_jsonl(stage_output(settings, "generate")))
    if not raws:
        raise FileNotFoundError("no generated samples; run the generate stage first")
    # 재작성 단계 기록: 지시 해소 판정과, 목표 상태 정답을 찾을 원 시나리오가 들어 있다
    context_by_scenario = {
        rec["scenario_id"]: rec for rec in read_jsonl(stage_output(settings, "contextualize"))
    }
    adapter = make_adapter(settings)

    client = LLMClient(settings, settings.run_dir())
    judge_models = {m for role in (roles.CHECKLIST, roles.PROBE) for m in role.models(settings)}
    jobs = [
        Job(
            key=raw["sample_id"],
            record_id=raw["scenario_id"],
            payload=(raw, context_by_scenario.get(raw["scenario_id"]) or {}),
        )
        for raw in raws
    ]
    result = run_jobs(
        settings,
        stage="verify",
        jobs=jobs,
        work=lambda job: _verify_one(client, settings, *job.payload, adapter),
        out_path=stage_output(settings, "verify"),
        key_field="sample_id",
        workers=max_concurrency(settings, judge_models),
        done_status="verified",
        on_error=_needs_review,
    )
    log.info(f"+{result.written} verified (needs_review {result.failed}) -> {result.output}")
    return result.as_dict("needs_review")
