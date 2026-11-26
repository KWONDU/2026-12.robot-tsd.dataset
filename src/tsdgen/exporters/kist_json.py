"""KIST 전달 패키지 내보내기 (kist_package/ 아래에 통째로 만든다).

받는 쪽 관점(입력→출력 인터페이스 확인과 예제 열람)에 필요한 것만 담는다:
사람용 README, 샘플별 JSON, 스키마 명세, 예시(shot)까지 채워 넣은 생성 프롬프트.

- 대상 샘플: export.kist_generator 모델이 생성하고 검수를 통과한 것
  (null이면 best_of_k 채택본). 단일 모델로 좁혀 전달 스토리를 단순하게 유지한다.
- id는 파일명과 동일한 순번_태스크명이다. 내부 식별자(스플릿·에피소드 조합)를
  노출하지 않는다.
- 제외: 검수 점수·모델명·선별 상태, 문장 렌더링본(내부 검수용 파생물),
  schema_version, 환경 컨텍스트의 내부 메타와 빈 값. 이 슬림화는 전달본에만
  적용되며 데이터셋 원본(jsonl)은 그대로다. 전달 프롬프트의 예시에도 같은
  기준을 적용해 패키지 안에서 표기가 일관되게 한다.
"""

import copy
import re
from typing import Any

import yaml

from tsdgen import domains, prompts, roles, world_state
from tsdgen.exporters.base import ExportContext
from tsdgen.models import SceneState, TaskSituationDescription, task_label, to_environment_context
from tsdgen.registry import EXPORT_FORMATS
from tsdgen.resources import load_resources

PACKAGE_DIR = "kist_package"
HANGUL_RE = re.compile(r"[가-힣]")
# 전달본에서 걷어내는 키. 원칙은 "모델이 실제로 받은 입력은 그대로 보여 준다"이고,
# 예외는 사용자가 명시적으로 뺀 내부 메타 둘뿐이다.
README_VIEW_PARTS = "kist_readme_views.yaml"  # 세계 상태 보기마다 다른 README 문장
INTERNAL_KEYS = {"schema_version", "state_source"}
# 협의자료가 고정한 관계 어휘(inside·on·near·held_by·in_contact)에 맞춘 이름 대응
PRED_DELIVERY = {"in": "inside"}
GOAL_RELATION = "relation"  # 관계 술어를 담는 목표 단언 종류 (goals.GOAL_KINDS 등록 이름)


