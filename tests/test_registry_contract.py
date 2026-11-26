"""레지스트리 계약 테스트.

허용값의 단일 출처가 실제로 하나인지, 등록된 전략이 이름만 다른 게 아니라 실제로
다르게 동작하는지, 축이 늘었을 때 빠뜨린 곳이 없는지를 확인한다.
"""

import pytest

from tsdgen import registry, roles
from tsdgen.registry import REGISTRIES, Registry


@pytest.fixture(autouse=True)
def _loaded():
    registry.load_all()


def test_every_registry_has_entries():
    empty = [r.what for r in REGISTRIES if not r.names()]
    assert not empty, f"등록이 비어 있는 축: {empty}"


def test_config_values_are_all_registered(settings):
    # config 검증이 도는 그 경로를 그대로 확인한다
    registry.validate_all(settings)


def test_unknown_name_is_rejected():
    with pytest.raises(ValueError, match="unknown"):
        registry.SOURCE_ADAPTERS.get("no_such_adapter")


def test_duplicate_registration_is_rejected():
    reg = Registry("test axis")
    reg.register("a")(object())
    with pytest.raises(ValueError, match="twice"):
        reg.register("a")(object())


def test_of_requires_single_select_axis(settings):
    # 단일 선택 축은 of()로 조회된다
    assert registry.SAMPLING_STRATEGIES.of(settings) is not None
    # 목록 축이나 내부 dispatch 축은 of()를 거부한다
    with pytest.raises(TypeError):
        registry.EXPORT_FORMATS.of(settings)
    with pytest.raises(TypeError):
        registry.ROLES.of(settings)


def test_every_role_has_a_mock_response():
    """파이프라인 역할과 대시보드 LLM 액션 전부에 mock 스텁이 있어야 한다.

    스텁이 빠진 이름은 mock 실행에서야 터지므로, 여기서 미리 잡는다.
    """
    from tsdgen.dashboard.llm_actions import LLM_ACTIONS

    needed = set(registry.ROLES.names()) | set(LLM_ACTIONS.names())
    assert needed <= set(registry.MOCK_RESPONSES.names())


def test_backends_declare_key_requirement():
    # 능력은 백엔드가 선언한다. config 검증이 이름으로 분기하지 않기 위한 계약이다.
    assert registry.LLM_BACKENDS.get("mock").needs_api_key is False
    assert registry.LLM_BACKENDS.get("openai_compat").needs_api_key is True


def test_selection_strategies_actually_differ(settings):
    """이름만 다른 게 아니라 동작이 다른지 확인한다."""
    from tsdgen.models import Sample

    def sample(sample_id: str, status: str) -> Sample:
        raw = {
            "sample_id": sample_id,
            "scenario_id": "s1",
            "domain": "household",
            "lang": "ko",
            "input": {
                "user_instruction": "x",
                "instruction_specificity": "natural",
                "environment_context": {},
            },
            "meta": {
                "status": status,
                "generator": {
                    "model": f"m-{sample_id}",
                    "provider": "mock",
                    "prompt_version": "gen_v1",
                    "temperature": 0.0,
                    "seed": 1,
                },
            },
        }
        return Sample.model_validate(raw)

    by_scenario = {"s1": [sample("a", "accepted"), sample("b", "accepted")]}
    kept_all = registry.SELECTION_STRATEGIES.get("all")(by_scenario)
    assert [s.meta.status for s in kept_all] == ["accepted", "accepted"]

    by_scenario = {"s1": [sample("a", "accepted"), sample("b", "accepted")]}
    best = registry.SELECTION_STRATEGIES.get("best_of_k")(by_scenario)
    assert sorted(s.meta.status for s in best) == ["accepted", "alternate"]


def test_state_call_strategies_actually_differ():
    from tsdgen.adapters.robocasa_staticmap import STATE_CALLS

    assert STATE_CALLS.get("open_door")("") == {"door_open": True}
    assert STATE_CALLS.get("close_door")("") == {"door_open": False}
    assert STATE_CALLS.get("set_knob_state")('mode="high"') == {"turned_on": True}
    assert STATE_CALLS.get("set_knob_state")('mode="off"') == {"turned_on": False}
    assert STATE_CALLS.get("set_door_state")("min=0.5") == {}


def test_role_tables_cover_every_role(settings):
    for role in roles.all_roles():
        assert role.prompt_version(settings)
        assert role.assumed_output_tokens(settings) > 0
        assert role.calls_per_scenario(settings) >= 1


