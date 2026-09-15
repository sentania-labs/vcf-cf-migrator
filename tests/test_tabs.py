"""The three right-hand panels, and the rule that keeps them usable.

The panels used to be stacked, so anything an action produced was on screen
whatever you had been looking at. Separating them introduced a failure that
does not exist on one page: an action can now land you on a panel that does
not show what you just asked for, and the button looks broken.

Fixture only. Nothing here reads the corpus.
"""
from __future__ import annotations

import pytest

from make_export_fixture import DASHBOARD_ID, OWNER
from vcfcf_migrator.ui import TABS, PageState, dispatch

DASH = f"dashboard:{DASHBOARD_ID}@{OWNER}"


@pytest.fixture
def state(config_dir, export_zip):
    return PageState(str(export_zip))


def test_the_page_opens_on_the_preview(state):
    assert state.tab == "preview"
    page = state.render()
    # The marked tab must be the one we are on. Asserting only that some tab
    # carries the attribute is true whichever tab is current.
    assert page.count("aria-current='true'") == 1
    marked = page.split("aria-current='true'")[1].split(">")[1].split("<")[0]
    assert marked == "Preview"


def test_only_one_panel_is_on_the_page_at_a_time(state):
    headings = {"preview": ("Preview", "Start here"),
                "commands": ("Commands",),
                "settings": ("Settings",)}
    for tab in TABS:
        dispatch(state, "/tab", {"tab": tab})
        page = state.render()
        assert any(f"<h2>{h}</h2>" in page for h in headings[tab]), tab
        for other in TABS:
            if other == tab:
                continue
            for h in headings[other]:
                assert f"<h2>{h}</h2>" not in page, f"{other} still showing while on {tab}"


def test_a_panel_that_does_not_exist_is_refused(state):
    dispatch(state, "/tab", {"tab": "../../etc/passwd"})
    assert state.tab == "preview"
    assert "there is no" in state.error and "passwd" in state.error


# The rule. Each case is an action, and something that must be on the page
# after it. If an action moves you to a panel that does not carry its result,
# the button appears to do nothing, which is exactly what happened while this
# was being built: inspect sent you to Commands and left the listing behind on
# Preview.
@pytest.mark.parametrize("path,form,marker", [
    ("/inspect", {"zip": "FIXTURE"}, "Listing</h2>"),
    ("/tree", {}, "Command output</h2>"),
    ("/corpus-check", {"dir": "TMP"}, "Command output</h2>"),
    ("/preview", {"key": DASH}, "class='pv-grid'"),
    # The one control outside the tabbed column, so it can be pressed from any
    # panel while its report renders on only one.
    ("/build", {"out": "TMPBUNDLE"}, "Last build</h2>"),
])
def test_an_action_leaves_its_result_where_it_puts_you(state, export_zip, tmp_path,
                                                       path, form, marker):
    diag = str(tmp_path / "d.jsonl")
    bundle = str(tmp_path / "b.zip")
    form = {k: (str(export_zip) if v == "FIXTURE"
                else str(tmp_path) if v == "TMP"
                else diag if v == "TMPFILE"
                else bundle if v == "TMPBUNDLE" else v)
            for k, v in form.items()}
    marker = diag if marker == "TMPFILE" else marker
    if path == "/build":
        dispatch(state, "/select-all", {})
    # From every panel, not one. Starting only from the panel an action
    # happens to land on lets an action that sets no tab at all pass: it was
    # already where it needed to be. /diagnostics survived exactly that way.
    for start in TABS:
        dispatch(state, "/tab", {"tab": start})
        dispatch(state, path, form)
        page = state.render()
        assert marker in page, (
            f"{path} pressed from the {start!r} panel left you on {state.tab!r}, "
            "which does not show its result"
        )


def test_diagnostics_reports_from_whatever_panel_you_are_on(state, tmp_path):
    """It has no panel of its own: it writes a file and says so in the banner
    above the panels. So it must not move you, and must report wherever you
    are. This is the exemption to the rule above, stated rather than implied.
    """
    out = tmp_path / "d.jsonl"
    for start in TABS:
        dispatch(state, "/tab", {"tab": start})
        dispatch(state, "/diagnostics", {"out": str(out)})
        assert state.tab == start, "diagnostics moved you for no reason"
        assert str(out) in state.render()
        assert state.error == ""
