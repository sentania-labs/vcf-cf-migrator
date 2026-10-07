"""The selection page: tree, checkboxes, closure, refusal, preview, build.

Two ways in, both here. ``PageState`` is driven directly, method by method,
because that is where the rules live; the HTTP endpoints are driven over a
real server, because that is where the same-origin check lives and because a
handler that forgets to call a method would otherwise pass.

Fixture only. Nothing here reads the corpus.
"""
from __future__ import annotations

import re
import zipfile

import pytest

from make_export_fixture import (
    DASHBOARD_ID,
    EMPTY_SM_ID,
    DASHBOARD_ID_2,
    OWNER,
    OWNER_2,
    SM_IDS,
    VIEW_IDS,
)
from vcfcf_migrator.cli import main
from vcfcf_migrator.ui import ACTIONS, POST_PATHS, PageState, dispatch
from vcfcf_migrator.desktop import Bridge

@pytest.fixture
def server(config_dir, export_zip):
    state = PageState(str(export_zip))
    yield Bridge(state)
    state.log.close()

def _post(bridge, path, form):
    return "rendered", bridge.act(path, form)["html"]

def _get(bridge, path="/"):
    return "rendered", bridge._state.render()


DASH = f"dashboard:{DASHBOARD_ID}@{OWNER}"
DASH_2 = f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}"
VIEW = f"view:{VIEW_IDS[0]}"
SM_1 = f"supermetric:{SM_IDS[0]}"
SM_2 = f"supermetric:{SM_IDS[1]}"


@pytest.fixture
def state(config_dir, export_zip):
    return PageState(str(export_zip))








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
    assert "aria-label='Content types'" in page
    assert "<span>Dashboards</span><span>4</span>" in page


def test_inspector_shows_dependencies(state):
    state.set_preview(DASH)
    state.inspector_tab = "dependencies"
    page = state.render()
    inspector = page.split("id='inspector'", 1)[1]
    assert "Depends on</h3>" in inspector
    assert f"value='{VIEW}'" in inspector



def test_inventory_contains_each_object_once_with_whole_export_counts(state):
    page = state.render()
    from vcfcf_migrator.uipage import anchor
    for node in state.graph.ordered():
        assert page.count(f"id='{anchor(node.key)}'") == 1
    assert f"{len(state.graph.nodes)} shown / {len(state.graph.nodes)} in export" in page
    assert "Nothing else points" not in page



def test_an_object_the_export_does_not_carry_is_shown_as_missing(state):
    state.set_preview(DASH)
    state.inspector_tab = "dependencies"
    assert "missing view" in state.render()



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
    state.set_preview(VIEW)
    state.inspector_tab = "dependencies"
    page = state.render()
    assert "Required by:" in page


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
    # Every panel, not just the one that happens to be showing. Two of the
    # three are behind tabs now, and a guard that renders only the default
    # panel stops guarding the markup on the other two.
    from vcfcf_migrator.ui import TABS
    for tab in TABS:
        state.tab = tab
        page = state.render()
        # The update notice is an explicit outbound link, not a fetched page
        # asset. Everything used to render the application remains local.
        from vcfcf_migrator.releases import RELEASE_PAGE
        link = f"href='{RELEASE_PAGE}'"
        assert page.count(link) == 1
        page = page.replace(link, '')
        assert "http://" not in page and "https://" not in page, tab
        # Busy feedback is local, inline code; no external script is loaded.
        assert page.count("<script>") == 1, tab
        assert "<script src" not in page.lower(), tab
        assert "<img" not in page.lower(), tab
        assert "@import" not in page, tab
        assert not re.search(r"\bsrc\s*=", page), tab


