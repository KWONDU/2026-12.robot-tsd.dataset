"""prepare 스테이지: 원천별 사전 처리를 어댑터에 위임한다.

무엇이 필요한지는 데이터셋마다 다르다. RoboCasa는 원본에 없는 가구 초기 상태를
시뮬레이터 소스에서 역산해야 하고, 다른 원천은 할 일이 없을 수도 있다. 그래서
이 스테이지는 무엇을 하는지 알지 않고 어댑터에게 맡기기만 한다.
"""

from tsdgen.adapters.base import make_adapter
from tsdgen.config import Settings
from tsdgen.log import get_logger
from tsdgen.stages.common import stage

log = get_logger("tsdgen.prepare")


@stage("prepare")
def run(settings: Settings) -> dict:
    result = make_adapter(settings).prepare()
    log.info(f"{settings.source.adapter}: {result}")
    return result
