"""설정 로더와 검증.

실행 대상 선택은 환경변수(STAGE_ENV_VAR) 하나로만 한다. 그 외 값은 전부 설정
파일에서 오며, config가 고르는 축은 로드 시점에 registry 등록 여부를 검증해
미등록 값을 거부한다.

설정 파일은 성격으로 나뉜다. config.yaml은 이번 실행의 조정값이고, 그 안의
registries가 가리키는 파일들은 실행마다 바뀌지 않는 대상의 속성이다. 값 하나의
출처는 여전히 한 곳이며, 로드 시점에 하나의 Settings로 합쳐진다. 비밀값은 어느
파일에도 없고 환경변수로만 오되, 편의를 위해 프로젝트 루트의 .env를 읽어
비어 있는 환경변수만 채운다(이미 설정된 값은 덮어쓰지 않는다).
"""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tsdgen import registry
from tsdgen.tasks.options import ComposeCfg

STAGE_ENV_VAR = "TSDGEN_STAGE"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCfg(StrictModel):
    run_id: str
    seed: int
    lang: str
    n_scenarios: int
    pipeline_order: list[str]


class PathsCfg(StrictModel):
    raw_cache: str
    scenarios: str
    static_map_dir: str
    llm_cache: str
    runs: str
    deliverables: str
    schema_dir: str
    prompts: str
    resources: str
    tmp: str
    thumbnails: str = "data/thumbnails"  # 장면 썸네일 <어댑터>/<장면 id의 /를 _로>.png


class SourceCfg(StrictModel):
    adapter: str
    prefetch_sources: list[str]
    # 어댑터별 설정. 스키마는 각 어댑터가 options_model로 선언하므로 데이터셋을 추가해도 이 파일은 바뀌지 않는다(검증은 로드 시점에 어댑터).
    options: dict[str, dict[str, Any]] = Field(default_factory=dict)


class SamplingCfg(StrictModel):
    # 선별 전략별 설정 블록은 전략 이름을 키로 둔다 (예: stratified: {...}). 스키마는 각 전략이 options_model로
    # 선언하므로 전략을 추가해도 이 파일은 바뀌지 않는다. 블록 이름과 내용은 로드 시점에 Settings가 검증한다.
    model_config = ConfigDict(extra="allow")

    strategy: str
    filters: list[str]
    same_scenarios_as_run: str | None = None  # 그 실행이 고른 장면을 그대로 다시 고른다 (null이면 전략)
    exclude_tasks_of_runs: list[str] = Field(default_factory=list)  # 이 실행들이 쓴 태스크는 빼고 고른다

    def options_block(self, strategy: str) -> dict[str, Any]:
        return (self.model_extra or {}).get(strategy) or {}


class ContextualizeCfg(StrictModel):
    specificity: str
    # 재작성이 해소 불가 판정을 받으면 이 구체성으로 한 번 더 쓴다 (스펙 6.2)
    fallback_specificity: str = "explicit"
    # LLM에게 세계 상태를 보여 주는 방식 (world_state.WORLD_STATE_VIEWS 등록 이름). 기본은 이전과 같은 json
    world_state: str = "json"
    # text 보기에서 가구 배치가 없는 물체의 받침 물체를 좌표로 찾는 기준 (수평 거리, 높이 차, m)
    support_radius_m: float = 0.06
    support_height_m: float = 0.08
    size_ratio_min: float = 1.15  # 같은 이름 물체의 크기 비교를 적는 부피 비 하한


