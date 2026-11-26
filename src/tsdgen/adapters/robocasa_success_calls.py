"""RoboCasa 성공 판정에 나오는 호출 하나하나를 정답 항목으로 옮기는 해석기 (robocasa_success가 쓴다).

호출 이름마다 해석기 하나를 SUCCESS_CALLS에 등록한다. 해석기는 (ast.Call, 호출 대상 가구 속성명)을 받아
정답 항목 dict를 돌려주고, 인자가 변수라 확정할 수 없으면 None을 돌려준다. 새 판정 호출은 여기 등록 하나로 끝난다.
"""

import ast

from tsdgen.goals.success_formula import FIXTURE_STATE
from tsdgen.registry import Registry

SUCCESS_CALLS = Registry("robocasa success call")
"""호출 이름 -> (ast.Call, 호출 대상 가구 속성명 | None) -> 정답 항목 dict | None (해석할 수 없으면 None)."""

ENV_ARG = "self"
RELATION = "relation"
# 냉장고 문 판정의 compartment 인자 -> 상태 키 (fridge.py, 기본은 fridge)
DOOR_KEYS = {"fridge": "door_open", "freezer": "freezer_door_open"}
DRAWER_KEY = "drawer_open"  # reg_type이 drawer인 문 판정은 그 칸(서랍)의 상태다


def _literal(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _fixture_attr(node: ast.AST | None) -> str | None:
    """`self.<속성>` 형태면 속성 이름 (태스크가 가구를 가리키는 참조 이름)."""
    is_self_attr = isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    return node.attr if is_self_attr and node.value.id == ENV_ARG else None


def _arg(call: ast.Call, position: int, keywords: tuple[str, ...]) -> ast.AST | None:
    """env(self)를 뺀 position번째 인자, 없으면 키워드 인자."""
    positional = [a for a in call.args if not (isinstance(a, ast.Name) and a.id == ENV_ARG)]
    if position < len(positional):
        return positional[position]
    return next((kw.value for kw in call.keywords if kw.arg in keywords), None)


def _relation(subject: str | None, predicate: str, target: dict | None) -> dict | None:
    if subject is None or target is None or None in target.values():
        return None
    return {"kind": RELATION, "subject": {"object": subject}, "predicate": predicate,
            "target": target, "value": True}


def _fixture_state(receiver: str | None, key: str | None, value: bool = True) -> dict | None:
    if receiver is None or key is None:
        return None
    return {"kind": FIXTURE_STATE, "subject": {"fixture_ref": receiver}, "predicate": key,
            "target": None, "value": value}


@SUCCESS_CALLS.register("check_obj_in_receptacle")
def _in_receptacle(call: ast.Call, _receiver: str | None) -> dict | None:
    obj = _literal(_arg(call, 0, ("obj_name",)))
    receptacle = _literal(_arg(call, 1, ("receptacle_name",)))
    return _relation(obj, "in", {"object": receptacle})


@SUCCESS_CALLS.register("obj_inside_of")
def _inside_of(call: ast.Call, _receiver: str | None) -> dict | None:
    obj = _literal(_arg(call, 0, ("obj_name",)))
    return _relation(obj, "in", {"fixture_ref": _fixture_attr(_arg(call, 1, ("fixture_id",)))})


@SUCCESS_CALLS.register("check_obj_fixture_contact")
def _fixture_contact(call: ast.Call, _receiver: str | None) -> dict | None:
    obj = _literal(_arg(call, 0, ("obj_name",)))
    target = {"fixture_ref": _fixture_attr(_arg(call, 1, ("fixture_name", "fixture")))}
    return _relation(obj, "on", target)


@SUCCESS_CALLS.register("check_obj_any_counter_contact")
def _any_counter(call: ast.Call, _receiver: str | None) -> dict | None:
    return _relation(_literal(_arg(call, 0, ("obj_name",))), "on", {"fixture_category": "counter"})


@SUCCESS_CALLS.register("check_rack_contact")
def _rack_contact(call: ast.Call, receiver: str | None) -> dict | None:
    """가구의 선반·랙에 닿았는지. 냉장고는 칸(compartment, 기본 냉장실)과 종류(reg_type, 기본 선반)를 고른다.
    씬에 그 가구의 칸이 있으면 그 종류의 칸으로, 없으면 가구 전체로 되짚는다(_ids_for)."""
    keywords = {kw.arg: kw.value for kw in call.keywords}
    compartment = _literal(keywords.get("compartment")) or "fridge"
    kinds = ["shelf"] if "reg_type" not in keywords else [
        k for k in ("shelf", "drawer") if k in ast.unparse(keywords["reg_type"])]
    regions = [f"{compartment}_{kind}" for kind in kinds] + ["rack"]
    target = {"fixture_ref": receiver, "regions": regions}
    return _relation(_literal(_arg(call, 0, ("obj_name",))), "in", target)


def _door_state(call: ast.Call, receiver: str | None, value: bool) -> dict | None:
    """문 열림·닫힘 판정. 냉장고는 칸(compartment, 기본 냉장실)과 종류(reg_type, 기본 문)를 고른다:
    냉동실이면 냉동실 문, 종류가 drawer면 그 칸의 서랍 상태다(씬에 서랍 칸이 없으면 가구로 되짚인다)."""
    keywords = {kw.arg: kw.value for kw in call.keywords}
    compartment = _literal(keywords.get("compartment")) or "fridge"
    entry = _fixture_state(receiver, DOOR_KEYS.get(compartment, "door_open"), value)
    if entry and "reg_type" in keywords and "drawer" in ast.unparse(keywords["reg_type"]):
        entry["subject"]["regions"] = [f"{compartment}_drawer"]
        entry["predicate"] = DRAWER_KEY
    return entry


@SUCCESS_CALLS.register("is_open")
def _is_open(call: ast.Call, receiver: str | None) -> dict | None:
    return _door_state(call, receiver, True)


@SUCCESS_CALLS.register("is_closed")
def _is_closed(call: ast.Call, receiver: str | None) -> dict | None:
    return _door_state(call, receiver, False)


@SUCCESS_CALLS.register("is_burner_on")
def _burner_on(_call: ast.Call, receiver: str | None) -> dict | None:
    return _fixture_state(receiver, "turned_on", True)


def _call_parts(call: ast.Call) -> tuple[str | None, str | None]:
    """(호출 이름, 호출 대상 가구 속성명). `OU.f(...)`면 대상 없음, `self.fx.f(...)`면 fx."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None, None
    return func.attr, _fixture_attr(func.value)
