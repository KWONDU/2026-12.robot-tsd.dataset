"""샘플 레코드에서 자주 꺼내는 값. 화면 모듈(views)과 판정 모듈(verdicts)이 같이 쓰므로 views 밖에 둔다."""

from tsdgen.models import task_label


def meta_of(sample: dict) -> dict:
    return sample.get("meta") or {}


def verification(sample: dict) -> dict:
    return meta_of(sample).get("verification") or {}


def status_of(sample: dict) -> str:
    return meta_of(sample).get("status", "?")


def model_of(sample: dict) -> str:
    return (meta_of(sample).get("generator") or {}).get("model", "?")


def task_of(sample: dict) -> str:
    return task_label(sample.get("scenario_id", ""))


def sid(sample: dict) -> str:
    return sample.get("sample_id", "?")
