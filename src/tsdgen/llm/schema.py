"""구조화 출력에 보내는 JSON 스키마의 모양.

strict(OpenAI structured outputs) 규칙: 모든 객체의 모든 칸이 required이고 additionalProperties는 false,
기본값(default)은 쓰지 않는다. 비워도 되는 칸은 pydantic이 이미 null을 허용하는 anyOf로 내므로, 필수로 올려도
모델은 null을 쓸 수 있다. 출력 검증은 여전히 pydantic 모델이 한다.

키가 정해지지 않은 사전(체크리스트 항목 이름 -> 점수 같은 것)은 strict 규칙으로 쓸 수 없다. 그런 스키마는
strict로 보내지 않고 안내(guided)로만 보낸다(strict_capable).
"""

from typing import Any


def strict_capable(schema: Any) -> bool:
    """키가 정해지지 않은 사전(additionalProperties가 스키마인 객체)이 하나도 없으면 strict로 보낼 수 있다."""
    if isinstance(schema, list):
        return all(strict_capable(item) for item in schema)
    if not isinstance(schema, dict):
        return True
    if isinstance(schema.get("additionalProperties"), dict):
        return False
    return all(strict_capable(value) for value in schema.values())


def strict_schema(schema: Any) -> Any:
    """스키마 전체(정의 포함)를 strict 모양으로 바꾼 사본."""
    if isinstance(schema, list):
        return [strict_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {key: strict_schema(value) for key, value in schema.items() if key != "default"}
    if "properties" in out:
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    return out