class ModelCfg(StrictModel):
    backend: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    supports_json_schema: bool = False
    # 스키마를 강제로 지키게 할지 (OpenAI structured outputs의 strict). 강제하면 모든 칸을 필수로 두고
    # 빈 값은 null로 받는 모양으로 바꿔 보낸다(llm/schema.py). vLLM·FriendliAI는 스키마를 늘 강제한다.
    json_schema_strict: bool = False
    token_param: str = "max_tokens"
    # 요청 본문에 그대로 실리는 모델별 파라미터. 역할이 정한 값보다 우선하며,
    # 값이 null이면 그 키를 아예 빼서 보낸다. 모델마다 받는 파라미터가 다르므로
    # (reasoning_effort, verbosity, temperature 미지원 등) 코드가 아니라 여기서 정한다.
    params: dict[str, Any] = Field(default_factory=dict)
    concurrency: int = 1
    enabled: bool = False
    license_note: str | None = None


class GenParamsCfg(StrictModel):
    temperature: float
    max_output_tokens: int
    json_retry: int
    # 이 역할이 원하는 추가 요청 파라미터 (reasoning_effort, verbosity 등).
    # 모델별 params가 나중에 적용되므로, 받지 못하는 모델은 그쪽에서 null로 뺄 수 있다.
    extra: dict[str, Any] = Field(default_factory=dict)


class RetryCfg(StrictModel):
    max_attempts: int
    base_delay_s: float
    request_timeout_s: float


class RolesCfg(StrictModel):
    rewriter: str
    generators: list[str]
    checklist_judge: dict[str, str]
    probe_judge: str
    resolvability_judge: str


class LLMCfg(StrictModel):
    models: dict[str, ModelCfg]
    roles: RolesCfg
    per_scenario_k: int
    selection: str
    generation: GenParamsCfg
    judging: GenParamsCfg
    retry: RetryCfg


class VerifyThresholdsCfg(StrictModel):
    checklist_min_item_score: int
    probe_min_score: int
    # 원천 성공 판정(정답) 대비 목표 상태 재현율의 하한. None이면 기록만 하고 탈락시키지 않는다.
    goal_oracle_min_recall: float | None = None


class VerifyCfg(StrictModel):
    checks: list[str]
    thresholds: VerifyThresholdsCfg
    # 채점 척도의 상한 (checklist 항목·probe 공통). 척도는 채점 프롬프트가 정의한다.
    score_max: int = 2


class EstimateCfg(StrictModel):
    chars_per_token: float
    assumed_output_tokens: dict[str, int]
    budget_usd: float | None
    sample_records: int | None = None  # 비용 추정을 이만큼의 장면으로 하고 전체 수로 늘린다 (null이면 전부)
    confirm_over_budget: bool
    prices_per_1m_tokens: dict[str, dict[str, float | None]]


class DashboardCfg(StrictModel):
    host: str
    port: int
    run_id: str | None
    poll_seconds: int = 15
    stale_after_min: int = 15
    model: str
    prompt_version: str
    temperature: float
    max_output_tokens: int
    json_retry: int
    extra: dict[str, Any] = Field(default_factory=dict)
    dataset: dict[str, list[str]] = Field(default_factory=dict)  # 최종 데이터셋: 도메인 -> 묶을 실행 (config 주석)


class ExportCfg(StrictModel):
    formats: list[str]
    # 패키지와 함께 전달하는 분석 리포트(Claude Artifact)의 URL. 발행 후 채우고
    # export를 다시 돌리면 README에 실린다. null이면 해당 줄을 생략한다.
    report_url: str | None = None
    # kist_json이 내보낼 생성 모델. 이 모델이 만들고 검수를 통과한 샘플만 내보낸다.
    # null이면 선별(best_of_k) 채택본을 내보낸다.
    kist_generator: str | None = None


class RegistriesCfg(StrictModel):
    models: str
    sources: str


