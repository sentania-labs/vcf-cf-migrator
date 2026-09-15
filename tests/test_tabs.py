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


# ---------------------------------------------------------------------------
# Disclosures (#20, reported from outside: "selecting the checkbox closes the
# window that displays all dashboards")
# ---------------------------------------------------------------------------

def _group_open(page, kind="dashboard"):
    import re
    m = re.search(r"<div class='kindgroup( on)?' id='[^']*kind-roots-" + kind + r"'", page)
    assert m, f"no {kind} group on the page"
    return bool(m.group(1))


def test_a_group_the_user_opened_survives_ticking_a_checkbox(state):
    """The reported bug. Every action redraws the whole tree, and the open
    state used to live only in the browser's <details>, so the redraw shut the
    list you were ticking and selecting several objects meant reopening the
    group between every click.
    """
    dispatch(state, "/disclose", {"id": "kind:roots:dashboard", "on": "0"})
    assert not _group_open(state.render())
    dispatch(state, "/disclose", {"id": "kind:roots:dashboard", "on": "1"})
    assert _group_open(state.render())

    node = next(n for n in state.graph.ordered() if n.kind == "dashboard")
    dispatch(state, "/select", {"key": node.key, "on": "1"})
    assert _group_open(state.render()), "ticking a checkbox shut the group again"
    dispatch(state, "/preview", {"key": node.key})
    assert _group_open(state.render()), "previewing shut the group"


def test_a_group_the_user_shut_stays_shut(state):
    dispatch(state, "/disclose", {"id": "kind:roots:dashboard", "on": "0"})
    node = next(n for n in state.graph.ordered() if n.kind == "dashboard")
    dispatch(state, "/select", {"key": node.key, "on": "1"})
    assert not _group_open(state.render()), "an action reopened a group the user shut"


def test_the_rows_are_on_the_page_even_when_the_group_is_shut(state):
    """A <details> kept its content in the document, so the browser's own find
    could reach it. Dropping the body when shut would have been cheaper and
    would have quietly taken that away."""
    node = next(n for n in state.graph.ordered() if n.kind == "dashboard")
    dispatch(state, "/disclose", {"id": "kind:roots:dashboard", "on": "0"})
    page = state.render()
    assert not _group_open(page)
    # The rows are in the document, not dropped.
    assert f"value='{node.key}'" in page
    # And the body carries the attribute that hides it. Asserting "hidden" is
    # somewhere on the page says nothing: every page has an <input
    # type='hidden'>, and with that assertion removing the attribute entirely
    # left the whole suite green.
    assert "<div class='disc-body' hidden='until-found'>" in page
    # until-found, not a bare hidden, so the browser can still find text in
    # here. A display:none rule of our own would defeat that.
    assert "disc-body[hidden]" not in page


def test_opening_a_group_comes_back_to_it(state):
    """Opening a group two thirds down a long tree and being sent back to the
    top would be its own version of this bug."""
    anchor = dispatch(state, "/disclose", {"id": "kind:roots:dashboard", "on": "1"})
    assert anchor
    assert f"id='{anchor}'" in state.render()


def test_a_disclosure_with_no_name_is_refused(state):
    dispatch(state, "/disclose", {"on": "1"})
    assert "no disclosure named" in state.error


def test_a_shut_group_says_so_to_a_screen_reader(state):
    dispatch(state, "/disclose", {"id": "kind:roots:dashboard", "on": "0"})
    assert "aria-expanded='false'" in state.render()
    dispatch(state, "/disclose", {"id": "kind:roots:dashboard", "on": "1"})
    assert "aria-expanded='true'" in state.render()


def test_the_two_tree_sections_do_not_share_one_control(state):
    """Six kinds appear in both sections on the corpus. One id made them a
    single control with a single state, so opening the lower group expanded
    the upper one and the anchor sent you to the wrong section."""
    page = state.render()
    ids = re.findall(r"<div class='kindgroup[^']*' id='([^']+)'", page)
    assert len(ids) == len(set(ids)), f"two groups share an id: {ids}"


def test_opening_a_different_export_forgets_the_old_disclosures(state, export_zip):
    dispatch(state, "/disclose", {"id": "kind:roots:dashboard", "on": "0"})
    assert state.disclosure
    state.open_export(str(export_zip))
    assert state.disclosure == {}, (
        "a small export's expanded groups would come back on a large one, "
        "which is what the default is there to prevent"
    )


def test_no_two_disclosures_share_an_id(state):
    """Every disclosure is its own control. An id built from the object alone
    made each copy of a shared dependency one control: opening it under one
    dashboard opened it under the other, and the anchor, which resolves to the
    first match, scrolled you there. The corpus has objects reached by three
    routes, so this is not hypothetical.
    """
    import collections
    page = state.render()
    ids = re.findall(r"<div class='(?:kindgroup|deps)[^']*' id='([^']+)'", page)
    assert ids
    dup = [k for k, n in collections.Counter(ids).items() if n > 1]
    assert not dup, f"{len(dup)} disclosures share an id, e.g. {dup[:2]}"


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


