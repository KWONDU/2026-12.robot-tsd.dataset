"""엔트리포인트: TSDGEN_STAGE=<stage> python -m tsdgen

argparse를 쓰지 않는다. 실행 대상 선택은 환경변수 하나로만 하고,
그 외 모든 설정은 config/config.yaml에서 온다.
"""

from tsdgen import registry
from tsdgen.config import current_stage, load_settings


def main() -> None:
    registry.load_all()
    stage_name = current_stage()
    stage = registry.STAGES.get(stage_name)
    settings = load_settings()
    stage(settings)


if __name__ == "__main__":
    main()
