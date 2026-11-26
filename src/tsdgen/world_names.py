"""설명문이 대상을 가리키는 이름을 id로 되짚거나 id를 이름으로 바꾼다 (world_state의 이름표를 쓴다).

모델은 설명문의 ref·subject·object에 세계 상태 글의 이름을 쓴다. 검수는 resolve()로 그 이름을 id에 되짚은 뒤
규칙 검사를 돌린다. 이름 하나가 여럿을 가리킬 수 있다(번호 뗀 이름, 칸 이름의 앞부분): 그때는 id를 이어 붙인
참조가 된다. 되짚지 못한 이름은 그대로 남아 접지 검사에서 씬에 없는 것으로 잡힌다.
"""

import copy
import re
from typing import Any

from tsdgen.goals.scene_index import REGION_SEP, any_of


def _swap(value: Any, mapping: dict[str, str]) -> Any:
    return mapping.get(value.strip(), value) if isinstance(value, str) else value


def map_pointers(tsd: dict, mapping: dict[str, str]) -> dict:
    """설명문에서 entity를 가리키는 칸의 값만 mapping으로 바꾼 사본. 문장 칸은 건드리지 않는다."""
    out = copy.deepcopy(tsd)
    for entries in ((out.get("target_objects") or {}).values()):
        for entry in entries or []:
            entry["ref"] = _swap(entry.get("ref"), mapping)
            if "alternatives" in entry:
                entry["alternatives"] = [_swap(a, mapping) for a in entry["alternatives"] or []]
    goal = out.get("task_goal")
    if isinstance(goal, dict):
        cases = goal.get("goal_state") or []  # 경우 목록 (2.2까지는 단언 목록 하나)
        for item in (a for c in cases for a in (c if isinstance(c, list) else [c])):
            item["subject"], item["object"] = (_swap(item.get("subject"), mapping),
                                               _swap(item.get("object"), mapping))
        for item in goal.get("completion_conditions") or []:
            item["subject"] = _swap(item.get("subject"), mapping)
    return out


def is_named(labels: dict[str, str]) -> bool:
    """이름표가 id와 다른 보기(모델이 id를 못 보는 보기)인지."""
    return any(entity != label for entity, label in labels.items())


class _NameLookup(dict):
    """이름표 -> id. 글에는 가구가 '조리대 4(아일랜드 조리대)'처럼 위치 설명을 붙여 나오므로, 모델이 그 표기를
    통째로 옮겨 써도 괄호 앞의 이름표로 찾는다. 번호를 뗀 이름('조리대')은 그 이름의 아무 것을 뜻하므로
    같은 이름의 id를 모두 이어 돌려준다. 칸 이름의 앞부분만 쓴 것('냉장고 냉동실')은 그 앞부분으로 시작하는
    칸 아무 것이고, 가구 이름에 칸의 종류만 붙인 것('스토브 화구', '냉장고 선반')은 그 가구의 그 종류 칸 아무 것이다.
    그래도 없으면 그대로 둬서 접지 검사가 잡게 한다."""

    def get(self, key, default=None):
        if key in self:
            return self[key]
        stem = " ".join(re.sub(r"\([^)]*\)", " ", key).split())  # 괄호 속 위치 설명은 이름이 아니다
        if stem in self:
            return self[stem]
        same = [(label, entity) for label, entity in self.items()
                if label.rsplit(" ", 1)[0] == stem and label.rsplit(" ", 1)[-1].isdigit()]
        ordered = sorted(same, key=lambda item: int(item[0].rsplit(" ", 1)[-1]))
        regions = sorted((label, entity) for label, entity in self.items() if REGION_SEP in entity)
        if not ordered:
            ordered = [(label, entity) for label, entity in regions if label.startswith(f"{stem} ")]
        if not ordered:
            ordered = [item for item in regions if self._of_kind(item[0], stem)]
        return any_of([entity for _label, entity in ordered]) if ordered else default

    @staticmethod
    def _of_kind(label: str, name: str) -> bool:
        """칸 이름표(스토브 앞 왼쪽 화구, 냉장고 냉장실 선반 2)가 '가구 이름 + 칸 종류'(스토브 화구, 냉장고 선반)에
        해당하는가: 이름을 어디서 둘로 나누든 앞이 이름표의 앞머리, 뒤가 번호를 뗀 이름표의 끝이면 된다."""
        plain = label.rsplit(" ", 1)[0] if label.rsplit(" ", 1)[-1].isdigit() else label
        words = name.split(" ")
        return any(plain.startswith(" ".join(words[:i]) + " ") and plain.endswith(" " + " ".join(words[i:]))
                   for i in range(1, len(words)))


def resolve(tsd: dict, labels: dict[str, str], places: dict[str, str] | None = None) -> dict:
    """모델이 쓴 이름표를 id로 되짚는다 (검수용). places는 글에 나온 '가구 + 칸' 이름 -> 그 가구 id."""
    lookup = {**(places or {}), **{label: entity for entity, label in labels.items()}}
    return map_pointers(tsd, _NameLookup(lookup))


def relabel(tsd: dict, labels: dict[str, str]) -> dict:
    """id를 이름표로 바꾼다 (골든 예시를 보기에 맞출 때)."""
    return map_pointers(tsd, labels)
