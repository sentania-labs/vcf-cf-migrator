"""Every source file has to parse on the oldest Python this package supports.

The floor is whatever ``pyproject.toml`` declares in ``requires-python``, read
by ``ci_checks.python_floor`` so the number lives in one place; the release
builds binaries from that floor, so a file that only parses on a newer
interpreter is a broken release, not a CI quirk.

This exists because one did ship that way: ``uipage._button`` carried a
backslash inside an f-string expression, which is a SyntaxError before 3.12.
Every local run and nine review rounds used 3.12, so nothing saw it until CI
ran the 3.9 leg. ``ast.parse(..., feature_version=...)`` costs nothing, needs
no network and no tool, and puts that check on every machine that runs the
suite.

What it does **not** cover: a name that exists on a newer standard library but
parses fine everywhere (``tomllib``, ``itertools.pairwise``, ``X | Y``
evaluated at runtime). CI runs ``vermin`` against the same floor for those,
and the floor it targets comes from this same reader.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

from ci_checks import python_floor, python_floor_text

REPO = pathlib.Path(__file__).resolve().parent.parent
# Everything this package ships or runs. ``build`` is a stale copy of the
# tree and ``corpus`` is the admin's own data, which is never Python.
ROOTS = ("src", "tools", "tests")
SKIP_PARTS = {".venv", "build", "corpus", ".pytest_cache", "__pycache__"}


def source_files():
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            if not SKIP_PARTS & set(path.parts):
                yield path


def test_there_are_source_files_to_check():
    """A sweep over an empty list passes for the wrong reason."""
    files = list(source_files())
    assert len(files) > 20, files


@pytest.mark.parametrize("path", list(source_files()), ids=lambda p: p.name)
def test_every_source_file_parses_on_the_oldest_supported_python(path):
    floor = python_floor()
    source = path.read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(path), feature_version=floor)
    except SyntaxError as e:
        raise AssertionError(
            f"{path.relative_to(REPO)}:{e.lineno} does not parse on Python "
            f"{python_floor_text()}, which pyproject declares as the floor: {e.msg}") from None


def test_the_floor_is_the_one_pyproject_declares():
    assert python_floor() >= (3, 0)
    assert python_floor_text() in (REPO / "pyproject.toml").read_text(encoding="utf-8")


def test_ci_runs_the_floor_it_declares():
    """The workflow's oldest matrix entry has to be the declared floor, or the
    leg that would have caught this is not being run."""
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    versions = []
    for line in workflow.splitlines():
        if "python:" in line and "[" in line:
            versions = [part.strip().strip('"\'')
                        for part in line.split("[", 1)[1].split("]", 1)[0].split(",")]
    assert versions, "the workflow declares no python matrix"
    oldest = min(tuple(int(n) for n in v.split(".")) for v in versions)
    assert oldest == python_floor(), (versions, python_floor_text())


# The one construct ``feature_version`` cannot see -------------------------
#
# ``ast.parse(..., feature_version=(3, 9))`` gates the grammar, not the
# tokenizer, and PEP 701 moved f-string scanning into the parser in 3.12. So
# the very line that broke the 3.9 leg, a backslash inside an f-string
# replacement field, parses clean here under any feature_version, and vermin
# does not flag it either (it reads the file with the running interpreter and
# sees nothing version-specific). On 3.9 the tokenizer refuses the file
# outright, which is what CI saw.
#
# On 3.12 the tree carries accurate positions inside f-strings, so the
# expression text can be recovered and checked. Below 3.12 the interpreter
# running this test would already have refused the file, so there is nothing
# left to check.

def _formatted_expressions(source: str):
    """Every replacement field's source text, on an interpreter that knows
    where they are."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FormattedValue):
            continue
        segment = ast.get_source_segment(source, node.value)
        if segment is not None:
            yield node, segment


@pytest.mark.skipif(sys.version_info < (3, 12),
                    reason="below 3.12 the interpreter refuses such a file on import")
@pytest.mark.parametrize("path", list(source_files()), ids=lambda p: p.name)
def test_no_f_string_expression_carries_a_backslash(path):
    source = path.read_text(encoding="utf-8")
    for node, segment in _formatted_expressions(source):
        assert "\\" not in segment, (
            f"{path.relative_to(REPO)}:{node.lineno} puts a backslash inside an f-string "
            f"expression ({segment!r}), which is a SyntaxError before 3.12 and this "
            f"package supports {python_floor_text()}. Build the value first and "
            "interpolate the name.")
