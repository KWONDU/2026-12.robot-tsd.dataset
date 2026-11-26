"""대시보드 표기 규칙 한 곳. 화면 모듈(views)과 진행 계산(progress)이 같이 쓰므로 views 밖에 둔다."""


def term(ko: str, en: str) -> str:
    """중요한 어휘는 영어 원어를 같이 적는다: "채택 (accepted)"."""
    return f"{ko} ({en})"
