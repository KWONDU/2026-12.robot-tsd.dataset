"""schemas 스테이지: 정규 모델에서 JSON Schema 파일을 schema/에 자동 생성."""

from tsdgen.config import Settings
from tsdgen.log import get_logger
from tsdgen.models import (
    Sample,
    ScenarioRecord,
    SceneState,
    TaskSituationDescription,
    VerificationResult,
)
from tsdgen.stages.common import stage
from tsdgen.util import write_json

log = get_logger("tsdgen.schemas")

_EXPORTED_MODELS = [SceneState, ScenarioRecord, TaskSituationDescription, Sample,
                    VerificationResult]


@stage("schemas")
def run(settings: Settings) -> dict:
    out_dir = settings.path("schema_dir")
    written = []
    for model in _EXPORTED_MODELS:
        schema = model.model_json_schema(by_alias=True)
        path = out_dir / f"{model.__name__}.schema.json"
        write_json(path, schema)
        written.append(path.name)
    log.info(f"{written} -> {out_dir}")
    return {"written": written, "output": str(out_dir)}
