"""코드 규약 자기 검사. 기준을 바꿀 때는 아래 상수를 고치고 docs/DECISIONS.md에 이유를 남긴다.

ruff(규칙 목록은 pyproject.toml)에 더해, ruff가 잡지 못하는 규약을 여기서 강제한다: 코드 구문 폭(문자열·주석
안의 글자는 세지 않음), 함수·파일 길이, print·argparse, 박아 둔 절대경로, 같은 대상의 문자열 분기 사슬,
사람이 읽는 문구의 em dash와 물결표.
"""

import ast
import io
import re
import subprocess
import sys
import tokenize
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
MAX_CODE_LINE = 100
MAX_FUNCTION_LINES = 60
MAX_FILE_LINES = 400
MIN_DISPATCH_BRANCHES = 3
ABSOLUTE_PATH = re.compile(r"""["'](/work|/home|/data|/mnt|/tmp|/root)/""")
FORBIDDEN_CALLS = {"print", "breakpoint"}
FORBIDDEN_IMPORTS = {"argparse"}
# 사람이 읽는 문구에 쓰지 않는 문자: em dash, 물결표(메신저에서 취소선이 된다)
FORBIDDEN_PROSE_CHARS = {"—": "em dash", "~": "물결표"}
# Python 3.12부터 f-string은 START/MIDDLE/END 토큰으로 나뉜다 (3.11에서는 STRING 하나)
FSTRING_START = getattr(tokenize, "FSTRING_START", None)
FSTRING_END = getattr(tokenize, "FSTRING_END", None)


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _where(path: Path, node: ast.AST) -> str:
    return f"{path.relative_to(PROJECT_ROOT)}:{getattr(node, 'lineno', 0)}"


def test_ruff_clean():
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src", "tests"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _text_spans(source: str) -> dict[int, list[tuple[int, int]]]:
    """줄마다 문자열 리터럴·주석이 차지하는 열 구간. 여러 줄에 걸친 문자열은 중간 줄 전체가 구간이다."""
    spans: dict[int, list[tuple[int, int]]] = defaultdict(list)

    def add(start: tuple[int, int], end: tuple[int, int]) -> None:
        for row in range(start[0], end[0] + 1):
            begin = start[1] if row == start[0] else 0
            spans[row].append((begin, end[1] if row == end[0] else sys.maxsize))

    opened = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in (tokenize.STRING, tokenize.COMMENT):
            add(token.start, token.end)
        elif token.type == FSTRING_START:
            opened.append(token.start)
        elif token.type == FSTRING_END:
            add(opened.pop(), token.end)
    return spans


def test_code_lines_fit_width():
    """100자 제한은 코드 구문에만 건다. 문자열(화면 문구, 설명, 프롬프트, docstring)과 주석 안의 글자는
    세지 않는다. 사람이 읽는 문장을 폭에 맞추려고 강제로 끊지 않게 하려는 것이다."""
    found = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        spans = _text_spans(source)
        for number, line in enumerate(source.splitlines(), 1):
            covered = sum(min(end, len(line)) - begin for begin, end in spans.get(number, []))
            if len(line) - covered > MAX_CODE_LINE:
                found.append(f"{path.relative_to(PROJECT_ROOT)}:{number}")
    assert not found, found


def test_function_and_file_size():
    too_long = []
    for path in _python_files():
        if len(path.read_text(encoding="utf-8").splitlines()) > MAX_FILE_LINES:
            too_long.append(str(path.relative_to(PROJECT_ROOT)))
        for node in ast.walk(_parse(path)):
            is_function = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            if is_function and node.end_lineno - node.lineno + 1 > MAX_FUNCTION_LINES:
                too_long.append(f"{_where(path, node)} {node.name}")
    assert not too_long, too_long


def test_no_print_breakpoint_or_argparse():
    found = []
    for path in _python_files():
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) in FORBIDDEN_CALLS:
                found.append(_where(path, node))
            modules = set()
            if isinstance(node, ast.Import):
                modules = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = {node.module}
            if FORBIDDEN_IMPORTS & modules:
                found.append(_where(path, node))
    assert not found, found


def test_no_hardcoded_absolute_paths():
    found = [
        f"{path.relative_to(PROJECT_ROOT)}:{number}"
        for path in _python_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if ABSOLUTE_PATH.search(line)
    ]
    assert not found, found


def _string_comparison_target(test: ast.expr) -> str | None:
    """`x == "문자열"` 형태면 비교 대상(x)의 표현을 돌려준다."""
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and isinstance(test.comparators[0], ast.Constant)
        and isinstance(test.comparators[0].value, str)
    ):
        return ast.dump(test.left)
    return None


def test_no_string_dispatch_chains():
    """같은 대상을 문자열 여러 개와 비교하는 if/elif 사슬은 등록부(Strategy)로 바꿔야 한다."""
    found = []
    for path in _python_files():
        for node in ast.walk(_parse(path)):
            targets = []
            branch = node
            while isinstance(branch, ast.If):
                targets.append(_string_comparison_target(branch.test))
                branch = branch.orelse[0] if len(branch.orelse) == 1 else None
            if targets and targets[0] and targets.count(targets[0]) >= MIN_DISPATCH_BRANCHES:
                found.append(_where(path, node))
    assert not found, found


def _authored_prose_files() -> list[Path]:
    """우리가 쓴 사람용 문구: 문서, 전달물 템플릿, 대시보드 화면, 파이썬 소스의 문자열·주석.
    외부에서 받은 명세(CODEBASE_SPECIFICATION.md)와 프롬프트 원문은 제외한다."""
    files = [PROJECT_ROOT / name for name in ("README.md", "SPEC.md", "CLAUDE.md") if (PROJECT_ROOT / name).exists()]
    files += sorted((PROJECT_ROOT / "docs").glob("*.md"))
    files += sorted((PROJECT_ROOT / "resources" / "templates").glob("*.md"))
    files += sorted((SRC / "tsdgen" / "dashboard" / "assets").glob("*"))
    return files + _python_files()


def test_prose_has_no_em_dash_or_tilde():
    found = [
        f"{path.relative_to(PROJECT_ROOT)}:{number} {name}"
        for path in _authored_prose_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for char, name in FORBIDDEN_PROSE_CHARS.items()
        if char in line
    ]
    assert not found, found


def test_prompt_templates_have_user_marker():
    from tsdgen.prompts import USER_MARKER, list_prompt_files

    for path in list_prompt_files(PROJECT_ROOT / "prompts"):
        assert USER_MARKER in path.read_text(encoding="utf-8"), path
