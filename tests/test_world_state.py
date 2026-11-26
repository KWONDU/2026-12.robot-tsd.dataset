"""세계 상태 보기: 모델에게 주는 글은 씬의 모든 entity를 빠짐없이, id 없이 담고, 이름표는 id로 정확히 되짚힌다."""

import re

from tsdgen.models import SceneState, to_environment_context
from tsdgen.resources import load_golden, load_resources
from tsdgen.world_state import (
    WORLD_STATE_VIEWS,
    is_named,
    name_labels,
    place_names,
    relabel,
    resolve,
)

ID = re.compile(r"\b(?:obj|fx)_\w*\d")


def _scene(fixtures, objects):
    return {"domain": "household", "fixtures": fixtures, "objects": objects,
            "agent": {"embodiment": "PandaOmron", "base_near_fixture_id": None, "holding": []}}


def _fixture(fid, name, states=None, where=None):
    return {"id": fid, "category": name, "name_ko": name, "location_desc": where, "states": states or {}}


def _object(oid, name, fixture_id=None, relation="on"):
    placement = {"fixture_id": fixture_id, "relation": relation} if fixture_id else None
    return {"id": oid, "category": name, "name_ko": name, "states": {}, "placement": placement}


def test_duplicate_names_get_numbers_and_unique_names_do_not():
    env = _scene([_fixture("fx_counter_1", "조리대"), _fixture("fx_counter_2", "조리대"),
                  _fixture("fx_sink_1", "싱크대")],
                 [_object("obj_001", "그릇", "fx_counter_2"), _object("obj_002", "그릇", "fx_counter_2")])
    labels = name_labels(env)
    assert labels["fx_counter_1"] == "조리대 1" and labels["fx_counter_2"] == "조리대 2"
    assert labels["fx_sink_1"] == "싱크대" and {labels["obj_001"], labels["obj_002"]} == {"그릇 1", "그릇 2"}


def test_text_view_mentions_every_entity_without_ids(settings):
    lexicon = load_resources(settings.path("resources"))
    env = _scene([_fixture(f"fx_drawer_{i}", "서랍", {"door_open": False}) for i in range(1, 6)]
                 + [_fixture("fx_counter_1", "조리대", where="아일랜드 조리대"),
                    _fixture("fx_microwave_1", "전자레인지", {"door_open": True})],
                 [_object("obj_001", "머그", "fx_counter_1"), _object("obj_002", "마시멜로")])
    world = WORLD_STATE_VIEWS.get("text")(env, lexicon, settings.contextualize)
    assert not ID.search(world.text)
    for label in world.labels.values():
        stem = label.rsplit(" ", 1)[0]
        assert label in world.text or stem in world.text, label
    assert "서랍 1부터 5까지: 문 닫힘" in world.text  # 같은 상태의 같은 가구는 한 줄로 줄인다
    assert "조리대(아일랜드 조리대) 위: 머그" in world.text
    assert "그 밖의 물체:\n- 마시멜로" in world.text and "전자레인지: 문 열림" in world.text


def test_pointer_fields_round_trip_between_ids_and_names(settings):
    golden = load_golden(settings.path("resources"))
    env = to_environment_context(SceneState.model_validate(golden["scene_state"]))
    labels = name_labels(env)
    named = relabel(golden["task_situation_description"], labels)
    assert named["target_objects"]["required"][0]["ref"] == "머그"
    assert named["task_goal"]["goal_state"][0][0]["object"] == "전자레인지"
    assert named["task_goal"]["summary"] == golden["task_situation_description"]["task_goal"]["summary"]
    assert resolve(named, labels) == golden["task_situation_description"]
    # 글에 나온 가구 표기를 위치 설명까지 통째로 옮겨 써도 같은 가구로 되짚는다
    named["target_objects"]["key_means"][0]["ref"] = "전자레인지(조리대 우측)"
    assert resolve(named, labels)["target_objects"]["key_means"][0]["ref"] == "fx_microwave_1"
    # 되짚지 못한 이름은 그대로 남아 접지 검사가 씬에 없는 것으로 잡는다
    named["target_objects"]["required"][0]["ref"] = "없는 물체"
    assert resolve(named, labels)["target_objects"]["required"][0]["ref"] == "없는 물체"


def test_json_view_is_the_previous_input(settings):
    lexicon = load_resources(settings.path("resources"))
    env = _scene([_fixture("fx_counter_1", "조리대")], [_object("obj_001", "머그", "fx_counter_1")])
    world = WORLD_STATE_VIEWS.get("json")(env, lexicon, settings.contextualize)
    assert '"fx_counter_1"' in world.text and not is_named(world.labels)


