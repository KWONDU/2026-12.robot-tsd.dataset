"""배치(factory_layout)를 씬 상태로 바꾼다: 곳은 설비(가구)로, 통로·구역·시설 알림은 지도(map)로, 물건은 놓인 곳의 칸에.

로봇은 지도를 미리 갖고 있다(곳, 구역, 통로, 막힌 통로, 출입 제한 구역, 시설 알림). 물건은 로봇이 지금 있는 곳의 것만
보이고(perceived), 다른 곳의 물건은 공장 시스템이 알려 준다(system). 이름표와 확률은 resources/adapters/factory_floor.yaml.
"""

import random
from collections import defaultdict
from typing import Any

from tsdgen.adapters.factory_layout import Layout
from tsdgen.adapters.simready_workshop_build import ItemSpec

FLOOR_ID = "fx_floor_1"


class FactoryScene:
    def __init__(self, layout: Layout, vocab: dict, items: list[ItemSpec], rng: random.Random):
        self.layout, self.vocab, self.items, self.rng = layout, vocab, items, rng
        self.fixtures: list[dict] = [{"id": FLOOR_ID, "category": "floor", "name_ko": "바닥"}]
        self.objects: list[dict] = []
        self.relations: list[dict] = []
        self.ids: dict[str, str] = {}  # 배치의 곳 이름 -> 설비 id
        self.notices: list[str] = []
        self.assets: dict[str, str] = {}

    def stations(self) -> None:
        counts: dict[str, int] = defaultdict(int)
        totals = {
            role: sum(1 for s in self.layout.stations if s.role == role)
            for role in self.vocab["roles"]
        }
        for station in self.layout.stations:
            rule = self.vocab["roles"][station.role]
            counts[station.role] += 1
            number = counts[station.role]
            fixture_id = f"fx_{rule['category']}_{number}"
            name = rule["ko"] if totals[station.role] == 1 else f"{rule['ko']} {number}"
            regions = []
            for key in rule.get("regions") or []:
                count = rule.get("shelves", 1) if key == "shelf" else 1
                label = self.vocab["region_ko"][key]
                regions += [
                    {
                        "key": f"{key}{n}" if count > 1 else key,
                        "name_ko": f"{label} {n}" if count > 1 else label,
                        "states": {},
                    }
                    for n in range(1, count + 1)
                ]
            states = dict.fromkeys(rule.get("states") or [], True)
            self.fixtures.append(
                {
                    "id": fixture_id,
                    "category": rule["category"],
                    "name_ko": name,
                    "location_desc": self.vocab["zones"][rule["zone"]],
                    "states": states,
                    "state_source": "metadata",
                    "regions": regions,
                }
            )
            self.ids[station.key] = fixture_id

    def _pool(self, name: str) -> list[ItemSpec]:
        return [i for i in self.items if name in i.traits or i.category == name]

    def put(self, item: ItemSpec, fixture_id: str, region: str, known_by: str) -> str:
        object_id = f"obj_{len(self.objects) + 1:03d}"
        relation = "on"
        self.objects.append(
            {
                "id": object_id,
                "category": item.category,
                "name_ko": item.ko,
                "placement": {"fixture_id": fixture_id, "relation": relation, "region": region},
                "size_m": [round(v, 3) for v in item.size] if item.size else None,
                "known_by": known_by,
            }
        )
        self.relations.append(
            {"subj": object_id, "pred": relation, "obj": fixture_id, "source": "rule"}
        )
        self.assets[object_id] = item.asset
        return object_id

    def contents(self, here: str) -> None:
        """곳마다 칸에 물건을 놓는다. 로봇이 있는 곳의 물건만 보이는 것이고, 나머지는 시스템이 알려 준 것이다."""
        for station in self.layout.stations:
            fixture_id = self.ids[station.key]
            fixture = next(f for f in self.fixtures if f["id"] == fixture_id)
            rules = dict(self.vocab["contents"].get(station.role) or {})
            known_by = "perceived" if fixture_id == here else "system"
            for region in fixture["regions"]:
                kind = region["key"].rstrip("0123456789")
                for name, low, high in rules.get(kind, []):
                    pool = self._pool(name)
                    for item in self.rng.sample(pool, min(len(pool), self.rng.randint(low, high))):
                        self.put(item, fixture_id, region["key"], known_by)
            if station.role in ("process", "assembly") and fixture["regions"]:
                for name, low, high in self.vocab["waste_at_stations"]:
                    pool = self._pool(name)
                    for item in self.rng.sample(pool, min(len(pool), self.rng.randint(low, high))):
                        self.put(item, fixture_id, fixture["regions"][-1]["key"], known_by)

    def downtime(self) -> None:
        """가공기·조립 스테이션 가운데 일부를 점검 정지로 (규칙집의 정해진 정지). 시설 알림에 남는다."""
        rule = self.vocab["downtime"]
        for fixture in self.fixtures:
            if "turned_on" in (fixture.get("states") or {}) and self.rng.random() < rule["odds"]:
                fixture["states"]["turned_on"] = False
                seconds = self.rng.choice(rule["seconds"])
                self.notices.append(
                    f"{fixture['name_ko']} 점검으로 정지, 약 {seconds}초 뒤 다시 가동"
                )

    def record(self) -> dict[str, Any]:
        return {"fixtures": self.fixtures, "objects": self.objects, "relations": self.relations}