def test_relation_export_strategies_actually_differ():
    """관계 술어마다 Arena 표현이 실제로 달라야 한다. 이름만 다르면 등록한 의미가 없다."""
    from tsdgen.adapters.arena_convert import CONTAINMENT_HINT, RELATION_EXPORT

    on_kind, on_params = RELATION_EXPORT.get("on")()
    near_kind, _ = RELATION_EXPORT.get("near")()
    in_kind, in_params = RELATION_EXPORT.get("in")()
    assert on_kind == "on" and not on_params
    assert near_kind == "next_to"
    # Arena에 포함 관계가 없어 on으로 나가되, 원래 술어가 남아야 왕복 복원이 된다
    assert in_kind == "on" and in_params[CONTAINMENT_HINT] == "in"


def test_relation_export_covers_every_scene_predicate():
    """씬 상태가 쓰는 술어 중 옮길 수 있는 것은 전부 등록되어 있어야 한다.

    빠진 술어는 조용히 사라지지 않고 손실로 보고되지만, on/in/near처럼 표현 가능한
    것을 빠뜨리면 그만큼 배치가 재현되지 않는다.
    """
    from tsdgen.adapters.arena_convert import RELATION_EXPORT

    assert {"on", "in", "near"} <= set(RELATION_EXPORT.names())


def test_dataset_source_strategies_split_real_keys():
    """사람 시연본과 mimicgen 합성본을 실제 키 모양으로 갈라야 한다."""
    from tsdgen.adapters.robocasa_fetch import DATASET_SOURCES

    human_key = "pretrain/atomic/T/20250820/lerobot.tar"
    mg_key = "pretrain/atomic/T/20250820/mg/demo/2025-08-20-21-53-08/lerobot.tar"
    assert DATASET_SOURCES.get("human")(human_key) is True
    assert DATASET_SOURCES.get("human")(mg_key) is False
    assert DATASET_SOURCES.get("mimicgen")(mg_key) is True
    assert DATASET_SOURCES.get("mimicgen")(human_key) is False


def test_adapter_contract_defaults_are_no_ops(settings):
    """계약의 기본 구현은 아무것도 하지 않고 그 사실을 말해야 한다.

    받을 것도 준비할 것도 없는 원천이 계약을 만족시키려고 빈 메서드를 베껴 쓰지
    않도록, 기본 동작을 계약 쪽에 둔다.
    """
    from tsdgen.adapters.base import SourceAdapter

    class Minimal(SourceAdapter):
        def iter_scenarios(self):
            return iter(())

    adapter = Minimal(settings)
    assert "skipped" in adapter.prefetch()
    assert "skipped" in adapter.prepare()
    assert list(adapter.iter_scenarios()) == []


def test_stages_declare_llm_usage():
    """예산 가드 위치는 스테이지 이름이 아니라 선언(calls_llm)으로 정해진다.

    LLM을 부르는 스테이지가 선언을 빠뜨리면 가드가 그 뒤로 밀려 비용이 먼저 나간다.
    역할 호출이 실제로 일어나는 스테이지와 선언이 일치하는지 확인한다.
    """
    declared = {n for n in registry.STAGES.names() if registry.STAGES.get(n).calls_llm}
    assert declared == {"contextualize", "generate", "verify"}
    assert registry.STAGES.get("run").orchestrates


def test_every_role_prompt_builds_from_one_value_table(settings):
    """메시지 조립은 역할과 무관한 한 함수가 한다. 등록된 역할 전부가 같은 값 표로 조립되어야
    비용 추정이 역할별 분기 없이 동작한다."""
    from tsdgen import prompts

    placeholders = ("ORIGINAL_INSTRUCTION", "REWRITTEN_INSTRUCTION", "USER_INSTRUCTION",
                    "ENVIRONMENT_CONTEXT", "TSD_JSON", "TSD_TEXT")
    values = dict.fromkeys(placeholders, "x")
    for role in roles.all_roles():
        messages = prompts.build_messages(settings, role, values)
        assert [m["role"] for m in messages] == ["system", "user"]
        assert all("<<" not in m["content"] for m in messages), role.name


def test_role_variant_changes_prompt_version(settings):
    """폴백 재작성처럼 변형을 바꿔 부르면 기록되는 프롬프트 버전도 그 변형이어야 한다."""
    base = roles.REWRITE.prompt_version(settings)
    assert base.endswith("_" + settings.contextualize.specificity)
    fallback = settings.contextualize.fallback_specificity
    assert roles.REWRITE.prompt_version(settings, fallback).endswith("_" + fallback)
    assert roles.GENERATE.prompt_version(settings) == settings.prompts_versions["generate"]
