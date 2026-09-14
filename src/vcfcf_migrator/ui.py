"""``vcfcf-migrator ui``: one local page, standard library only.

Serves on 127.0.0.1 on a free port (or ``--port``), opens the browser, and
shows: the tool and library versions, the corpus directory setting (editable,
persisted through ``settings.save_settings``), an inspect form for an export
zip, and buttons for the commands that are not implemented yet, which answer
with the same message the CLI prints. Ctrl-C stops the process and with it
the page. Nothing here listens on any other interface.
"""
from __future__ import annotations

import html
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from vcfcf_migrator import settings as _settings
from vcfcf_migrator.cli import NOT_IMPLEMENTED, SPEC_POINTER, version_lines
from vcfcf_migrator.export_reader import NotAnExport, UnsupportedExport, read_export, render_text

_STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 60rem; padding: 0 1rem; color: #222; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
pre { background: #f4f4f4; padding: 1rem; overflow-x: auto; }
input[type=text] { width: 100%; max-width: 40rem; padding: 0.3rem; }
form { margin: 0.5rem 0; } button { padding: 0.3rem 0.8rem; margin-right: 0.5rem; }
.msg { background: #fff7d6; padding: 0.6rem; border-left: 4px solid #d9a400; }
.err { background: #fde8e8; padding: 0.6rem; border-left: 4px solid #c00; }
small { color: #666; }
"""


class PageState:
    """What the page shows; one instance per server."""

    def __init__(self, zip_path: Optional[str], corpus_cli: Optional[str]):
        self.zip_path = zip_path or ""
        self.corpus_cli = corpus_cli
        self.message = ""
        self.error = ""
        self.listing = ""
        self.as_json = False
        if self.zip_path:
            self.run_inspect(self.zip_path)

    def run_inspect(self, zip_path: str, as_json: bool = False) -> None:
        import json

        self.zip_path = zip_path
        self.as_json = as_json
        self.listing, self.error = "", ""
        if not zip_path:
            self.error = "no export zip given"
            return
        try:
            export = read_export(zip_path)
        except (NotAnExport, UnsupportedExport) as e:
            self.error = str(e)
            return
        self.listing = json.dumps(export.as_dict(), indent=2) if as_json else render_text(export)

    def render(self) -> str:
        corpus, source = _settings.corpus_dir(self.corpus_cli)
        e = html.escape
        parts = [
            "<!doctype html><html><head><meta charset='utf-8'>",
            "<title>vcfcf-migrator</title><style>", _STYLE, "</style></head><body>",
            "<h1>vcfcf-migrator</h1>",
            "<pre id='versions'>", e("\n".join(version_lines())), "</pre>",
        ]
        if self.message:
            parts.append(f"<p class='msg'>{e(self.message)}</p>")
        if self.error:
            parts.append(f"<p class='err'>{e(self.error)}</p>")

        parts += [
            "<h2>Settings</h2>",
            "<form method='post' action='/settings'>",
            "<label>Corpus directory (real export zips; never inside the repo)<br>",
            f"<input type='text' name='corpus_dir' id='corpus_dir' value='{e(str(corpus))}'></label><br>",
            f"<small>current value from: {e(source)}. Saved values go to {e(str(_settings.settings_path()))}. ",
            f"The {e(_settings.ENV_CORPUS)} environment variable and the --corpus flag override the saved value.</small><br>",
            "<button type='submit'>Save corpus directory</button>",
            "</form>",
            "<h2>Inspect an export</h2>",
            "<form method='post' action='/inspect'>",
            "<label>Export zip path<br>",
            f"<input type='text' name='zip' id='zip' value='{e(self.zip_path)}'></label><br>",
            f"<label><input type='checkbox' name='json' value='1'{' checked' if self.as_json else ''}> Show as JSON (inspect --json)</label><br>",
            "<button type='submit'>Inspect</button>",
            "</form>",
        ]
        if self.listing:
            parts += ["<pre id='listing'>", e(self.listing), "</pre>"]

        parts += ["<h2>Other commands</h2><p><small>tree, build and corpus-check land in later milestones; the buttons answer as the CLI does.</small></p>"]
        for cmd in ("tree", "build", "corpus-check"):
            parts += [
                f"<form method='post' action='/run' style='display:inline'>",
                f"<input type='hidden' name='cmd' value='{cmd}'>",
                f"<button type='submit'>{cmd}</button></form>",
            ]
        parts += ["<p><small>Stop the page with Ctrl-C in the terminal that started it.</small></p>",
                  "</body></html>"]
        return "".join(parts)


def _handler_for(state: PageState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send_page(self, code: int = 200) -> None:
            body = state.render().encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _redirect_home(self) -> None:
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _form(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

        def do_GET(self):  # noqa: N802
            if urllib.parse.urlsplit(self.path).path != "/":
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send_page()

        def do_POST(self):  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            form = self._form()
            state.message, state.error = "", ""
            if path == "/settings":
                value = form.get("corpus_dir", "").strip()
                if value:
                    saved = _settings.save_settings({"corpus_dir": value})
                    state.message = f"corpus directory saved to {saved}"
                else:
                    state.error = "corpus directory cannot be empty"
            elif path == "/inspect":
                state.run_inspect(form.get("zip", "").strip(), as_json=form.get("json") == "1")
            elif path == "/run":
                cmd = form.get("cmd", "")
                if cmd in NOT_IMPLEMENTED:
                    state.message = f"vcfcf-migrator {cmd}: not implemented in M3, see spec ({SPEC_POINTER})"
                else:
                    state.error = f"unknown command {cmd}"
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._redirect_home()

    return Handler


def make_server(zip_path: Optional[str] = None, port: int = 0, corpus_cli: Optional[str] = None) -> ThreadingHTTPServer:
    """A bound server on 127.0.0.1; the caller runs it. Tests use this."""
    state = PageState(zip_path, corpus_cli)
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler_for(state))
    server.daemon_threads = True
    return server


def serve(zip_path: Optional[str] = None, port: int = 0, open_browser: bool = True,
          corpus_cli: Optional[str] = None) -> int:
    server = make_server(zip_path, port, corpus_cli)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"vcfcf-migrator ui: {url} (Ctrl-C to stop)")
    if open_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nvcfcf-migrator ui: stopped", file=sys.stderr)
    finally:
        server.server_close()
    return 0