class Settings(StrictModel):
    registries: RegistriesCfg
    project: ProjectCfg
    paths: PathsCfg
    source: SourceCfg
    sampling: SamplingCfg
    contextualize: ContextualizeCfg
    compose: dict[str, ComposeCfg] = Field(default_factory=dict)  # 도메인 이름 -> 그 도메인의 작업 합성 설정
    llm: LLMCfg
    prompts_versions: dict[str, str]
    verify: VerifyCfg
    estimate: EstimateCfg
    dashboard: DashboardCfg
    export: ExportCfg
    schema_versions: dict[str, str]

    root: Path = PROJECT_ROOT

    def path(self, name: str) -> Path:
        """paths 섹션의 항목을 프로젝트 루트 기준 절대 경로로 돌려준다."""
        return self.root / getattr(self.paths, name)

    def run_dir(self) -> Path:
        return self.path("runs") / self.project.run_id

    @property
    def domain(self) -> str:
        """이 실행의 도메인. 원천이 정한다(sources.yaml의 그 원천 블록 domain)."""
        domain = getattr(self.adapter_options(self.source.adapter), "domain", None)
        if not domain:
            raise ValueError(f"source adapter '{self.source.adapter}' declares no domain")
        return domain

    def adapter_options(self, adapter_name: str) -> Any:
        """어댑터가 선언한 스키마로 검증된 설정. 어댑터만 자기 설정 모양을 안다."""
        model = registry.SOURCE_ADAPTERS.get(adapter_name).options_model
        return model.model_validate(self.source.options.get(adapter_name) or {})

    def sampling_options(self, strategy_name: str | None = None) -> Any:
        """선별 전략이 선언한 스키마로 검증된 설정. 전략만 자기 설정 모양을 안다."""
        name = strategy_name or self.sampling.strategy
        model = registry.SAMPLING_STRATEGIES.get(name).options_model
        return model.model_validate(self.sampling.options_block(name))

    def model_cfg(self, key: str) -> ModelCfg:
        if key not in self.llm.models:
            raise ValueError(
                f"model '{key}' is not in llm.models; allowed: {sorted(self.llm.models)}"
            )
        return self.llm.models[key]

    def allowed_specificities(self) -> list[str]:
        """rewrite 프롬프트 파일 목록에서 허용 specificity를 유도한다(단일 출처)."""
        version = self.prompts_versions["rewrite"]
        prefix = f"{version}_"
        rewrite_dir = self.path("prompts") / "rewrite"
        return sorted(
            p.stem.removeprefix(prefix)
            for p in rewrite_dir.glob(f"{prefix}*.txt")
        )

    @model_validator(mode="after")
    def _validate_against_registries(self) -> "Settings":
        registry.load_all()
        registry.validate_all(self)
        self._validate_pipeline()
        self._validate_adapter_options()
        self._validate_sampling_options()
        self._validate_dashboard()
        self._validate_role_bindings()
        self._validate_role_tables()
        self._validate_specificity()
        return self

    def _validate_dashboard(self) -> None:
        self.model_cfg(self.dashboard.model)

    def _validate_pipeline(self) -> None:
        """다른 스테이지를 돌리는 스테이지(run 등)는 파이프라인 순서에 넣을 수 없다."""
        nested = [n for n in self.project.pipeline_order if registry.STAGES.get(n).orchestrates]
        if nested:
            raise ValueError(f"pipeline_order must not contain orchestrating stages: {nested}")

    def _validate_adapter_options(self) -> None:
        """설정에 적힌 어댑터 옵션이 그 어댑터의 스키마와 맞는지 확인한다."""
        for adapter_name in self.source.options:
            self.adapter_options(adapter_name)

    def _validate_sampling_options(self) -> None:
        """전략별 설정 블록의 이름이 등록된 전략이고, 내용이 그 전략의 스키마와 맞는지 확인한다."""
        for block_name in self.sampling.model_extra or {}:
            self.sampling_options(block_name)
        self.sampling_options()

    def _validate_role_bindings(self) -> None:
        """역할에 묶인 모델이 정의되어 있고, 채점 모델이 생성 모델과 다른지 확인한다."""
        roles = self.llm.roles
        bound = [roles.rewriter, roles.probe_judge, roles.resolvability_judge, *roles.generators]
        bound += list(roles.checklist_judge.keys()) + list(roles.checklist_judge.values())
        for key in bound:
            model = self.model_cfg(key)
            # 키가 필요한지는 백엔드 전략이 스스로 선언한다 (이름으로 분기하지 않는다)
            backend = registry.LLM_BACKENDS.get(model.backend)
            if backend.needs_api_key and model.api_key_env is None:
                raise ValueError(
                    f"model '{key}': api_key_env is required for backend {model.backend}"
                )
        for gen in roles.generators:
            if not self.llm.models[gen].enabled:
                raise ValueError(f"generator '{gen}' is bound but enabled=false")
            judge = roles.checklist_judge.get(gen)
            if judge is None:
                raise ValueError(f"generator '{gen}' has no checklist_judge mapping")
            if judge == gen:
                raise ValueError(f"checklist judge must differ from generator (both '{gen}')")

    def _validate_role_tables(self) -> None:
        """역할별 표(프롬프트 버전, 출력 토큰 가정)가 등록된 역할을 빠짐없이 덮는지 확인한다."""
        expected = set(registry.ROLES.names())
        for field, table in (
            ("prompts_versions", self.prompts_versions),
            ("estimate.assumed_output_tokens", self.estimate.assumed_output_tokens),
        ):
            missing = sorted(expected - set(table))
            extra = sorted(set(table) - expected)
            if missing or extra:
                raise ValueError(
                    f"{field} must have exactly the registered roles {sorted(expected)}; "
                    f"missing={missing} unexpected={extra}"
                )

    def _validate_specificity(self) -> None:
        allowed = self.allowed_specificities()
        cfg = self.contextualize
        for spec in (cfg.specificity, cfg.fallback_specificity):
            if allowed and spec not in allowed:
                raise ValueError(
                    f"unknown specificity '{spec}'; allowed (from prompt files): {allowed}"
                )


