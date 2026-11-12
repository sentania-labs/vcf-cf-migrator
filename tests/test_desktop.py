"""The native window: the bridge, the fallback, and the seams that keep the
two window modes from drifting apart.

The window itself cannot be opened here, so nothing below tries. What is
tested is everything on either side of it: that the page handed to the window
carries the script that makes its buttons work, that the script hands actions
back through one dispatch, that nothing binds a port, and that a machine with
no webview is told why.

Fixture only. Nothing here reads the corpus.
"""
from __future__ import annotations

import os
import re

import pytest

from pages import visible
from vcfcf_migrator import desktop, ui
from vcfcf_migrator.cli import main
from vcfcf_migrator.ui import ACTIONS, PageState


@pytest.fixture
def state(config_dir, export_zip):
    return PageState(str(export_zip))


# --- the page the window receives -----------------------------------------

def test_the_window_gets_the_page_the_state_renders(state):
    """The window must not get a different page."""
    assert desktop.page_html(PageState(state.zip_path)) == PageState(state.zip_path).render()


def test_every_control_on_every_screen_names_an_action_that_exists(state):
    """A button whose action is not registered is a dead button."""
    bridge = desktop.Bridge(state)
    key = next(n.key for n in state.graph.ordered() if n.kind == 'supermetric' and n.name == '[Fixture] SM Empty')
    seen = set()

    def look():
        page = state.render()
        seen.update(re.findall(r'data-act="([^"]+)"', page))

    look()
    bridge.act('/select', {'key': key, 'on': '1'})
    bridge.act('/focus', {'key': key})
    look()
    for screen in ('owner', 'review', 'save', 'target', 'help', 'open', 'connect'):
        state.screen = screen
        look()
    state.screen = 'save'
    state.save_state = 'exists'
    look()
    assert seen and seen <= set(ACTIONS) | {'/conn-test'}, sorted(seen - set(ACTIONS))


# --- the bridge ------------------------------------------------------------

def test_bridge_reaches_every_action_and_always_answers_with_a_page(state):
    bridge = desktop.Bridge(state)
    for path in ACTIONS:
        res = bridge.act(path, {})
        assert set(res) == {"html", "focus"}, path
        assert "<html" in res["html"].lower(), path


def test_bridge_refuses_an_action_that_does_not_exist(state):
    bridge = desktop.Bridge(state)
    res = bridge.act("/there-is-no-such-thing", {})
    assert state.note.kind == 'bad' and "no action called" in state.note.text
    assert "<html" in res["html"].lower()


def test_bridge_coerces_form_values_to_strings(state):
    """The webview hands over JSON, so a value can arrive as null or a boolean."""
    bridge = desktop.Bridge(state)
    bridge.act("/filter", {"f": "all", "q": None})
    assert state.filter_text == ""


def test_typing_in_the_search_box_draws_only_the_list(state):
    bridge = desktop.Bridge(state)
    parts = bridge.find('cluster')
    assert set(parts) == {'list', 'cnt', 'addall'}
    assert state.filter_text == 'cluster'
    assert '<html' not in parts['list'] and 'Cluster' in parts['list']
    assert parts['cnt'] == f'{len(state.visible_nodes())} of {len(state.graph.nodes)}'
    assert 'No names contain' in bridge.find('zzz-nothing')['list']
    # The program and the page agree on what is shown: the bulk button acts on it.
    bridge.find('SM Empty')
    assert [n.name for n in state.visible_nodes()] == ['[Fixture] SM Empty']


# --- the seams -------------------------------------------------------------

def test_a_bridge_action_writes_exactly_one_page_action_log_line(state):
    """Every way in logs identically because only one place logs at all."""
    before = len(state.log.snapshot())
    desktop.Bridge(state).act('/filter', {'f': 'all', 'q': ''})
    logged = [e for e in state.log.snapshot()[before:]
              if e.get('event') == 'page.action' and e.get('action') == '/filter']
    assert len(logged) == 1


def test_a_dropped_file_opens_through_the_logged_dispatch(config_dir, export_zip):
    state = PageState()
    event = {'dataTransfer': {'files': [{'pywebviewFullPath': str(export_zip)}]}}
    desktop.Bridge(state).dropped(event)
    logged = [e for e in state.log.snapshot()
              if e.get('event') == 'page.action' and e.get('action') == '/open']
    assert len(logged) == 1
    assert state.graph is not None and state.screen == 'choose'


