"""실행 하나의 점검표: 설명문 필드가 실제로 어떻게 채워졌는지를 수치로 낸다 (LLM 불필요).

대량 생성 전에 구조·프롬프트를 고칠 때, 고치기 전후를 같은 눈금으로 비교하려는 것이다. 보는 것:
수율(판정, 처음 걸린 검사, 지시 해소), 목표 표현력(빈 목표 상태, 자유 문장으로 뺀 완료 기준, 경우 수),
칸 사이 겹침(시간 칸의 순서 서술, 공간 칸, 이름 중복), 생성 모델 사이 일치(같은 장면에서 고른 물체·역할·목표 상태),
정답 대조(재현율, 놓친 조건의 종류), LLM 채점이 정답 대조와 맞는 정도.
레코드는 dict로 읽는다(예전 판의 실행도 같은 눈금으로 재려고).
"""

import re
from collections import Counter, defaultdict
from statistics import mean
from typing import Any

from tsdgen import world_state

ROLES = ("required", "key_means", "auxiliary")
ORDER_WORDS = re.compile(r"다음|뒤에|후에|한 뒤|먼저|전에|이후|선행|마친")
SUPPORT = {"on", "in"}  # 위·안은 같은 받침 관계로 본다
REPEATED_SHARE = 0.25  # 템플릿의 설명문 가운데 서로 다른 것이 이 비율 이하면 대부분 반복으로 센다


def _tsd(record: dict) -> dict:
    return (record.get("output") or {}).get("task_situation_description") or {}


def _pointed(record: dict) -> dict:
    """설명문의 이름을 가리킨 대상(id)으로 바꾼 것. 같은 대상을 '싱크대'와 '싱크대(아일랜드)'로 달리 적은 것을
    다른 물체로 세지 않으려는 것이다. 이름표가 없는 예전 실행(id로 쓴 설명문)은 그대로다."""
    inp = record.get("input") or {}
    labels = inp.get("entity_labels") or {}
    if not labels:
        return _tsd(record)
    # 위치 설명으로 가리킨 이름('아일랜드 식탁')도 되짚는다. 칸 이름 사전은 여기서 필요 없어 비운다
    places = world_state.place_names(inp.get("environment_context") or {}, labels, {})
    return world_state.resolve(_tsd(record), labels, places)


def _model(record: dict) -> str:
    return ((record.get("meta") or {}).get("generator") or {}).get("model", "?")


def _verification(record: dict) -> dict:
    return (record.get("meta") or {}).get("verification") or {}


def goal_cases(tsd: dict) -> list[list[dict]]:
    """목표 상태를 경우 목록으로 (2.2까지의 평평한 목록은 경우 하나)."""
    goal = tsd.get("task_goal")
    cases = goal.get("goal_state") or [] if isinstance(goal, dict) else []
    return cases if not cases or isinstance(cases[0], list) else [cases]


def _entries(tsd: dict) -> list[tuple[str, dict]]:
    targets = tsd.get("target_objects") or {}
    return [(role, e) for role in ROLES for e in targets.get(role) or []]


def _conditions(tsd: dict) -> list[dict]:
    goal = tsd.get("task_goal")
    return goal.get("completion_conditions") or [] if isinstance(goal, dict) else []


def _share(count: int, total: int) -> float | None:
    return round(count / total, 3) if total else None


