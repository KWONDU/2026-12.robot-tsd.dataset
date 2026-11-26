"""데이터 구조 화면: 샘플 한 건과 그 안의 입력·출력·검수 결과가 어떤 JSON인지 접고 펴는 나무로 보여 준다.

나무의 키에 마우스를 올리거나 키를 고르면 그 필드의 뜻·종류·필수 여부·허용 값이 보인다. 키 설명은 코드의
스키마(pydantic 모델)와 화면용 사전에서, 체크리스트 항목의 기준은 이 실행의 채점 프롬프트에서 읽는다.

값은 모두 이 실행의 채택 샘플 한 건의 기록 그대로다(입력은 모델이 받은 지시와 세계 상태, 출력은 설명문).
"""

from tsdgen.dashboard import rubric
from tsdgen.dashboard.data import RunData
from tsdgen.dashboard.json_tree import TreeOptions, json_tree
from tsdgen.dashboard.verdicts import ACCEPTED
from tsdgen.dashboard.views.base import Block, sid, status_of, term, view
from tsdgen.models import Sample, SampleInput, TaskSituationDescription, VerificationResult

structure = view("structure", "데이터 구조")

def _example(data: RunData) -> dict:
    samples = data.samples()
    accepted = [s for s in samples if status_of(s) == ACCEPTED]
    return (accepted or samples or [{}])[0]


def _labels(data: RunData) -> dict[str, str]:
    return data.lexicon().get("schema_field_ko", {})


def _doc(key: str, title: str, source: str, tree: dict, expand: int, note: str, **extra) -> dict:
    return {"key": key, "title": title, "source": source, "tree": tree, "expand": expand,
            "note": note, **extra}


def _sample_doc(data: RunData) -> dict | None:
    sample = _example(data)
    if not sample:
        return None
    tree = json_tree(Sample.model_json_schema(), sample, TreeOptions(_labels(data)))
    return _doc("sample", "샘플 한 건", f"이 실행의 채택 샘플 {sid(sample)}", tree, 1,
                "모델은 input을 받아 intermediate(추론)와 output(설명문)을 내요. meta는 학습 데이터가 아니라 생성·검수 기록이에요.")


def _input_doc(data: RunData) -> dict | None:
    sample = _example(data)
    inp = sample.get("input")
    if not inp:
        return None
    tree = json_tree(SampleInput.model_json_schema(), inp, TreeOptions(_labels(data)))
    return _doc("input", term("입력", "input"), f"이 실행의 채택 샘플 {sid(sample)}", tree, 1, "")


def _output_doc(data: RunData) -> dict | None:
    sample = _example(data)
    tsd = (sample.get("output") or {}).get("task_situation_description")
    if not tsd:
        return None
    tree = json_tree(TaskSituationDescription.model_json_schema(), tsd, TreeOptions(_labels(data)))
    return _doc("output", term("출력: 작업상황설명문", "task_situation_description"),
                f"이 실행의 채택 샘플 {sid(sample)}", tree, 2, "")


def _verification_doc(data: RunData) -> dict | None:
    sample = _example(data)
    result = (sample.get("meta") or {}).get("verification")
    if not result:
        return None
    names, criteria = data.lexicon()["checklist_items"], rubric.checklist_criteria(data)
    labels = {**_labels(data),
              **{k: f"{n}. 채점 기준: {criteria.get(k, '-')}" for k, n in names.items()}}
    tree = json_tree(VerificationResult.model_json_schema(), result, TreeOptions(labels))
    return _doc("verification", term("검수 결과", "verification"),
                f"이 실행의 채택 샘플 {sid(sample)}", tree, 2,
                "검수 항목마다 결과가 한 칸씩 있어요. checklist의 items를 펴서 항목 키에 마우스를 올리면 채점 기준이 나와요.",
                rubric=rubric.checklist_summary(data))


@structure
def documents(data: RunData) -> Block:
    docs = [_sample_doc(data), _input_doc(data), _output_doc(data), _verification_doc(data)]
    return {"kind": "json", "label": "데이터 구조",
            "note": "위에서 문서를 고르세요. ▸를 누르면 펼쳐지고, 키에 마우스를 올리거나 키를 누르면 오른쪽에 그 필드의 설명이 나와요.",
            "data": {"docs": [d for d in docs if d]}}
