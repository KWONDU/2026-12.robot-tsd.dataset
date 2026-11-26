"""prefetch 스테이지: 설정된 소스들의 원천 데이터를 캐시에 미리 확보한다.

ingest는 필요한 태스크를 그때그때 받지만, 네트워크가 없는 환경이나 대량
실행을 앞두고 미리 받아 두고 싶을 때 이 스테이지를 쓴다. 이미 받은 항목은
건너뛰므로 중단 후 다시 실행해도 된다.
"""

from tsdgen.adapters.base import make_adapter
from tsdgen.config import Settings
from tsdgen.log import get_logger
from tsdgen.stages.common import stage

log = get_logger("tsdgen.prefetch")


@stage("prefetch")
def run(settings: Settings) -> dict:
    results = {
        name: make_adapter(settings, name).prefetch() for name in settings.source.prefetch_sources
    }
    log.info(f"done: {list(results)}")
    return results
