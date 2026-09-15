"""The native window: the bridge, the fallback, and the seams that keep the
two window modes from drifting apart.

The window itself cannot be opened here, so nothing below tries. What is
tested is everything on either side of it: that the page handed to the window
carries the script that makes its buttons work, that the script hands actions
back through the same dispatch the server uses, that nothing binds a port, and
that a machine with no webview is told why it got a browser instead.

Fixture only. Nothing here reads the corpus.
"""
from __future__ import annotations

import inspect
import re

import pytest

from vcfcf_migrator import desktop, ui
from vcfcf_migrator.cli import main
from vcfcf_migrator.ui import ACTIONS, PageState


@pytest.fixture
def state(config_dir, export_zip):
    return PageState(str(export_zip))


# --- the page the window receives -----------------------------------------

def test_page_html_injects_the_bridge_once_before_body_ends(state):
    html = desktop.page_html(state)
    assert html.count("window.pywebview.api.act") == 1
    assert html.index("window.pywebview.api.act") < html.index("</body>")


def test_page_html_is_the_same_page_the_server_renders(state):
    """The window must not get a different page, only an extra script."""
    plain = PageState(state.zip_path).render()
    windowed = desktop.page_html(PageState(state.zip_path))
    assert desktop._BRIDGE_JS in windowed
    assert windowed.replace(desktop._BRIDGE_JS, "") == plain


def test_bridge_script_carries_the_submitting_button():
    """FormData leaves the submit button out; a real POST carries it.

    At least one form on the page has a named submit button, so dropping these
    lines would break it in a way that looks like a backend fault. This test is
    the thing that notices.
    """
    assert "ev.submitter" in desktop._BRIDGE_JS
    assert "data[by.name] = by.value" in desktop._BRIDGE_JS


def test_bridge_listens_on_document_not_body():
    """Every action replaces the body, so a body listener works exactly once."""
    assert "document.addEventListener('submit'" in desktop._BRIDGE_JS


# --- the bridge ------------------------------------------------------------

def test_bridge_reaches_every_action_the_server_exposes(state):
    """No action is reachable in the browser but not in the window."""
    bridge = desktop.Bridge(state)
    for path in ACTIONS:
        res = bridge.act(path, {})
        assert set(res) == {"html", "anchor"}, path
        assert "<html" in res["html"].lower(), path
        assert desktop._BRIDGE_JS in res["html"], path


def test_bridge_refuses_an_action_that_does_not_exist(state):
    bridge = desktop.Bridge(state)
    res = bridge.act("/there-is-no-such-thing", {})
    assert "no page action" in state.error
    assert "<html" in res["html"].lower()


def test_bridge_coerces_form_values_to_strings(state):
    """The webview hands over JSON, so a checkbox can arrive as a boolean."""
    bridge = desktop.Bridge(state)
    bridge.act("/filter", {"filter": None})
    assert state.filter_text == ""


# --- the seams that stop the two modes drifting ----------------------------

def test_one_dispatch_writes_the_page_action_log_line():
    """Both modes log identically because only one place logs at all."""
    src = inspect.getsource(ui)
    assert src.count('_runlog.info("page.action"') == 1
    assert src.count('state.log.phase("page"') + src.count('.log.phase("page"') >= 1
    assert 'page.action' not in inspect.getsource(desktop)


def test_the_post_handler_goes_through_dispatch():
    src = inspect.getsource(ui._handler_for)
    assert "dispatch(state," in src
    # The handler must not run an action itself; that is what would let the
    # two modes diverge.
    assert "ACTIONS.get(" not in src


def test_the_bridge_goes_through_dispatch():
    assert "ui.dispatch(" in inspect.getsource(desktop.Bridge.act)


# --- nothing is listening --------------------------------------------------

