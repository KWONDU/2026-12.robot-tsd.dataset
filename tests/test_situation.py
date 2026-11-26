"""상황 사실 대조(검수 항목 situation)와 공장 길의 '돌아가는 까닭', 도메인 칸을 뺀 프롬프트가 가정·물류에서 그대로인지."""

import pytest

from tsdgen import domains
from tsdgen.config import CONFIG_PATH, PROJECT_ROOT, Settings, load_config_document
from tsdgen.prompts import fill, load_template
from tsdgen.resources import load_resources
from tsdgen.tasks.factory_view import FactoryView
from tsdgen.verify.situation import aisle_numbers, check_situation

LABELS = {"fx_asm_1": "조립 스테이션 1", "fx_asm_12": "조립 스테이션 12"}
FACTS = {"detour": {"closed": ["통로 4"], "restricted": ["통로 15"]}, "wait_for": ["fx_asm_1"],
         "window_s": [120, 300], "duration_s": 60}


def _tsd(time: str, *constraints: str) -> dict:
    return {"spatiotemporal_conditions": {"time": time, "space": {"start": None, "end": None}},
            "constraints": [{"type": "spatial", "description": c} for c in constraints]}


def _words(settings) -> dict:
    return load_resources(settings.path("resources"))["situation_words"]


def test_aisle_numbers_reads_lists_after_one_prefix(settings):
    words = _words(settings)
    assert aisle_numbers("통로 40, 9, 11, 38(조립 스테이션 4를 거침)로 돌아감", words) == {"40", "9", "11", "38"}
    assert aisle_numbers("통로 16과 통로 19를 지나 조립 스테이션 4로", words) == {"16", "19"}


def test_all_facts_present_passes(settings):
    tsd = _tsd("120초에서 300초 사이, 조립 스테이션 1이 다시 가동될 때까지 기다리고 가공에 1분",
               "통로 4는 막혔고 통로 15는 출입 제한 구역을 지나므로 통로 1, 12로 돌아가야 함")
    result = check_situation(tsd, FACTS, LABELS, _words(settings))
    assert result.pass_ and result.available and not result.missing


def test_missing_detour_wait_and_time_are_named(settings):
    tsd = _tsd("조립 스테이션 12가 다시 가동될 때까지", "통로 4가 막혀 통로 1로 돌아가야 함")
    result = check_situation(tsd, FACTS, LABELS, _words(settings))
    assert not result.pass_
    assert result.missing == ["restricted: 통로 15", "wait_for: 조립 스테이션 1", "window_s: 120",
                              "window_s: 300", "duration_s: 60"]


def test_no_facts_is_not_checked(settings):
    result = check_situation(_tsd(""), None, {}, _words(settings))
    assert result.pass_ and not result.available


def _view(one_way: bool) -> FactoryView:
    """a - b 직통(3 m, 한 방향 b->a 또는 막힘)과 a - c - b 우회(6 m)가 있는 지도."""
    aisles = [{"a": "b", "b": "a", "name_ko": "통로 1", "length_m": 3.0, "closed": not one_way,
               "one_way": one_way, "note": None},
              {"a": "a", "b": "c", "name_ko": "통로 2", "length_m": 3.0, "closed": False, "one_way": False,
               "note": None},
              {"a": "c", "b": "b", "name_ko": "통로 3", "length_m": 3.0, "closed": False, "one_way": False,
               "note": None}]
    scene = {"map": {"zones": [], "stations": {}, "aisles": aisles, "notices": []},
             "fixtures": [], "objects": [], "agent": {"base_near_fixture_id": "a"}}
    return FactoryView(scene, {})


@pytest.mark.parametrize("one_way, cause", [(True, "one_way"), (False, "closed")])
def test_detour_cause_is_the_unusable_aisle_on_the_shortest_path(one_way, cause):
    view = _view(one_way)
    assert view.blocked("a", "b") == {cause: ("통로 1",)}  # 직통을 쓸 수 없어 통로 2, 3으로 돌아간다
    assert view.blocked("b", "a") == ({} if one_way else {"closed": ("통로 1",)})  # 한 방향은 순방향이면 그대로
    assert view.route_traits(("a", "b"), ("b", "a")) == ((cause,), () if one_way else ("closed",))


@pytest.mark.parametrize("adapter", ["robocasa365", "simready_warehouse"])
def test_domain_slots_leave_household_and_logistics_prompts_unchanged(adapter):
    data = load_config_document(CONFIG_PATH, PROJECT_ROOT)
    data["source"]["adapter"] = adapter
    settings = Settings.model_validate(data)
    values = domains.prompt_values(settings)
    pairs = [("generate", "gen_v18", "gen_v19"), ("checklist", "check_v18", "check_v19"),
             ("checklist", "check_v19", "check_v20"), ("generate", "gen_v19", "gen_v20"),
             ("resolvability", "resolve_v6", "resolve_v7"), ("rewrite", "rewrite_v6_natural", "rewrite_v7_natural")]
    for role, old, new in pairs:
        before = fill(load_template(settings, role, old)[0], values, strict=False)
        assert fill(load_template(settings, role, new)[0], values, strict=False) == before, new
