"""제조 작업장 장면을 규칙으로 만든다: 에셋 목록(크기·관절)과 작업대 틀(resources/adapters/simready_workshop.yaml).

설비는 틀이 정한 종류 가운데 seed로 하나를 고르고, 칸은 에셋의 관절 수로 센다(서랍 관절이 4개면 서랍 4칸).
여닫힘과 켜짐 상태는 그 관절이 실제로 있는 에셋만 갖는다. 칸마다 틀이 정한 종류의 물건을 seed로 골라 넣는다.
좌표는 만들지 않고 놓인 곳(설비, 칸, 담긴 용기)만 정한다. 모델 입력은 이것으로 충분하다(놓인 곳, 상태, 크기).
"""

import random
import re
from dataclasses import dataclass, field

FLOOR_ID = "fx_floor_1"


@dataclass
class ItemSpec:
    asset: str
    category: str
    ko: str
    traits: list[str]
    size: list[float] | None


@dataclass
class FixtureSpec:
    asset: str
    category: str
    ko: str
    regions: list[dict]  # [{key, relation, state, name_ko}] 위에서부터
    states: list[str] = field(default_factory=list)  # 설비 자체의 상태 키


def _joint_count(row: dict, prefix: str) -> int:
    pattern = re.compile(rf"^(joint_)?{prefix}", re.IGNORECASE)
    return sum(1 for j in row.get("joints") or [] if pattern.match(j["name"]))


def item_specs(rows: list[dict], vocab: dict) -> list[ItemSpec]:
    """에셋 목록 가운데 물건 표에 맞는 것. 분류·이름에 정규식 묶음(볼트 굵기, 트레이 색)을 채운다."""
    found = []
    for row in rows:
        for rule in vocab["items"]:
            hit = re.match(rule["match"], row["asset"])
            if not hit:
                continue
            groups = hit.groups()
            category = rule["category"].format(None, *groups)
            ko = rule["ko"].format(None, *groups, color=vocab["colors"].get(groups[0] if groups else "", ""))
            found.append(ItemSpec(row["asset"], category, ko, list(rule["traits"]), row.get("size_m")))
            break
    return found


def _regions(row: dict, rule: dict, vocab: dict) -> list[dict]:
    regions = []
    for spec in rule["regions"]:
        count = spec.get("count")
        if count is None:
            count = _joint_count(row, spec["from_joint"]) if spec.get("from_joint") else 1
        count = min(count, spec.get("count_max", count))
        names = vocab["region_ko_by_fixture"].get(f'{rule["category"]}.{spec["key"]}',
                                                  vocab["region_ko"][spec["key"]])
        regions.extend({"key": f'{spec["key"]}{n}' if count > 1 else spec["key"],
                        "kind": spec["key"], "relation": spec["relation"], "state": spec.get("state"),
                        "name_ko": f"{names} {n}" if count > 1 else names}
                       for n in range(1, count + 1))
    return regions


def fixture_specs(rows: list[dict], vocab: dict) -> dict[str, list[FixtureSpec]]:
    """설비 분류 -> 그 분류의 에셋들. 상태는 그 상태를 만드는 관절이 있는 에셋만 갖는다."""
    found: dict[str, list[FixtureSpec]] = {}
    for row in rows:
        rule = next((r for r in vocab["fixtures"] if re.match(r["match"], row["asset"])), None)
        if rule is None:
            continue
        states = [key for key, prefix in (rule.get("states") or {}).items() if _joint_count(row, prefix)]
        spec = FixtureSpec(row["asset"], rule["category"], rule["ko"], _regions(row, rule, vocab), states)
        found.setdefault(rule["category"], []).append(spec)
    return found


class SceneMaker:
    """장면 하나를 만든다: 작업대 틀을 고르고, 설비를 놓고, 상태를 정하고, 칸을 채운다."""

    def __init__(self, items: list[ItemSpec], fixtures: dict[str, list[FixtureSpec]], vocab: dict,
                 rng: random.Random):
        self.items, self.fixture_pool, self.vocab, self.rng = items, fixtures, vocab, rng
        self.fixtures: list[dict] = [{"id": FLOOR_ID, "category": "floor", "name_ko": "바닥"}]
        self.objects: list[dict] = []
        self.relations: list[dict] = []
        self.counts: dict[str, int] = {}
        self.assets: dict[str, str] = {}

    def matching(self, name: str) -> list[ItemSpec]:
        """fill의 이름에 맞는 물건들: 묶음 조건, 같은 이름의 분류, 같은 이름의 성질 순."""
        group = self.vocab["groups"].get(name)
        if group and group.get("traits"):
            return [i for i in self.items if set(group["traits"]) <= set(i.traits)]
        if group and group.get("prefix"):
            return [i for i in self.items if i.category.startswith(group["prefix"])]
        exact = [i for i in self.items if i.category == name]
        return exact or [i for i in self.items if name in i.traits]

    def place_fixture(self, category: str) -> tuple[dict, FixtureSpec] | None:
        pool = self.fixture_pool.get(category)
        if not pool:
            return None
        spec = self.rng.choice(pool)
        self.counts[category] = self.counts.get(category, 0) + 1
        entry_id = f"fx_{category}_{self.counts[category]}"
        odds = self.vocab["state_odds"]
        states = {key: self.rng.random() < odds[key] for key in spec.states}
        for first, second in self.vocab.get("exclusive_states") or []:  # 문이 열린 채 돌지 않는다
            if states.get(first) and states.get(second):
                states[second] = False
        regions = []
        for region in spec.regions:
            own = ({region["state"]: self.rng.random() < odds[region["state"]]}
                   if region.get("state") else {})
            regions.append({"key": region["key"], "name_ko": region["name_ko"], "states": own})
        entry = {"id": entry_id, "category": category, "name_ko": spec.ko, "states": states,
                 "state_source": "metadata", "regions": regions}
        self.fixtures.append(entry)
        self.assets[entry_id] = spec.asset
        return entry, spec

    def add_object(self, item: ItemSpec, host: str, relation: str, region: str | None) -> str:
        object_id = f"obj_{len(self.objects) + 1:03d}"
        entry = {"id": object_id, "category": item.category, "name_ko": item.ko,
                 "size_m": [round(v, 3) for v in item.size] if item.size else None}
        if host.startswith("fx_"):
            entry["placement"] = {"fixture_id": host, "relation": relation,
                                  **({"region": region} if region else {})}
        self.objects.append(entry)
        self.relations.append({"subj": object_id, "pred": relation, "obj": host, "source": "rule"})
        self.assets[object_id] = item.asset
        return object_id

    def fill(self, entry: dict, spec: FixtureSpec, rules: dict, tray_rules: list) -> None:
        """칸마다 틀의 규칙대로 물건을 넣는다. 트레이를 넣으면 그 안에 작은 부품을 담는다."""
        for region in spec.regions:
            for name, low, high in rules.get(region["kind"], []):
                pool = self.matching(name)
                for item in self.rng.sample(pool, min(len(pool), self.rng.randint(low, high))):
                    placed = self.add_object(item, entry["id"], region["relation"], region["key"])
                    if "holder" in item.traits:
                        self.fill_holder(placed, tray_rules)

    def fill_holder(self, holder: str, rules: list) -> None:
        for name, low, high in rules:
            pool = self.matching(name)
            for item in self.rng.sample(pool, min(len(pool), self.rng.randint(low, high))):
                self.add_object(item, holder, "in", None)
