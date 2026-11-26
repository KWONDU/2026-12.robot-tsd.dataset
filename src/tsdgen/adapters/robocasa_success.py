"""RoboCasa 태스크의 성공 판정(`_check_success`)에서 목표 상태 정답을 정적으로 뽑는다.

RoboCasa는 태스크 클래스마다 성공 조건을 코드로 갖고 있다(예: `check_obj_in_receptacle(self, "chicken",
"serving_tray")`). 목표 상태는 이것을 우리 어휘의 단언으로 옮긴 것이므로, 여기서 뽑은 단언은 LLM이 쓴
목표 상태를 대조할 정답이 된다. prepare 단계가 태스크 소스를 분석해 success_map.yaml을 만들고, 시나리오를
검수할 때 원본 이름(provenance)을 중립 id로 바꿔 대조한다.

성공 판정을 그대로 논리식으로 읽는다: 대입한 변수는 그 식으로 펼치고, and·or·not, `if X: return False` 같은
조건문과 return을 따라간 뒤, 성공하는 경우를 "이 조건들이 모두 성립" 묶음 몇 가지(대안)로 정리한다.
예를 들어 "레몬 둘은 한 그릇에, 브로콜리는 다른 그릇에(어느 쪽이든)"는 대안 두 가지가 된다.
확실한 것만 남긴다. 해석할 수 없는 판정(인자가 변수, 등록 안 된 호출, 반복문)과 관계의 부정은 그 대안에서
빼고, 상태 판정 앞의 `not`은 값을 뒤집는다(`not turned_on` -> turned_on=false). 어떤 대안이 비면 확실히
요구되는 것이 없으므로 정답을 내지 않는다. `if self.behavior == "open":` 분기는 태스크 클래스가 정한 behavior
값으로 미리 골라 펼친다. 로봇이 손을 떼었는지(gripper_obj_far) 같은 실행 조건은 목표 상태가 아니라 옮기지 않는다.
해석할 호출은 SUCCESS_CALLS에, 원본 상태 키를 우리 상태 키로 옮기는 표는 어댑터 자원에 있다.
"""

import ast
import copy
import re
from pathlib import Path
from typing import Any

from tsdgen.adapters.robocasa_success_calls import (
    SUCCESS_CALLS,
    _call_parts,
    _fixture_attr,
    _fixture_state,
    _literal,
)
from tsdgen.goals.success_formula import (
    INHERIT,
    UNKNOWN,
    alternatives_of,
    substitute,
)
from tsdgen.models import GoalAssertion, region_id

METHOD = "_check_success"
BEHAVIOR = "behavior"
# 가구 상태를 dict로 돌려주는 메서드. `self.fx.get_state()["turned_on"]`처럼 키로 읽는다.
STATE_ACCESSORS = {"get_state", "get_handle_state"}


def _state_dict_vars(method: ast.AST) -> dict[str, str | None]:
    """`이름 = self.fx.get_state(...)`처럼 상태 dict를 받아 둔 변수 -> 가구 속성명."""
    found = {}
    for node in ast.walk(method):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            name, receiver = _call_parts(node.value)
            target = node.targets[0]
            if name in STATE_ACCESSORS and isinstance(target, ast.Name):
                found[target.id] = receiver
    return found


def _state_receiver(node: ast.Subscript, state_vars: dict) -> tuple[bool, str | None]:
    """상태 dict 읽기라면 (True, 가구 속성명). `get_state()[키]`와 받아 둔 변수[키] 둘 다."""
    if isinstance(node.value, ast.Call):
        name, receiver = _call_parts(node.value)
        return name in STATE_ACCESSORS, receiver
    if isinstance(node.value, ast.Name) and node.value.id in state_vars:
        return True, state_vars[node.value.id]
    return False, None


def _entry_of(node: ast.AST, state_keys: dict, state_vars: dict) -> tuple[bool, dict | None]:
    """(등록된 판정인가, 정답 항목). 호출 판정과 상태 dict 읽기 둘 다 본다."""
    if isinstance(node, ast.Call):
        name, receiver = _call_parts(node)
        if name in SUCCESS_CALLS.names():
            return True, SUCCESS_CALLS.get(name)(node, receiver)
    if isinstance(node, ast.Subscript):
        is_state, receiver = _state_receiver(node, state_vars)
        if is_state:
            return True, _fixture_state(receiver, state_keys.get(_literal(node.slice) or ""))
    return False, None


