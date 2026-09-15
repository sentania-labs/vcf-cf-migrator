"""The selection page: tree, checkboxes, closure, refusal, preview, build.

Two ways in, both here. ``PageState`` is driven directly, method by method,
because that is where the rules live; the HTTP endpoints are driven over a
real server, because that is where the same-origin check lives and because a
handler that forgets to call a method would otherwise pass.

Fixture only. Nothing here reads the corpus.
"""
from __future__ import annotations

import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import pytest

from make_export_fixture import (
    DASHBOARD_ID,
    DASHBOARD_ID_2,
    OWNER,
    OWNER_2,
    SM_IDS,
    VIEW_IDS,
)
from vcfcf_migrator.cli import main
from vcfcf_migrator.ui import ACTIONS, POST_PATHS, PageState, make_server

DASH = f"dashboard:{DASHBOARD_ID}@{OWNER}"
DASH_2 = f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}"
VIEW = f"view:{VIEW_IDS[0]}"
SM_1 = f"supermetric:{SM_IDS[0]}"
SM_2 = f"supermetric:{SM_IDS[1]}"


@pytest.fixture
def state(config_dir, export_zip):
    return PageState(str(export_zip))


@pytest.fixture
def server(config_dir, export_zip):
    srv = make_server(zip_path=str(export_zip), port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def _post(srv, path, form, headers=None):
    url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return r.status, r.read().decode("utf-8")


def _get(srv, path="/"):
    with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}{path}") as r:
        return r.status, r.read().decode("utf-8")


# ---------------------------------------------------------------------------
# The tree on the page
# ---------------------------------------------------------------------------

def test_the_page_shows_every_object_with_a_checkbox(state):
    page = state.render()
    assert state.graph is not None
    for node in state.graph.ordered():
        assert f"value='{node.key}'" in page, node.key
    assert page.count("type='checkbox'") >= len(state.graph.nodes)
    # Grouped by kind, with a count per group, so 66 dashboards do not arrive
    # as 66 undifferentiated rows.
    assert "<details class='kindgroup'" in page
    assert "dashboard<span class='n'>" in page


def test_the_tree_shows_what_each_object_depends_on(state):
    page = state.render()
    assert "depends on" in page
    # The dashboard's view, and the view's super metric, are reachable in the
    # nested disclosure under the dashboard.
    assert f"value='{VIEW}'" in page and f"value='{SM_1}'" in page


def test_the_tree_says_which_half_of_the_split_a_count_belongs_to(state):
    """The list is in two halves, and a per-kind count in the first half
    counts that half. Without saying so, "56 of 181 views" reads as 125 views
    that went missing."""
    page = state.render()
    graph = state.graph
    roots = len(graph.roots())
    others = len(graph.nodes) - roots
    assert f"{len(graph.nodes)} object(s): {roots} that nothing else points at" in page
    assert f"{others} reached only as a dependency" in page
    assert "Nothing else points at these" in page
    assert "Reached only as a dependency of something above" in page
    # A kind split across both halves says so where its number is.
    split = [k for k in {n.kind for n in graph.nodes.values()}
             if 0 < sum(1 for n in graph.roots() if n.kind == k) < len(graph.by_kind(k))]
    assert split, "the fixture should have at least one kind in both halves"
    for kind in split:
        here = sum(1 for n in graph.roots() if n.kind == kind)
        assert f"{here} of {len(graph.by_kind(kind))} here" in page


def test_an_object_the_export_does_not_carry_is_shown_as_missing(state):
    page = state.render()
    assert "missing supermetric" in page


# ---------------------------------------------------------------------------
# Selection, closure and refusal
# ---------------------------------------------------------------------------

def test_checking_a_dashboard_pulls_in_what_it_needs(state):
    state.toggle(DASH, on=True)
    assert state.selected_keys() == {DASH}
    carried = state.selection_keys()
    assert {DASH, VIEW, SM_1} <= carried
    assert "pulled in" in state.message
    assert not state.error


def test_a_pulled_in_object_says_what_needs_it(state):
    state.toggle(DASH, on=True)
    assert state.required_by(VIEW) == [DASH]
    page = state.render()
    assert "required by" in page


def test_unchecking_something_still_needed_is_refused_with_the_reason(state):
    state.toggle(DASH, on=True)
    before = state.selection_keys()
    state.toggle(VIEW, on=False)
    assert state.error.startswith("refused:")
    assert "required by" in state.error
    assert "[Fixture] Cluster Overview" in state.error
    assert state.selection_keys() == before, "nothing may change on a refusal"


def test_unchecking_a_pick_that_something_else_still_needs_is_refused(state):
    """The view is picked by hand and also needed by a picked dashboard.
    Unchecking it cannot drop it, so it is refused rather than silently kept."""
    state.toggle(VIEW, on=True)
    state.toggle(DASH, on=True)
    state.toggle(VIEW, on=False)
    assert state.error.startswith("refused:")
    assert VIEW in state.selected_keys()


