"""실제 JSON 값을 화면용 나무로 옮기고, 키마다 스키마의 뜻을 붙인다 (데이터 구조 화면, 샘플 상세의 JSON 보기).

나무의 모양은 값이 정한다(배열은 실제 원소 수만큼). 스키마에는 있는데 값에 없는 키도 빠뜨리지 않고 '없음'으로
보여서 구조 전체가 드러나게 한다. 키 설명은 화면용 사전(schema_field_ko)이 먼저이고, 없으면 스키마의
description을 쓴다. "모델.필드" 키로 같은 이름의 필드를 모델마다 다르게 설명할 수 있다.

노드: {key, path, kind, value?, children?, desc, type, required, enum?, note?}. kind는 값의 모양(object, array,
string, number, boolean, null, absent, hidden)이고 type은 스키마가 정한 종류(string, array, enum 등)다.
"""

from dataclasses import dataclass, field
from typing import Any

ABSENT_NOTE = "이 예시에는 없는 필드예요"


@dataclass
class TreeOptions:
    labels: dict[str, str]
    hidden: dict[str, str] = field(default_factory=dict)  # 키 -> 값을 보이지 않는 이유 (모델에 안 보내는 것)


def _resolve(schema: dict, defs: dict) -> tuple[dict, str | None]:
    """$ref와 null 선택지를 걷어낸 실제 스키마, 그리고 그것이 정의(모델) 이름이면 그 이름."""
    options = [o for o in schema.get("anyOf", [schema]) if o.get("type") != "null"]
    chosen = options[0] if options else schema
    ref = chosen.get("$ref")
    if ref:
        name = ref.split("/")[-1]
        return defs.get(name, {}), name
    return chosen, None


def _schema_kind(schema: dict) -> str:
    if "enum" in schema:
        return "enum"
    return schema.get("type", "object" if "properties" in schema else "")


def _value_kind(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    return "null" if value is None else "string"


@dataclass
class _Walk:
    defs: dict
    opts: TreeOptions

    def describe(self, key: str, owner: str | None, raw: dict) -> str:
        own = self.opts.labels.get(f"{owner}.{key}") if owner else None
        described = (raw.get("description") or "").split(". ")[0]
        return own or self.opts.labels.get(key) or described

    def node(self, key: Any, path: str, raw: dict, value: Any, owner: str | None,
             required: bool) -> dict[str, Any]:
        schema, def_name = _resolve(raw, self.defs)
        node: dict[str, Any] = {"key": key, "path": path, "type": _schema_kind(schema),
                                "desc": self.describe(str(key), owner, raw), "required": required,
                                "kind": _value_kind(value)}
        if "enum" in schema:
            node["enum"] = [str(v) for v in schema["enum"]]
        if isinstance(key, str) and key in self.opts.hidden:
            return {**node, "kind": "hidden", "note": self.opts.hidden[key]}
        if isinstance(value, dict):
            node["children"] = self.members(schema, value, path, def_name)
        elif isinstance(value, list):
            item_raw = schema.get("items", {})
            item_owner = _resolve(item_raw, self.defs)[1]
            node["children"] = [self.node(i, f"{path}[{i}]", item_raw, v, item_owner, True)
                                for i, v in enumerate(value)]
        else:
            node["value"] = value
        return node

    def members(self, schema: dict, value: dict, path: str, owner: str | None) -> list[dict]:
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        required = set(schema.get("required", []))
        keys = [*props, *[k for k in value if k not in props]]
        out = []
        for key in keys:
            sub = props.get(key) or (extra if isinstance(extra, dict) else {})
            child = self.node(key, f"{path}.{key}" if path else key, sub, value.get(key), owner,
                              key in required)
            if key not in value and child["kind"] != "hidden":
                child.update(kind="absent", note=ABSENT_NOTE)
                child.pop("value", None)
            out.append(child)
        return out


def json_tree(schema: dict, value: Any, opts: TreeOptions) -> dict[str, Any]:
    """루트 스키마(모델의 model_json_schema) 하나와 같은 모양의 값 하나를 나무로."""
    walk = _Walk(schema.get("$defs", {}), opts)
    root = walk.node("", "", schema, value, None, True)
    if isinstance(value, dict):
        root["children"] = walk.members(schema, value, "", schema.get("title"))
    return root