def test_a_drop_with_no_file_path_does_nothing(config_dir):
    state = PageState()
    bridge = desktop.Bridge(state)
    bridge.dropped({'dataTransfer': {'files': [{'name': 'x.zip'}]}})
    bridge.dropped(None)
    assert state.job is None and state.graph is None
    assert [e for e in state.log.snapshot() if e.get('event') == 'page.action'] == []


# --- nothing is listening --------------------------------------------------

def test_desktop_mode_binds_nothing(monkeypatch, config_dir, export_zip):
    opened = {}

    def fake_run(state, **kwargs):
        opened["state"] = state
        opened["kwargs"] = kwargs

    monkeypatch.setattr(desktop, "run", fake_run)
    assert ui.run_desktop(zip_path=str(export_zip)) == 0
    # The export is opened by the window once its bridge is ready, not before.
    assert opened["state"].graph is None and opened["kwargs"] == {'initial_zip': str(export_zip)}


def test_desktop_path_never_constructs_a_server(monkeypatch, config_dir):
    import sys
    import types

    started = {}

    class Loaded(list):
        def __iadd__(self, fn):
            self.append(fn)
            return self

    class Window:
        def __init__(self):
            self.events = types.SimpleNamespace(loaded=Loaded())

    fake = types.ModuleType('webview')
    fake.create_window = lambda *args, **kwargs: Window()
    fake.start = lambda *args, **kwargs: started.update(kwargs)
    monkeypatch.setitem(sys.modules, 'webview', fake)
    desktop.run(PageState())
    assert started.get('http_server') is False


# --- the fallback ----------------------------------------------------------

def test_availability_reports_a_reason(monkeypatch):
    ok, why = desktop.available()
    assert isinstance(ok, bool)
    if not ok:
        assert why, "a machine that cannot open a window must say why"


# --- what earlier reviews caught, still guarded ----------------------------

def test_bridge_returns_a_focus_target_that_is_on_the_page(state):
    """The value has to be an id that is actually on the page, or the window
    loses the keyboard's place exactly as if there were none."""
    bridge = desktop.Bridge(state)
    key = next(iter(state.graph.ordered())).key
    res = bridge.act("/select", {"key": key, "on": "1"})
    assert res["focus"], "ticking something must say where the keyboard goes back to"
    assert f'id="{res["focus"]}"' in res["html"]


def test_an_action_that_raises_lands_on_the_page_not_in_the_void(state, monkeypatch):
    """A rejected bridge call used to leave the window silently unresponsive."""
    def boom(*_args, **_kwargs):
        raise OSError("disk went away")

    monkeypatch.setitem(ui.ACTIONS, "/save", boom)
    bridge = desktop.Bridge(state)
    res = bridge.act("/save", {})
    assert state.note.kind == 'bad' and "disk went away" in state.note.text
    assert "That did not work." in visible(res["html"])


def test_a_sign_in_action_that_raises_never_shows_what_was_typed(state, monkeypatch):
    def boom(_state, form):
        raise ValueError("could not use " + form['password'])

    monkeypatch.setitem(ui.ACTIONS, "/connect", boom)
    res = desktop.Bridge(state).act("/connect", {'password': 'hunter2-invented'})
    assert 'hunter2-invented' not in res['html'] and 'ValueError' in state.note.text


def test_a_keyerror_inside_an_action_is_not_reported_as_a_missing_action(state, monkeypatch):
    def boom(*_args, **_kwargs):
        raise KeyError("some-inner-lookup")

    monkeypatch.setitem(ui.ACTIONS, "/again", boom)
    desktop.Bridge(state).act("/again", {})
    assert "no action called" not in state.note.text
    assert "KeyError" in state.note.text


def test_nothing_but_cancel_gets_through_while_a_job_is_on_screen(state):
    state.job = ui.Job('read', 'big.zip')
    before = list(state.picked)
    key = next(iter(state.graph.ordered())).key
    desktop.Bridge(state).act('/select', {'key': key, 'on': '1'})
    assert state.picked == before
    page = visible(state.render())
    assert 'Reading big.zip' in page and 'Cancelling changes nothing.' in page
    desktop.Bridge(state).act('/cancel-job', {})
    assert state.job is None and 'Cancelled.' in state.note.text