def _compare(node: ast.Compare, read: Any) -> tuple:
    """`X == True`, `X is False`, `X != False` 같은 참거짓 비교만 푼다."""
    if len(node.ops) != 1 or not isinstance(node.comparators[0], ast.Constant):
        return UNKNOWN
    value, op = node.comparators[0].value, node.ops[0]
    if not isinstance(value, bool) or not isinstance(op, ast.Eq | ast.Is | ast.NotEq | ast.IsNot):
        return UNKNOWN
    keep = value == isinstance(op, ast.Eq | ast.Is)
    inner = read(node.left)
    return inner if keep else ("not", inner)


def _assigned_names(stmt: ast.stmt) -> set[str]:
    """문장 안에서 값이 바뀌는 이름 (대입, 증분 대입, 반복 변수)."""
    single = ast.AugAssign | ast.AnnAssign | ast.For
    targets = [n.target for n in ast.walk(stmt) if isinstance(n, single)]
    targets += [t for n in ast.walk(stmt) if isinstance(n, ast.Assign) for t in n.targets]
    return {n.id for target in targets for n in ast.walk(target) if isinstance(n, ast.Name)}


# 성공 여부에 영향이 없는 문장 (안쪽 함수 정의는 그 함수를 부르는 판정이 해석할 수 없는 판정이 된다)
SKIPPED = (ast.Expr, ast.Pass, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import,
           ast.ImportFrom)


class _Reader:
    """성공 판정 한 메서드를 논리식으로 읽는다. 식: atom(entry|None, line), and, or, not, const, inherit.
    대입은 실행 순서대로 따라가서 이름을 그때의 식으로 펼친다(`x = A` 다음 `x = x and B`도 된다)."""

    def __init__(self, state_keys: dict, state_vars: dict):
        self.state_keys, self.state_vars = state_keys, state_vars

    def expr(self, node: ast.AST, env: dict[str, tuple]) -> tuple:
        if isinstance(node, ast.BoolOp):
            parts = [self.expr(v, env) for v in node.values]
            return ("and" if isinstance(node.op, ast.And) else "or", parts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return ("not", self.expr(node.operand, env))
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return ("const", node.value)
        if isinstance(node, ast.Compare):
            return self._threshold(node) or _compare(node, lambda n: self.expr(n, env))
        if isinstance(node, ast.Name):
            return env.get(node.id, UNKNOWN)
        if isinstance(node, ast.Call) and _call_parts(node)[0] == METHOD:
            return INHERIT
        registered, entry = _entry_of(node, self.state_keys, self.state_vars)
        return ("atom", entry, getattr(node, "lineno", 0)) if registered else UNKNOWN

    def _threshold(self, node: ast.Compare) -> tuple | None:
        """`상태["lid"] <= 0.01`처럼 수치 상태를 문턱값과 견주는 판정. 작다 쪽이면 그 상태가 거짓(닫힘),
        크다 쪽이면 참(열림)이다. 표(state_keys)에 있는 상태 키만 옮긴다."""
        left, op = node.left, node.ops[0]
        number = isinstance(node.comparators[0], ast.Constant) and \
            isinstance(node.comparators[0].value, int | float) and \
            not isinstance(node.comparators[0].value, bool)
        if len(node.ops) != 1 or not number or not isinstance(left, ast.Subscript):
            return None
        is_state, receiver = _state_receiver(left, self.state_vars)
        key = self.state_keys.get(_literal(left.slice) or "")
        if not (is_state and key and isinstance(op, ast.Lt | ast.LtE | ast.Gt | ast.GtE)):
            return None
        entry = _fixture_state(receiver, key, isinstance(op, ast.Gt | ast.GtE))
        return ("atom", entry, node.lineno)

    def body(self, statements: list[ast.stmt], env: dict[str, tuple] | None = None) -> tuple:
        """문장 목록이 성공을 돌려주는 조건. if는 두 갈래를 각각 이어 읽는다."""
        env = dict(env or {})
        for i, stmt in enumerate(statements):
            rest = statements[i + 1:]
            if isinstance(stmt, ast.Return):
                return self.expr(stmt.value, env) if stmt.value is not None else ("const", False)
            if isinstance(stmt, ast.If):
                test = self.expr(stmt.test, env)
                return ("or", [("and", [test, self.body(stmt.body + rest, env)]),
                               ("and", [("not", test), self.body(stmt.orelse + rest, env)])])
            single = isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
            if single and isinstance(stmt.targets[0], ast.Name):
                env[stmt.targets[0].id] = self.expr(stmt.value, env)
            elif not isinstance(stmt, SKIPPED):
                if any(isinstance(n, ast.Return) for n in ast.walk(stmt)):
                    return UNKNOWN  # 안에서 돌려주는 반복문 등은 따라가지 않는다
                env.update(dict.fromkeys(_assigned_names(stmt), UNKNOWN))  # 값만 계산하는 반복문
        return ("const", False)  # 끝까지 return이 없으면 None(거짓)을 돌려준다


class _BehaviorSpecializer(ast.NodeTransformer):
    """`if self.behavior == "x":` 분기를 태스크의 behavior 값으로 골라 펼친다."""

    def __init__(self, behavior: str):
        self.behavior = behavior

    def visit_If(self, node: ast.If) -> Any:
        test = node.test
        is_behavior_test = (
            isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq) and _fixture_attr(test.left) == BEHAVIOR
            and _literal(test.comparators[0]) is not None
        )
        if not is_behavior_test:
            return self.generic_visit(node)
        chosen = node.body if _literal(test.comparators[0]) == self.behavior else node.orelse
        visited = [self.visit(stmt) for stmt in chosen]
        return [s for item in visited for s in (item if isinstance(item, list) else [item])]


