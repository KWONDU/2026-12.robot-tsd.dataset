"""KIST 검토용 Markdown 내보내기 (스펙 6.6).

샘플마다 유저 입력 / 환경 컨텍스트 요약 표 / 추론 과정 / TSD 표(4필드) /
검수 결과 요약을 담고, 상단에 스키마 설명과 읽는 법을 둔다.
"""

from tsdgen import world_state
from tsdgen.exporters.base import ExportContext
from tsdgen.goals.kinds import entity_label
from tsdgen.goals.verifier import render_goal_lines
from tsdgen.models import Sample
from tsdgen.registry import EXPORT_FORMATS
from tsdgen.render import space_text
from tsdgen.resources import load_resources


def _env_summary(sample: Sample, lexicon: dict) -> list[str]:
    env = sample.input.environment_context
    lines = ["| 구분 | id | 명칭 | 위치/상태 |", "|---|---|---|---|"]
    fixtures = {f["id"]: f for f in env.get("fixtures", [])}
    for obj in env.get("objects", []):
        placement = obj.get("placement")
        loc = ""
        if placement:
            fx = fixtures.get(placement["fixture_id"], {})
            rel = lexicon["relation_ko"].get(placement["relation"], placement["relation"])
            loc = f"{fx.get('name_ko', placement['fixture_id'])} {rel}"
        lines.append(f"| 물체 | {obj['id']} | {obj['name_ko']} | {loc} |")
    for fx in env.get("fixtures", []):
        states = ", ".join(f"{k}={v}" for k, v in (fx.get("states") or {}).items()) or "-"
        loc = fx.get("location_desc") or "-"
        lines.append(f"| fixture | {fx['id']} | {fx['name_ko']} | {loc} / {states} |")
    return lines


def _tsd_table(sample: Sample, lexicon: dict) -> list[str]:
    tsd = sample.output.task_situation_description
    st = tsd.spatiotemporal_conditions
    env = sample.input.environment_context
    named = world_state.is_named(sample.input.entity_labels)  # 이름으로 가리키는 보기면 이름 사전이 필요 없다
    names = {} if named else {e["id"]: e.get("name_ko", e["id"])
                              for e in [*env.get("objects", []), *env.get("fixtures", [])]}
    goal_lines = render_goal_lines(tsd.task_goal, names, lexicon)
    lines = ["| 필드 | 내용 |", "|---|---|"]
    lines.append(f"| 작업 목표 | {tsd.task_goal.summary} |")
    lines.append(f"| 목표 상태 | {'; '.join(goal_lines) or '없음'} |")
    lines.append(f"| 시간·공간 조건 | 시간: {st.time or '없음'} / 공간: {space_text(st.space) or '없음'} |")
    parts = []
    for label, entries in (
        ("필수", tsd.target_objects.required),
        ("핵심 수단", tsd.target_objects.key_means),
        ("보조", tsd.target_objects.auxiliary),
    ):
        for e in entries:
            details = [d for d in (e.location, getattr(e, "state", None)) if d]
            name = entity_label(e.ref, names)
            parts.append(f"[{label}] {name}" + (f"({', '.join(details)})" if details else ""))
    lines.append(f"| 대상 물체 | {'; '.join(parts)} |")
    constraints = "; ".join(f"[{c.type}] {c.description}" for c in tsd.constraints) or "없음"
    lines.append(f"| 제약 사항 | {constraints} |")
    return lines


def _verification_summary(sample: Sample, score_max: int) -> str:
    verification = sample.meta.verification
    if verification is None:
        return "검수 결과 없음"
    parts = [f"schema {'PASS' if verification.schema_valid else 'FAIL'}"]
    if verification.grounding:
        parts.append(f"접지 {'PASS' if verification.grounding.pass_ else 'FAIL'}")
    if verification.structure:
        parts.append(f"구조 {'PASS' if verification.structure.pass_ else 'FAIL'}")
    if verification.checklist:
        max_total = score_max * len(verification.checklist.items)
        parts.append(f"체크리스트 {verification.checklist.total}/{max_total}")
    if verification.sufficiency_probe:
        probe = verification.sufficiency_probe
        missing = f" (누락: {', '.join(probe.missing_info)})" if probe.missing_info else ""
        parts.append(f"probe {probe.score}/{score_max}{missing}")
    return ", ".join(parts)


def render_review_body(ctx: ExportContext) -> str:
    """읽는 법 + 채택 샘플 전건. README 통합본과 단독 md가 공유하는 본문이다."""
    lexicon = load_resources(ctx.settings.path("resources"))
    lines = [ctx.template("review_header.md").format(date=ctx.date_str)]
    for i, sample in enumerate(ctx.by_status("accepted"), start=1):
        lines.append(f"## 샘플 {i}: `{sample.sample_id}`\n")
        lines.append(f"**① 유저 입력**: {sample.input.user_instruction}\n")
        lines.append("**② 환경 컨텍스트 요약**\n")
        lines.extend(_env_summary(sample, lexicon))
        lines.append("")
        # 추론 과정 원문은 jsonl의 intermediate.reasoning에 있다. 문서에는 중복해 싣지 않는다.
        lines.append("**③ 작업상황설명문(TSD)**\n")
        lines.extend(_tsd_table(sample, lexicon))
        lines.append("")
        summary = _verification_summary(sample, ctx.settings.verify.score_max)
        lines.append(f"**④ 검수 결과**: {summary}\n")
        lines.append("---\n")
    return "\n".join(lines)


@EXPORT_FORMATS.register("md")
def export_markdown(ctx: ExportContext) -> None:
    """단독 검토 문서. README 통합 구성에서는 쓰지 않는다 (formats에서 md 제외)."""
    out_path = ctx.out_dir / f"review_{ctx.date_str}.md"
    out_path.write_text(render_review_body(ctx), encoding="utf-8")
