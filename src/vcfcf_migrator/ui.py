"""``vcfcf-migrator ui``: the selection page, standard library only.

Serves on 127.0.0.1 on a free port (or ``--port``), opens the browser, and
shows the thing the milestone is for: the export's dependency tree with a
checkbox per object, dependencies pulled in automatically and labelled with
what needs them, a refusal with the reason when something still needed is
unchecked, the preview of the selected object in place, the counts of what a
build would carry, and a Build button that writes the bundle and says where it
went. Every command the CLI has, and every setting it takes, has a control
here (house rule: every setting has a GUI option).

**All state lives in ``PageState``, and every action is a method on it.** The
handler's job is to check the origin, read the form and call one method; the
page is rendered from the state afterwards. That is what lets the tests drive
the page's behaviour in process, without a browser, and it keeps the two ways
in (the CLI and the page) over one set of rules rather than two.

Nothing here listens on any other interface, and a POST is accepted only when
its Origin (or Host, when the browser sends no Origin) is this server's own
http://127.0.0.1:<port> or http://localhost:<port> (admins type localhost):
any other web page the admin has open could otherwise post to the loopback
port and rewrite settings, read a local path, or write a file. Such a request
gets 403.

**That check is a CSRF control, not an access control.** It stops another web
page in the admin's browser from driving this port. It cannot stop a process
on the same machine, which can set any header it likes, and the page reads
and writes whatever paths the admin gives it (`/open`, `/build`, `/settings`,
`/corpus-check`) with the admin's own rights. On the single-user workstation
this tool ships for, that is the model: the page is the admin, for as long as
the process runs.
"""
from __future__ import annotations

import io
import json
import os
import shlex
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional

import vcfcf_core

from vcfcf_migrator import __version__
from vcfcf_migrator import bundle as _bundle
from vcfcf_migrator import graph as _graph
from vcfcf_migrator import runlog as _runlog
from vcfcf_migrator import selection as _selection
from vcfcf_migrator import settings as _settings
from vcfcf_migrator import uipage
from vcfcf_migrator.export_reader import (
    NotAnExport,
    read_export,
    read_members,
    render_text,
)
from vcfcf_migrator.rawdoc import RawDocError
from vcfcf_migrator.wording import plural

# The commands whose exact command line the page can hand back, so an admin
# can script what they just did by hand.
COMMANDS = ("tree", "preview", "build", "corpus-check")


def _shell_quote(value: str, windows: Optional[bool] = None) -> str:
    """Quote a value so the command line reads as one argument, on the shell
    it is going to be pasted into.

    Double quotes were chosen here to be Windows-safe, since the single quotes
    ``shlex.quote`` emits are literal characters to cmd.exe and the command
    then fails in exactly the case the quoting exists for. That reasoning
    holds, and it is only half the problem: inside double quotes a POSIX shell
    still substitutes ``$name`` and ``` `command` ```, so a path carrying
    either produced a line that writes somewhere else or runs substituted
    text. So the quoting follows the platform the page is running on, which is
    the machine whose shell the admin will paste into: ``shlex.quote`` there,
    and the double-quoted form on Windows.

    *windows* overrides the platform, for the tests that check both forms.
    """
    text = str(value)
    on_windows = os.name == "nt" if windows is None else windows
    if not on_windows:
        return shlex.quote(text)
    # A backslash is an ordinary path character on Windows, so it does not
    # force quoting there; the rest are cmd.exe metacharacters or whitespace.
    if text and not any(ch in text for ch in ' \t"\'&|<>^()$`'):
        return text
    # cmd.exe cannot express an embedded double quote inside a quoted
    # argument, so it is doubled, which is what PowerShell and the C runtime
    # parser both take.
    return '"' + text.replace('"', '""') + '"'


