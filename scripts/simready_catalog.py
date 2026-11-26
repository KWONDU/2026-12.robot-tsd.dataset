"""NVIDIA SimReady 에셋 모음(nvidia/simready-assets)의 에셋별 기본 파일에서 목록을 만든다: 이름, 분류 폴더,
크기(m), 질량, Wikidata 코드, 움직이는 부분 수, 그리고 받아 둔 경우 관절(이름·종류·범위).

에셋 하나는 기본 USD 하나와 payloads 폴더로 되어 있다. 기본 파일의 레이어 메타데이터에 크기·질량이 있고,
관절은 payloads/Physics/physics.usda에 있다. 그림·형상 파일은 받지 않아도 된다(시뮬레이터 없이 읽는다).
받은 파일을 여는 스크립트라 격리 모드로 돌린다:
    .conda/bin/python -I scripts/simready_catalog.py <usd-core 폴더> <에셋 폴더> <출력 json>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from pxr import Sdf, Usd  # noqa: E402

JOINT_FILE = "simready_usd/payloads/Physics/physics.usda"
ROOT_GLOB = "simready_usd/*.usd*"


def joints(asset_dir: Path) -> list[dict] | None:
    """관절 목록. 관절 파일을 받지 않았으면 None."""
    path = asset_dir / JOINT_FILE
    if not path.exists():
        return None
    stage = Usd.Stage.Open(str(path))
    found = []
    for prim in stage.TraverseAll():  # 관절은 정의 없이 덮어쓰기(over)만 한 부모 아래에 있어 Traverse로는 안 보인다
        kind = str(prim.GetTypeName())
        if "Joint" not in kind or kind == "PhysicsFixedJoint":
            continue
        limits = [prim.GetAttribute(n).Get() for n in ("physics:lowerLimit", "physics:upperLimit")]
        found.append({"name": prim.GetName(), "type": kind.replace("Physics", ""),
                      "limits": [float(v) if v is not None else None for v in limits]})
    return found


def entry(asset_dir: Path) -> dict | None:
    roots = sorted(asset_dir.glob(ROOT_GLOB))
    if not roots:
        return None
    data = dict(Sdf.Layer.FindOrOpen(str(roots[0])).customLayerData)
    meta = data.get("SimReady_Metadata") or {}
    extents = meta.get("asset_extents")
    return {"asset": asset_dir.name, "group": str(data.get("asset_dir", "")).split("/")[-2:-1],
            "size_m": [round(float(v), 4) for v in extents] if extents else None,
            "mass_kg": meta.get("mass"), "qcode": meta.get("qcode"),
            "moving_parts": meta.get("rigid_body_count"), "license": meta.get("asset_license"),
            "joints": joints(asset_dir)}


def main() -> None:
    assets, out = Path(sys.argv[2]), Path(sys.argv[3])
    rows = [e for e in (entry(d) for d in sorted(assets.iterdir()) if d.is_dir()) if e]
    for row in rows:
        row["group"] = row["group"][0] if row["group"] else None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": "nvidia/simready-assets", "assets": rows}, ensure_ascii=False),
                   encoding="utf-8")
    sys.stdout.write(f"{len(rows)} assets -> {out}\n")


main()