def test_objects_without_fixture_are_placed_on_the_object_right_below(settings):
    """가구 배치가 없는 물체는 좌표로 바로 아래 물체를 찾아 '접시 1 위'로 쓴다. 멀리 있으면 놓인 곳을 모른다고 쓴다."""
    lexicon = load_resources(settings.path("resources"))

    def posed(oid, name, pos, fixture_id=None):
        return {**_object(oid, name, fixture_id), "pose": {"pos": pos, "quat": [1, 0, 0, 0]}}

    env = _scene([_fixture("fx_counter_1", "조리대")],
                 [posed("obj_001", "접시", [0.40, -5.28, 0.926], "fx_counter_1"),
                  posed("obj_002", "각설탕", [0.403, -5.279, 0.933]),
                  posed("obj_003", "사과", [1.5, -2.0, 0.95])])
    text = WORLD_STATE_VIEWS.get("text")(env, lexicon, settings.contextualize).text
    assert "접시 위: 각설탕" in text and "그 밖의 물체:\n- 사과" in text


def test_objects_resting_in_a_container_are_inside_it(settings):
    lexicon = load_resources(settings.path("resources"))

    def posed(oid, name, category, pos, fixture_id=None):
        obj = {**_object(oid, name, fixture_id), "category": category}
        return {**obj, "pose": {"pos": pos, "quat": [1, 0, 0, 0]}}

    env = _scene([_fixture("fx_counter_1", "조리대")],
                 [posed("obj_001", "밀폐용기", "tupperware", [0.4, -1.0, 0.93], "fx_counter_1"),
                  posed("obj_002", "닭다리", "chicken_drumstick", [0.41, -1.0, 0.95])])
    text = WORLD_STATE_VIEWS.get("text")(env, lexicon, settings.contextualize).text
    assert "밀폐용기 안: 닭다리" in text


def test_text_view_writes_region_neighbor_and_holder(settings):
    """원천이 알려 준 칸(냉동실 선반), 배치 기준 옆 가구(싱크대 옆), 기준 물체(접시 위)를 놓인 곳에 쓴다.
    상태가 없는 가구는 이름만 적고, 데이터에 없다는 말('모름' 등)은 쓰지 않는다."""
    lexicon = load_resources(settings.path("resources"))
    fridge = {**_fixture("fx_fridge_1", "냉장고"), "category": "fridge"}
    env = _scene([fridge, _fixture("fx_counter_1", "조리대"), _fixture("fx_sink_1", "싱크대")],
                 [_object("obj_001", "닭다리", "fx_fridge_1", "in"), _object("obj_002", "접시", "fx_counter_1"),
                  _object("obj_003", "바게트")])
    env["objects"][0]["placement"]["region"] = "freezer_shelf2"
    env["objects"][1]["placement"]["near_fixture_id"] = "fx_sink_1"
    env["relations"] = [{"subj": "obj_003", "pred": "on", "obj": "obj_002", "source": "metadata"}]
    world = WORLD_STATE_VIEWS.get("text")(env, lexicon, settings.contextualize)
    assert "냉장고 냉동실 선반 위: 닭다리" in world.text
    assert "조리대 위, 싱크대 옆: 접시" in world.text and "접시 위: 바게트" in world.text
    assert "\n- 싱크대" in world.text and "모름" not in world.text and "상태 정보" not in world.text


def test_region_names_from_the_text_resolve_to_their_fixture(settings):
    """글에 나온 '냉장고 냉동실 선반'은 냉장고를 가리킨다. 글에 없는 칸 이름은 되짚지 않는다."""
    from tsdgen.world_state import place_names

    lexicon = load_resources(settings.path("resources"))
    fridge = {**_fixture("fx_fridge_1", "냉장고"), "category": "fridge"}
    env = _scene([fridge], [_object("obj_001", "닭다리", "fx_fridge_1", "in")])
    env["objects"][0]["placement"]["region"] = "freezer_shelf2"
    labels = name_labels(env)
    places = place_names(env, labels, lexicon)
    assert places == {"냉장고 냉동실 선반": "fx_fridge_1"}
    tsd = {"target_objects": {"key_means": [{"ref": "냉장고 냉동실 선반"}, {"ref": "냉장고 냉장실 서랍"}]}}
    refs = [e["ref"] for e in resolve(tsd, labels, places)["target_objects"]["key_means"]]
    assert refs == ["fx_fridge_1", "냉장고 냉장실 서랍"]


