"""fetch_videos 스테이지: 선별된 시나리오의 시연 영상만 캐시에 확보한다.

대시보드 샘플 상세의 열람용이며 LLM 입력이 아니므로 pipeline_order에 넣지 않는다.
필요할 때 TSDGEN_STAGE=fetch_videos 로 따로 돌린다. tar 구조상 태스크당 앞 구간
수십에서 수백 MB를 흘려보내야 하므로 (docs/RECON_ROBOCASA365.md) 수신량이 크다.
"""

from tsdgen.adapters.base import make_adapter
from tsdgen.config import Settings
from tsdgen.log import get_logger
from tsdgen.models import ScenarioRecord
from tsdgen.stages.common import stage, stage_output
from tsdgen.util import read_jsonl

log = get_logger("tsdgen.fetch_videos")


@stage("fetch_videos")
def run(settings: Settings) -> dict:
    in_path = stage_output(settings, "sample")
    if not in_path.exists():
        raise FileNotFoundError("no sampled scenarios; run the sample stage first")
    records = [ScenarioRecord.model_validate(r) for r in read_jsonl(in_path)]
    result = make_adapter(settings).fetch_videos(records)
    log.info(f"videos for {len(records)} scenarios: {result}")
    return result