# --- availability ----------------------------------------------------------

def test_availability_checks_the_backend_not_just_the_package(monkeypatch):
    """Importing `webview` proves only that a pure-Python package is present;
    the thing that opens a window is resolved later, which is exactly where a
    one-file binary loses it."""
    import sys
    import types

    monkeypatch.setitem(sys.modules, 'webview', types.ModuleType('webview'))
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(desktop, '_BACKENDS', {'linux': ('vcfm_missing_backend',)})
    ok, why = desktop.available(refresh=True)
    assert not ok and 'vcfm_missing_backend' in why
    monkeypatch.setitem(sys.modules, 'vcfm_missing_backend', types.ModuleType('vcfm_missing_backend'))
    assert desktop.available(refresh=True) == (True, '')


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


# --- choosing an export file ------------------------------------------------

def test_the_file_window_is_the_way_in_and_a_path_box_only_the_fallback(state):
    state.screen = 'open'
    page = state.render()
    assert 'Choose the export file…' in visible(page) and 'id="zip"' not in page
    # No file window to open: the same button offers a place to type instead.
    state.file_picker = None
    ui.dispatch(state, "/pick-export", {})
    page = state.render()
    assert state.typed_path and 'id="zip"' in page
    assert 'The file window could not be opened.' in visible(page)


def test_choosing_opens_whatever_the_file_window_returns(config_dir, export_zip):
    state = PageState()
    state.file_picker = lambda: str(export_zip)
    ui.dispatch(state, "/pick-export", {})
    assert state.zip_path == str(export_zip)
    assert state.graph is not None and state.screen == 'choose'


def test_cancelling_the_file_window_is_not_an_error(state):
    """A cancelled dialog returns nothing, and a red bar for "I changed my
    mind" is the kind of thing that makes a tool feel broken."""
    before = state.zip_path
    state.file_picker = lambda: None
    ui.dispatch(state, "/pick-export", {})
    assert state.note is None and state.open_error is None and not state.typed_path
    assert state.zip_path == before


def test_a_file_window_that_blows_up_offers_the_path_box(state):
    def broken():
        raise RuntimeError("no portal service")

    state.file_picker = broken
    state.screen = 'open'
    res = desktop.Bridge(state).act("/pick-export", {})
    assert state.typed_path and 'id="zip"' in res["html"]
    assert "no portal service" not in res["html"]


def test_a_typed_path_opens_like_a_chosen_file(config_dir, export_zip):
    state = PageState()
    state.typed_path = True
    desktop.Bridge(state).act("/open", {'zip': f'  {export_zip}  '})
    assert state.graph is not None and state.zip_path == str(export_zip)


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
    """The page has to render before there is a window.

    This drives the function rather than reading it. The version of this test
    that grepped the source passed while the binding was broken.
    """
    holder = {}
    pick = desktop._picker_for(holder)          # built with no window at all
    assert pick() is None                        # pressed too early: no crash
    holder["window"] = _FakeWindow(result=("/tmp/x.zip",))
    assert pick() == ("/tmp/x.zip",)             # pressed later: finds it


def test_the_picker_asks_for_one_file_and_starts_in_downloads(fake_webview, tmp_path, monkeypatch):
    home = tmp_path / 'home'
    (home / 'Downloads').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    holder = {"window": _FakeWindow(result=None)}
    desktop._picker_for(holder)()
    assert holder["window"].kwargs["allow_multiple"] is False
    assert holder["window"].kwargs["directory"] == str(home / 'Downloads')


@pytest.mark.parametrize("returned,expect_open", [
    ("PATH", True),            # a bare string
    (("PATH",), True),         # the sequence a dialog actually hands back
    (["PATH"], True),
    (None, False),
    ((), False),
    ("", False),
])
def test_every_shape_a_file_dialog_can_return(config_dir, export_zip, returned, expect_open):
    """Unwrapped, a one-item tuple reaches open_export and raises a TypeError.
    Normalising is done in this layer because this is the layer a test can
    reach without a display."""
    state = PageState()
    value = returned
    if returned is not None:
        value = type(returned)(str(export_zip) for _ in returned) if isinstance(
            returned, (list, tuple)) else (str(export_zip) if returned else "")
    state.file_picker = lambda: value
    ui.dispatch(state, "/pick-export", {})
    assert state.note is None or state.note.kind == 'ok'
    assert (state.zip_path == str(export_zip)) is expect_open


