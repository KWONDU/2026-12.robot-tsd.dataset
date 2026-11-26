"""SimReady 창고 팩의 조립체(작업대, 랙, 팔레트 묶음, 카트)에서 놓인 에셋의 이름·위치·크기를 뽑아 JSON으로 쓴다.

USD를 읽는 일은 여기서만 한다(usd-core가 필요하다). 파이프라인의 원천 어댑터는 이 결과 파일만 읽으므로
USD 라이브러리가 없어도 된다. 받은 파일을 여는 스크립트라 격리 모드로 돌린다:
    .conda/bin/python -I scripts/simready_extract.py <usd-core가 설치된 폴더> <팩 폴더> <출력 json>
"""
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from pxr import Gf, Usd, UsdGeom  # noqa: E402

PHYSICS_SUFFIX = "_physics"  # 목록(CSV)의 이름은 물리 속성이 붙은 판의 이름이다
ASSEMBLY_DIR = "Props/assembly"
CATALOG_FILE = "physical_ai_simready_warehouse_01.csv"
UPRIGHT_COS = 0.95  # 에셋의 위쪽 축이 수직과 이루는 각의 코사인이 이보다 작으면 기울어 있다 (약 18도)


def read_catalog(pack: Path) -> dict[str, dict]:
    with open(pack / CATALOG_FILE, encoding="utf-8-sig") as f:
        return {row["asset_name"].lower().replace(PHYSICS_SUFFIX, ""): row for row in csv.DictReader(f)}


def referenced(prim) -> list[str]:
    """프림이 참조하는 에셋 파일 이름(확장자 없이)."""
    refs = prim.GetMetadata("references") or prim.GetMetadata("payload")
    if not refs:
        return []
    items = list(getattr(refs, "prependedItems", [])) + list(getattr(refs, "explicitItems", []))
    paths = [str(getattr(item, "assetPath", "")) for item in items]
    return [re.sub(r"\.usd[a-z]?$", "", p.split("/")[-1]) for p in paths if p]


def placed_assets(stage, catalog: dict[str, dict]) -> list[dict]:
    """목록에 있는 에셋이 놓인 자리. 에셋 안쪽의 부품은 세지 않는다(가장 바깥 것만)."""
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    meters = UsdGeom.GetStageMetersPerUnit(stage)
    found, taken = [], []
    for prim in stage.Traverse():
        path = prim.GetPath().pathString
        if any(path.startswith(parent + "/") for parent in taken):
            continue
        name = next((n for n in referenced(prim) if n.lower() in catalog), None)
        if not name:
            continue
        taken.append(path)
        bound = cache.ComputeWorldBound(prim)
        box = bound.ComputeAlignedRange()
        if box.IsEmpty():
            continue
        row = catalog[name.lower()]
        found.append({
            "asset": name, "label": row["label"], "classification": row["classification"],
            "mass_kg": float(row["mass"]) if row["mass"] else None,
            "min_m": [round(v * meters, 4) for v in box.GetMin()],
            "max_m": [round(v * meters, 4) for v in box.GetMax()],
            **_oriented(cache, prim, meters),
        })
    return found


def _oriented(cache, prim, meters: float) -> dict:
    """에셋 자체의 크기(돌려 놓아도 변하지 않는 가로·세로·높이)와 똑바로 서 있는지.
    축에 맞춘 상자는 비스듬히 돌려 놓은 물체를 실제보다 크게 잡으므로, 담김·쌓임 판단에는 이 크기를 쓴다."""
    local = cache.ComputeUntransformedBound(prim).ComputeAlignedRange()
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    axes = [Gf.Vec3d(matrix.GetRow3(k)) for k in range(3)]
    scale = [axis.GetLength() for axis in axes]
    size = [round((local.GetMax()[k] - local.GetMin()[k]) * scale[k] * meters, 4) for k in range(3)]
    up = axes[2].GetNormalized() if scale[2] else Gf.Vec3d(0, 0, 1)
    return {"size_m": size, "upright": bool(abs(up[2]) > UPRIGHT_COS)}


def main() -> None:
    pack, out = Path(sys.argv[2]), Path(sys.argv[3])
    catalog = read_catalog(pack)
    cells = {}
    for folder in sorted((pack / ASSEMBLY_DIR).iterdir()):
        files = sorted(f for f in folder.glob("*.usd") if PHYSICS_SUFFIX not in f.name)
        if not files:
            continue
        stage = Usd.Stage.Open(str(files[0]))
        cells[folder.name] = {"file": str(files[0].relative_to(pack)),
                              "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
                              "items": placed_assets(stage, catalog)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"pack": pack.name, "cells": cells}, ensure_ascii=False), encoding="utf-8")
    sys.stdout.write(f"{len(cells)} cells, {sum(len(c['items']) for c in cells.values())} placed assets -> {out}\n")


main()