def test_desktop_mode_binds_nothing(monkeypatch, config_dir, export_zip):
    opened = {}

    def fake_run(state, **kwargs):
        opened["state"] = state

    monkeypatch.setattr(desktop, "run", fake_run)
    assert ui.run_desktop(zip_path=str(export_zip)) == 0
    # origins is the server's same-origin allow list. Empty means there is no
    # socket for anything to reach.
    assert opened["state"].origins == ()


def test_desktop_path_never_constructs_a_server():
    assert "ThreadingHTTPServer" not in inspect.getsource(ui.run_desktop)
    assert "ThreadingHTTPServer" not in inspect.getsource(desktop)


# --- the fallback ----------------------------------------------------------

def test_availability_reports_a_reason(monkeypatch):
    ok, why = desktop.available()
    assert isinstance(ok, bool)
    if not ok:
        assert why, "a machine that cannot open a window must say why"


def test_ui_falls_back_to_the_browser_and_says_why(monkeypatch, capsys, export_zip):
    monkeypatch.setattr(desktop, "available", lambda: (False, "NoWebviewHere: nope"))
    served = {}

    def fake_serve(**kwargs):
        served.update(kwargs)
        return 0

    monkeypatch.setattr(ui, "serve", fake_serve)
    assert main(["ui", str(export_zip)]) == 0
    assert served, "it must fall back to the server, not give up"
    err = capsys.readouterr().err
    assert "NoWebviewHere: nope" in err
    assert "--server" in err


def test_server_flag_skips_the_window_entirely(monkeypatch, export_zip):
    def boom():
        raise AssertionError("--server must not even ask about a window")

    monkeypatch.setattr(desktop, "available", boom)
    monkeypatch.setattr(ui, "serve", lambda **kwargs: 0)
    assert main(["ui", str(export_zip), "--server"]) == 0


# --- what the review caught, now guarded -----------------------------------

def test_no_inline_handler_submits_a_form_without_requestsubmit(state):
    """`form.submit()` fires no submit event, so the window never sees it.

    That is how the tree checkboxes came to be dead in the window while the
    buttons beside them worked. Any new inline handler that reaches for
    `submit()` without trying `requestSubmit()` first brings the bug back, so
    this looks at the rendered page rather than at one known string.
    """
    state.toggle(next(iter(state.selection_keys()), "") or "", on=False)
    page = state.render()
    for match in re.finditer(r"\.submit\(\)", page):
        window = page[max(0, match.start() - 120):match.start()]
        assert "requestSubmit" in window, (
            "a form is submitted without requestSubmit; the desktop window "
            "cannot see that and the control will be dead in it"
        )


def test_the_bridge_prefers_a_field_over_the_button_that_submitted_it():
    """Parity with the server, which parses first-wins.

    The button is written into the dict before FormData overwrites it, so a
    field of the same name wins in both modes. Reversed, the same click would
    do different things in the browser and in the window.
    """
    js = desktop._BRIDGE_JS
    assert js.index("data[by.name] = by.value") < js.index("new FormData(form)")


def test_clear_filter_works_and_does_not_collide_with_the_field(state):
    """It used to be a second control named `filter`, so the server's
    first-wins parse threw it away and Clear did nothing at all."""
    state.set_filter("dash")
    assert state.filter_text == "dash"
    # Exactly what a real POST of that form sends.
    ui.dispatch(state, "/filter", {"filter": "dash", "clear-filter": "1"})
    assert state.filter_text == ""


