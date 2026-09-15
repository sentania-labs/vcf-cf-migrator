"""The same page, in a window of its own, with no listener behind it.

The browser mode in ``ui.py`` binds a loopback socket and points a browser at
it. That works, but it puts a listening socket on an admin's workstation, and
some sites will not have one, whatever the address it is bound to. Arguing
about whether that policy is well founded does not help the admin standing in
front of it.

So this is the same page driven directly. The window is handed the rendered
HTML rather than a URL, and a small script inside it hands form submissions
back to Python through the webview's own bridge. Nothing binds a port, nothing
listens, and no other process on the machine can reach it. That is a stronger
position than the browser mode's same-origin check, which exists precisely
because a bound socket is reachable by anything that can find it.

What it is NOT is a second implementation of the page. It renders through
``uipage`` and acts through ``ui.dispatch``, the same two calls the server
makes, so a fix to either lands in both modes and the run log reads the same
whichever window you opened.

Not every machine can do this. It needs a system webview, which macOS and
Windows have and a Linux box may not, so ``available()`` reports rather than
assumes, and the CLI falls back to browser mode saying why.
"""

from typing import Any, Dict, Optional, Tuple

# Kept verbatim in one place so the test that proves the bridge carries a named
# submit button has something to point at.
_BRIDGE_JS = """
<script>
(function () {
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
    // wins over it. That is the order the server's parse uses, and the two
    // modes have to agree about it or the same click does different things in
    // each window.
    var by = ev.submitter;
    if (by && by.name) { data[by.name] = by.value; }
    new FormData(form).forEach(function (value, key) { data[key] = value; });
    var action = form.getAttribute('action') || '/';
    window.pywebview.api.act(action, data).then(function (res) {
      var doc = new DOMParser().parseFromString(res.html, 'text/html');
      document.body.replaceWith(doc.body);
      if (doc.title) { document.title = doc.title; }
      // The anchor is why selecting the twentieth object does not throw you
      // back to the top of a four-hundred-row tree. The server gets this from
      // a redirect; here it comes back beside the page.
      var target = res.anchor ? document.getElementById(res.anchor) : null;
      if (target) { target.scrollIntoView(); } else { window.scrollTo(0, 0); }
    }).catch(function (err) {
      // Without this the window just stops responding to a button, with
      // nothing on screen and the reason on a stderr nobody launched it from.
      banner('That action failed, and the page below may now be out of date: ' + err);
    });
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
    told why they got a browser. It is not worth baking into the page header,
    which ends up inside a diagnostics file an admin may hand to somebody else.
    """
    return (reason.split(":", 1)[0] or "unavailable").strip()


def page_html(state: Any) -> str:
    """The page as the window should receive it: rendered, plus the bridge."""
    html = state.render()
    # Before </body> so the document is parsed by the time it runs, and so a
    # page that somehow lacks the tag still gets the script rather than
    # silently losing every button on it.
    if "</body>" in html:
        return html.replace("</body>", _BRIDGE_JS + "</body>", 1)
    return html + _BRIDGE_JS


class Bridge:
    """What the page in the window is allowed to ask Python to do.

    One method. It takes the same action path and form dict the POST handler
    takes, and it refuses anything not in the same table, so the window cannot
    reach further into the process than the browser mode can.
    """

    def __init__(self, state: Any) -> None:
        self._state = state

    def act(self, path: str, form: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        from . import ui

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
        height: int = 860) -> None:  # pragma: no cover - needs a display
    """Open the window and block until the user closes it."""
    import webview

    bridge = Bridge(state)
    holder: Dict[str, Any] = {}
    # Set before the first render, so the very first page carries the button.
    state.file_picker = _picker_for(holder)
    holder["window"] = webview.create_window(title, html=page_html(state),
                                             js_api=bridge, width=width,
                                             height=height)
    webview.start()
