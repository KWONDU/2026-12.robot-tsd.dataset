"""작업 유형 변주 백엔드 인터페이스 (P1, 스펙 9.3).

유형별 변주 생성은 Tier A(메타데이터 재조합)와 Tier B(시뮬레이터 reset)
두 백엔드를 가질 수 있다. P0에서는 인터페이스와 등록 지점만 둔다.

사람이 설계한 작업 유형 파일로 변주를 만드는 자리다. RoboCasa 태스크 템플릿 그대로 주방과 seed를 바꿔
reset하는 장면 합성은 여기가 아니라 어댑터의 synthesize()(robocasa_synth.py, synthesize 스테이지)에 있다.
"""

from tsdgen.config import Settings
from tsdgen.registry import VARIATION_BACKENDS


@VARIATION_BACKENDS.register("tier_a_recombine")
class TierARecombineBackend:
    def __init__(self, settings: Settings):
        self.settings = settings

    def generate_variations(self, task_type: dict, count: int) -> list[dict]:
        raise NotImplementedError("tier_a_recombine variation backend is a P1 placeholder")


@VARIATION_BACKENDS.register("tier_b_sim_reset")
class TierBSimResetBackend:
    def __init__(self, settings: Settings):
        self.settings = settings

    def generate_variations(self, task_type: dict, count: int) -> list[dict]:
        raise NotImplementedError("tier_b_sim_reset variation backend is a P3 placeholder")
