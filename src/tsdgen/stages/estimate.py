"""estimate 스테이지: 실제 호출 없이 프롬프트를 구성해 토큰·비용을 추정한다.

100건/20건 판단의 근거 도구이고, run 스테이지의 예산 가드가 이 값을 쓴다.

누가 몇 번 호출되고 어느 모델이 맡는지는 여기서 다시 세지 않는다. 역할
레지스트리의 calls_per_scenario와 model_for가 그 단일 출처이며, 통합 테스트가
실제 호출 기록과 대조해 어긋나면 실패한다. 여기서 하는 근사는 프롬프트 길이
하나뿐이다. 재작성 결과와 실제 생성물 대신 원 지시문과 골든 예시를 넣어 잰다.
근사값은 자리 표시자 이름별로 한 번만 정하고 모든 역할에 같은 표를 넘기므로,
역할이 늘어도 이 파일은 바뀌지 않는다 (새 자리 이름이 생기면 조립이 오류로 알린다).
"""

from collections import defaultdict

from tsdgen import prompts, roles, world_state
from tsdgen.config import Settings
from tsdgen.llm.client import token_cost
from tsdgen.log import get_logger
from tsdgen.models import ScenarioRecord, TaskSituationDescription, to_environment_context
from tsdgen.render import render_tsd_text
from tsdgen.resources import load_resources
from tsdgen.runs import ESTIMATE
from tsdgen.stages.common import stage, stage_output
from tsdgen.util import read_jsonl, write_json

log = get_logger("tsdgen.estimate")


def _chars(messages: list[dict]) -> int:
    return sum(len(m["content"]) for m in messages)


def _golden_tsd(settings: Settings) -> tuple[dict, str]:
    golden = prompts.golden_example(settings)
    tsd = TaskSituationDescription.model_validate(golden["task_situation_description"])
    lexicon = load_resources(settings.path("resources"))
    return golden["task_situation_description"], render_tsd_text(tsd, lexicon)


def _representative_values(
    settings: Settings, record: ScenarioRecord, golden_tsd: dict, golden_text: str
) -> dict[str, str]:
    """프롬프트 자리별 근사값. 실제 호출 전이라 재작성문·생성물 자리에 대체물을 넣는다.
    세계 상태는 실제로 모델이 받을 보기 그대로 만든다 (보기에 따라 길이가 크게 다르다)."""
    original = record.source.original_instruction_en
    lexicon = load_resources(settings.path("resources"))
    world = world_state.render(settings, to_environment_context(record.scene_state), lexicon)
    return {
        "ORIGINAL_INSTRUCTION": original,
        "REWRITTEN_INSTRUCTION": original,  # 재작성문 대신 원문
        "USER_INSTRUCTION": original,
        "ENVIRONMENT_CONTEXT": world.text,
        "TSD_JSON": prompts.as_json(golden_tsd),  # 생성물 대신 골든 예시
        "TSD_TEXT": golden_text,
    }


def calls_per_scenario(settings: Settings) -> int:
    """등록된 역할들이 시나리오 한 건에 대해 만드는 총 호출 수."""
    return sum(r.calls_per_scenario(settings) for r in roles.all_roles())


def build_estimate(settings: Settings) -> dict:
    rows = read_jsonl(stage_output(settings, "sample"))
    records = [ScenarioRecord.model_validate(r) for r in rows]
    if not records:
        raise FileNotFoundError("no sampled scenarios; run ingest and sample stages first")

    chars_per_token = settings.estimate.chars_per_token
    per_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"input": 0.0, "output": 0.0, "calls": 0}
    )
    golden_tsd, golden_text = _golden_tsd(settings)
    sample = settings.estimate.sample_records
    step = max(1, len(records) // sample) if sample else 1
    measured = records[::step]  # 고르게 띄엄띄엄 (전부 세면 장면 2만 개에 프롬프트 10만 개를 만든다)
    scale = len(records) / len(measured)
    for record in measured:
        values = _representative_values(settings, record, golden_tsd, golden_text)
        for role in roles.all_roles():
            in_chars = _chars(prompts.build_messages(settings, role, values))
            for slot in range(role.calls_per_scenario(settings)):
                model = role.model_for(settings, roles.generator_for_slot(settings, slot))
                bucket = per_model[model]
                bucket["input"] += in_chars / chars_per_token * scale
                bucket["output"] += role.assumed_output_tokens(settings) * scale
                bucket["calls"] += scale

    total_cost: float | None = 0.0
    details = {}
    for model, tokens in sorted(per_model.items()):
        share = (settings.estimate.prices_per_1m_tokens.get(model) or {}).get("expected_cached_share") or 0
        cost = token_cost(settings, model, tokens["input"], tokens["output"], tokens["input"] * share)
        if cost is None:
            total_cost = None
        elif total_cost is not None:
            total_cost += cost
        details[model] = {
            "calls": int(tokens["calls"]),
            "input_tokens": int(tokens["input"]),
            "output_tokens": int(tokens["output"]),
            "cost_usd": round(cost, 4) if cost is not None else None,
        }

    return {
        "n_scenarios": len(records),
        "per_scenario_k": settings.llm.per_scenario_k,
        "calls_per_scenario": calls_per_scenario(settings),
        "total_calls": sum(d["calls"] for d in details.values()),
        "per_model": details,
        "total_cost_usd": round(total_cost, 4) if total_cost is not None else None,
        "budget_usd": settings.estimate.budget_usd,
        "note": "재작성 결과 대신 원 지시문으로 근사한 추정치",
    }


def print_estimate(estimate: dict) -> None:
    log.info(f"scenarios={estimate['n_scenarios']} "
          f"calls/scenario={estimate['calls_per_scenario']} total={estimate['total_calls']}")
    for model, detail in estimate["per_model"].items():
        cost = detail["cost_usd"] if detail["cost_usd"] is not None else "unknown (단가표 미기입)"
        log.info(f"  {model:>18}: {detail['calls']:5d} calls, "
              f"in {detail['input_tokens']:,} tok, out {detail['output_tokens']:,} tok, "
              f"cost {cost}")
    total = estimate["total_cost_usd"]
    log.info(f"  total cost: {total if total is not None else 'unknown (단가표 미기입)'} USD "
          f"(budget: {estimate['budget_usd']})")


@stage("estimate")
def run(settings: Settings) -> dict:
    estimate = build_estimate(settings)
    write_json(settings.run_dir() / ESTIMATE, estimate)
    print_estimate(estimate)
    return estimate
