"""Native webview window and action bridge. No local HTTP server is used."""

import json
import threading
import webbrowser
from typing import Any, Dict, Optional, Tuple

from . import __version__, releases

# Kept verbatim in one place so the test that proves the bridge carries a named
# submit button has something to point at.
_BRIDGE_JS = """
<script>
(function () {
  var releaseTag = null;
  function showRelease() {
    var link = document.getElementById('release-notice');
    if (link && releaseTag) {
      link.textContent = releaseTag + ' available ↗';
      link.hidden = false;
    }
  }
  window.addEventListener('pywebviewready', function () {
    window.pywebview.api.check_release().then(function (tag) {
      releaseTag = tag;
      showRelease();
    }).catch(function () {});
  }, {once: true});
  document.addEventListener('click', function (ev) {
    if (ev.target.closest && ev.target.closest('#release-notice')) {
      ev.preventDefault();
      window.pywebview.api.open_release().catch(function () {});
    }
  });
  function banner(text) {
    var bar = document.createElement('div');
    bar.setAttribute('role', 'alert');
    bar.style.cssText = 'background:#8d1515;color:#fff;padding:10px 14px;font:14px system-ui';
    bar.textContent = text;
    document.body.insertBefore(bar, document.body.firstChild);
  }
  // Delegated on document, never on the body, because every action replaces
  // the body wholesale. A listener bound to the body would work exactly once.
  document.addEventListener('submit', function (ev) {
    var form = ev.target;
    if (!form || form.tagName !== 'FORM') { return; }
    ev.preventDefault();
    var data = {};
    // The submitting button goes in FIRST, so that a field of the same name
    // wins over it, including repeated fields in selection forms.
    var by = ev.submitter;
    if (by && by.name) { data[by.name] = by.value; }
    new FormData(form).forEach(function (value, key) { data[key] = value; });
    var action = form.getAttribute('action') || '/';
    if (!window.migratorActivity.start(action)) { return; }
    if (action === '/connect') { form.reset(); }
    var navigation = window.migratorNavigation.capture(action);
    Promise.resolve().then(function () {
      return window.pywebview.api.act(action, data);
    }).then(function (res) {
      data = null;
      var doc = new DOMParser().parseFromString(res.html, 'text/html');
      document.body.replaceWith(doc.body);
      showRelease();
      if (doc.title) { document.title = doc.title; }
      // Keep the selected object in view after the page is redrawn.
      var target = res.anchor ? document.getElementById(res.anchor) : null;
      if (navigation) { window.migratorNavigation.restore(navigation); }
      else if (target) { target.scrollIntoView(); } else { window.scrollTo(0, 0); }
    }).catch(function (err) {
      data = null;
      // Without this the window just stops responding to a button, with
      // nothing on screen and the reason on a stderr nobody launched it from.
      banner(action === '/connect' ? 'Connection failed. Check the source export job before retrying.' : 'That action failed, and the page below may now be out of date: ' + err);
    }).finally(function () { window.migratorActivity.finish(); });
  }, true);
})();
</script>
"""


# Which module actually opens a window, per platform. Importing `webview`
# proves only that a pure-Python package was installed or bundled; the backend
# is resolved later, inside `webview.start()`, and on a one-file binary that is
# exactly where it goes missing. Naming the backend here is what lets a build
# that lost it fail in CI instead of on someone's desktop.
_BACKENDS = {
    "linux": ("webview.platforms.qt",),
    "darwin": ("webview.platforms.cocoa",),
    "win32": ("webview.platforms.edgechromium", "webview.platforms.winforms"),
}

_probed: Optional[Tuple[bool, str]] = None


def _probe() -> Tuple[bool, str]:
    import importlib
    import sys

    try:
        import webview  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on the machine
        return False, f"{type(exc).__name__}: {exc}"
    candidates = _BACKENDS.get(sys.platform)
    if not candidates:
        return False, f"no supported window backend on {sys.platform}"
    reason = ""
    for name in candidates:
        try:
            importlib.import_module(name)
            return True, ""
        except Exception as exc:  # pragma: no cover - depends on the machine
            reason = f"{type(exc).__name__}: {exc}"
    return False, reason


def available(refresh: bool = False) -> Tuple[bool, str]:
    """Can this machine open a native window, and if not, why not.

    Returns (ok, reason). Cached, because this is reached from `version_lines`,
    which the page header renders on every single action; a failed import is
    not cached by Python, so without this each render would walk sys.path
    again looking for something that is not there.
    """
    global _probed
    if _probed is None or refresh:
        _probed = _probe()
    return _probed