def test_unchecking_a_pick_nothing_else_needs_removes_it_and_its_dependencies(state):
    state.toggle(DASH, on=True)
    state.toggle(DASH, on=False)
    assert state.selected_keys() == set()
    assert state.selection_keys() == set()
    assert "removed" in state.message
    assert "no longer needed" in state.message


def test_select_all_and_clear(state):
    state.select_all()
    assert len(state.selection_keys()) == len(state.graph.nodes)
    state.clear()
    assert state.selection_keys() == set()


def test_a_selection_can_be_given_as_the_lines_build_select_takes(state):
    state.apply_lines(f"# a comment\n{DASHBOARD_ID_2}\n\n")
    assert DASH_2 in state.selected_keys()
    assert VIEW_IDS[1] in " ".join(state.selection_keys())
    assert state.selection_lines().strip() == DASH_2


def test_lines_naming_something_absent_are_refused_and_change_nothing(state):
    state.toggle(DASH, on=True)
    before = state.selection_keys()
    state.apply_lines("not-a-real-object")
    assert "does not carry" in state.error
    assert state.selection_keys() == before


def test_opening_another_export_drops_the_old_selection(state, tmp_path):
    from make_export_fixture import build_export_zip

    state.toggle(DASH, on=True)
    other = tmp_path / "other.zip"
    other.write_bytes(build_export_zip(without=["reports.zip"]))
    state.open_export(str(other))
    assert state.selected_keys() == set()
    assert state.preview_key == ""


# ---------------------------------------------------------------------------
# The preview, in place
# ---------------------------------------------------------------------------

def test_the_preview_shows_in_place_and_closes(state):
    state.set_preview(DASH)
    page = state.render()
    assert "class='pv-grid'" in page
    assert "[Fixture] Cluster List" in page      # the embedded view's columns
    assert "Close the preview" in page
    state.set_preview("")
    assert "class='pv-grid'" not in state.render()


def test_previewing_something_absent_is_refused(state):
    state.set_preview("view:nope")
    assert "carries no object" in state.error


def test_the_page_carries_no_external_resource(state):
    state.toggle(DASH, on=True)
    state.set_preview(DASH)
    page = state.render()
    assert "http://" not in page and "https://" not in page
    assert "<script" not in page.lower()
    assert "<img" not in page.lower()
    assert "@import" not in page
    assert not re.search(r"\bsrc\s*=", page)