class PageState:
    """What the page shows, and every action it can take. One per server."""

    def __init__(self, zip_path: Optional[str] = None, corpus_cli: Optional[str] = None,
                 log_cli: Optional[str] = None,
                 log_level_cli: Optional[str] = None, log_format_cli: Optional[str] = None):
        # The page always keeps its events in memory, whether or not a file is
        # asked for, because "save diagnostics" has to be answerable after the
        # run that went wrong rather than only before it.
        self.log = _runlog.NULL
        self.zip_path = zip_path or ""
        self.corpus_cli = corpus_cli
        # The command line's log settings, threaded in the way the corpus
        # directory already was. Without this the page
        # resolved from the environment and the settings file only, so
        # ``ui --log run.log`` was accepted, advertised in --help and in the
        # README, and wrote a four line file with nothing the page did in it.
        self.log_cli = log_cli
        self.log_level_cli = log_level_cli
        self.log_format_cli = log_format_cli
        self.origins = ()  # set once the server is bound: 127.0.0.1 and localhost on this port
        self.message = ""
        self.error = ""
        self.listing = ""
        self.command_output = ""
        self.as_json = False
        self.filter_text = ""
        self.preview_key = ""
        self.build_out = ""
        self.build_report = ""
        self.members = None
        self.graph = None
        self.picked: List[str] = []
        self.selection: Optional[_selection.Selection] = None
        self.diagnostics_out = ""
        self.open_log()
        if self.zip_path:
            self.open_export(self.zip_path)

    # -- the log -----------------------------------------------------------

    def open_log(self) -> None:
        """Open (or re-open) this page's log from the current settings.

        **The redactor carries over.** It holds everything harvested from the
        export that is open: the owners, their pseudonyms and every person in
        the export's user documents. A fresh one knows none of that, so a log
        opened after the admin changed a setting logged the names the previous
        one excluded, and the diagnostics file the README tells a customer to
        mail carried them too.
        """
        destination, self.log_from = _runlog.resolve_destination(self.log_cli)
        level, self.log_level_from = _runlog.resolve_level(self.log_level_cli)
        fmt, _fmt_from = _runlog.resolve_format(self.log_format_cli)
        old = self.log
        try:
            self.log = _runlog.open_log(destination, level=level, fmt=fmt,
                                        keep_events=True,
                                        redactor=old.redactor if old else None)
        except (_runlog.BadLogSetting, OSError) as e:
            self.error = f"cannot write the log to {destination}: {e}"
            return
        if old is not None and getattr(old, "events", None):
            # A level or destination change mid-session keeps the story so far.
            self.log.events = list(old.events) + self.log.events
        if old is not _runlog.NULL:
            old.close()
        _runlog.set_current(self.log)
        self.log.header(["ui"], tool_version=__version__,
                        core_version=vcfcf_core.__version__)

    def log_settings(self):
        """Destination, level and where each came from, for the page."""
        destination, destination_from = _runlog.resolve_destination(self.log_cli)
        level, level_from = _runlog.resolve_level(self.log_level_cli)
        return destination, destination_from, level, level_from

    def _last_event(self, code: str) -> Optional[dict]:
        for event in reversed(self.log.events or []):
            if event.get("event") == code:
                return event
        return None

    def save_diagnostics(self, out_path: str) -> None:
        """One file holding the run header, the input fingerprint, every log
        event and the bundle's manifest, ready to attach to a mail."""
        out = (out_path or "").strip() or self.default_diagnostics_out()
        header = self._last_event("run.start")
        source = self._last_event("input.fingerprint")
        bundle = self._last_event("output.fingerprint")
        document = _runlog.diagnostics_document(list(self.log.events or []),
                                                header=header, source=source,
                                                bundle=bundle)
        try:
            target = Path(out)
            if target.parent and str(target.parent):
                target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(document, encoding="utf-8")
        except OSError as e:
            _runlog.error("output.unwritable", path=out, reason=str(e))
            self.error = f"cannot write {out}: {e}"
            return
        self.diagnostics_out = str(target)
        self.message = (f"diagnostics written to {target}: "
                        f"{plural(len(self.log.events or []), 'event')}, "
                        + _runlog.DIAGNOSTICS_CONTENTS)

    def default_diagnostics_out(self) -> str:
        if not self.zip_path:
            return "vcfcf-migrator-diagnostics.jsonl"
        source = Path(self.zip_path)
        return str(source.with_name(source.stem + "-diagnostics.jsonl"))

    # -- loading -----------------------------------------------------------

    def open_export(self, zip_path: str) -> None:
        """Read the export and build the graph, then swap.

        Nothing is cleared until the replacement parses. A mistyped path used
        to empty the page and take the admin's prepared selection with it,
        with no way back: the old export was gone from the state before the
        new one had been shown to be readable. So the reading happens into
        locals, and the state changes only once there is something to change
        it to.

        A *successful* open still resets everything: a selection carried
        across two exports would name objects this one does not have.
        """
        if not zip_path:
            self.error = "no export zip given; the export already open is unchanged"
            return
        try:
            members = read_members(zip_path)
            graph = _graph.build_graph(members.data)
        except (NotAnExport, RawDocError) as e:
            self.error = str(e)
            if self.graph is not None:
                still = f"; {self.zip_path} is still open"
                if self.picked:
                    still += f" with {plural(len(self.picked), 'object')} picked"
                self.error += still
            return

        self.zip_path = zip_path
        self.members, self.graph = members, graph
        self.listing = self.command_output = self.build_report = ""
        self.preview_key = self.filter_text = self.build_out = ""
        self.picked = []
        self._reclose()
        counts = graph.counts()
        self.message = (f"{zip_path}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                        if counts else f"{zip_path}: no content objects")

    def default_out(self) -> str:
        if not self.zip_path:
            return "bundle.zip"
        source = Path(self.zip_path)
        return str(source.with_name(source.stem + "-bundle.zip"))

    # -- the selection -----------------------------------------------------

    def _reclose(self) -> None:
        self.selection = _selection.close(self.graph, self.picked) if self.graph else None

    def selected_keys(self) -> set:
        """What the admin picked by hand."""
        return set(self.picked)

    def selection_keys(self) -> set:
        """What a build would carry: the picks plus everything they need."""
        return set(self.selection.keys) if self.selection else set()

    def required_by(self, key: str) -> List[str]:
        """The selected objects that depend on *key*, which is what makes it
        impossible to drop while they are carried."""
        if not self.graph or not self.selection:
            return []
        return [k for k in self.selection.keys
                if key in self.graph.edges.get(k, []) and k != key]

    def toggle(self, key: str, on: bool) -> None:
        """Check or uncheck one object.

        Checking pulls in what it needs and says what it pulled in. Unchecking
        something another selected object still needs is refused with the
        reason: the alternative is a bundle whose documents point at objects it
        does not carry, which is the one failure subsetting can introduce by
        itself.
        """
        if self.graph is None:
            self.error = "open an export first"
            return
        node = self.graph.nodes.get(key)
        if node is None:
            self.error = f"this export carries no object {key}"
            return
        if on:
            if key in self.picked:
                self.message = f"{node.label()} was already selected"
                return
            before = self.selection_keys()
            self.picked.append(key)
            self._reclose()
            pulled = [self.graph.nodes[k].label() for k in self.selection.keys
                      if k not in before and k != key and k in self.graph.nodes]
            self.message = f"selected {node.label()}"
            if pulled:
                self.message += (f"; pulled in {len(pulled)} it depends on: "
                                 + ", ".join(pulled[:6])
                                 + (f" and {len(pulled) - 6} more" if len(pulled) > 6 else ""))
            gaps = self.graph.missing_for(key)
            if gaps:
                self.message += (f". {plural(len(gaps), 'object')} it references "
                                 + ("is" if len(gaps) == 1 else "are")
                                 + " not in this export; the target instance needs them "
                                   "already")
            return

        needed_by = self.required_by(key)
        if key not in self.picked:
            if needed_by:
                self.error = self._refusal(node, needed_by)
            else:
                self.message = f"{node.label()} was not selected"
            return
        trial = [k for k in self.picked if k != key]
        trial_selection = _selection.close(self.graph, trial)
        if key in trial_selection.keys:
            self.error = self._refusal(node, needed_by)
            return
        before = self.selection_keys()
        self.picked = trial
        self.selection = trial_selection
        dropped = [k for k in before if k not in self.selection_keys() and k != key]
        self.message = f"removed {node.label()}"
        if dropped:
            self.message += (f"; {plural(len(dropped), 'object')} it had pulled in "
                             + ("is" if len(dropped) == 1 else "are")
                             + " no longer needed and "
                             + ("was" if len(dropped) == 1 else "were") + " dropped too")
        if self.preview_key == key:
            self.preview_key = ""

    def _refusal(self, node, needed_by: List[str]) -> str:
        names = [self.graph.nodes[k].label() for k in needed_by if k in self.graph.nodes]
        return (f"refused: {node.label()} cannot be dropped while it is required by "
                + ", ".join(names[:5])
                + (f" and {len(names) - 5} more" if len(names) > 5 else "")
                + ". Remove those first, or leave it in the bundle.")

    def select_all(self) -> None:
        if self.graph is None:
            self.error = "open an export first"
            return
        self.picked = [n.key for n in self.graph.ordered()]
        self._reclose()
        self.message = f"selected every object in the export ({len(self.picked)})"

    def clear(self) -> None:
        if self.graph is None:
            self.error = "open an export first"
            return
        self.picked = []
        self._reclose()
        self.message = "cleared the selection"

    def apply_lines(self, text: str) -> None:
        """Take a selection the way ``build --select`` takes a file: one object
        per line, ``#`` comments allowed. The page's equivalent of the flag."""
        if self.graph is None:
            self.error = "open an export first"
            return
        lines = [line.split("#", 1)[0].strip() for line in (text or "").splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            self.picked = []
            self._reclose()
            self.message = "cleared the selection (no lines given)"
            return
        try:
            keys = _selection.resolve(self.graph, lines)
        except _selection.BadSelection as e:
            self.error = str(e)
            return
        self.picked = keys
        self._reclose()
        self.message = (f"applied {plural(len(lines), 'line')}: {len(self.picked)} picked, "
                        f"{len(self.selection.keys)} after closure")

    def selection_lines(self) -> str:
        """The current picks as the lines a selection file would hold."""
        return "\n".join(self.picked)

    def set_preview(self, key: str) -> None:
        if not key:
            self.preview_key = ""
            return
        if self.graph is None or key not in self.graph.nodes:
            self.error = f"this export carries no object {key}"
            return
        self.preview_key = key

    def set_filter(self, text: str) -> None:
        self.filter_text = (text or "").strip()

    # -- the commands ------------------------------------------------------

    def build(self, out_path: str) -> None:
        """Write the bundle for the closed selection, exactly as the CLI does.

        Same closure, same writer, same arguments: the page is another way in
        to one build path, not a second one, so a bundle built here is the
        bundle ``build --select`` writes for the same picks.
        """
        if self.graph is None or self.members is None:
            self.error = "open an export first"
            return
        if not (self.selection and self.selection.keys):
            self.error = "the selection is empty, no bundle written"
            return
        out = (out_path or "").strip() or self.default_out()
        self.build_out = out
        try:
            result = _bundle.build_bundle(self.members.data, self.members.order, self.graph,
                                          self.selection, out, marker=self.members.marker,
                                          directories=self.members.directories,
                                          directory_order=self.members.directory_order)
        except OSError as e:
            self.error = f"cannot write {out}: {e}"
            return
        self.build_report = (_selection.render(self.graph, self.selection)
                             + _bundle.render(result))
        self.message = f"bundle written to {result.path}"

    def run_inspect(self, zip_path: str, as_json: bool = False) -> None:
        self.as_json = as_json
        self.listing = ""
        target = (zip_path or self.zip_path or "").strip()
        if not target:
            self.error = "no export zip given"
            return
        try:
            export = read_export(target)
        except NotAnExport as e:
            self.error = str(e)
            return
        self.listing = (json.dumps(export.as_dict(), indent=2) if as_json
                        else render_text(export))

    def run_tree(self, as_json: bool = False) -> None:
        if self.graph is None:
            self.error = "open an export first"
            return
        self.command_output = (json.dumps(_graph.as_dict(self.graph), indent=2) if as_json
                               else _graph.render_tree(self.graph))

    def run_corpus_check(self, directory: str) -> None:
        from vcfcf_migrator.corpus_check import run

        target, source = _settings.corpus_dir(directory or self.corpus_cli)
        buffer = io.StringIO()
        code = run(target, source, buffer)
        self.command_output = buffer.getvalue()
        self.message = f"corpus-check over {target} finished with exit code {code}"

    def save_setting(self, form: dict) -> None:
        if "corpus_dir" in form:
            value = form.get("corpus_dir", "").strip()
            if not value:
                self.error = "corpus directory cannot be empty"
                return
            saved = _settings.save_settings({"corpus_dir": value})
            self.message = f"corpus directory saved to {saved}"
            return
        if "log_file" in form:
            value = form.get("log_file", "").strip()
            _settings.save_settings({"log_file": value})
            self.open_log()
            self.message = (f"the run log goes to {value}" if value
                            else "the run log is off; the page still keeps this session's "
                                 "events for the diagnostics file")
            return
        if "log_level" in form:
            value = form.get("log_level", "").strip().lower()
            if value not in _runlog.LEVELS:
                self.error = (f"log level {value!r} is not one of "
                              + ", ".join(_runlog.LEVEL_NAMES))
                return
            _settings.save_settings({"log_level": value})
            self.open_log()
            self.message = f"log level {value}"
            return
        self.error = "nothing to save"

    def command_line(self, cmd: str) -> str:
        """The exact command for *cmd*, with whatever the page is set to.

        The page does these itself now; the line is for the admin who wants to
        script what they just did, and it has to be a command that runs, so a
        path with a space in it (ordinary on every OS this ships for) is
        quoted rather than left as two arguments.
        """
        corpus, _src = _settings.corpus_dir(self.corpus_cli)
        head = "vcfcf-migrator"
        if cmd == "corpus-check":
            return f"{head} corpus-check {_shell_quote(str(corpus))}"
        target = _shell_quote(self.zip_path) if self.zip_path else "<export.zip>"
        if cmd == "preview":
            what = _shell_quote(self.preview_key) if self.preview_key else "<uuid>"
            return f"{head} preview {target} {what}"
        if cmd == "build":
            out = _shell_quote(self.build_out or self.default_out())
            return f"{head} build {target} --select <picks.txt> --out {out}"
        return f"{head} {cmd} {target}"

    def render(self) -> str:
        """The page, and the end of the story so far.

        A page can be closed at any moment, so a log it wrote has to end. The
        page renders after every action, so ``run.end`` goes here: without it a
        truncated log and a finished one look the same, which is the one thing
        a log must never be ambiguous about.
        """
        page = uipage.render(self)
        self.log.finish(1 if self.error else 0, what="page")
        return page


# ---------------------------------------------------------------------------
# What a POST can ask for: one table, read by the handler and by the test that
# proves every one of these refuses a cross-origin post. A fourteenth entry
# added here is guarded by that test the moment it exists, which a
# hand-maintained list in the test could not promise.
# ---------------------------------------------------------------------------

def _act_settings(state: "PageState", form: dict) -> str:
    state.save_setting(form)
    return ""


def _act_open(state: "PageState", form: dict) -> str:
    state.open_export(form.get("zip", "").strip())
    return ""


def _act_inspect(state: "PageState", form: dict) -> str:
    state.run_inspect(form.get("zip", "").strip(), as_json=form.get("json") == "1")
    return ""


def _act_tree(state: "PageState", form: dict) -> str:
    state.run_tree(as_json=form.get("json") == "1")
    return ""


def _act_select(state: "PageState", form: dict) -> str:
    key = form.get("key", "")
    state.toggle(key, on=form.get("on", "1") == "1")
    # The page comes back anchored at the row that was just toggled, so a
    # tree scrolled halfway down does not jump to the top on every click.
    return uipage.anchor(key)


def _act_select_all(state: "PageState", _form: dict) -> str:
    state.select_all()
    return ""


def _act_clear(state: "PageState", _form: dict) -> str:
    state.clear()
    return ""


def _act_apply_lines(state: "PageState", form: dict) -> str:
    state.apply_lines(form.get("lines", ""))
    return ""


def _act_preview(state: "PageState", form: dict) -> str:
    state.set_preview(form.get("key", ""))
    return ""


def _act_filter(state: "PageState", form: dict) -> str:
    state.set_filter(form.get("filter", ""))
    return ""


def _act_build(state: "PageState", form: dict) -> str:
    state.build(form.get("out", ""))
    return ""


def _act_corpus_check(state: "PageState", form: dict) -> str:
    state.run_corpus_check(form.get("dir", "").strip())
    return ""


def _act_diagnostics(state: "PageState", form: dict) -> str:
    state.save_diagnostics(form.get("out", ""))
    return ""


def _act_run(state: "PageState", form: dict) -> str:
    cmd = form.get("cmd", "")
    if cmd in COMMANDS:
        state.message = state.command_line(cmd)
    else:
        state.error = f"unknown command {cmd}"
    return ""


ACTIONS = {
    "/settings": _act_settings,
    "/open": _act_open,
    "/inspect": _act_inspect,
    "/tree": _act_tree,
    "/select": _act_select,
    "/select-all": _act_select_all,
    "/clear": _act_clear,
    "/apply-lines": _act_apply_lines,
    "/preview": _act_preview,
    "/filter": _act_filter,
    "/build": _act_build,
    "/corpus-check": _act_corpus_check,
    "/diagnostics": _act_diagnostics,
    "/run": _act_run,
}
POST_PATHS = tuple(sorted(ACTIONS))


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

        def _redirect_home(self, anchor: str = "") -> None:
            self.send_response(303)
            self.send_header("Location", "/" + (f"#{anchor}" if anchor else ""))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _form(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

        def do_GET(self):  # noqa: N802
            if urllib.parse.urlsplit(self.path).path != "/":
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send_page()

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is not None:
                return origin.rstrip("/") in state.origins
            host = self.headers.get("Host") or ""
            return f"http://{host}" in state.origins

        def do_POST(self):  # noqa: N802
            if not self._same_origin():
                body = ("403: cross-origin POST refused; only this page may post here "
                        f"(accepted origins: {', '.join(state.origins)})\n").encode("utf-8")
                self.send_response(403)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            path = urllib.parse.urlsplit(self.path).path
            action = ACTIONS.get(path)
            if action is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            form = self._form()
            state.message, state.error = "", ""
            # The page is a second way in to the same commands, so what it was
            # asked to do belongs in the same log the CLI writes. Form values
            # go through the same exclusion rules as everything else.
            _runlog.set_current(state.log)
            _runlog.info("page.action", action=path,
                         fields={k: v for k, v in form.items() if k != "lines"})
            with state.log.phase("page", action=path):
                anchor = action(state, form) or ""
            self._redirect_home(anchor)

    return Handler


def make_server(zip_path: Optional[str] = None, port: int = 0, corpus_cli: Optional[str] = None,
                log_cli: Optional[str] = None,
                log_level_cli: Optional[str] = None,
                log_format_cli: Optional[str] = None) -> ThreadingHTTPServer:
    """A bound server on 127.0.0.1; the caller runs it. Tests use this."""
    state = PageState(zip_path, corpus_cli,
                      log_cli, log_level_cli, log_format_cli)
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler_for(state))
    server.daemon_threads = True
    port = server.server_address[1]
    state.origins = (f"http://127.0.0.1:{port}", f"http://localhost:{port}")
    server.page_state = state
    return server


def serve(zip_path: Optional[str] = None, port: int = 0, open_browser: bool = True,
          corpus_cli: Optional[str] = None,
          log_cli: Optional[str] = None, log_level_cli: Optional[str] = None,
          log_format_cli: Optional[str] = None) -> int:
    server = make_server(zip_path, port, corpus_cli,
                         log_cli, log_level_cli, log_format_cli)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"vcfcf-migrator ui: {url} (Ctrl-C to stop)", flush=True)
    if open_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nvcfcf-migrator ui: stopped", file=sys.stderr)
    finally:
        server.server_close()
    return 0