def short_reason(reason: str) -> str:
    """The reason, without the host paths an import error tends to carry.

    The long form is worth printing to someone's terminal when they are being
    told why their window cannot open. It is not worth baking into the page header,
    which ends up inside a diagnostics file an admin may hand to somebody else.
    """
    return (reason.split(":", 1)[0] or "unavailable").strip()


def page_html(state: Any, initial_zip: Optional[str] = None) -> str:
    """The page as the window should receive it: rendered, plus the bridge."""
    html = state.render()
    bridge_script = _BRIDGE_JS
    if initial_zip:
        path_json = json.dumps(str(initial_zip)).replace("<", "\\u003c")
        bridge_script += """<script>
window.addEventListener('pywebviewready', function () {
  var input = document.getElementById('zip');
  if (input) { input.value = PATH; input.form.requestSubmit(); }
}, {once: true});
</script>""".replace('PATH', path_json)
    # Before </body> so the document is parsed by the time it runs, and so a
    # page that somehow lacks the tag still gets the script rather than
    # silently losing every button on it.
    if "</body>" in html:
        return html.replace("</body>", bridge_script + "</body>", 1)
    return html + bridge_script


class Bridge:
    """Expose only the registered application actions to the native webview."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self._release_lock = threading.Lock()
        self._release_checked = False
        self._release_tag = None

    def check_release(self) -> Optional[str]:
        # pywebview invokes bridge methods on worker threads. This lock is
        # separate from content actions, so the network never holds them up.
        with self._release_lock:
            if not self._release_checked:
                self._release_checked = True
                self._release_tag = releases.newer_release(__version__)
            return self._release_tag

    def open_release(self) -> bool:
        # Never accept a URL from the page or the release API.
        return webbrowser.open(releases.RELEASE_PAGE, new=2)

    def act(self, path: str, form: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        from . import ui

        with self._state.action_lock:
            path = str(path)
            fields = {str(k): "" if v is None else str(v) for k, v in (form or {}).items()}
            anchor = ""
            # The lookup is checked separately from running the action. Wrapping
            # both in `except KeyError` would report a stray KeyError from deep
            # inside an action as "no such page action", which is a lie that would
            # cost someone an afternoon.
            if path not in ui.ACTIONS:
                self._state.message = ""
                self._state.error = f"this build has no page action called {path!r}"
            else:
                try:
                    anchor = ui.dispatch(self._state, path, fields)
                except Exception as exc:
                    # The window has no status bar and no console the user will
                    # find. An action that dies has to say so on the page.
                    self._state.message = ""
                    self._state.error = f"{type(exc).__name__}: {exc}"
            return {"html": page_html(self._state), "anchor": anchor or ""}


def _picker_for(holder: Dict[str, Any]) -> Any:
    """A callable that asks the machine for an export zip, or returns None.

    The window is taken out of ``holder`` when the button is pressed, not when
    this is built. The page has to be rendered before there is a window to
    create, and the render is what decides whether the button appears at all,
    so binding the window eagerly would hide the button on the first page and
    show it only after some other action had redrawn.

    ``holder`` is the seam that makes this testable without a display: a test
    puts an object with a ``create_file_dialog`` method in it. An earlier
    version of this function was marked no-cover and guarded only by a test
    that grepped its source, which passed while the function was broken.
    """
    import webview

    def pick() -> Any:
        window = holder.get("window")
        if window is None:
            return None
        try:
            chosen = window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("Content export (*.zip)", "All files (*.*)"),
            )
        except Exception:
            # A broken file chooser must not take the window down with it:
            # the path box beside it still works.
            return None
        return chosen

    return pick


def run(state: Any, title: str = "VCF content migrator", width: int = 1280,
        height: int = 860, initial_zip: Optional[str] = None) -> None:  # pragma: no cover - needs a display
    """Open the window and block until the user closes it."""
    import webview

    bridge = Bridge(state)
    holder: Dict[str, Any] = {}
    # Set before the first render, so the very first page carries the button.
    state.file_picker = _picker_for(holder)
    holder["window"] = webview.create_window(title, html=page_html(state, initial_zip),
                                             js_api=bridge, width=width,
                                             height=height)
    def progress(stage):
        try:
            holder['window'].evaluate_js('window.migratorActivity.stage(' + json.dumps(stage) + ')')
        except Exception:
            pass

    state.source_progress = progress
    import sys

    webview.start(gui="qt" if sys.platform.startswith("linux") else None, http_server=False)
