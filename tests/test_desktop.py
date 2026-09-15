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
    page = state.render()
    assert "Clear</button>" in page, "the control this guards is not on the page"
    for form in re.findall(r"<form\b.*?</form>", page, re.S):
        names = re.findall(r"""\bname=['"]([^'"]+)['"]""", form)
        # A checkbox group would legitimately repeat a name; this page has none.
        assert len(names) == len(set(names)), f"duplicate field name in {names}"


def test_bridge_returns_the_anchor_so_the_window_does_not_jump_to_the_top(state):
    bridge = desktop.Bridge(state)
    res = bridge.act("/select", {"key": _some_key(state), "tick": "on"})
    assert "anchor" in res
    assert res["anchor"], "selecting an object must come back with somewhere to scroll to"


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
