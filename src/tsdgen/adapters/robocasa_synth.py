"""RoboCasa365 장면 합성: 주방을 고정하고 태스크 템플릿과 seed를 바꿔 시뮬레이터를 reset한다.

받은 시연은 태스크마다 장면이 몇 개뿐이라 데이터셋 크기를 채울 수 없다. 여기서는 주방(배치·스타일 쌍) 몇 개를
앵커로 고르고, 주방마다 태스크 템플릿을 하나씩 얹어 seed를 바꿔 가며 reset해서 장면과 지시문을 새로 얻는다.
reset은 시뮬레이터 환경(.venv-sim)의 작업자 스크립트가 하고, 결과는 받은 시연과 같은 모양의 에피소드
폴더(ep_meta.json, model.xml.gz, states.npz)로 남으므로 씬 추출과 그 뒤 파이프라인은 그대로다.
지시와 장면이 어긋나거나 성립하지 않는 조합은 여기서 고르지 않는다: 전부 만들고 검수가 거른다.

계획은 설정과 project.seed로 정해진다(같은 설정이면 같은 장면). 이미 만든 에피소드와 실패로 기록된 조합은
다시 돌리지 않으므로, 중간에 끊겨도 다시 실행하면 이어서 한다.
"""

import json
import os
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from tsdgen.log import get_logger
from tsdgen.util import derive_seed

log = get_logger("tsdgen.adapters.robocasa365.synth")

SPLIT = "synth"  # 합성 장면의 split 이름 (받은 시연은 pretrain, target)
DATASET_SOURCE = "sim_reset"
JOBS_ENV = "TSDGEN_SYNTH_JOBS"  # 작업자 스크립트가 작업 목록 경로를 읽는 환경변수
# 작업자 프로세스 하나가 스레드를 여럿 잡으면 여러 작업자가 코어를 두고 다툰다. 하나씩만 쓰게 한다
SINGLE_THREAD = dict.fromkeys(
    ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"), "1")
EPISODE_FILES = ("ep_meta.json", "model.xml.gz", "states.npz")
FAILURE_FILE = "failure.json"
LAYOUT_FACTOR, STYLE_FACTOR = 10_000, 100  # 에피소드 번호 = 배치*10000 + 스타일*100 + 순번


class RoboCasaSynthesisOptions(BaseModel):
    """장면 합성 범위 (config의 source.options.robocasa365.synthesis)."""

    model_config = ConfigDict(extra="forbid")

    kitchens: int  # 앵커 주방 수. 배치·스타일 쌍을 project.seed로 고른다
    templates: list[str] | None = None  # 태스크 템플릿 이름. null이면 splits/families의 태스크 전부
    seeds_per_template: int = 1  # 주방·템플릿마다 reset 횟수 (STYLE_FACTOR 미만)
    workers: int = 8  # 동시에 돌릴 작업자 프로세스 수
    job_timeout_s: int = 180  # reset 한 건의 제한 시간


@dataclass(frozen=True)
class Episode:
    """시나리오로 옮길 에피소드 폴더 하나 (받은 시연과 합성 장면이 함께 쓰는 모양)."""

    task_dir: Path
    split: str
    family: str
    task: str
    index: int
    dataset_source: str
    robot: str
    instruction: str | None = None


def kitchens(simulator: Any, count: int, seed: int) -> list[tuple[int, int]]:
    """앵커 주방 (배치, 스타일) 쌍. 고를 수 있는 범위 전체를 seed로 섞어 앞에서부터 고른다. 그래서 주방 수를
    늘려도 앞서 고른 주방은 그대로이고, 이미 만든 장면에 새 주방의 장면만 더해진다."""
    layouts = range(simulator.layout_ids[0], simulator.layout_ids[1] + 1)
    styles = range(simulator.style_ids[0], simulator.style_ids[1] + 1)
    pairs = [(layout, style) for layout in layouts for style in styles]
    random.Random(seed).shuffle(pairs)
    return pairs[:count]


def plan(settings: Any, options: Any, tasks: list[tuple[str, str, str]]) -> list[dict]:
    """reset 작업 목록. tasks는 원천의 (split, family, task)이고 템플릿으로는 family와 task만 쓴다."""
    synthesis, simulator = options.synthesis, options.simulator
    wanted = set(synthesis.templates) if synthesis.templates is not None else None
    templates = sorted({(family, task) for _split, family, task in tasks
                        if wanted is None or task in wanted})
    root = settings.path("raw_cache") / SPLIT
    jobs = []
    for layout, style in kitchens(simulator, synthesis.kitchens, settings.project.seed):
        for family, task in templates:
            for k in range(synthesis.seeds_per_template):
                index = layout * LAYOUT_FACTOR + style * STYLE_FACTOR + k
                task_dir = root / family / task
                jobs.append({
                    "task": task, "family": family, "layout": layout, "style": style, "index": index,
                    "seed": derive_seed(settings.project.seed, task, layout, style, k),
                    "task_dir": str(task_dir),
                    "out": str(task_dir / "extras" / f"episode_{index:06d}"),
                    "robot": simulator.robot, "obj_instance_split": simulator.obj_instance_split,
                    "timeout_s": synthesis.job_timeout_s,
                })
    return jobs


def _state(job: dict) -> str:
    out = Path(job["out"])
    if all((out / name).exists() for name in EPISODE_FILES):
        return "done"
    return "failed" if (out / FAILURE_FILE).exists() else "pending"


def _launch(settings: Any, simulator: Any, jobs: list[dict], workers: int) -> None:
    """작업을 작업자 프로세스에 나눠 주고 모두 끝날 때까지 기다린다."""
    python = settings.root / simulator.venv / "bin" / "python"
    script = settings.root / simulator.reset_worker
    work_dir = settings.path("tmp") / "synth_jobs"
    work_dir.mkdir(parents=True, exist_ok=True)
    running = []
    for i in range(min(workers, len(jobs))):
        job_file = work_dir / f"jobs_{i:03d}.jsonl"
        chunk = jobs[i::workers]
        job_file.write_text("".join(json.dumps(j) + "\n" for j in chunk), encoding="utf-8")
        log_file = (work_dir / f"worker_{i:03d}.log").open("w", encoding="utf-8")
        process = subprocess.Popen([str(python), str(script)], stdout=log_file, stderr=log_file,
                                   env={**os.environ, **SINGLE_THREAD, JOBS_ENV: str(job_file)})
        running.append((process, log_file))
    for process, log_file in running:
        process.wait()
        log_file.close()


def run(settings: Any, options: Any, tasks: list[tuple[str, str, str]]) -> list[Episode]:
    """계획한 장면 가운데 아직 없는 것을 reset으로 만들고, 만들어져 있는 에피소드를 모두 돌려준다."""
    if options.synthesis is None or options.simulator is None:
        raise ValueError("scene synthesis needs source options 'synthesis' and 'simulator'")
    jobs = plan(settings, options, tasks)
    pending = [job for job in jobs if _state(job) == "pending"]
    log.info(f"synthesis plan: {len(jobs)} scenes, {len(pending)} to reset "
             f"({options.synthesis.workers} workers)")
    if pending:
        _launch(settings, options.simulator, pending, options.synthesis.workers)
    states = [_state(job) for job in jobs]
    log.info(f"synthesis: done {states.count('done')}, failed {states.count('failed')}, "
             f"unfinished {states.count('pending')}")
    return [Episode(Path(job["task_dir"]), SPLIT, job["family"], job["task"], job["index"],
                    DATASET_SOURCE, job["robot"])
            for job, state in zip(jobs, states, strict=True) if state == "done"]