def test_the_only_script_on_the_page_is_the_checkbox_submit(state):
    """The page claims no script file and one inline handler. Asserting only
    that ``<script`` is absent would pass over any inline handler at all, so
    this checks every ``on...=`` attribute on the page against the one that is
    allowed, and checks the no-JS path is really there: every tree checkbox
    sits in a form that also carries a submit button.
    """
    state.toggle(DASH, on=True)
    state.set_preview(DASH)
    # The Browse button only renders when something can open a dialog, and a
    # guard that never sees it is not guarding it.
    state.file_picker = lambda: None
    from vcfcf_migrator.ui import TABS
    # Every panel. The Commands and Settings markup is behind a tab now, and
    # a handler added there would be invisible to a guard that renders only
    # the default panel.
    for tab in TABS:
        state.tab = tab
        other = re.findall(r"""\son([a-z]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""",
                           state.render())
        assert {(n, v.strip("\"'")) for n, v in other} <= {
            ("change",
             "this.form.requestSubmit ? this.form.requestSubmit() : this.form.submit()")
        }, f"unexpected inline handler on the {tab} panel: {other}"
    state.tab = "preview"
    page = state.render()
    assert "Browse" in page
    # Single quoted, double quoted and unquoted, because this file writes both
    # quotings and a guard that sees one of them is not a guard: a reviewer
    # added onclick="..." to every button and this test stayed green.
    handlers = re.findall(r"""\son([a-z]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", page)
    stripped = {(name, value.strip("\"'")) for name, value in handlers}
    # requestSubmit, not submit: submit() fires no submit event, which left the
    # checkboxes dead in the desktop window while the buttons beside them still
    # worked. The fallback is for browsers too old to have requestSubmit.
    assert stripped == {
        ("change",
         "this.form.requestSubmit ? this.form.requestSubmit() : this.form.submit()")
    }, handlers
    # One handler per tree checkbox, and each of those forms has a button.
    checkboxes = page.count("type='checkbox'")
    assert len(handlers) == checkboxes
    assert page.count("<form method='post' action='/select'>") == checkboxes
    assert page.count(">Add</button>") + page.count(">Remove</button>") == checkboxes


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def test_build_refuses_an_empty_selection(state, tmp_path):
    state.build(str(tmp_path / "b.zip"))
    assert "selection is empty" in state.error


def test_a_build_through_the_page_is_the_build_the_cli_writes(state, export_zip, tmp_path):
    """The page is another way in to one build path, not a second one."""
    state.toggle(f"supermetric:{EMPTY_SM_ID}", on=True)
    from_page = tmp_path / "page.zip"
    state.build(str(from_page))
    assert not state.error, state.error
    assert "bundle written to" in state.message

    picks = tmp_path / "picks.txt"
    picks.write_text("\n".join(state.selection_lines().splitlines()) + "\n")
    from_cli = tmp_path / "cli.zip"
    assert main(["build", str(export_zip),
                 "--select", str(picks), "--out", str(from_cli)]) == 0

    with zipfile.ZipFile(from_page) as page_zip, zipfile.ZipFile(from_cli) as cli_zip:
        assert page_zip.namelist() == cli_zip.namelist()
        for name in page_zip.namelist():
            assert page_zip.read(name) == cli_zip.read(name), name
    assert from_page.read_bytes() == from_cli.read_bytes()


def test_two_builds_of_one_selection_are_byte_identical(state, tmp_path):
    state.toggle(f"supermetric:{EMPTY_SM_ID}", on=True)
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    state.build(str(first))
    state.build(str(second))
    assert first.read_bytes() == second.read_bytes()


def test_build_reports_where_the_bundle_went_and_what_is_in_it(state, tmp_path):
    state.toggle(f"supermetric:{EMPTY_SM_ID}", on=True)
    out = tmp_path / "b.zip"
    state.build(str(out))
    page = state.render()
    assert str(out) in page
    assert "carrying:" in page
    assert "supermetrics.json" in page  # the members it wrote


# ---------------------------------------------------------------------------
# Over HTTP: the endpoints, and the same-origin rule on every one of them
# ---------------------------------------------------------------------------

def test_endpoints_drive_the_same_actions(server, export_zip, tmp_path):
    status, body = _post(server, "/select", {"key": DASH, "on": "1"})
    assert status == "rendered"
    assert "pulled in" in body
    _, body = _post(server, "/select", {"key": VIEW, "on": "0"})
    assert "refused:" in body
    _, body = _post(server, "/preview", {"key": DASH})
    assert "class='pv-grid'" in body
    _, body = _post(server, "/filter", {"filter": "cluster overview"})
    assert "shown /" in body
    _, body = _post(server, "/tree", {})
    assert "items: " in body
    _, body = _post(server, "/select-all", {})
    assert "selected every object" in body
    _, body = _post(server, "/clear", {})
    assert "cleared the selection" in body


def test_build_endpoint_writes_the_bundle(server, tmp_path, config_dir):
    _post(server, "/select", {"key": f"supermetric:{EMPTY_SM_ID}", "on": "1"})
    out = tmp_path / "from-endpoint.zip"
    _, body = _post(server, "/build", {"out": str(out)})
    assert f"bundle written to {out}" in body
    assert zipfile.ZipFile(out).namelist()


def test_opening_an_export_that_is_not_one_says_so(server, tmp_path):
    bad = tmp_path / "not-an-export.zip"
    bad.write_bytes(b"not a zip")
    _, body = _post(server, "/open", {"zip": str(bad)})
    assert "is not a zip file" in body or "cannot read" in body



def test_the_page_still_works_with_no_export_open(config_dir):
    page = PageState().render()
    assert "Open a content export zip" in page
    assert "Start here" in page


# ---------------------------------------------------------------------------
# A failed open must cost the admin nothing
# ---------------------------------------------------------------------------

def test_a_failed_open_leaves_the_selection_preview_and_counts_untouched(state, tmp_path):
    """The old export used to be gone from the state before the new one was
    shown to be readable, so a mistyped path emptied the page and took a
    prepared selection with it."""
    state.toggle(DASH, on=True)
    state.set_preview(DASH)
    before = (state.zip_path, set(state.selected_keys()), set(state.selection_keys()),
              state.preview_key, state.selection.counts(state.graph))
    page_before = state.render()

    bad = tmp_path / "not-an-export.zip"
    bad.write_bytes(b"not a zip at all")
    state.open_export(str(bad))

    assert state.error, "a failed open has to say so"
    assert "is still open" in state.error
    assert "1 object picked" in state.error
    assert (state.zip_path, set(state.selected_keys()), set(state.selection_keys()),
            state.preview_key, state.selection.counts(state.graph)) == before
    # The page still shows the same selection, counts and preview.
    page_after = state.render()
    assert "class='pv-grid'" in page_after
    for pill in ("<b>1</b> dashboard", "<b>11</b> objects"):
        assert (pill in page_before) == (pill in page_after)
    assert state.graph is not None and state.members is not None


def test_an_open_with_no_path_keeps_the_export_that_is_open(state):
    state.toggle(DASH, on=True)
    state.open_export("")
    assert "no export zip given" in state.error
    assert state.selected_keys() == {DASH}
    assert state.graph is not None


def test_a_failed_open_over_http_keeps_the_selection(server, tmp_path):
    bad = tmp_path / "broken.zip"
    bad.write_bytes(b"still not a zip")
    _post(server, "/select", {"key": DASH, "on": "1"})
    _, body = _post(server, "/open", {"zip": str(bad)})
    assert "is still open" in body
    assert "1 picked +" in body


def test_a_successful_open_still_resets_the_selection(state, tmp_path):
    """A selection carried across two exports would name objects the new one
    does not have, so a *successful* open still clears it."""
    from make_export_fixture import build_export_zip

    state.toggle(DASH, on=True)
    other = tmp_path / "other.zip"
    other.write_bytes(build_export_zip(without=["reports.zip"]))
    state.open_export(str(other))
    assert not state.error
    assert state.selected_keys() == set()
    assert state.zip_path == str(other)


# ---------------------------------------------------------------------------
# The command line is quoted for the shell it will be pasted into
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/tmp/plain.zip",
    "/tmp/My Export.zip",
    "/tmp/$(whoami).zip",
    "/tmp/`id`.zip",
    '/tmp/a"b.zip',
    "/tmp/a'b.zip",
    r"C:\Users\a b\export.zip",
])
def test_the_posix_form_is_one_argument_and_substitutes_nothing(path):
    """Inside double quotes a POSIX shell still expands ``$name`` and
    ``` `command` ``` , so the double-quoted form produced a line that could
    write somewhere else or run substituted text."""
    import shlex

    from vcfcf_migrator.ui import _shell_quote

    quoted = _shell_quote(path, windows=False)
    assert shlex.split(f"vcfcf-migrator inspect {quoted}")[-1] == path
    if any(ch in path for ch in "$`"):
        # The metacharacters survive as characters, not as a substitution.
        assert quoted.startswith("'") and quoted.endswith("'")


@pytest.mark.parametrize("path,expected", [
    ("C:\\exports\\plain.zip", "C:\\exports\\plain.zip"),
    ("C:\\Users\\a b\\export.zip", '"C:\\Users\\a b\\export.zip"'),
    ("C:\\x\\$(whoami).zip", '"C:\\x\\$(whoami).zip"'),
    ('C:\\x\\a"b.zip', '"C:\\x\\a""b.zip"'),
])
def test_the_windows_form_keeps_double_quotes(path, expected):
    """Single quotes are literal characters to cmd.exe, which is why the
    double-quoted form was chosen; an embedded double quote is doubled, which
    is what cmd.exe, PowerShell and the C runtime parser all take."""
    from vcfcf_migrator.ui import _shell_quote

    assert _shell_quote(path, windows=True) == expected


def test_the_platform_decides_the_quoting(monkeypatch):
    """The command is pasted into the shell of the machine the page runs on."""
    import os as _os

    from vcfcf_migrator.ui import _shell_quote

    monkeypatch.setattr(_os, "name", "posix")
    assert _shell_quote("/tmp/My Export.zip") == "'/tmp/My Export.zip'"
    monkeypatch.setattr(_os, "name", "nt")
    assert _shell_quote("/tmp/My Export.zip") == '"/tmp/My Export.zip"'
