"""The three right-hand panels, and the rule that keeps them usable.

The panels used to be stacked, so anything an action produced was on screen
whatever you had been looking at. Separating them introduced a failure that
does not exist on one page: an action can now land you on a panel that does
not show what you just asked for, and the button looks broken.

Fixture only. Nothing here reads the corpus.
"""
from __future__ import annotations

import re

import pytest

from make_export_fixture import DASHBOARD_ID, OWNER
from vcfcf_migrator.ui import TABS, PageState, dispatch

DASH = f"dashboard:{DASHBOARD_ID}@{OWNER}"


@pytest.fixture
def state(config_dir, export_zip):
    return PageState(str(export_zip))


def test_the_page_opens_on_inventory_with_an_inspector(state):
    assert state.tab == "preview"
    page = state.render()
    assert "Choose content</h2>" in page
    assert "Preview</h2>" in page



def test_only_one_panel_is_on_the_page_at_a_time(state):
    headings = {"preview": ("Preview", "Start here"),
                "commands": ("Commands",),
                "settings": ("Settings",), "review": ("Review bundle",)}
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
        state.toggle(next(n.key for n in state.graph.ordered() if n.name == "[Fixture] SM Empty"), True)
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


# ---------------------------------------------------------------------------
# Disclosures (#20, reported from outside: "selecting the checkbox closes the
# window that displays all dashboards")
# ---------------------------------------------------------------------------

def test_category_and_search_survive_selection_and_preview(state):
    dispatch(state, "/category", {"kind": "dashboard"})
    dispatch(state, "/filter", {"filter": "Cluster"})
    before = [n.key for n in state.visible_nodes()]
    dispatch(state, "/select", {"key": DASH, "on": "1"})
    dispatch(state, "/preview", {"key": DASH})
    assert [n.key for n in state.visible_nodes()] == before
    assert state.inventory_kind == "dashboard"
    assert state.filter_text == "Cluster"


def test_new_export_resets_inventory_filters(state, export_zip):
    dispatch(state, "/category", {"kind": "dashboard"})
    dispatch(state, "/filter", {"filter": "Cluster"})
    state.open_export(str(export_zip))
    assert state.inventory_kind == "all"
    assert state.filter_text == ""


def test_an_anchor_survives_a_name_that_is_not_ascii():
    """str.isalnum() is true for every script, and the server writes the
    anchor into a Location header that http.server encodes as Latin-1. A
    custom group named in Japanese raised UnicodeEncodeError and the response
    was dropped, so the click silently did nothing. That predates disclosures:
    selecting a row anchors the same way.
    """
    from vcfcf_migrator.uipage import anchor

    for key in ["customgroup:クラスタ", "customgroup:Сервер", "deps:customgroup:クラスタ"]:
        a = anchor(key)
        assert a.isascii(), a
        ("/" + f"#{a}").encode("latin-1")  # what send_header does
    # And two different names must not collapse onto one id.
    assert anchor("customgroup:クラスタ") != anchor("customgroup:サーバ")
    # A plain ASCII key is spelled exactly as it always was.
    assert anchor("kind:roots:view") == "node-kind-roots-view"


def test_selecting_an_object_named_in_another_script_still_answers(config_dir):
    from vcfcf_migrator.desktop import Bridge
    from vcfcf_migrator.ui import PageState

    bridge = Bridge(PageState())
    result = bridge.act('/select', {'key': 'customgroup:クラスタ', 'on': '1'})
    assert '<!doctype html>' in result['html']
    result = bridge.act('/disclose', {'id': 'deps:customgroup:クラスタ', 'on': '1'})
    assert '<!doctype html>' in result['html']


# ---------------------------------------------------------------------------
# Names in full (#22, reported from outside: "I would like to see the full
# names listed instead of the truncated names")
# ---------------------------------------------------------------------------

def test_inventory_names_wrap_instead_of_truncating(state):
    page = state.render()
    css = page[page.index("<style>"):page.index("</style>")]
    rule = css[css.index('.inventory .name-button'):].split('}', 1)[0]
    assert 'overflow-wrap:anywhere' in rule
    assert 'text-overflow:ellipsis' not in rule
    assert 'white-space:nowrap' not in rule


def test_the_name_is_in_the_visible_label_not_only_in_an_attribute(state):
    """A version of this stripped the title and asserted the name was still
    somewhere on the page. It passed with the visible label replaced by a
    placeholder, because the same row carries the name a third time in the
    checkbox's aria-label. It has to be in the element a reader sees.
    """
    import html as _html

    longest = max(state.graph.ordered(), key=lambda n: len(n.name))
    page = state.render()
    labels = re.findall(r"<button class='name-button'[^>]*>(.*?)</button>", page, re.S)
    assert labels, "no name labels on the page"
    visible = [re.sub(r"<[^>]+>", "", one) for one in labels]
    assert _html.escape(longest.name) in "".join(labels) or longest.name in visible, (
        f"{longest.name!r} is not in any visible row label")


def test_identifiers_live_in_inspector_details(state):
    state.set_preview(DASH)
    state.inspector_tab = "details"
    page = state.render()
    inspector = page.split("id='inspector'", 1)[1]
    assert "Identifier</dt>" in inspector
    assert state.graph.nodes[DASH].uuid in inspector