def _fridge_scene():
    fridge = {**_fixture("fx_fridge_1", "냉장고", {"door_open": False}), "category": "fridge", "regions": [
        {"key": "fridge_shelf1", "name_ko": "냉장실 선반 1", "states": {}},
        {"key": "fridge_shelf0", "name_ko": "냉장실 선반 2", "states": {}},
        {"key": "freezer_shelf0", "name_ko": "냉동실 선반", "states": {}}]}
    stove = {**_fixture("fx_stove_1", "스토브", {"turned_on": True}), "category": "stove", "regions": [
        {"key": "front_left", "name_ko": "앞 왼쪽 화구", "states": {"turned_on": True}},
        {"key": "rear_left", "name_ko": "뒤 왼쪽 화구", "states": {"turned_on": False}}]}
    env = _scene([fridge, stove, _fixture("fx_counter_1", "조리대"),
                  _fixture("fx_counter_2", "조리대", where="아일랜드 식탁")],
                 [_object("obj_001", "닭다리", "fx_fridge_1", "in"), _object("obj_002", "그릇", "fx_counter_1"),
                  _object("obj_003", "그릇", "fx_counter_1")])
    env["objects"][0]["placement"]["region"] = "freezer_shelf0"
    env["objects"][1]["size_m"], env["objects"][2]["size_m"] = [0.2, 0.2, 0.1], [0.3, 0.3, 0.1]
    return env


def test_text_view_lists_regions_sizes_and_described_fixtures(settings):
    """가구의 칸은 이름을 갖고 목록으로 적힌다. 위치 설명이 있는 가구는 묶여 사라지지 않고, 크기가 뚜렷이 다르면 적는다."""
    lexicon = load_resources(settings.path("resources"))
    world = WORLD_STATE_VIEWS.get("text")(_fridge_scene(), lexicon, settings.contextualize)
    assert world.labels["fx_fridge_1/fridge_shelf0"] == "냉장고 냉장실 선반 2"
    assert "냉장고 냉동실 선반 위: 닭다리" in world.text
    assert "- 냉장고: 문 닫힘. 칸: 냉장실 선반 1, 2(위에서부터), 냉동실 선반" in world.text
    assert "앞 왼쪽 화구(가동 중), 뒤 왼쪽 화구" in world.text
    assert "- 조리대 2(아일랜드 식탁)" in world.text and "- 조리대 1\n" in world.text + "\n"
    assert "- 그릇: 큰 것부터 그릇 2, 그릇 1" in world.text


def test_similar_sizes_share_a_tier():
    """크기가 비슷한 것이 섞여 있어도 뚜렷이 다른 것끼리의 순서는 적는다. 모두 비슷하면 적지 않는다."""
    from tsdgen.world_state import _size_lines

    def env(*sizes):
        return {"objects": [{"id": f"o{i}", "name_ko": "병", "size_m": [s, s, s]}
                            for i, s in enumerate(sizes, start=1)]}

    labels = {f"o{i}": f"병 {i}" for i in range(1, 5)}
    assert _size_lines(env(0.2, 0.1, 0.2, 0.05), labels, 1.15) == [
        "- 병: 큰 것부터 병 1·병 3(서로 비슷), 병 2, 병 4"]
    assert _size_lines(env(0.2, 0.2), labels, 1.15) == []


def test_goal_can_point_at_a_region(settings):
    """목표 상태가 칸을 가리키면 그 칸에 놓였는지를 본다. 번호 뗀 칸 이름은 그 종류의 아무 칸이다."""
    from tsdgen.goals.kinds import GOAL_KINDS
    from tsdgen.goals.scene_index import SceneIndex
    from tsdgen.models import GoalAssertion

    lexicon = load_resources(settings.path("resources"))
    env = _fridge_scene()
    labels = WORLD_STATE_VIEWS.get("text")(env, lexicon, settings.contextualize).labels
    index = SceneIndex.from_env(env)

    def holds(target):
        tsd = {"task_goal": {"goal_state": [[{"kind": "relation", "subject": "닭다리", "predicate": "on",
                                              "object": target, "value": True}]]}}
        assertion = GoalAssertion.model_validate(resolve(tsd, labels)["task_goal"]["goal_state"][0][0])
        assert GOAL_KINDS.get("relation").problems(assertion, index, lexicon) == []
        return GOAL_KINDS.get("relation").holds(assertion, index)

    assert holds("냉장고 냉동실 선반") is True and holds("냉장고") is True
    assert holds("냉장고 냉장실 선반") is False  # 번호 없이: 냉장실 선반 1, 2 어느 쪽도 아니다
    burner = GoalAssertion(kind="state", subject="fx_stove_1/front_left", predicate="turned_on")
    assert GOAL_KINDS.get(burner.kind).holds(burner, index) is True