def field_stats(records: list[dict]) -> dict[str, Any]:
    """필드가 채워진 모양: 빈 목표, 자유 문장 완료 기준, 칸 사이 겹침."""
    n, c, cases, kinds = len(records), Counter(), Counter(), Counter()
    for record in records:
        tsd = _tsd(record)
        goal = goal_cases(tsd)
        cases[len(goal)] += 1
        conds = {x.get("kind") for x in _conditions(tsd)}
        kinds.update(x.get("kind") for x in _conditions(tsd))
        c["goal_empty"] += not goal
        c["unresolved"] += "unresolved" in conds
        c["negated_relation"] += any(a.get("kind") == "relation" and a.get("value") is False
                                     for case in goal for a in case)
        st = tsd.get("spatiotemporal_conditions") or {}
        c["time"] += bool(st.get("time"))
        c["time_is_ordering"] += bool(st.get("time")) and bool(ORDER_WORDS.search(st["time"]))
        space = st.get("space")  # 3.1까지는 문장, 3.2부터 {start, end}
        c["space"] += bool(any(space.values()) if isinstance(space, dict) else space)
        entries = _entries(tsd)
        c["entries"] += len(entries)
        c["name_repeats_ref"] += sum(1 for _r, e in entries if e.get("name_ko") == e.get("ref"))
        c["constraints"] += len(tsd.get("constraints") or [])
        c["reasoning_chars"] += len((record.get("intermediate") or {}).get("reasoning") or "")
    return {
        "samples": n,
        "goal_state_empty": _share(c["goal_empty"], n),
        "has_unresolved_condition": _share(c["unresolved"], n),
        "has_negated_relation": _share(c["negated_relation"], n),
        "goal_cases": dict(sorted(cases.items())),
        "completion_kinds": dict(kinds),
        "time_filled": _share(c["time"], n),
        "time_is_ordering": _share(c["time_is_ordering"], c["time"]),
        "space_filled": _share(c["space"], n),
        "name_repeats_ref": _share(c["name_repeats_ref"], c["entries"]),
        "constraints_per_sample": round(c["constraints"] / n, 2) if n else None,
        "reasoning_chars_mean": c["reasoning_chars"] // n if n else None,
    }


def _roles(tsd: dict) -> dict[str, str]:
    return {e.get("ref"): role for role, e in _entries(tsd)}


def _canonical_goal(tsd: dict) -> frozenset:
    def fact(a: dict) -> tuple:
        predicate = "support" if a.get("predicate") in SUPPORT else a.get("predicate")
        return (a.get("kind"), a.get("subject"), predicate, a.get("object"), a.get("value"))
    return frozenset(frozenset(fact(a) for a in case) for case in goal_cases(tsd))


def agreement(records: list[dict]) -> dict[str, Any] | None:
    """같은 장면을 두 생성 모델이 얼마나 같게 썼는지. 생성 모델이 둘이 아니면 None."""
    by_scene: dict[str, dict[str, dict]] = defaultdict(dict)
    for record in records:
        by_scene[record.get("scenario_id")][_model(record)] = _pointed(record)
    pairs = [list(p.values()) for p in by_scene.values() if len(p) == 2]
    if not pairs:
        return None
    c, flips = Counter(), Counter()
    for a, b in pairs:
        ra, rb = _roles(a), _roles(b)
        shared = set(ra) & set(rb)
        c["same_entities"] += set(ra) == set(rb)
        c["same_roles"] += set(ra) == set(rb) and all(ra[k] == rb[k] for k in shared)
        c["same_required"] += ({k for k, v in ra.items() if v == ROLES[0]}
                               == {k for k, v in rb.items() if v == ROLES[0]})
        c["same_goal"] += _canonical_goal(a) == _canonical_goal(b)
        flips.update("/".join(sorted((ra[k], rb[k]))) for k in shared if ra[k] != rb[k])
    n = len(pairs)
    return {"scenes": n, "same_entities": _share(c["same_entities"], n),
            "same_entities_and_roles": _share(c["same_roles"], n),
            "same_required_set": _share(c["same_required"], n),
            "same_goal_state": _share(c["same_goal"], n), "role_flips": dict(flips)}


def oracle_stats(records: list[dict]) -> dict[str, Any]:
    """정답 대조: 재현율과 놓친 조건의 종류, 그리고 LLM 채점(목표 반영 점수)과의 관계."""
    recalls, missed, counted, judge = [], Counter(), Counter(), Counter()
    for record in records:
        v = _verification(record)
        oracle = v.get("goal_oracle") or {}
        if not oracle.get("available") or oracle.get("recall") is None:
            continue
        recalls.append(oracle["recall"])
        held = oracle.get("already_holds") or [False] * len(oracle["oracle"])
        for item, hit, holds in zip(oracle["oracle"], oracle["matched"], held, strict=False):
            first = item["any_of"][0]
            key = f'{first["kind"]}:{first["predicate"]}'
            counted[key] += not holds
            missed[key] += (not holds) and (not hit)
        score = ((v.get("checklist") or {}).get("items") or {}).get("goal_reflected", {}).get("score")
        if score is not None:
            judge[f'{"recall=1" if oracle["recall"] == 1 else "recall<1"}/score={score}'] += 1
    return {"with_recall": len(recalls), "recall_mean": round(mean(recalls), 3) if recalls else None,
            "recall_full": _share(sum(1 for r in recalls if r == 1), len(recalls)),
            "missed_by_kind": {k: f"{missed[k]}/{counted[k]}" for k in sorted(counted)},
            "judge_vs_oracle": dict(sorted(judge.items()))}