def test_selecting_an_object_named_in_another_script_still_answers(config_dir, tmp_path):
    """End to end over the real server, because the failure was in the header
    rather than in anything a direct call would reach."""
    import threading
    import urllib.parse
    import urllib.request

    from vcfcf_migrator.ui import make_server

    srv = make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        data = urllib.parse.urlencode({"key": "customgroup:クラスタ", "on": "1"}).encode()
        req = urllib.request.Request(base + "/select", data=data,
                                     headers={"Origin": base})
        assert urllib.request.urlopen(req).status == 200
        data = urllib.parse.urlencode({"id": "deps:customgroup:クラスタ", "on": "1"}).encode()
        req = urllib.request.Request(base + "/disclose", data=data,
                                     headers={"Origin": base})
        assert urllib.request.urlopen(req).status == 200
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------------------
# Names in full (#22, reported from outside: "I would like to see the full
# names listed instead of the truncated names")
# ---------------------------------------------------------------------------

def test_no_rule_truncates_a_name_in_the_tree(state):
    """Dependency rows are indented once per level, so the deeper an object
    sits the less width it has. With an ellipsis rule that meant the rows an
    admin reads to decide what to carry were the ones cut off first.

    This looks at the stylesheet the page actually ships rather than at the
    source, so a rule reintroduced anywhere is caught.
    """
    page = state.render()
    css = page[page.index("<style>"):page.index("</style>")].replace(" ", "").replace("\n", "")
    # Every rule whose selector ends in .name or .why, not just the first.
    # Checking only the first let the old rule come back inside the
    # max-width:900px block, which is exactly where someone reaches for
    # nowrap, and named two spellings of truncation out of several.
    bad = ("text-overflow:ellipsis", "white-space:nowrap", "text-wrap:nowrap",
           "-webkit-line-clamp")
    found = 0
    for match in re.finditer(r"\{[^{}]*\}", css):
        selector = css[:match.start()].rsplit("}", 1)[-1].rsplit("{", 1)[-1]
        if not (selector.endswith(".name") or selector.endswith(".why")):
            continue
        found += 1
        for smell in bad:
            assert smell not in match.group(0), f"{selector} truncates again: {match.group(0)}"
    assert found >= 2, f"the rules this guards are gone from the stylesheet ({found})"
    # And the layout that keeps the name a readable width. "required by" used
    # to share the name's line, and because both can shrink, the two squeezed
    # each other: measured on a real export at a normal 1280 desktop, 402 of
    # 756 rows went over 60px tall and the worst name was eight lines deep in
    # a 110px column. Giving it a line of its own is the fix, so it is guarded.
    assert "flex-wrap:wrap" in css[css.index(".row{"):css.index("}", css.index(".row{"))]
    why = css[css.index(".row.why{"):css.index("}", css.index(".row.why{"))]
    assert "flex:10100%" in why.replace(" ", ""), (
        f"required by shares the name's line again: {why}")


def test_the_name_is_in_the_visible_label_not_only_in_an_attribute(state):
    """A version of this stripped the title and asserted the name was still
    somewhere on the page. It passed with the visible label replaced by a
    placeholder, because the same row carries the name a third time in the
    checkbox's aria-label. It has to be in the element a reader sees.
    """
    import html as _html

    longest = max(state.graph.ordered(), key=lambda n: len(n.name))
    page = state.render()
    labels = re.findall(r"<span class='name'[^>]*>(.*?)</span></span>", page, re.S)
    assert labels, "no name labels on the page"
    visible = [re.sub(r"<[^>]+>", "", one) for one in labels]
    assert _html.escape(longest.name) in "".join(labels) or longest.name in visible, (
        f"{longest.name!r} is not in any visible row label")


def test_the_identifier_beside_a_name_is_a_uuid_or_nothing(state):
    """It used to be the first eight characters of whatever the identifier
    was. On the corpus that rendered "SymptomD" 43 times and "AlertDef" 13,
    and for a custom group, which has no uuid, it was the name cut to eight
    characters, sitting immediately beside the full name.
    """
    from vcfcf_migrator.uipage import _short_ident

    assert _short_ident("SymptomDefinition-VMWARE-CPU_high", "CPU high") == ""
    assert _short_ident("Hosts, VMs and Datastores", "Hosts, VMs and Datastores") == ""
    assert _short_ident("2d7b8c1e-4f11-4c7a-9a55-0c1f2e3d4a5b", "A dash") == "2d7b8c1e"

    page = state.render()
    shown = [s for s in re.findall(r"<span class='uuid'>([^<]+)</span>", page)
             if not s.startswith("owner ")]
    for one in shown:
        assert re.fullmatch(r"[0-9a-fA-F]{8}", one), f"not a uuid prefix: {one!r}"
