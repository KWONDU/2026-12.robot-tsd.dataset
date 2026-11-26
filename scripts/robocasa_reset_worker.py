"""RoboCasa365 태스크를 정한 주방(배치·스타일)과 seed로 reset해 첫 상태를 에피소드 폴더로 남긴다.

시뮬레이터 환경(.venv-sim)의 파이썬으로 돌린다. 파이프라인 쪽(tsdgen.adapters.robocasa_synth)이 작업 목록을
jsonl 파일로 써 주고 환경변수 TSDGEN_SYNTH_JOBS로 그 경로를 알려 준다. 작업 한 줄은
{"task", "layout", "style", "seed", "out", "robot", "obj_instance_split", "timeout_s"}이다.

작업마다 out 폴더에 데이터셋 에피소드와 같은 세 파일(ep_meta.json, model.xml.gz, states.npz)을 쓴다.
그래서 기존 씬 추출이 그대로 읽는다. 실패하거나 시간이 넘으면 세 파일 대신 failure.json에 이유를 남기고
다음 작업으로 넘어간다 (작업 하나의 실패가 묶음을 멈추지 않는다).
"""

import gzip
import json
import os
import signal
from pathlib import Path

import numpy as np
import robocasa  # noqa: F401  (RoboCasa 태스크를 robosuite에 등록한다)
import robosuite
from robosuite.controllers import load_composite_controller_config

JOBS_ENV = "TSDGEN_SYNTH_JOBS"
EPISODE_FILES = ("ep_meta.json", "model.xml.gz", "states.npz")
FAILURE_FILE = "failure.json"


class JobTimeout(Exception):
    pass


def _on_alarm(_signum, _frame):
    raise JobTimeout("reset took longer than the job timeout")


def reset_once(job: dict) -> None:
    out = Path(job["out"])
    env = robosuite.make(
        env_name=job["task"],
        robots=job["robot"],
        controller_configs=load_composite_controller_config(controller=None, robot=job["robot"]),
        has_renderer=False,
        has_offscreen_renderer=False,
        use_camera_obs=False,
        use_object_obs=True,
        ignore_done=True,
        seed=job["seed"],
        layout_and_style_ids=[(job["layout"], job["style"])],
        obj_instance_split=job["obj_instance_split"],
    )
    try:
        env.reset()
        ep_meta = env.get_ep_meta()
        out.mkdir(parents=True, exist_ok=True)
        with gzip.open(out / "model.xml.gz", "wt", encoding="utf-8") as f:
            f.write(env.sim.model.get_xml())
        np.savez_compressed(out / "states.npz", states=np.asarray([env.sim.get_state().flatten()]))
        # ep_meta를 마지막에 쓴다: 이 파일이 있으면 세 파일이 다 있는 것이다
        (out / "ep_meta.json").write_text(json.dumps(ep_meta, ensure_ascii=False), encoding="utf-8")
    finally:
        env.close()


def main() -> None:
    jobs = [json.loads(line) for line in Path(os.environ[JOBS_ENV]).read_text().splitlines() if line]
    signal.signal(signal.SIGALRM, _on_alarm)
    for job in jobs:
        out = Path(job["out"])
        if all((out / name).exists() for name in EPISODE_FILES) or (out / FAILURE_FILE).exists():
            continue
        signal.alarm(int(job["timeout_s"]))
        try:
            reset_once(job)
        except Exception as error:  # noqa: BLE001  (어떤 실패든 기록하고 다음 작업으로)
            out.mkdir(parents=True, exist_ok=True)
            reason = {"error": f"{type(error).__name__}: {error}"[:500]}
            (out / FAILURE_FILE).write_text(json.dumps(reason, ensure_ascii=False), encoding="utf-8")
        finally:
            signal.alarm(0)


if __name__ == "__main__":
    main()
