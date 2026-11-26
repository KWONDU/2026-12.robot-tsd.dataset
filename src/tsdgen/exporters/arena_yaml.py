"""Arena 환경 스펙 내보내기.

우리가 만든 시나리오를 Arena에서 빌드·실행할 수 있게 env spec YAML로 내보낸다.
에이로봇이 요구하는 수준은 물리 정밀도가 아니라 "물체를 두면 팔이 드는 동작이
성공하는가"이므로, 배치가 성립하는 스펙을 만드는 것이 목표다.

두 표현의 표현력이 달라 일부 항목은 옮겨지지 않는다. 무엇이 빠졌는지는
arena_unmapped.json에 시나리오별로 남기고, 요약을 로그로 알린다. 조용히 버리지 않는다.
"""

from tsdgen.adapters import arena_convert
from tsdgen.exporters.base import ExportContext
from tsdgen.log import get_logger
from tsdgen.models import SceneState
from tsdgen.registry import EXPORT_FORMATS
from tsdgen.resources import load_arena_mapping
from tsdgen.util import write_json

log = get_logger("tsdgen.export.arena")

OUT_DIR = "arena_specs"


@EXPORT_FORMATS.register("arena_yaml")
def export_arena_yaml(ctx: ExportContext) -> None:
    mapping = load_arena_mapping(ctx.settings.path("resources"))
    out_dir = ctx.out_dir / OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, dict] = {}
    written = 0
    for sample in ctx.by_status("accepted"):
        scenario = ctx.scenario(sample.scenario_id)
        if scenario is None:
            continue
        scene = SceneState.model_validate(scenario["scene_state"])
        env_name = sample.scenario_id.replace("/", "_")
        env_spec, unmapped = arena_convert.spec_from_scene(
            scene, mapping, description=sample.input.user_instruction, env_name=env_name
        )
        (out_dir / f"{env_name}.yaml").write_text(env_spec.to_yaml(), encoding="utf-8")
        written += 1
        report[sample.scenario_id] = {
            "capability": arena_convert.capability_report(scene, mapping),
            "unmapped": unmapped,
        }

    write_json(ctx.out_dir / "arena_unmapped.json", report)
    if report:
        totals = [r["capability"] for r in report.values()]
        obj_ok = sum(t["objects_mappable"] for t in totals)
        obj_all = sum(t["objects_total"] for t in totals)
        fx_ok = sum(t["fixtures_mappable"] for t in totals)
        fx_all = sum(t["fixtures_total"] for t in totals)
        log.info(
            f"{written} env specs -> {out_dir} | 물체 {obj_ok}/{obj_all} · "
            f"fixture {fx_ok}/{fx_all} 대응 (나머지는 arena_unmapped.json)"
        )