ENV_FILE = ".env"
PRICE_KEY = "price_per_1m_tokens"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_env_file(root: Path) -> list[str]:
    """.env의 값으로 비어 있는 환경변수만 채운다. 이미 설정된 값은 그대로 둔다."""
    path = root / ENV_FILE
    if not path.exists():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name and not os.environ.get(name):
            os.environ[name] = value.strip().strip("\"'")
            loaded.append(name)
    return loaded


def load_config_document(config_path: Path | None = None, root: Path | None = None) -> dict:
    """설정 파일들을 읽어 하나의 문서로 합친다. 검증은 Settings가 한다."""
    path = config_path or CONFIG_PATH
    data = _read_yaml(path)
    project_root = root or path.resolve().parents[1]
    data["root"] = project_root

    models_doc = _read_yaml(project_root / data["registries"]["models"])
    data.setdefault("llm", {})["models"] = {
        key: {k: v for k, v in spec.items() if k != PRICE_KEY}
        for key, spec in models_doc["models"].items()
    }
    data.setdefault("estimate", {})["prices_per_1m_tokens"] = {
        key: spec.get(PRICE_KEY) or {"input": None, "output": None}
        for key, spec in models_doc["models"].items()
    }

    # 원천의 속성(sources.yaml)과 이번 실행의 범위(config.yaml)를 어댑터별로 합친다
    sources_doc = _read_yaml(project_root / data["registries"]["sources"])
    options = data["source"].setdefault("options", {})
    for name, spec in sources_doc.items():
        options[name] = {**spec, **(options.get(name) or {})}
    load_env_file(project_root)
    return data


def load_settings(config_path: Path | None = None, root: Path | None = None) -> Settings:
    return Settings.model_validate(load_config_document(config_path, root))


def current_stage() -> str:
    stage = os.environ.get(STAGE_ENV_VAR)
    if not stage:
        raise SystemExit(
            f"set {STAGE_ENV_VAR} to one of: {registry.STAGES.names()} "
            f"(예: {STAGE_ENV_VAR}=run python -m tsdgen)"
        )
    return stage
