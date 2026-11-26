"""마크다운 컨벤션 자기 검사.

산문은 문단·항목마다 한 줄로 쓰고 임의 개행을 넣지 않는다. 코드용 줄 길이
규칙이 사람이 읽는 문장을 자르는 일을 막기 위한 것이며, 검사 대상은 우리가
작성한 문서(README, docs/, 전달물 템플릿)다. 외부에서 받은 명세 문서는 제외.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 목록 항목은 들여쓴 중첩 목록도 새 블록이다 (손으로 접은 개행이 아님)
BLOCK_START_RE = re.compile(r"^(#{1,6} |\s*[-*+] |\s*\d+[.)] |> |\||---|===|\s*$)")


def _authored_markdown() -> list[Path]:
    files = [PROJECT_ROOT / name for name in ("README.md", "SPEC.md", "CLAUDE.md") if (PROJECT_ROOT / name).exists()]
    files += sorted((PROJECT_ROOT / "docs").glob("*.md"))
    files += sorted((PROJECT_ROOT / "resources" / "templates").glob("*.md"))
    return files


def _wrapped_lines(text: str) -> list[tuple[int, str]]:
    """빈 줄로 시작하지 않는 산문 연속 줄(= 손으로 접은 개행)을 찾는다."""
    offenders = []
    in_fence = False
    previous_blank = True
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            previous_blank = False
            continue
        if in_fence:
            continue
        if not line.strip():
            previous_blank = True
            continue
        if not previous_blank and not BLOCK_START_RE.match(line):
            offenders.append((number, line))
        previous_blank = False
    return offenders


@pytest.mark.parametrize("path", _authored_markdown(), ids=lambda p: p.name)
def test_markdown_paragraphs_are_single_lines(path: Path):
    offenders = _wrapped_lines(path.read_text(encoding="utf-8"))
    assert not offenders, (
        f"{path.relative_to(PROJECT_ROOT)}에 손으로 접은 개행이 있다: "
        + "; ".join(f"L{n}: {line[:40]}" for n, line in offenders)
    )
