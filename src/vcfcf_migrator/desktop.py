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
  var lookupGeneration = 0, lookupTimer = null, testGeneration = 0;
  function invalidateConnection() {
    lookupGeneration++;
    testGeneration++;
    clearTimeout(lookupTimer);
    var status = document.getElementById('test-status');
    if (status) status.textContent = '';
  }
  function loadSources() {
    var address = document.getElementById('source-address');
    var ca = document.getElementById('source-ca_file');
    var ignore = document.getElementById('source-ignore');
    if (!address || !ca || !ignore) return;
    ca.disabled = ignore.checked;
    document.querySelector('[data-pick="ca"]').disabled = ignore.checked;
    document.getElementById('ssl-warning').hidden = !ignore.checked;
    var seq = ++lookupGeneration;
    var status = document.getElementById('source-status');
    var list = document.getElementById('login-sources');
    list.replaceChildren();
    var local = document.createElement('option'); local.value = 'Local'; list.appendChild(local);
    // Invalid addresses do not dispatch a request. Python also validates at the trust boundary.
    try {
      var url = new URL(address.value);
      if (url.protocol !== 'https:' || !url.hostname || url.username || url.password || url.search || url.hash ||
          !['', '/', '/suite-api', '/suite-api/', '/suite-api/api', '/suite-api/api/'].includes(url.pathname)) throw Error();
    } catch (_) { status.textContent = 'Enter a valid HTTPS server URL to load login sources. Manual entry is always available.'; return; }
    status.textContent = 'Loading login sources…';
    window.pywebview.api.login_sources(address.value, ca.value, ignore.checked).then(function (result) {
      if (seq !== lookupGeneration || !list.isConnected) return;
      (result.sources || []).filter(function(name) { return name.toLowerCase() !== 'local'; }).forEach(function (name) {
        var option = document.createElement('option'); option.value = name; list.appendChild(option);
      });
      status.textContent = result.error ? result.error + ' Enter a login source manually.' :
        (result.sources.length ? 'Login sources loaded. You can also enter one manually.' : 'No additional login sources returned. Use Local or enter one manually.');
    }).catch(function () {
      if (seq === lookupGeneration && list.isConnected) status.textContent = 'Could not load login sources. Enter one manually.';
    });
  }
  document.addEventListener('input', function (ev) {
    if (!ev.target.closest('.connect-form')) return;
    testGeneration++;
    document.getElementById('test-status').textContent = '';
    if (['source-address', 'source-ca_file'].includes(ev.target.id)) {
      invalidateConnection();
      document.getElementById('source-status').textContent = 'Waiting for server settings…';
      lookupTimer = setTimeout(loadSources, 400);
    }
  });
  document.addEventListener('change', function (ev) {
    if (!['source-address', 'source-ca_file', 'source-ignore'].includes(ev.target.id)) return;
    invalidateConnection();
    loadSources();
  });
  document.addEventListener('click', function (ev) {
    var picker = ev.target.closest('[data-pick]');
    if (picker && !picker.disabled) {
      var input = document.getElementById(picker.dataset.target);
      picker.disabled = true;
      window.pywebview.api.pick_path(picker.dataset.pick, input.value).then(function (result) {
        if (!input.isConnected) return;
        if (result.error) { banner(result.error); return; }
        if (result.path) { input.value = result.path; input.dispatchEvent(new Event('change', {bubbles:true})); }
      }).catch(function () { banner('The file picker could not open. Enter the path manually.'); })
        .finally(function () { picker.disabled = picker.dataset.pick === 'ca' && document.getElementById('source-ignore')?.checked; });
    }
    if (ev.target.id === 'test-connection') {
      var button = ev.target, form = button.form, data = {};
      // Testing does not require the unrelated export encryption password.
      var inputs = ['source-address', 'source-username', 'source-password'];
      if (inputs.some(function(id) { return !document.getElementById(id).reportValidity(); })) return;
      new FormData(form).forEach(function (value, key) { if (key !== 'export_password') data[key] = value; });
      var seq = ++testGeneration;
      var status = document.getElementById('test-status');
      status.textContent = 'Testing connection…'; button.disabled = true;
      window.pywebview.api.test_connection(data).then(function (result) {
        if (seq === testGeneration && status.isConnected) status.textContent = result;
      }).catch(function () {
        if (seq === testGeneration && status.isConnected) status.textContent = 'Connection failed. Check the server settings and credentials.';
      }).finally(function () { data = null; button.disabled = false; });
    }
  });
  function banner(text) {
    var bar = document.createElement('div');
    bar.setAttribute('role', 'alert');
    bar.className = 'err';
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
    invalidateConnection();
    if (!window.migratorActivity.start(action)) { return; }
    if (action === '/connect') { form.reset(); }
    var navigation = window.migratorNavigation.capture(action);
    Promise.resolve().then(function () {
      if (action === '/diagnostics' && data.manual !== '1') {
        return window.pywebview.api.pick_path('diagnostics', data.out || '').then(function (result) {
          if (result.error) { throw Error(result.error); }
          if (!result.path) return null;
          data.out = result.path;
          return window.pywebview.api.act(action, data);
        });
      }
      return window.pywebview.api.act(action, data);
    }).then(function (res) {
      data = null;
      if (!res) return;
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

    def login_sources(self, address: str, ca_file: str, ignore_ssl: bool) -> dict:
        from .source import Client, SourceError
        try:
            return {'sources': Client(address, ca_file=ca_file, ignore_ssl=ignore_ssl, request_timeout=10).login_sources()}
        except SourceError as exc:
            return {'error': str(exc)}
        except Exception:
            return {'error': 'Could not load login sources. Enter a source manually.'}

    def test_connection(self, form: dict) -> str:
        from .source import Client, SourceError
        try:
            Client(form.get('address', ''), ca_file=form.get('ca_file', ''),
                   ignore_ssl=form.get('ignore_ssl') in (True, '1'), request_timeout=10).test_connection(
                form.get('username', ''), form.get('password', ''), form.get('auth_source', ''))
            return 'Connection succeeded. No export was started.'
        except SourceError as exc:
            return str(exc)
        except Exception:
            return 'Connection failed. Check the address and credentials.'
        finally:
            form.clear()

    def pick_path(self, kind: str, current: str = '') -> dict:
        from pathlib import Path
        import webview
        window = getattr(self, '_window', None)
        if not window or kind not in ('ca', 'bundle', 'diagnostics'):
            return {'error': 'The file picker is unavailable. Enter the path manually.'}
        try:
            if kind == 'ca':
                chosen = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                    file_types=('PEM certificates (*.pem;*.crt)', 'All files (*.*)'))
            else:
                extension = '.zip' if kind == 'bundle' else '.jsonl'
                target = Path(self._state.output_path(current or ('bundle.zip' if kind == 'bundle' else 'diagnostics.jsonl'), extension))
                chosen = window.create_file_dialog(webview.SAVE_DIALOG, directory=str(target.parent),
                    save_filename=target.name,
                    file_types=(('Zip files (*.zip)' if kind == 'bundle' else 'JSON lines (*.jsonl)'),))
            value = (chosen[0] if chosen else '') if isinstance(chosen, (list, tuple)) else (chosen or '')
            if value and kind != 'ca':
                value = self._state.output_path(value, extension)
            return {'path': value}
        except Exception:
            return {'error': 'The file picker could not open. Enter the path manually.'}

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
    if initial_zip:
        state.source_mode = 'zip'
    state.confirm = lambda title, message: holder['window'].create_confirmation_dialog(title, message)
    import shutil
    import sys
    if sys.platform in ('darwin', 'win32') or shutil.which('xdg-open'):
        state.show_in_folder = show_in_folder
    holder["window"] = webview.create_window(title, html=page_html(state, initial_zip),
                                             js_api=bridge, width=width,
                                             height=height)
    bridge._window = holder["window"]
    def progress(stage):
        try:
            holder['window'].evaluate_js('window.migratorActivity.stage(' + json.dumps(stage) + ')')
        except Exception:
            pass

    state.source_progress = progress
    import sys

    webview.start(gui="qt" if sys.platform.startswith("linux") else None, http_server=False)


def show_in_folder(path: str) -> bool:
    """Reveal only a successfully written bundle, without invoking a shell."""
    import os
    from pathlib import Path
    import subprocess
    import sys
    target = Path(path).resolve()
    if not target.is_file():
        return False
    try:
        if sys.platform == 'darwin':
            subprocess.Popen(['open', '-R', str(target)])
        elif sys.platform == 'win32':
            os.startfile(str(target.parent))
        else:
            subprocess.Popen(['xdg-open', str(target.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False
