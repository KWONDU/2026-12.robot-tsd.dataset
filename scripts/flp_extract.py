"""공개 시설 배치(FLP) 벤치마크 인스턴스를 제조 지도 생성기가 읽는 JSON으로 옮긴다.

원본: joehuchette/FloorLayout.jl(MIT)의 src/get_problem_data.jl에 실린 실제 문헌 인스턴스
(Armour & Buffa 1962, Bazaraa 1975, van Camp 1991, Bozer 1991·1997). 인스턴스마다 공장 크기, 부서(작업장) 면적,
부서 사이 물동량(위 삼각 행렬, i < j면 i에서 j로 흐른다고 본다)을 그대로 옮긴다. Rand*, hp*, small_test는 뺀다.
사용: .conda/bin/python scripts/flp_extract.py <get_problem_data.jl> <출력 json>
"""

import json
import re
import sys
from pathlib import Path

KEEP = ("Armour62_1", "Armour62_2", "Bazaraa75_1", "Bazaraa75_2", "Camp91", "Bozer91", "Bozer97_1", "Bozer97_2")
BLOCK = re.compile(r"problem_specific\(::Val\{:(\w+)\}\)(.*?)\nend", re.S)


def _numbers(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text)]


def _instance(name: str, body: str) -> dict | None:
    size = re.search(r"Lˣ,\s*Lʸ\s*=\s*([\d.]+),\s*([\d.]+)", body)
    areas = re.search(r"α\s*=\s*\[([^\]]*)\]", body)
    flows = re.search(r"\bc\s*=\s*\[(.*?)\]", body, re.S)
    if not (size and areas):
        return None
    alpha = _numbers(areas.group(1))
    if flows:  # 행렬을 그대로 적은 꼴
        rows = [line.split("#")[0] for line in flows.group(1).splitlines()]
        matrix = [_numbers(row) for row in rows if _numbers(row)]
    else:  # c[i, j] = 값 으로 하나씩 적은 꼴 (i에서 j로 흐름, 1부터 센다)
        matrix = [[0.0] * len(alpha) for _ in alpha]
        for i, j, value in re.findall(r"c\[\s*(\d+),\s*(\d+)\]\s*=\s*([\d.]+)", body):
            matrix[int(i) - 1][int(j) - 1] = float(value)
    if len(matrix) != len(alpha) or any(len(row) != len(alpha) for row in matrix):
        return None
    return {"name": name, "facility": [float(size.group(1)), float(size.group(2))],
            "areas": alpha, "flows": matrix}


def main() -> None:
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    text = source.read_text(encoding="utf-8")
    found = [_instance(name, body) for name, body in BLOCK.findall(text) if name in KEEP]
    instances = [i for i in found if i]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"source": "joehuchette/FloorLayout.jl (MIT)", "instances": instances},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stderr.write(f"{len(instances)} instances: {[i['name'] + ':' + str(len(i['areas'])) for i in instances]}\n")


if __name__ == "__main__":
    main()