def test_the_page_has_no_two_controls_sharing_a_name_in_one_form(state):
    """The bug class behind Clear: duplicate names are collapsed from opposite
    ends by the browser and by the bridge, so they cannot be allowed.

    The filter must be set and something selected first. Several controls,
    Clear among them, only render once there is something to act on, and a
    version of this test that rendered an empty page passed while the very
    collision it was written for was still in the markup.
    """
    state.set_filter("dash")
    state.select_all()
    state.set_preview(_some_key(state))
    # Every optional control has to be on the page, or this guard inspects
    # markup that is not the markup a user sees.
    state.file_picker = lambda: None
    seen = []
    # Every panel. Two of the three are behind tabs, and a collision on either
    # of them would be invisible to a guard that renders only the default one.
    for tab in ui.TABS:
        state.tab = tab
        page = state.render()
        seen.append(page)
        for form in re.findall(r"<form\b.*?</form>", page, re.S):
            names = re.findall(r"""\bname=['"]([^'"]+)['"]""", form)
            # A checkbox group would legitimately repeat a name; none here.
            assert len(names) == len(set(names)), f"duplicate field name in {names} ({tab})"
    joined = "".join(seen)
    assert "Clear</button>" in joined, "the control this guards is not on the page"
    assert "Browse" in joined, "the control this guards is not on the page"


def test_bridge_returns_an_anchor_that_names_a_real_row(state):
    """Non-empty is not enough, and asserting only that was how a broken
    anchor passed review: the value has to be an id that is actually on the
    page, or the window scrolls to the top exactly as if there were none."""
    bridge = desktop.Bridge(state)
    key = _some_key(state)
    res = bridge.act("/select", {"key": key, "on": "1"})
    assert res["anchor"], "selecting an object must come back with somewhere to scroll to"
    assert f"id='{res['anchor']}'" in res["html"], (
        f"anchor {res['anchor']!r} names no element on the page"
    )


def _some_key(state):
    return next(iter(state.graph.ordered())).key


def test_an_action_that_raises_lands_on_the_page_not_in_the_void(state, monkeypatch):
    """A rejected bridge call used to leave the window silently unresponsive."""
    def boom(*_args, **_kwargs):
        raise OSError("disk went away")

    monkeypatch.setitem(ui.ACTIONS, "/build", boom)
    bridge = desktop.Bridge(state)
    res = bridge.act("/build", {"out": "/nowhere/x.zip"})
    assert "disk went away" in state.error
    assert "<html" in res["html"].lower()


def test_the_bridge_script_handles_a_rejected_call():
    assert ".catch(" in desktop._BRIDGE_JS


def test_a_keyerror_inside_an_action_is_not_reported_as_a_missing_action(state, monkeypatch):
    def boom(*_args, **_kwargs):
        raise KeyError("some-inner-lookup")

    monkeypatch.setitem(ui.ACTIONS, "/tree", boom)
    desktop.Bridge(state).act("/tree", {})
    assert "no page action" not in state.error
    assert "KeyError" in state.error


# --- availability ----------------------------------------------------------

def test_availability_checks_the_backend_not_just_the_package():
    """Importing `webview` proves only that a pure-Python package is present;
    the thing that opens a window is resolved later, which is exactly where a
    one-file binary loses it."""
    src = inspect.getsource(desktop)
    assert "webview.platforms" in src


def test_availability_is_cached(monkeypatch):
    calls = []

    def counted():
        calls.append(1)
        return (False, "X: nope")

    monkeypatch.setattr(desktop, "_probed", None)
    monkeypatch.setattr(desktop, "_probe", counted)
    desktop.available()
    desktop.available()
    assert len(calls) == 1, "the page header calls this on every render"


def test_short_reason_drops_the_host_path():
    long = "ImportError: dlopen(/Users/someone/private/lib/_objc.so): not found"
    assert desktop.short_reason(long) == "ImportError"
    assert "/Users/someone" not in desktop.short_reason(long)


# --- browsing for an export (#10) ------------------------------------------

def test_browse_button_appears_only_when_something_can_open_a_dialog(state):
    """Browser mode cannot read a path off the machine, so offering the button
    there would be a control that does nothing."""
    assert state.file_picker is None
    assert "Browse" not in state.render()
    state.file_picker = lambda: None
    assert "Browse" in state.render()


