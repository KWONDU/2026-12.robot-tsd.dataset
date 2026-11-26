"""대시보드 화면들. 임포트 순서가 화면 순서다 (규약은 base.py).

화면 하나가 질문 하나에 답한다: 개요(결과가 어떤가), 태스크(장면마다 무엇을 받아 무엇을 냈나),
프롬프트(모델에게 무엇을 시켰나), 데이터 구조(데이터가 어떤 JSON인가), 실행 정보(어떤 설정으로 얼마 들었나).
"""

# isort: off
# 화면 순서를 정하는 임포트라 정렬하지 않는다
from tsdgen.dashboard.views import overview  # noqa: F401
from tsdgen.dashboard.views import task_list  # noqa: F401
from tsdgen.dashboard.views import prompts  # noqa: F401
from tsdgen.dashboard.views import structure  # noqa: F401
from tsdgen.dashboard.views import run_info  # noqa: F401
# isort: on
from tsdgen.dashboard.views.base import VIEWS, catalog, render  # noqa: F401
