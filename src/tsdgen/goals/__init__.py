"""목표 상태(task_goal.goal_state).

작업 목표를 기계가 확인할 수 있는 형태로 옮긴 것이다. 단언·판정의 종류는 kinds.py의 등록부가 정하고,
verifier.py가 씬 상태 하나에 대 만족 / 불만족 / 판단 불가를 판정하며, validation.py가 생성된 목표의
형식·접지·일관성을 검사한다. 원천의 성공 판정(정답)과의 대조는 oracle.py가 한다.
"""

from tsdgen.goals import kinds  # noqa: F401 - 임포트만으로 종류가 등록된다