def test_a_dialog_returning_something_unusable_offers_the_path_box(state):
    state.file_picker = lambda: b"/tmp/bytes.zip"
    ui.dispatch(state, "/pick-export", {})
    assert state.typed_path


# --- dropping a file on the window -------------------------------------------

def test_a_dropped_file_is_opened_like_a_chosen_one(config_dir, export_zip):
    state = PageState()
    bridge = desktop.Bridge(state)
    refreshed = []
    bridge._window = type('W', (), {'evaluate_js': lambda self, code: refreshed.append(code)})()
    bridge.dropped({'dataTransfer': {'files': [{'name': 'x.zip', 'pywebviewFullPath': str(export_zip)}]}})
    assert state.graph is not None and state.zip_path == str(export_zip)
    assert refreshed == ['window.migrator && window.migrator.refresh()']


@pytest.mark.parametrize('event', [None, {}, {'dataTransfer': {'files': []}},
                                   {'dataTransfer': {'files': [{'name': 'no-path.zip'}]}}])
def test_a_drop_with_no_usable_path_does_nothing(config_dir, event):
    state = PageState()
    desktop.Bridge(state).dropped(event)
    assert state.graph is None and state.open_error is None


def test_a_drop_is_ignored_away_from_the_open_step(state, tmp_path):
    other = tmp_path / 'other.zip'
    other.write_bytes(b'not a zip')
    before = state.zip_path
    desktop.Bridge(state).dropped({'dataTransfer': {'files': [{'pywebviewFullPath': str(other)}]}})
    assert state.zip_path == before and state.open_error is None and state.screen == 'choose'


def test_missing_window_fails_without_binding_a_socket(monkeypatch, capsys, export_zip):
    import socket
    monkeypatch.setattr(desktop, "available", lambda: (False, "backend unavailable"))
    def refuse_bind(*args, **kwargs):
        raise AssertionError("No UI path may bind a socket")
    monkeypatch.setattr(socket.socket, "bind", refuse_bind)
    assert main(["ui", str(export_zip)]) == 1
    assert "native window unavailable" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--server", "--port", "--no-browser"])
def test_retired_server_options_are_rejected(flag):
    with pytest.raises(SystemExit) as exc:
        main(["ui", flag])
    assert exc.value.code == 2


def test_initial_export_opens_after_the_window_bridge_is_ready(config_dir):
    page = desktop.page_html(ui.PageState(), initial_zip='/tmp/a</script>.zip')
    assert '/tmp/a</script>.zip' not in page
    assert r'/tmp/a\u003c/script>.zip' in page


@pytest.mark.parametrize("platform,expect_icon", [
    ("linux", True),
    ("win32", False),      # WinForms loads only .ico; the exe's embedded icon is used
    ("darwin", False),     # the .icns in the bundle is used
])
def test_only_linux_is_handed_the_png_icon(monkeypatch, platform, expect_icon):
    import sys
    import types

    started = {}

    class Loaded(list):
        def __iadd__(self, fn):
            self.append(fn)
            return self

    fake = types.ModuleType('webview')
    fake.create_window = lambda *a, **k: types.SimpleNamespace(events=types.SimpleNamespace(loaded=Loaded()))
    fake.start = lambda *a, **k: started.update(k)
    monkeypatch.setitem(sys.modules, 'webview', fake)
    monkeypatch.setattr(sys, 'platform', platform)
    desktop.run(PageState())
    if expect_icon:
        assert started['icon'].endswith('app.png') and os.path.isfile(started['icon'])
    else:
        assert started['icon'] is None


def test_app_icon_assets_ship_and_parse():
    import struct
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert (root / "src/vcfcf_migrator/assets/app.svg").read_text().startswith("<svg")
    assert (root / "src/vcfcf_migrator/assets/app.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    ico = (root / "packaging/icons/app.ico").read_bytes()
    assert struct.unpack("<HHH", ico[:6])[1:] == (1, 7)
    icns = (root / "packaging/icons/app.icns").read_bytes()
    assert icns[:4] == b"icns" and struct.unpack(">I", icns[4:8])[0] == len(icns)
