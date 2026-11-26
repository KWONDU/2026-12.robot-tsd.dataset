"""로깅 초기화 지점.

모듈마다 핸들러를 붙이면 포맷이 갈라지고 로그가 중복되므로, 초기화는 여기 한 번만
한다. 출력은 stdout으로 보내고 루트 로거로는 전파하지 않는다. 로그 파일은 실행 쪽에서
리다이렉션으로 만든다(logs/ 아래). 이름은 tsdgen.<모듈>을 쓴다.
"""

import logging
import sys

ROOT_NAME = "tsdgen"
FORMAT = "[%(asctime)s %(name)s %(levelname)s] %(message)s"
TIME_FORMAT = "%H:%M:%S"

_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(FORMAT, datefmt=TIME_FORMAT))
        root = logging.getLogger(ROOT_NAME)
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        root.propagate = False
        _configured = True
    return logging.getLogger(name)
