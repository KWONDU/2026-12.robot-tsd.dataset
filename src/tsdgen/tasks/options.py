"""작업 합성 설정 (config의 compose 블록)."""

from pydantic import BaseModel, ConfigDict, Field


class ComposeCfg(BaseModel):
    """작업 합성(compose 스테이지): 장면에 있는 것으로 작업 유형의 빈칸을 채워 지시와 목표 상태를 만든다."""

    model_config = ConfigDict(extra="forbid")

    types: list[str] = Field(default_factory=list)  # 쓸 작업 유형 (tasks.base.TASK_TYPES 등록 이름)
    base_split: str = "synth"  # 바탕으로 쓸 장면의 split
    per_signature: int = 1  # 같은 상황(signature)을 몇 개까지 고를지. 배경만 다른 반복을 막는다
    per_type: int = 1000  # 유형 하나에서 고르는 수의 상한 (한 유형이 수를 다 채우지 않게)
    # 고르는 방식 (tasks.compose.SELECTIONS 등록 이름). distinct는 유형 안에서 무작위, balanced는 유형 안에서
    # 난이도 단계를 고르게 채우고 덜 나온 물체 종류·장면을 먼저 고른다 (tasks/balance.py)
    selection: str = "distinct"
    pool_factor: int = 50  # balanced: 유형마다 살펴볼 후보 수 = per_type × 이 값 (섞은 순서의 앞에서)
    level_max: int = 5  # balanced: 난이도 단계의 상한 (이보다 단계가 많으면 같은 단계로 본다)
    # balanced: 한 장면에서 고르는 작업 수의 상한. 넘는 것은 남은 것이 없을 때만 고른다 (상황을 버리지는 않는다).
    # null이면 두지 않는다: 장면(씬 상태)은 겹쳐도 되고 작업과 설명문이 달라야 한다 (2026-11-15 사용자)
    scene_cap: int | None = None
    candidates: int = 4  # balanced: 상황마다 남겨 둘 장면 후보 수 (그 상황이 나온 장면들에서 무작위로, 덜 쓴 장면을 고른다)
    # 장면마다 작업을 찾는 일을 나눠 돌릴 프로세스 수. 결과는 같고 시간만 줄어든다 (1이면 한 프로세스)
    workers: int = 1


def compose_options(settings) -> ComposeCfg:
    """이 실행 도메인의 작업 합성 설정 (config의 compose.<도메인>). 도메인마다 쓸 유형과 바탕 장면이 다르다."""
    blocks = settings.compose
    if settings.domain not in blocks:
        raise ValueError(f"no compose settings for domain '{settings.domain}'; configured: {sorted(blocks)}")
    return blocks[settings.domain]
