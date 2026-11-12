"""Native webview window and action bridge. No local HTTP server is used."""

import json
import os
import threading
import time
import webbrowser
from typing import Any, Dict, Optional, Tuple

from . import __version__, releases

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
    """The page as the window should receive it.

    The page carries its own script. The only thing added here is the export
    to open once the bridge is ready, when the app was started with one.
    """
    html = state.render()
    if not initial_zip:
        return html
    path_json = json.dumps(str(initial_zip)).replace("<", "\\u003c")
    script = f"<script>window.migratorInitialZip = {path_json};</script>"
    # Before </body> so a page that somehow lacks the tag still gets it.
    if "</body>" in html:
        return html.replace("</body>", script + "</body>", 1)
    return html + script


class Bridge:
    """Expose only the registered application actions to the native webview."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self._release_lock = threading.Lock()

    def _page(self, focus: str = "") -> Dict[str, str]:
        """The page as it stands. A screen that cannot be drawn must not
        leave the window stuck on it: whatever was laid over the page is
        closed and the step itself is drawn, with the reason said."""
        from . import ui
        state = self._state
        try:
            return {"html": page_html(state), "focus": focus or ""}
        except Exception as exc:  # noqa: BLE001 - said on the page
            state.preview_key, state.doc, state.gloss = '', '', False
            if state.ask is not None:
                state._drop_question()
            state.note = ui.Note('bad', '<b>That could not be shown.</b> The app hit a problem it did not '
                                        f'expect ({ui.e(type(exc).__name__)}). Nothing was changed.')
            try:
                return {"html": page_html(state), "focus": ""}
            except Exception:  # noqa: BLE001
                state.screen = 'open' if state.graph is None else 'choose'
                state.focus_key, state.filter_kind, state.filter_text = '', 'all', ''
                return {"html": page_html(state), "focus": ""}

    def _client(self, address: str, timeout: int = 10):
        from .source import Client
        cert = self._state.conn.get('cert') or {}
        return Client(address, ca_file=cert.get('path', ''), ignore_ssl=cert.get('mode') == 'skip',
                      request_timeout=timeout)

    def login_sources(self, address: str) -> dict:
        """The sign-in choices the server at *address* offers, as the markup
        for the "This account is" list and the two hints beside it."""
        from . import screens
        from .source import SourceError
        try:
            names = self._client(address).login_sources()
        except SourceError:
            names = None
        except Exception:
            names = None
        lookup = 'failed' if names is None else 'ok'
        with self._state.action_lock:
            self._state.conn['sources'] = names or []
            self._state.conn['lookup'] = lookup
        return {'options': screens.source_options(names or []),
                'address_hint': screens.ADDRESS_HINTS[lookup],
                'source_hint': screens.SOURCE_HINTS[lookup]}

    def test_connection(self, form: dict) -> Dict[str, str]:
        """Check the address, user name and password. Starts no export.

        The network call runs without the action lock, so the window stays
        usable; only the result is written under it. Nothing typed is kept or
        logged.
        """
        from .source import CertificateProblem, SignInRefused, SourceError
        state = self._state
        result, text = 'ok', ''
        try:
            self._client(str(form.get('address', ''))).test_connection(
                str(form.get('username', '')), str(form.get('password', '')),
                str(form.get('auth_source', '')))
        except CertificateProblem:
            result = 'cert'
        except SignInRefused:
            result = 'auth'
        except SourceError as exc:
            result, text = 'other', str(exc)
        except Exception:
            result, text = 'other', 'The server could not be reached. Check the address.'
        finally:
            form.clear()
        with state.action_lock:
            state.conn['tested'], state.conn['tested_text'], state.conn['error'] = result, text, None
            if result == 'cert':
                # Whatever certificate choice was in force did not work.
                state.conn['cert'] = None
            state.version += 1
            return self._page('conn-test')

    def pick_path(self, kind: str, current: str = '') -> dict:
        """One of the computer's own file windows: a certificate to open, or a
        place to save a bundle or a support report."""
        from pathlib import Path
        import webview
        window = getattr(self, '_window', None)
        if not window or kind not in ('ca', 'bundle', 'report'):
            return {'error': 'The file window is not available here.'}
        try:
            if kind == 'ca':
                chosen = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                    file_types=('PEM certificates (*.pem;*.crt)', 'All files (*.*)'))
            else:
                extension = {'bundle': '.zip', 'report': '.txt'}[kind]
                default = (self._state.default_out() if kind == 'bundle'
                           else str(self._state.download_directory() / 'support-report.txt'))
                target = Path(self._state.output_path(current, extension, default))
                types = {'bundle': 'Zip files (*.zip)', 'report': 'Text files (*.txt)'}[kind]
                chosen = window.create_file_dialog(webview.SAVE_DIALOG, directory=str(target.parent),
                    save_filename=target.name, file_types=(types,))
            value = (chosen[0] if chosen else '') if isinstance(chosen, (list, tuple)) else (chosen or '')
            if value and kind != 'ca':
                exact = str(Path(value).expanduser().resolve())
                value = self._state.output_path(value, extension)
                return {'path': value, 'confirmed': value == exact}
            return {'path': value}
        except Exception:
            return {'error': 'The file window could not be opened.'}

    def check_release(self) -> dict:
        """Ask once whether a newer version exists, and hand back the header
        button that says so."""
        # pywebview invokes bridge methods on worker threads. This lock is
        # separate from content actions, so the network never holds them up.
        from . import screens
        with self._release_lock:
            if not self._state.update_checked:
                tag, reached = releases.release_status(__version__)
                self._state.update_tag, self._state.update_known = tag, reached
                self._state.update_checked = True
            return {'tag': self._state.update_tag, 'html': screens.update_button(self._state)}

    def progress(self, version: Any = '') -> Dict[str, str]:
        """Wait a moment for the page to change, and return it if it did.

        The window calls this in a loop while a job is on screen. It holds no
        lock while it waits, so Cancel gets through.
        """
        state = self._state
        deadline = time.monotonic() + 1.0
        while str(state.version) == str(version) and time.monotonic() < deadline:
            job = state.job
            if job is not None:
                job.done.wait(0.1)
            else:
                time.sleep(0.05)
        if str(state.version) == str(version):
            return {}
        with state.action_lock:
            return self._page()

    def find(self, text: Any = '') -> Dict[str, str]:
        """The list for what has been typed in the search box, and nothing else."""
        from . import screens, ui
        state = self._state
        with state.action_lock:
            if state.graph is None or state.job is not None:
                return {}
            ui.dispatch(state, '/find', {'q': '' if text is None else str(text)})
            return screens.list_parts(state)

    def act(self, path: str, form: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        from . import ui

        with self._state.action_lock:
            path = str(path)
            fields = {str(k): "" if v is None else str(v) for k, v in (form or {}).items()}
            focus = ""
            # The lookup is checked separately from running the action. Wrapping
            # both in `except KeyError` would report a stray KeyError from deep
            # inside an action as "no such page action", which is a lie that would
            # cost someone an afternoon.
            if path not in ui.ACTIONS:
                self._state.note = ui.Note('bad', f'This version of the app has no action called {ui.e(path)}.')
            else:
                try:
                    focus = ui.dispatch(self._state, path, fields)
                except Exception as exc:
                    # The window has no status bar and no console the user will
                    # find. An action that dies has to say so on the page.
                    # What went wrong while signing in can carry what was
                    # typed, so only its kind is shown for those actions.
                    detail = (type(exc).__name__ if path.startswith('/conn')
                              else f'{type(exc).__name__}: {exc}')
                    self._state.note = ui.Note(
                        'bad', '<b>That did not work.</b> The app hit a problem it did not expect '
                               f'({ui.e(detail)}). Nothing was saved. If it keeps '
                               'happening, save a support report from Help and settings.')
            job = self._state.job
        # A job that finishes at once should not flash a working screen: give
        # it a moment, with the lock released so it can swap its result in.
        if job is not None:
            job.done.wait(0.3)
        with self._state.action_lock:
            return self._page(focus)

    def dropped(self, event: Any) -> None:
        """A file was dropped on the window. Open it like a chosen file, then
        have the page draw itself again."""
        from . import ui
        try:
            files = (event or {}).get('dataTransfer', {}).get('files', [])
            path = next((f.get('pywebviewFullPath') for f in files if f.get('pywebviewFullPath')), None)
        except AttributeError:
            path = None
        if not path:
            return
        state = self._state
        with state.action_lock:
            if state.job is not None or state.ask or state.screen not in ('open', 'connect'):
                return
            try:
                ui.dispatch(state, '/open', {'zip': str(path)})
            except Exception:
                return
        window = getattr(self, '_window', None)
        if window is not None:
            try:
                window.evaluate_js('window.migrator && window.migrator.refresh()')
            except Exception:
                pass


def _picker_for(holder: Dict[str, Any]) -> Any:
    """A callable that asks the machine for an export zip, or returns None.

    The window is taken out of ``holder`` when the button is pressed, not when
    this is built. The page has to be rendered before there is a window to
    create.

    ``holder`` is the seam that makes this testable without a display: a test
    puts an object with a ``create_file_dialog`` method in it.

    The file window starts in Downloads, which is where a browser puts an
    export.
    """
    import webview
    from pathlib import Path

    def pick() -> Any:
        window = holder.get("window")
        if window is None:
            return None
        downloads = Path.home() / 'Downloads'
        return window.create_file_dialog(
            webview.OPEN_DIALOG,
            directory=str(downloads) if downloads.is_dir() else '',
            allow_multiple=False,
            file_types=("Content export (*.zip)", "All files (*.*)"),
        )

    return pick


def _bind_drop(window: Any, bridge: "Bridge") -> None:
    """Let a file be dropped onto the window, where the platform passes its
    path on. Where it does not, the button beside the drop area still works."""
    try:
        from webview.dom import DOMEventHandler
        document = window.dom.document
        document.events.dragover += DOMEventHandler(lambda event: None, True, True)
        document.events.drop += DOMEventHandler(bridge.dropped, True, True)
    except Exception:
        pass


def run(state: Any, title: str = "VCF Content Migrator", width: int = 1280,
        height: int = 860, initial_zip: Optional[str] = None) -> None:  # pragma: no cover - needs a display
    """Open the window and block until the user closes it."""
    import shutil
    import sys
    import webview

    bridge = Bridge(state)
    holder: Dict[str, Any] = {}
    # Set before the first render, so the very first page is the real one.
    state.file_picker = _picker_for(holder)
    state.dialog = bridge.pick_path
    state.open_url = lambda url: bool(webbrowser.open(url, new=2))
    state.background = True
    if sys.platform in ('darwin', 'win32') or shutil.which('xdg-open'):
        state.show_in_folder = show_in_folder
    holder["window"] = webview.create_window(title, html=page_html(state, initial_zip),
                                             js_api=bridge, width=width,
                                             height=height, min_size=(min(width, 1024), min(height, 700)))
    bridge._window = holder["window"]
    holder["window"].events.loaded += lambda: _bind_drop(holder["window"], bridge)
    webview.start(gui="qt" if sys.platform.startswith("linux") else None, http_server=False)


def show_in_folder(path: str) -> bool:
    """Reveal only a file this app wrote, without invoking a shell."""
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
