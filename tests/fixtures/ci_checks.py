"""The numbers CI needs, taken from the code and the fixture rather than typed.

A workflow that carries a content number of its own goes stale the moment the
fixture grows, and it goes stale in the one place the local suite does not
look: `tests/` compares against the fixture's own expectations and stays
green, while CI fails on a hand-copied count. That happened once, with
``len(items) == 16`` against a fixture that had grown to 20.

So the workflow asks here instead, and owns nothing:

    python tests/fixtures/ci_checks.py listing inspect.json
    python tests/fixtures/ci_checks.py listing bundle.json --bundle
    python tests/fixtures/ci_checks.py floor          -> 8.10
    python tests/fixtures/ci_checks.py below-floor    -> 8.9
    python tests/fixtures/ci_checks.py preview-object -> dashboard:<uuid>@<owner>
    python tests/fixtures/ci_checks.py python-floor   -> 3.9
    python tests/fixtures/ci_checks.py preview preview.html

``listing`` compares an ``inspect --json`` document against
``make_export_fixture.EXPECTED_ITEMS``, the same set the suite asserts on, so
the two can never disagree. ``floor`` and ``below-floor`` come from
``export_reader.VERSION_FLOOR``, so moving the floor moves the workflow's
declared versions with it.

Run from anywhere; the fixture module is found next to this file.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_export_fixture import (  # noqa: E402
    DASHBOARD_ID,
    EXPECTED_CARRIED,
    EXPECTED_DASHBOARD_LISTINGS,
    EXPECTED_ITEMS,
    OWNER,
)


def floor_text() -> str:
    from vcfcf_migrator.export_reader import VERSION_FLOOR

    return ".".join(str(part) for part in VERSION_FLOOR)


def below_floor_text() -> str:
    """A version the tool must refuse, derived from the floor it enforces."""
    from vcfcf_migrator.export_reader import VERSION_FLOOR

    major, minor = VERSION_FLOOR[0], VERSION_FLOOR[1]
    return f"{major}.{minor - 1}" if minor else f"{major - 1}.0"


def python_floor() -> tuple:
    """The oldest Python this package supports, read from pyproject.

    From ``requires-python``, never typed here: the floor is declared in one
    place and the release builds binaries from it, so a second copy of the
    number is a second thing to forget. Parsed with a regex rather than a TOML
    reader because ``tomllib`` arrived in 3.11 and this has to run on the
    floor itself.
    """
    import re

    text = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^requires-python\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise AssertionError("pyproject.toml declares no requires-python")
    version = re.search(r"(\d+)\.(\d+)", match.group(1))
    if not version:
        raise AssertionError(f"cannot read a version out of {match.group(1)!r}")
    return (int(version.group(1)), int(version.group(2)))


def python_floor_text() -> str:
    return ".".join(str(part) for part in python_floor())


def preview_object() -> str:
    """An object spelling the workflow can preview, taken from the fixture so
    the workflow owns no identifier of its own.

    A dashboard rather than a view: the dashboard carries one widget of every
    type the preview draws, so the installed console script exercises the
    renderers rather than only the view path.
    """
    return f"dashboard:{DASHBOARD_ID}@{OWNER}"


def check_preview(text: str) -> str:
    """A preview page is one self-contained HTML file that reaches nothing.

    The suite checks this too; CI checks the *installed* console script's
    output, which is the artifact an admin runs.
    """
    assert text.startswith("<!doctype html>"), text[:80]
    for forbidden in ("http://", "https://", "<script", "<img", "@import"):
        assert forbidden not in text.lower(), forbidden
    # Names from the export: the dashboard's own title, and the embedded
    # view's. Plus the marks of two renderers actually running, so a preview
    # that degraded to placeholders fails here.
    for expected in ("[Fixture] Cluster Overview", "[Fixture] Cluster List",
                     "<polyline", "pv-tiles"):
        assert expected in text, f"{expected} is missing from the preview"
    return f"preview is self-contained HTML, {len(text)} bytes, no external reference"


def check_listing(doc: dict, bundle: bool = False) -> str:
    """Compare an ``inspect --json`` document with what the fixture holds.

    The same item set is expected of the source export and of a select-all
    bundle built from it, which is the round trip in one line. They differ in
    one thing: a bundle carries no member this tool does not understand, so
    *bundle* flips the "carried, not inspected" assertion from the fixture's
    set to the empty set rather than skipping it.

    Returns a one-line summary; raises ``AssertionError`` naming the
    difference, so a failure says which items appeared or went missing rather
    than only that two numbers differ.
    """
    got = {(item["kind"], item["name"], item["uuid"]) for item in doc["items"]}
    extra = sorted(got - EXPECTED_ITEMS)
    absent = sorted(EXPECTED_ITEMS - got)
    if extra or absent:
        raise AssertionError(
            f"the listing does not match the fixture: {len(absent)} expected item(s) "
            f"missing {absent}, {len(extra)} unexpected item(s) {extra}")
    # A dashboard under two owners lists once per owner, which a set of
    # (kind, name, uuid) collapses, so the total is derived rather than
    # compared: every expected item lists once, except dashboards, which list
    # once per owner.
    unique_dashboards = sum(1 for kind, _n, _u in EXPECTED_ITEMS if kind == "dashboard")
    want = len(EXPECTED_ITEMS) - unique_dashboards + EXPECTED_DASHBOARD_LISTINGS
    listings = len(doc["items"])
    assert listings == want, (listings, want, doc["counts"])
    assert doc["counts"]["dashboard"] == EXPECTED_DASHBOARD_LISTINGS, doc["counts"]
    want_carried = set() if bundle else EXPECTED_CARRIED
    assert set(doc["carried"]) == want_carried, (doc["carried"], want_carried)
    return (f"listing matches the fixture: {listings} items, {doc['counts']}"
            + (", no unreadable member carried" if bundle else ""))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="what", required=True)
    sub.add_parser("floor", help="the lowest source version the tool accepts")
    sub.add_parser("preview-object", help="an object of the fixture to preview")
    sub.add_parser("python-floor", help="the oldest Python this package supports")
    prev = sub.add_parser("preview", help="check a preview HTML file")
    prev.add_argument("path", help="the HTML file, or - for stdin")
    sub.add_parser("below-floor", help="a source version the tool must refuse")
    listing = sub.add_parser("listing", help="check an inspect --json document")
    listing.add_argument("path", help="the JSON file, or - for stdin")
    listing.add_argument("--bundle", action="store_true",
                         help="the document describes a bundle, which carries no "
                              "member this tool does not understand")
    args = parser.parse_args(argv)

    if args.what == "floor":
        print(floor_text())
        return 0
    if args.what == "below-floor":
        print(below_floor_text())
        return 0
    if args.what == "python-floor":
        print(python_floor_text())
        return 0
    if args.what == "preview-object":
        print(preview_object())
        return 0
    if args.what == "preview":
        text = (sys.stdin.read() if args.path == "-"
                else Path(args.path).read_text(encoding="utf-8"))
        print(check_preview(text))
        return 0
    text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
    print(check_listing(json.loads(text), bundle=args.bundle))
    return 0


if __name__ == "__main__":
    sys.exit(main())
