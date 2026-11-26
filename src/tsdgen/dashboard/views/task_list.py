"""태스크 화면 자리. 장면 목록과 상세는 프런트엔드가 태스크 API(/api/runs/<실행>/tasks)로 직접 그린다.

분석 블록은 없고, 화면 목록에서 이 자리(개요 다음)에 보이게 하려고 등록만 한다.
"""

from tsdgen.dashboard.views.base import view

tasks = view("tasks", "태스크")