def test_region_name_without_number_means_any_of_those_regions(settings):
    """번호 뗀 칸 이름은 그 칸들 아무 것, 칸 이름의 앞부분은 그 말로 시작하는 칸 아무 것이다(가구가 아니다).
    이름에 붙여 쓴 괄호 설명은 어디에 있든 무시한다."""
    lexicon = load_resources(settings.path("resources"))
    env = _fridge_scene()
    world = WORLD_STATE_VIEWS.get("text")(env, lexicon, settings.contextualize)
    places = place_names(env, world.labels, lexicon)
    names = ["냉장고 냉장실 선반", "냉장고 냉장실", "냉장고(주방 안쪽) 냉장실 선반 2"]
    tsd = {"target_objects": {"key_means": [{"ref": name} for name in names]}}
    refs = [e["ref"] for e in resolve(tsd, world.labels, places)["target_objects"]["key_means"]]
    shelves = "fx_fridge_1/fridge_shelf1|fx_fridge_1/fridge_shelf0"
    assert refs[0] == shelves and refs[2] == "fx_fridge_1/fridge_shelf0"
    assert set(refs[1].split("|")) >= set(shelves.split("|")) and "fx_fridge_1" not in refs[1].split("|")


def test_shared_location_hint_names_the_group(settings):
    """같은 위치 설명을 나눠 가진 조리대들은 그 설명('아일랜드 식탁')으로 묶어 가리킬 수 있다."""
    lexicon = load_resources(settings.path("resources"))
    counters = [{**_fixture(f"fx_counter_{i}", "조리대"), "location_desc": "아일랜드 식탁" if i > 1 else None}
                for i in (1, 2, 3)]
    env = _scene(counters, [])
    labels = name_labels(env)
    places = place_names(env, labels, lexicon)
    tsd = {"target_objects": {"key_means": [{"ref": "아일랜드 식탁"}, {"ref": "조리대 2(아일랜드 식탁)"}]}}
    refs = [e["ref"] for e in resolve(tsd, labels, places)["target_objects"]["key_means"]]
    assert refs == ["fx_counter_2|fx_counter_3", "fx_counter_2"]


def test_fixture_adjacency_is_written_next_to_the_fixture(settings):
    """씬이 '조리대 near 싱크대' 관계를 주면 가구 줄에 '싱크대 옆'이라고 적는다."""
    lexicon = load_resources(settings.path("resources"))
    env = _scene([_fixture("fx_counter_1", "조리대"), _fixture("fx_counter_2", "조리대"),
                  _fixture("fx_sink_1", "싱크대")], [])
    env["relations"] = [{"subj": "fx_counter_2", "pred": "near", "obj": "fx_sink_1", "source": "sim"}]
    text = WORLD_STATE_VIEWS.get("text")(env, lexicon, settings.contextualize).text
    assert "- 조리대 2: 싱크대 옆" in text and "- 조리대 1\n" in text + "\n"


def test_fixture_plus_region_kind_means_any_region_of_that_kind(settings):
    """'스토브 화구'는 스토브의 화구 아무 것, '냉장고 선반'은 냉장고의 선반 아무 것이다."""
    lexicon = load_resources(settings.path("resources"))
    world = WORLD_STATE_VIEWS.get("text")(_fridge_scene(), lexicon, settings.contextualize)
    burners = {e for e, label in world.labels.items() if label.endswith("화구")}
    shelves = {e for e, label in world.labels.items() if "선반" in label}
    stove = next(label for e, label in world.labels.items() if e.startswith("fx_stove") and "/" not in e)
    tsd = {"target_objects": {"key_means": [{"ref": f"{stove} 화구"}, {"ref": "냉장고 선반"},
                                            {"ref": "냉장고 화구"}]}}
    refs = [e["ref"] for e in resolve(tsd, world.labels)["target_objects"]["key_means"]]
    assert burners and set(refs[0].split("|")) == burners
    assert set(refs[1].split("|")) == shelves and refs[2] == "냉장고 화구"
