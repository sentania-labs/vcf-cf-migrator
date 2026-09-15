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
    assert "aria-current='page'" in state.render()


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
    assert "no" in state.error


# The rule. Each case is an action, and something that must be on the page
# after it. If an action moves you to a panel that does not carry its result,
# the button appears to do nothing, which is exactly what happened while this
# was being built: inspect sent you to Commands and left the listing behind on
# Preview.
@pytest.mark.parametrize("path,form,marker", [
    ("/inspect", {"zip": "FIXTURE"}, "Listing</h2>"),
    ("/tree", {}, "Command output</h2>"),
    ("/corpus-check", {"dir": "TMP"}, "Command output</h2>"),
    ("/preview", {"key": DASH}, "pv-grid"),
    ("/diagnostics", {"out": "TMPFILE"}, "Diagnostics file"),
])
def test_an_action_leaves_its_result_where_it_puts_you(state, export_zip, tmp_path,
                                                       path, form, marker):
    form = {k: (str(export_zip) if v == "FIXTURE"
                else str(tmp_path) if v == "TMP"
                else str(tmp_path / "d.jsonl") if v == "TMPFILE" else v)
            for k, v in form.items()}
    # Start somewhere else, so a result that only shows on the panel you were
    # already on cannot pass by accident.
    dispatch(state, "/tab", {"tab": "settings"})
    dispatch(state, path, form)
    page = state.render()
    assert marker in page, (
        f"{path} left you on the {state.tab!r} panel, which does not show its result"
    )
