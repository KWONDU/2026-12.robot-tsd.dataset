"""구조화 출력 스키마: strict 모양은 모든 칸을 필수로, 추가 칸을 막고, 비워도 되는 칸은 null로 받는다."""

from tsdgen import roles
from tsdgen.llm.client import json_schema_of
from tsdgen.llm.schema import strict_capable, strict_schema


def _objects(node):
    if isinstance(node, dict):
        if "properties" in node:
            yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from _objects(value)


def test_strict_schema_requires_every_field_and_allows_null():
    schema = strict_schema(json_schema_of(roles.GENERATE.output_model))
    objects = list(_objects(schema))
    assert objects and all(set(o["required"]) == set(o["properties"]) for o in objects)
    assert all(o["additionalProperties"] is False for o in objects)
    assert "default" not in str(schema)
    entry = schema["$defs"]["TSDAuxiliaryObject"]["properties"]["location"]
    assert {"type": "null"} in entry["anyOf"]  # 위치가 없으면 null로 비운다


def test_open_maps_stay_guided():
    """체크리스트 출력(항목 이름 -> 점수)은 키가 열린 사전이라 strict로 보내지 않는다. 생성 출력은 strict로 보낸다."""
    assert strict_capable(json_schema_of(roles.GENERATE.output_model))
    assert not strict_capable(json_schema_of(roles.CHECKLIST.output_model))
