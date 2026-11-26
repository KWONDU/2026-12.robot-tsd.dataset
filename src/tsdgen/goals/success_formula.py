"""성공 판정 논리식을 정답 대안으로 정리한다 (원천과 무관한 부분).

원천 어댑터가 자기 성공 판정 코드를 이 모양의 식으로 읽어 오면, 여기서 "이 조건들이 모두 성립" 묶음 몇 가지(대안)로
바꾼다. 식: ("atom", 정답 단언 | None, 줄 번호), ("and", [식]), ("or", [식]), ("not", 식), ("const", 참거짓),
("inherit",)(부모 판정 자리). 정답 단언은 {kind, subject, predicate, target, value}이다. None 원자는 해석할 수 없는
판정이라 그 대안에서 빼고, 관계의 부정도 목표 상태로 옮기지 않으므로 뺀다. 어떤 대안이 비면 확실히 요구되는 것이
없으므로 정답을 내지 않는다.
"""

FIXTURE_STATE = "state"  # 가구 상태 판정을 옮긴 단언의 종류 (models.STATE_KIND)
UNKNOWN = ("atom", None, 0)  # 해석할 수 없는 판정
INHERIT = ("inherit",)  # 부모 판정을 이어 쓰는 자리
MAX_ALTERNATIVES = 256  # 정리 전 경우가 이보다 많으면 정답을 내지 않는다 (판정이 너무 복잡하다)


def substitute(formula: tuple, parent: tuple | None) -> tuple:
    """inherit 자리에 부모 판정을 넣는다 (부모가 없으면 해석할 수 없는 판정)."""
    if formula == INHERIT:
        return parent if parent is not None else UNKNOWN
    if formula[0] in ("and", "or"):
        return (formula[0], [substitute(f, parent) for f in formula[1]])
    if formula[0] == "not":
        return ("not", substitute(formula[1], parent))
    return formula


def _cases(formula: tuple, positive: bool = True) -> list[list[tuple]] | None:
    """성공하는 경우들: 경우 하나는 (atom, 성립해야 하는가) 목록이다. 너무 많으면 None."""
    kind = formula[0]
    if kind == "atom":
        return [[(formula, positive)]]
    if kind == "const":
        return [[]] if formula[1] == positive else []
    if kind == "not":
        return _cases(formula[1], not positive)
    parts = [_cases(f, positive) for f in formula[1]]
    if any(p is None for p in parts):
        return None
    if (kind == "or") == positive:  # 갈래 중 하나
        joined = [case for part in parts for case in part]
    else:  # 갈래 모두
        joined = [[]]
        for part in parts:
            joined = [a + b for a in joined for b in part]
            if len(joined) > MAX_ALTERNATIVES:
                return None
    return joined if len(joined) <= MAX_ALTERNATIVES else None


def _fact(atom: tuple, positive: bool) -> dict | None:
    """경우의 원자 하나를 정답 단언으로. 해석할 수 없거나 관계의 부정이면 None."""
    entry = atom[1]
    if entry is None:
        return None
    if positive:
        return {**entry, "line": atom[2]}
    if entry["kind"] == FIXTURE_STATE:
        return {**entry, "value": not entry["value"], "line": atom[2]}
    return None


def _key(fact: dict) -> tuple:
    return (fact["kind"], str(fact["subject"]), fact["predicate"], str(fact["target"]))


def alternatives_of(formula: tuple) -> tuple[list[list[dict]], int]:
    """(대안 목록, 뺀 판정 수). 대안 하나라도 확실한 단언이 없으면 정답을 내지 않는다(빈 목록)."""
    cases = _cases(formula)
    if not cases:
        return [], 1
    alternatives, dropped = [], 0
    for case in cases:
        facts = {}
        for atom, positive in case:
            fact = _fact(atom, positive)
            if fact is None:
                dropped += 1
            elif _key(fact) in facts and facts[_key(fact)]["value"] != fact["value"]:
                facts = None  # 같은 사실이 참이면서 거짓: 성립할 수 없는 경우
                break
            else:
                facts[_key(fact)] = fact
        if facts is not None:
            alternatives.append(list(facts.values()))
    if not alternatives or any(not alt for alt in alternatives):
        return [], dropped
    # 다른 대안을 포함하는 대안은 뺀다 (작은 대안이 성립하면 성공이므로 큰 쪽은 정답 판정에 군더더기)
    keys = [{(*_key(f), f["value"]) for f in alt} for alt in alternatives]
    kept = [i for i in range(len(alternatives))
            if not any(j != i and keys[j] < keys[i] for j in range(len(keys)))]
    unique = {tuple(sorted(keys[i])): alternatives[i] for i in kept}
    return list(unique.values()), dropped