def test_browsing_opens_whatever_the_dialog_returns(state, export_zip):
    state.zip_path = ""
    state.file_picker = lambda: str(export_zip)
    ui.dispatch(state, "/pick-export", {})
    assert state.zip_path == str(export_zip)
    assert state.graph is not None


def test_cancelling_the_dialog_is_not_an_error(state):
    """A cancelled dialog returns nothing, and a red error bar for "I changed
    my mind" is the kind of thing that makes a tool feel broken."""
    before = state.zip_path
    state.file_picker = lambda: None
    ui.dispatch(state, "/pick-export", {})
    assert state.error == ""
    assert state.zip_path == before


def test_browsing_without_a_dialog_says_what_to_do_instead(state):
    state.file_picker = None
    ui.dispatch(state, "/pick-export", {})
    assert "type the path" in state.error


def test_a_dialog_that_blows_up_does_not_take_the_window_with_it(state):
    def broken():
        raise RuntimeError("no portal service")

    state.file_picker = broken
    res = desktop.Bridge(state).act("/pick-export", {})
    assert "no portal service" in state.error
    assert "<html" in res["html"].lower()


class _FakeWindow:
    """Stands in for the pywebview window, which needs a display."""

    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.calls = 0

    def create_file_dialog(self, *_args, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if self.raises is not None:
            raise self.raises
        return self.result


@pytest.fixture
def fake_webview(monkeypatch):
    """`desktop._picker_for` imports webview for its OPEN_DIALOG constant."""
    import sys
    import types

    mod = types.ModuleType("webview")
    mod.OPEN_DIALOG = 10
    monkeypatch.setitem(sys.modules, "webview", mod)
    return mod


def test_the_picker_resolves_its_window_when_pressed_not_when_built(fake_webview):
    """The page has to render before there is a window, and the render decides
    whether the button is there at all, so an eagerly bound window would hide
    the button on the first page shown.

    This drives the function rather than reading it. The version of this test
    that grepped the source passed while the binding was broken.
    """
    holder = {}
    pick = desktop._picker_for(holder)          # built with no window at all
    assert pick() is None                        # pressed too early: no crash
    holder["window"] = _FakeWindow(result=("/tmp/x.zip",))
    assert pick() == ("/tmp/x.zip",)             # pressed later: finds it


def test_the_picker_asks_for_one_file(fake_webview):
    holder = {"window": _FakeWindow(result=None)}
    desktop._picker_for(holder)()
    assert holder["window"].kwargs["allow_multiple"] is False


def test_a_dialog_that_raises_returns_nothing_rather_than_propagating(fake_webview):
    holder = {"window": _FakeWindow(raises=RuntimeError("no portal"))}
    assert desktop._picker_for(holder)() is None


@pytest.mark.parametrize("returned,expect_open", [
    ("PATH", True),            # a bare string
    (("PATH",), True),         # the sequence a dialog actually hands back
    (["PATH"], True),
    (None, False),
    ((), False),
    ("", False),
])
def test_every_shape_a_file_dialog_can_return(state, export_zip, returned, expect_open):
    """Unwrapped, a one-item tuple reaches open_export and raises a TypeError
    that lands in front of the user as "argument should be a str or an
    os.PathLike". Normalising is done in this layer because this is the layer
    a test can reach without a display."""
    state.zip_path = ""
    value = returned
    if returned is not None:
        value = type(returned)(str(export_zip) for _ in returned) if isinstance(
            returned, (list, tuple)) else (str(export_zip) if returned else "")
    state.file_picker = lambda: value
    ui.dispatch(state, "/pick-export", {})
    assert state.error == "", state.error
    assert (state.zip_path == str(export_zip)) is expect_open


def test_a_dialog_returning_something_unusable_says_so_instead_of_crashing(state):
    state.file_picker = lambda: b"/tmp/bytes.zip"
    ui.dispatch(state, "/pick-export", {})
    assert "unusable" in state.error
    assert "bytes" in state.error