def test_the_only_script_on_the_page_is_the_checkbox_submit(state):
    """The page claims no script file and one inline handler. Asserting only
    that ``<script`` is absent would pass over any inline handler at all, so
    this checks every ``on...=`` attribute on the page against the one that is
    allowed, and checks the no-JS path is really there: every tree checkbox
    sits in a form that also carries a submit button.
    """
    state.toggle(DASH, on=True)
    state.set_preview(DASH)
    page = state.render()
    # Single quoted, double quoted and unquoted, because this file writes both
    # quotings and a guard that sees one of them is not a guard: a reviewer
    # added onclick="..." to every button and this test stayed green.
    handlers = re.findall(r"""\son([a-z]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", page)
    stripped = {(name, value.strip("\"'")) for name, value in handlers}
    assert stripped == {("change", "this.form.submit()")}, handlers
    # One handler per tree checkbox, and each of those forms has a button.
    checkboxes = page.count("type='checkbox' class='tick'")
    assert len(handlers) == checkboxes
    assert page.count("<form method='post' action='/select'>") == checkboxes
    assert page.count(">add</button>") + page.count(">remove</button>") == checkboxes


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def test_build_refuses_without_a_declared_source_version(state, tmp_path):
    state.toggle(DASH, on=True)
    state.build(str(tmp_path / "b.zip"))
    assert "no source version declared" in state.error
    assert not (tmp_path / "b.zip").exists()


def test_build_refuses_an_empty_selection(state, tmp_path, monkeypatch):
    monkeypatch.setenv("VCFCF_MIGRATOR_SOURCE_VERSION", "9.0.2")
    state.build(str(tmp_path / "b.zip"))
    assert "selection is empty" in state.error


def test_a_build_through_the_page_is_the_build_the_cli_writes(state, export_zip, tmp_path,
                                                              monkeypatch):
    """The page is another way in to one build path, not a second one."""
    monkeypatch.setenv("VCFCF_MIGRATOR_SOURCE_VERSION", "9.0.2")
    state.toggle(DASH, on=True)
    state.toggle("symptom:SymptomDefinition-VMWARE-Fixture_CPU_high", on=True)
    from_page = tmp_path / "page.zip"
    state.build(str(from_page))
    assert not state.error, state.error
    assert "bundle written to" in state.message

    picks = tmp_path / "picks.txt"
    picks.write_text("\n".join(state.selection_lines().splitlines()) + "\n")
    from_cli = tmp_path / "cli.zip"
    assert main(["--source-version", "9.0.2", "build", str(export_zip),
                 "--select", str(picks), "--out", str(from_cli)]) == 0

    with zipfile.ZipFile(from_page) as page_zip, zipfile.ZipFile(from_cli) as cli_zip:
        assert page_zip.namelist() == cli_zip.namelist()
        for name in page_zip.namelist():
            assert page_zip.read(name) == cli_zip.read(name), name
    assert from_page.read_bytes() == from_cli.read_bytes()


def test_two_builds_of_one_selection_are_byte_identical(state, tmp_path, monkeypatch):
    monkeypatch.setenv("VCFCF_MIGRATOR_SOURCE_VERSION", "9.0.2")
    state.select_all()
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    state.build(str(first))
    state.build(str(second))
    assert first.read_bytes() == second.read_bytes()


def test_build_reports_where_the_bundle_went_and_what_is_in_it(state, tmp_path, monkeypatch):
    monkeypatch.setenv("VCFCF_MIGRATOR_SOURCE_VERSION", "9.0.2")
    state.toggle(DASH, on=True)
    out = tmp_path / "b.zip"
    state.build(str(out))
    page = state.render()
    assert str(out) in page
    assert "carrying:" in page
    assert "dashboards/" in page  # the members it wrote


# ---------------------------------------------------------------------------
# Over HTTP: the endpoints, and the same-origin rule on every one of them
# ---------------------------------------------------------------------------

def test_endpoints_drive_the_same_actions(server, export_zip, tmp_path):
    status, body = _post(server, "/select", {"key": DASH, "on": "1"})
    assert status == 200
    assert "pulled in" in body
    _, body = _post(server, "/select", {"key": VIEW, "on": "0"})
    assert "refused:" in body
    _, body = _post(server, "/preview", {"key": DASH})
    assert "class='pv-grid'" in body
    _, body = _post(server, "/filter", {"filter": "cluster overview"})
    assert "object(s) match" in body
    _, body = _post(server, "/tree", {})
    assert "items: " in body
    _, body = _post(server, "/select-all", {})
    assert "selected every object" in body
    _, body = _post(server, "/clear", {})
    assert "cleared the selection" in body


def test_build_endpoint_writes_the_bundle(server, tmp_path, config_dir):
    _post(server, "/settings", {"source_version": "9.0.2"})
    _post(server, "/select", {"key": DASH, "on": "1"})
    out = tmp_path / "from-endpoint.zip"
    _, body = _post(server, "/build", {"out": str(out)})
    assert f"bundle written to {out}" in body
    assert zipfile.ZipFile(out).namelist()


def test_opening_an_export_that_is_not_one_says_so(server, tmp_path):
    bad = tmp_path / "not-an-export.zip"
    bad.write_bytes(b"not a zip")
    _, body = _post(server, "/open", {"zip": str(bad)})
    assert "is not a zip file" in body or "cannot read" in body


ENDPOINT_FORMS = [
    ("/open", {"zip": "/tmp/whatever.zip"}),
    ("/select", {"key": DASH, "on": "1"}),
    ("/select-all", {}),
    ("/clear", {}),
    ("/apply-lines", {"lines": DASH}),
    ("/preview", {"key": DASH}),
    ("/filter", {"filter": "x"}),
    ("/build", {"out": "/tmp/pwned.zip"}),
    ("/tree", {}),
    ("/corpus-check", {"dir": "/tmp"}),
    ("/inspect", {"zip": "/tmp/whatever.zip"}),
    ("/settings", {"corpus_dir": "/pwned"}),
    ("/run", {"cmd": "tree"}),
]


def test_the_cross_origin_test_covers_every_endpoint_the_server_has():
    """The list above is derived, not remembered. A fourteenth entry in
    ``ui.ACTIONS`` fails here until it is covered, which is the same
    derive-do-not-hand-copy rule ``tests/fixtures/ci_checks.py`` exists for."""
    assert {path for path, _form in ENDPOINT_FORMS} == set(POST_PATHS) == set(ACTIONS)


@pytest.mark.parametrize("path,form", ENDPOINT_FORMS)
def test_every_endpoint_refuses_a_cross_origin_post(server, path, form):
    """A page on another origin must not be able to drive this one. The new
    endpoints write files and read paths, so the rule matters more here than
    it did when the page only saved settings."""
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(server, path, form, headers={"Origin": "http://evil.example"})
    assert e.value.code == 403
    assert "cross-origin POST refused" in e.value.read().decode()


def test_a_refused_cross_origin_build_writes_nothing(server, tmp_path):
    out = tmp_path / "never.zip"
    _post(server, "/settings", {"source_version": "9.0.2"})
    _post(server, "/select", {"key": DASH, "on": "1"})
    with pytest.raises(urllib.error.HTTPError):
        _post(server, "/build", {"out": str(out)}, headers={"Origin": "http://evil.example"})
    assert not out.exists()


def test_the_page_still_works_with_no_export_open(config_dir):
    page = PageState().render()
    assert "Open an export zip" in page
    assert "Start here" in page