def _source_ids(provenance: dict) -> dict[str, str]:
    """중립 id -> 원본 데이터셋의 실제 이름. 생성 때는 정답 누출을 막으려고
    obj_001처럼 중립화하지만, 전달본은 원본이 쓰는 이름(provenance에 보존됨)으로
    되돌린다. 치환은 레코드 직렬화 문자열 전체에 적용되어 설명문·추론 속 참조까지
    함께 바뀐다. 씬 안에서 이름이 겹치는 경우(실측 0건)만 중립 id를 유지한다."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for names in (provenance.get("source_object_names", {}),
                  provenance.get("source_fixture_names", {})):
        for neutral, source_name in names.items():
            if source_name and source_name not in used:
                mapping[neutral] = source_name
                used.add(source_name)
    return mapping


def _apply_ids(blob: str, mapping: dict[str, str]) -> str:
    for old in sorted(mapping, key=len, reverse=True):  # fx_cabinet_1이 _17을 깨지 않게
        blob = re.sub(re.escape(old) + r"(?![0-9])", mapping[old], blob)
    return blob


def _rename_preds(env: dict) -> None:
    """관계 술어를 협의자료의 고정 어휘로 맞춘다 (in → inside).

    role은 모델 입력에 없던 필드이므로 전달본에도 넣지 않는다 (지어내지 않는다).
    """
    for relation in env.get("relations", []):
        relation["pred"] = PRED_DELIVERY.get(relation.get("pred"), relation.get("pred"))
    for obj in env.get("objects", []):
        placement = obj.get("placement")
        if placement:
            placement["relation"] = PRED_DELIVERY.get(
                placement.get("relation"), placement.get("relation"))


def _rename_goal_preds(tsd: dict) -> None:
    """목표 상태의 관계 술어도 환경 컨텍스트와 같은 전달 어휘로 맞춘다."""
    cases = (tsd.get("task_goal") or {}).get("goal_state", [])  # 경우 목록 (예전 판은 단언 목록 하나)
    for assertion in (a for c in cases for a in (c if isinstance(c, list) else [c])):
        if assertion.get("kind") == GOAL_RELATION:
            predicate = assertion.get("predicate")
            assertion["predicate"] = PRED_DELIVERY.get(predicate, predicate)


def _fix_untranslated_names(env: dict, lexicon: dict) -> None:
    """추출 당시 어휘집에 없어 원문으로 남은 name_ko를 현재 어휘집으로 다시 채운다.

    원문은 물체 카테고리(saucepan)일 수도, 원본 fixture 클래스명(BlenderLid)일 수도 있으므로
    "한글이 하나도 없다"로 판정한다. 물체는 물체 사전을, fixture는 fixture 사전을 먼저 본다.
    """
    sources = {
        "objects": (lexicon["category_names_ko"], lexicon["fixture_names_ko"]),
        "fixtures": (lexicon["fixture_names_ko"], lexicon["category_names_ko"]),
    }
    for kind, (primary, secondary) in sources.items():
        for entry in env.get(kind, []):
            name = entry.get("name_ko", "")
            if not name or HANGUL_RE.search(name):
                continue
            category = entry.get("category")
            entry["name_ko"] = primary.get(category) or secondary.get(category) or name


def _prune(value: Any) -> Any:
    """내부 메타 키만 재귀적으로 걷어낸다. null·빈 dict·빈 list는 공통 형식이
    명시적으로 표기하는 값이므로 그대로 둔다 (role: null, state: {} 등)."""
    if isinstance(value, dict):
        return {k: _prune(v) for k, v in value.items() if k not in INTERNAL_KEYS}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def _delivery_schema() -> dict:
    """전달용 스키마: 모델 정의 그대로다."""
    return TaskSituationDescription.model_json_schema()


def _delivery_output(output: dict) -> dict:
    """예시 출력의 관계 술어를 전달 어휘로 맞춘 사본."""
    tsd = copy.deepcopy(output["task_situation_description"])
    _rename_goal_preds(tsd)
    return {**output, "task_situation_description": tsd}


def _delivery_prompt(ctx: ExportContext) -> str:
    """생성 프롬프트를 예시(shot)까지 채워 렌더링한다. 입력 자리 두 개만 남긴다."""
    settings = ctx.settings
    role = roles.GENERATE
    system_t, user_t = prompts.load_template(settings, role.name, role.prompt_version(settings))
    instruction, world, output = prompts.golden_parts(settings)
    if world_state.is_named(world.labels):  # 모델이 이름표만 보는 보기: 예시도 그 보기 그대로 싣는다
        system = prompts.fill(system_t, {
            **domains.prompt_values(settings),
            "GOLDEN_INSTRUCTION": instruction, "GOLDEN_CONTEXT": world.text,
            "GOLDEN_OUTPUT": prompts.as_json(_delivery_output(output)),
            "GOLDEN_EXAMPLES": prompts.examples_block(
                [(i, w, _delivery_output(o)) for i, w, o in prompts.golden_examples(settings)]),
        }, strict=False)
        return f"{system}\n{prompts.USER_MARKER}\n{user_t}\n"
    golden = prompts.golden_example(settings)
    golden_ctx = to_environment_context(SceneState.model_validate(golden["scene_state"]))
    golden_tsd = dict(golden["task_situation_description"])
    _rename_goal_preds(golden_tsd)
    golden_env = _prune(golden_ctx)
    _rename_preds(golden_env)
    system = prompts.fill(system_t, {
        **domains.prompt_values(settings),
        "GOLDEN_INSTRUCTION": golden["user_instruction"],
        "GOLDEN_CONTEXT": prompts.as_json(golden_env),
        "GOLDEN_OUTPUT": prompts.as_json({"reasoning": golden["reasoning"],
                                          "task_situation_description": golden_tsd}),
    }, strict=False)
    golden_provenance = (golden["scene_state"] or {}).get("provenance") or {}
    rendered = f"{system}\n{prompts.USER_MARKER}\n{user_t}\n"
    return _apply_ids(rendered, _source_ids(golden_provenance))


def _chosen_samples(ctx: ExportContext) -> list:
    generator = ctx.settings.export.kist_generator
    if generator is None:
        return sorted(ctx.by_status("accepted"), key=lambda s: s.scenario_id)
    chosen = [s for s in ctx.samples
              if s.meta.generator and s.meta.generator.model == generator
              and s.meta.verification and s.meta.verification.final_pass]
    if not chosen:
        models = sorted({s.meta.generator.model for s in ctx.samples if s.meta.generator})
        raise ValueError(
            f"kist_generator '{generator}'의 통과 샘플이 없다. 존재하는 모델: {models}")
    return sorted(chosen, key=lambda s: s.scenario_id)


def _verifier_models(ctx: ExportContext) -> list[str]:
    """전달 샘플을 검수한 모델. 대상 생성 모델에 짝지어진 채점 모델과 probe 모델이다."""
    settings = ctx.settings
    generator = settings.export.kist_generator
    generators = [generator] if generator else settings.llm.roles.generators
    return sorted(
        {role.model_for(settings, g) for role in (roles.CHECKLIST, roles.PROBE) for g in generators}
    )


def _delivery_record(sample: Any, record_id: str, lexicon: dict) -> dict:
    """샘플 하나의 전달용 레코드: id + 만든 방법(생성 모델, 태스크 템플릿) + 모델이 받은 입력 + 모델의 출력."""
    tsd = sample.output.task_situation_description.model_dump(mode="json", by_alias=True)
    _rename_goal_preds(tsd)
    if world_state.is_named(sample.input.entity_labels):  # 모델은 자연어 세계 상태를 받았다
        env: Any = sample.input.world_state
    else:
        env = _prune(sample.input.environment_context)
        _rename_preds(env)
        _fix_untranslated_names(env, lexicon)
    return {
        "id": record_id,
        "meta": {
            "generator_model": sample.meta.generator.model if sample.meta.generator else None,
            "task_template": sample.meta.task_template,
        },
        "input": {
            "user_instruction": sample.input.user_instruction,
            "original_instruction_en": sample.input.original_instruction_en,
            "environment_context": env,
        },
        "output": {
            "reasoning": sample.intermediate.reasoning if sample.intermediate else "",
            "task_situation_description": tsd,
        },
    }


@EXPORT_FORMATS.register("kist_json")
def export_kist_json(ctx: ExportContext) -> None:
    lexicon = load_resources(ctx.settings.path("resources"))
    chosen = _chosen_samples(ctx)
    root = ctx.out_dir / PACKAGE_DIR
    samples_dir = root / "samples"
    schema_dir = root / "schema"
    samples_dir.mkdir(parents=True, exist_ok=True)
    schema_dir.mkdir(parents=True, exist_ok=True)

    width = max(len(str(len(chosen))), 1)
    for i, sample in enumerate(chosen, start=1):
        stem = f"{i:0{width}d}_{task_label(sample.scenario_id, sample.scenario_id)}"
        record = _delivery_record(sample, stem, lexicon)
        scenario = ctx.scenario(sample.scenario_id) or {}
        provenance = (scenario.get("scene_state") or {}).get("provenance") or {}
        blob = _apply_ids(prompts.as_json(record), _source_ids(provenance))
        (samples_dir / f"{stem}.json").write_text(blob, encoding="utf-8")

    (schema_dir / "task_situation_description.schema.json").write_text(
        prompts.as_json(_delivery_schema()), encoding="utf-8")
    (root / "generation_prompt.txt").write_text(_delivery_prompt(ctx), encoding="utf-8")
    view = yaml.safe_load(ctx.template(README_VIEW_PARTS))[ctx.settings.contextualize.world_state]
    readme = ctx.template("kist_readme.md").format(
        n_samples=len(chosen),
        input_environment=view["environment"],
        source_description=ctx.settings.adapter_options(ctx.settings.source.adapter).description_ko,
        fixture_word=domains.profile(ctx.settings)["fixture_ko"],
        pointer_note=view["pointer"],
        generator=ctx.settings.export.kist_generator or "복수 모델",
        verifier=", ".join(_verifier_models(ctx)),
    )
    (root / "README.md").write_text(readme, encoding="utf-8")
