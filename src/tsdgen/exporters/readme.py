"""전달 패키지 README 내보내기. 문서는 이 하나로 통합한다.

파일 구성·생성 설정·리포트 URL(export.report_url) 뒤에 읽는 법과 채택 샘플
전건을 이어 붙인다. 통계·분석 해설은 함께 전달하는 Artifact 리포트가 맡는다
(docs/DECISIONS.md의 KIST 전달 zip 구성 결정).
"""

from tsdgen.exporters.base import ExportContext
from tsdgen.exporters.markdown import render_review_body
from tsdgen.registry import EXPORT_FORMATS


@EXPORT_FORMATS.register("readme")
def export_readme(ctx: ExportContext) -> None:
    settings = ctx.settings
    roles = settings.llm.roles
    judge_pairs = ", ".join(f"{g}→{j}" for g, j in roles.checklist_judge.items())
    report_line = (
        f"## 분석 리포트\n\n- {settings.export.report_url}\n\n"
        if settings.export.report_url else ""
    )
    content = ctx.template("deliverable_readme.md").format(
        date=ctx.date_str,
        report_line=report_line,
        adapter=settings.source.adapter,
        generators=", ".join(roles.generators),
        k=settings.llm.per_scenario_k,
        selection=settings.llm.selection,
        rewriter=roles.rewriter,
        judges=judge_pairs,
        probe_judge=roles.probe_judge,
        prompt_versions=", ".join(
            f"{k}={v}" for k, v in settings.prompts_versions.items()
        ),
        specificity=settings.contextualize.specificity,
    )
    (ctx.out_dir / "README.md").write_text(
        content + render_review_body(ctx), encoding="utf-8")