def yield_stats(records: list[dict], selected: list[dict], contexts: list[dict],
                check_fields: list[str]) -> dict[str, Any]:
    """판정 분포, 처음 걸린 검사, 지시가 해소된 장면 수."""
    first_fail: Counter[str] = Counter()
    for record in records:
        v = _verification(record)
        if v.get("final_pass"):
            continue
        fields = [*check_fields, "instruction_resolvability"]
        failed = next((f for f in fields if isinstance(v.get(f), dict) and v[f].get("pass") is False),
                      "schema" if v.get("schema_valid") is False else "other")
        first_fail[failed] += 1
    resolved = sum(1 for c in contexts if (c.get("resolvability") or {}).get("pass"))
    return {"status": dict(Counter((s.get("meta") or {}).get("status") for s in selected)),
            "passed": sum(1 for r in records if _verification(r).get("final_pass")),
            "first_failed_check": dict(first_fail.most_common()),
            "scenes_resolvable": f"{resolved}/{len(contexts)}"}


def _numberless(value: Any) -> str:
    """'조리대 3'의 번호처럼 장면마다 달라질 뿐인 숫자를 뗀 글. 값이 없으면 빈 글."""
    return re.sub(r"\s*\d+", "", value) if isinstance(value, str) else ""


def description_key(tsd: dict) -> str:
    """설명문의 내용을 가르는 열쇠. 목표 상태, 완료 판정의 종류와 값, 대상 물체의 역할·위치·상태, 제약의 유형이
    같으면 같은 설명문으로 본다. 가구 번호만 다른 것과 문장 표현만 다른 것은 같은 것이다."""
    cases = sorted(sorted(f'{a.get("kind")}|{_numberless(a.get("subject"))}|{a.get("predicate")}|'
                          f'{_numberless(a.get("object"))}|{a.get("value")}' for a in case)
                   for case in goal_cases(tsd))
    conditions = sorted(f'{c.get("kind")}|{c.get("value")}|{c.get("unit")}' for c in _conditions(tsd))
    targets = sorted(f'{role}|{_numberless(e.get("ref"))}|{_numberless(e.get("location"))}|'
                     f'{e.get("state") or ""}' for role, e in _entries(tsd))
    constraints = sorted(c.get("type") or "" for c in tsd.get("constraints") or [])
    return repr((cases, conditions, targets, constraints))


def diversity(records: list[dict]) -> dict[str, Any]:
    """검수를 통과한 설명문이 서로 얼마나 다른가. 장면만 바뀌고 설명문이 같은 샘플은 데이터를 늘리지 못하므로,
    규모를 키우기 전에 이 수를 본다. 템플릿 안에서의 비율은 그 템플릿을 더 돌려도 새 설명문이 나오는지를 말해 준다."""
    passed = [r for r in records if _verification(r).get("final_pass")]
    by_template: dict[str, list[str]] = defaultdict(list)
    for record in passed:
        template = (record.get("meta") or {}).get("task_template") or "?"
        by_template[template].append(description_key(_tsd(record)))
    distinct = sum(len(set(keys)) for keys in by_template.values())
    shares = sorted(len(set(keys)) / len(keys) for keys in by_template.values())
    return {
        "passed": len(passed),
        "distinct_descriptions": distinct,
        "distinct_share": _share(distinct, len(passed)),
        "templates": len(by_template),
        "per_template_share_median": round(shares[len(shares) // 2], 3) if shares else None,
        "templates_mostly_repeated": sum(1 for share in shares if share <= REPEATED_SHARE),
    }


def audit(records: list[dict], selected: list[dict], contexts: list[dict],
          check_fields: list[str]) -> dict[str, Any]:
    by_model: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_model[_model(record)].append(record)
    return {
        "yield": yield_stats(records, selected, contexts, check_fields),
        "fields": field_stats(records),
        "diversity": diversity(records),
        "agreement": agreement(records),
        "oracle": oracle_stats(records),
        "by_model": {m: {"passed": sum(1 for r in rs if _verification(r).get("final_pass")),
                         "fields": field_stats(rs), "oracle": oracle_stats(rs)}
                     for m, rs in sorted(by_model.items())},
    }
