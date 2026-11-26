"""dashboard 스테이지: 로컬 대시보드 서버를 띄운다.

TSDGEN_STAGE=dashboard 로 실행하며, 주소·포트·LLM 모델은 config의 dashboard
섹션이 정한다. 다른 스테이지와 달리 끝나지 않고 Ctrl+C까지 서비스한다.
"""

from tsdgen.config import Settings
from tsdgen.dashboard.server import serve
from tsdgen.stages.common import stage


@stage("dashboard")
def run(settings: Settings) -> dict:
    serve(settings)
    return {"stopped": True}