def extract_method(
    method: ast.FunctionDef, state_keys: dict[str, str], behavior: str | None = None
) -> tuple:
    """성공 판정 메서드 하나의 논리식 (부모 판정을 부르는 자리는 inherit로 남는다)."""
    if behavior is not None:
        method = _BehaviorSpecializer(behavior).visit(copy.deepcopy(method))
    return _Reader(state_keys, _state_dict_vars(method)).body(method.body)


def _init_behavior(cls: ast.ClassDef) -> str | None:
    """`super().__init__(behavior="x")` 또는 클래스 속성 `behavior = "x"`로 정한 값."""
    for node in ast.walk(cls):
        if isinstance(node, ast.Call) and _call_parts(node)[0] == "__init__":
            value = next((_literal(kw.value) for kw in node.keywords if kw.arg == BEHAVIOR), None)
            if value is not None:
                return value
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == BEHAVIOR for t in node.targets
        ):
            return _literal(node.value)
    return None


def _class_table(src_dir: Path) -> dict[str, dict[str, Any]]:
    """클래스 이름 -> {bases, method, behavior, file}."""
    table: dict[str, dict[str, Any]] = {}
    for path in sorted(src_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            method = next((b for b in node.body
                           if isinstance(b, ast.FunctionDef) and b.name == METHOD), None)
            table[node.name] = {
                "bases": [b.id for b in node.bases if isinstance(b, ast.Name)],
                "method": method,
                "behavior": _init_behavior(node),
                "file": str(path.relative_to(src_dir)),
            }
    return table


def _chain(name: str, table: dict) -> list[str]:
    """태스크 클래스에서 첫 번째 부모를 따라 올라가는 이름 목록 (순환은 끊는다)."""
    chain: list[str] = []
    while name in table and name not in chain:
        chain.append(name)
        bases = table[name]["bases"]
        name = bases[0] if bases else ""
    return chain


def _resolve(task: str, table: dict, state_keys: dict[str, str]) -> dict | None:
    """태스크 하나의 정답. 성공 판정은 가장 가까운 조상 것을 쓰고, super()를 부르면 그 위 것을 이어 붙인다.
    behavior는 태스크에서 가장 가까운 클래스가 정한 값이다."""
    chain = _chain(task, table)
    behavior = next((table[c]["behavior"] for c in chain if table[c]["behavior"]), None)
    owners = [c for c in chain if table[c]["method"] is not None]
    if not owners:
        return None
    formula = None
    for owner in reversed(owners):  # 가장 먼 조상부터 읽어 자식의 inherit 자리에 넣는다
        formula = substitute(extract_method(table[owner]["method"], state_keys, behavior), formula)
    alternatives, uncertain = alternatives_of(formula)
    first = owners[0]
    return {"defined_in": f"{table[first]['file']}:{first}", "behavior": behavior,
            "alternatives": alternatives, "uncertain": uncertain}


def build_success_map(src_dir: Path, state_keys: dict[str, str]) -> dict[str, dict]:
    """태스크 클래스 이름 -> 정답 항목 (원본 이름 공간). state_keys는 원본 상태 키 -> 우리 상태 키."""
    table = _class_table(src_dir)
    resolved = {name: _resolve(name, table, state_keys) for name in sorted(table)}
    return {name: value for name, value in resolved.items() if value is not None}


def _ids_for(ref: dict, scene: dict[str, Any]) -> list[str]:
    """정답 항목의 원본 참조를 이 씬의 중립 id 후보로 바꾼다. 못 찾으면 빈 목록."""
    provenance = scene.get("provenance") or {}
    object_ids = {name: oid for oid, name in (provenance.get("source_object_names") or {}).items()}
    fixture_ids = {name: fid for fid, name in (provenance.get("source_fixture_names") or {}).items()}
    fixture_refs = provenance.get("source_fixture_refs") or {}
    if "object" in ref:
        return [object_ids[ref["object"]]] if ref["object"] in object_ids else []
    if "fixture_ref" in ref:
        source = fixture_refs.get(ref["fixture_ref"])
        if source not in fixture_ids:
            return []
        fixture = next((f for f in scene.get("fixtures", []) if f["id"] == fixture_ids[source]), {})
        wanted = ref.get("regions") or []  # 칸 종류 (끝의 번호를 뗀 칸 이름과 맞춘다)
        regions = [region_id(fixture["id"], r["key"]) for r in fixture.get("regions") or []
                   if re.sub(r"\d+$", "", r["key"]) in wanted]
        return regions or [fixture_ids[source]]
    category = ref.get("fixture_category")
    return [f["id"] for f in scene.get("fixtures", []) if f.get("category") == category]


def _resolve_alternative(entries: list[dict], scene: dict[str, Any],
                         key_aliases: dict[str, dict]) -> list[list[GoalAssertion]]:
    """대안 하나를 이 씬의 id 공간으로. 항목 하나는 후보 단언 목록(그중 하나면 맞음)이 된다.
    이 씬에서 참조를 풀 수 없는 항목은 뺀다 (원본 이름과 씬 추출이 어긋난 경우). 상태 키는 주어 가구의
    종류에서 부르는 이름으로 바꾼다(key_aliases: 가구 분류 -> {원래 키: 그 종류의 키})."""
    categories = {f["id"]: f.get("category") for f in scene.get("fixtures", [])}
    resolved = []
    for entry in entries:
        subjects = _ids_for(entry["subject"], scene)
        targets = _ids_for(entry["target"], scene) if entry["target"] else [None]
        if len(subjects) != 1 or not targets:
            continue
        renamed = key_aliases.get(categories.get(subjects[0]) or "") or {}
        predicate = renamed.get(entry["predicate"], entry["predicate"])
        resolved.append([
            GoalAssertion(kind=entry["kind"], subject=subjects[0], predicate=predicate,
                          object=target, value=entry["value"])
            for target in targets
        ])
    return resolved


def oracle_alternatives(
    task_entry: dict, scene: dict[str, Any], key_aliases: dict[str, dict] | None = None
) -> list[list[list[GoalAssertion]]]:
    """태스크 정답(success_map 한 항목)을 이 씬의 대안 목록으로. 비는 대안이 생기면 정답을 내지 않는다.
    예전 형식(assertions 하나)은 대안 하나로 읽는다."""
    raw = task_entry.get("alternatives")
    raw = raw if raw is not None else [task_entry.get("assertions") or []]
    resolved = [_resolve_alternative(entries, scene, key_aliases or {}) for entries in raw]
    return [] if not resolved or any(not alt for alt in resolved) else resolved
