"""Native-window state and actions for content selection and bundle building."""
from __future__ import annotations

import io
from functools import wraps
import json
import os
import shlex
import sys
import threading
from pathlib import Path
from typing import List, Optional

import vcfcf_core

from vcfcf_migrator import __version__
from vcfcf_migrator import bundle as _bundle
from vcfcf_migrator import graph as _graph
from vcfcf_migrator import runlog as _runlog
from vcfcf_migrator import selection as _selection
from vcfcf_migrator import settings as _settings
from vcfcf_migrator import source as _source
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


def serialized(method):
    """One state mutation or snapshot at a time, including direct callers."""
    @wraps(method)
    def call(state, *args, **kwargs):
        with state.action_lock:
            return method(state, *args, **kwargs)
    return call


class PageState:
    """What the page shows, and every action it can take. One per window."""

    def __init__(self, zip_path: Optional[str] = None, corpus_cli: Optional[str] = None,
                 log_cli: Optional[str] = None,
                 log_level_cli: Optional[str] = None, log_format_cli: Optional[str] = None):
        # The page always keeps its events in memory, whether or not a file is
        # asked for, because "save diagnostics" has to be answerable after the
        # run that went wrong rather than only before it.
        self.action_lock = threading.RLock()
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
        # The native window supplies its file chooser before rendering.
        self.file_picker = None
        self.source_snapshot = None
        self.source_progress = lambda stage: None
        # Retain the selected panel across page redraws.
        self.tab = "preview"
        # Which disclosures the user has opened or closed, by id. A <details>
        # element toggles in the browser and tells the server nothing, so with
        # the state living only in the DOM every action redrew the tree from
        # scratch and shut the group you were working in. Ticking one checkbox
        # collapsed the list you were ticking, which made selecting several
        # objects, the thing this tool is for, close to unusable. Missing key
        # means "whatever the default for that disclosure is".
        self.disclosure = {}
        self.message = ""
        self.error = ""
        self.listing = ""
        self.command_output = ""
        self.as_json = False
        self.filter_text = ""
        self.inventory_kind = "all"
        self.preview_key = ""
        self.preview_expanded = False
        self.inspector_tab = "preview"
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
            replacement = _runlog.open_log(destination, level=level, fmt=fmt,
                                        keep_events=True,
                                        redactor=old.redactor if old is not _runlog.NULL else None)
        except (_runlog.BadLogSetting, OSError) as e:
            self.error = f"cannot write the log to {destination}: {e}"
            return
        if old is not _runlog.NULL:
            old.reconfigure(replacement)
        else:
            self.log = replacement
            self.log.header(["ui"], tool_version=__version__,
                            core_version=vcfcf_core.__version__)
        _runlog.set_current(self.log)

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
        events = self.log.snapshot()
        document = _runlog.diagnostics_document(events,
                                                header=header, source=source,
                                                bundle=bundle, retention=self.log.retention())
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
                        f"{plural(len(events), 'event')}, "
                        + _runlog.DIAGNOSTICS_CONTENTS)

    def default_diagnostics_out(self) -> str:
        if self.source_snapshot is not None:
            return str(self.download_directory() / "vcfcf-migrator-diagnostics.jsonl")
        if not self.zip_path:
            return "vcfcf-migrator-diagnostics.jsonl"
        source = Path(self.zip_path)
        return str(source.with_name("vcfcf-migrator-diagnostics.jsonl"))

    # -- loading -----------------------------------------------------------

    @serialized
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

        if self.source_snapshot is None or Path(zip_path).resolve() != self.source_snapshot.path.resolve():
            self.close_source()
        self.zip_path = zip_path
        self.members, self.graph = members, graph
        self.listing = self.command_output = self.build_report = ""
        self.preview_key = self.filter_text = self.build_out = ""
        # Which groups are open belongs to the export being looked at: a
        # small one opens its groups by default, and carrying that into a
        # large one expands exactly what the default is there to prevent.
        self.disclosure = {}
        self.inventory_kind = "all"
        self.preview_expanded = False
        self.inspector_tab = "preview"
        self.tab = "preview"
        self.picked = []
        self._reclose()
        counts = graph.counts()
        self.message = (f"Opened {Path(zip_path).name}: {len(graph.nodes)} objects available"
                        if counts else f"{zip_path}: no content objects")

    def close_source(self) -> None:
        if self.source_snapshot is not None:
            self.source_snapshot.close()
            self.source_snapshot = None

    @serialized
    def connect_source(self, form: dict) -> None:
        snapshot = None
        try:
            if not form.get('username') or not form.get('password') or not form.get('export_password'):
                raise _source.SourceError('Enter your username, password, and an export encryption password.')
            client = _source.Client(form.get('address', ''), ca_file=form.get('ca_file', '').strip(),
                                    progress=self.source_progress)
            snapshot = client.acquire(form['username'], form['password'],
                                      form.get('auth_source', '').strip(), form['export_password'])
            self.open_export(str(snapshot.path))
            if self.zip_path != str(snapshot.path):
                raise _source.SourceError('The downloaded export could not be read. The current inventory is unchanged.')
            self.source_snapshot = snapshot
            snapshot = None
            self.message = ('Loaded Operations content. Keep the export encryption password for the target import. '
                            'The downloaded source is temporary and is removed when replaced or the app closes.')
        except _source.SourceError as exc:
            self.error = str(exc)
        except Exception:
            # Never expose connection exceptions, which may contain request data.
            self.error = 'The connection or content load failed. Check the source export job before retrying.'
        finally:
            form.clear()
            if snapshot is not None:
                snapshot.close()

    @staticmethod
    def download_directory() -> Path:
        downloads = Path.home() / 'Downloads'
        return downloads if downloads.is_dir() else Path.home()

    def default_out(self) -> str:
        if self.source_snapshot is not None:
            return str(self.download_directory() / 'bundle.zip')
        if not self.zip_path:
            return "bundle.zip"
        source = Path(self.zip_path)
        return str(source.with_name(source.stem + "-bundle.zip"))

    # -- the selection -----------------------------------------------------

    def _reclose(self) -> None:
        self.selection = _selection.close(self.graph, self.picked) if self.graph else None

    def _set_picked(self, keys) -> None:
        picked = list(keys)
        closed = _selection.close(self.graph, picked) if self.graph else None
        self.picked, self.selection = picked, closed

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

    @serialized
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
            self._set_picked(self.picked + [key])
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
                                 + " not in this export; review the missing references before building")
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

    @serialized
    def select_all(self) -> None:
        if self.graph is None:
            self.error = "open an export first"
            return
        self._set_picked(n.key for n in self.graph.ordered())
        self.message = f"selected every object in the export ({len(self.picked)})"

    @serialized
    def clear(self) -> None:
        if self.graph is None:
            self.error = "open an export first"
            return
        self._set_picked([])
        self.message = "cleared the selection"

    @serialized
    def apply_lines(self, text: str) -> None:
        """Take a selection the way ``build --select`` takes a file: one object
        per line, ``#`` comments allowed. The page's equivalent of the flag."""
        if self.graph is None:
            self.error = "open an export first"
            return
        lines = [line.split("#", 1)[0].strip() for line in (text or "").splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            self._set_picked([])
            self.message = "cleared the selection (no lines given)"
            return
        try:
            keys = _selection.resolve(self.graph, lines)
        except _selection.BadSelection as e:
            self.error = str(e)
            return
        self._set_picked(keys)
        self.message = (f"applied {plural(len(lines), 'line')}: {len(self.picked)} picked, "
                        f"{len(self.selection.keys)} after closure")

    def selection_lines(self) -> str:
        """The current picks as the lines a selection file would hold."""
        return "\n".join(self.picked)

    def set_preview(self, key: str) -> None:
        if not key:
            self.preview_expanded = False
            self.preview_key = ""
            return
        if self.graph is None or key not in self.graph.nodes:
            self.error = f"this export carries no object {key}"
            return
        self.preview_key = key

    def set_filter(self, text: str) -> None:
        self.filter_text = (text or "").strip()

    def visible_nodes(self):
        if self.graph is None:
            return []
        nodes = uipage._matches(self.graph, self.filter_text)
        if self.inventory_kind == "selected":
            included = self.selection_keys()
            return [n for n in nodes if n.key in included]
        if self.inventory_kind != "all":
            return [n for n in nodes if n.kind == self.inventory_kind]
        return nodes

    @serialized
    def select_shown(self):
        keys = list(dict.fromkeys(self.picked + [n.key for n in self.visible_nodes()]))
        self._set_picked(keys)
        self.message = "selected the shown content; existing picks are retained"

    @serialized
    def remove_pick(self, key):
        if key not in self.picked:
            self.error = "this object is not a direct pick"
            return
        self._set_picked(k for k in self.picked if k != key)
        self.message = "removed pick"
        if key in self.selection_keys():
            self.message += "; still included because another pick requires it"

    # -- the commands ------------------------------------------------------

    @serialized
    def build(self, out_path: str) -> None:
        """Write the bundle for the closed selection, exactly as the CLI does.

        The UI refuses missing references before calling the shared writer.
        For a complete selection the bytes match ``build --select``.
        """
        if self.graph is None or self.members is None:
            self.error = "open an export first"
            return
        if not (self.selection and self.selection.keys):
            self.error = "the selection is empty, no bundle written"
            return
        try:
            _selection.refuse_missing_dependencies(self.graph, self.selection)
        except _selection.MissingDependency as exc:
            self.message = ""
            self.error = f"refused: {exc}; no bundle written"
            self.build_report = "Build refused. No bundle written.\n" + "\n".join(exc.lines)
            self.tab = "review"
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
        self.tab = "review"
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

    @serialized
    def render(self) -> str:
        """Measure page generation without claiming the session has ended."""
        with self.log.phase('render'):
            return uipage.render(self)


# ---------------------------------------------------------------------------
# Registered actions are the only entry points exposed by the native bridge.

@serialized
def dispatch(state: "PageState", path: str, form: dict) -> str:
    """Run a registered native-window action and return its scroll anchor.

    Raises KeyError for an action that does not exist.
    """
    action = ACTIONS[path]
    state.message, state.error = "", ""
    # The page is a second way in to the same commands, so what it was asked to
    # do belongs in the same log the CLI writes. Form values go through the
    # same exclusion rules as everything else.
    _runlog.set_current(state.log)
    _runlog.info("page.action", action=path,
                 fields={} if path == "/connect" else {k: v for k, v in form.items() if k != "lines"})
    with state.log.phase("page", action=path):
        return action(state, form) or ""


def _act_settings(state: "PageState", form: dict) -> str:
    state.save_setting(form)
    return ""


def _act_open(state: "PageState", form: dict) -> str:
    state.open_export(form.get("zip", "").strip())
    return ""


# The right-hand panels, and which action lands you on which. An action whose
# output appears on a panel has to move you to that panel, or the button
# appears to do nothing at all.
TABS = ("preview", "commands", "settings", "review")


def _act_disclose(state: "PageState", form: dict) -> str:
    """Open or shut one disclosure, and stay where you were on the page."""
    which = (form.get("id") or "").strip()
    if not which:
        state.error = "no disclosure named"
        return ""
    state.disclosure[which] = form.get("on") == "1"
    # Back to the thing that was clicked. Opening a group two thirds of the
    # way down a long tree and being returned to the top would be its own
    # version of the bug this fixes.
    return uipage.anchor(which)


def _act_tab(state: "PageState", form: dict) -> str:
    wanted = (form.get("tab") or "").strip()
    if wanted in TABS:
        state.tab = wanted
        if wanted == "preview":
            state.preview_expanded = False
    else:
        state.error = f"there is no {wanted!r} panel"
    return ""


def _act_pick_export(state: "PageState", _form: dict) -> str:
    """Open the machine's own file dialog and load whatever comes back.

    This goes through the action table like every other button rather than
    being a special case in the window's bridge, so it is logged the same way
    and there is still exactly one place a page action runs.
    """
    if state.file_picker is None:
        state.error = ("this window cannot open a file chooser; "
                       "type the path into the box instead")
        return ""
    chosen = state.file_picker()
    # Normalising happens here, in the layer the tests drive, not inside the
    # dialog wrapper that needs a display to reach. A file dialog hands back a
    # sequence even when it was told to allow one file, and an unwrapped tuple
    # reaching open_export raises a TypeError that lands in front of the user
    # as "argument should be a str or an os.PathLike".
    if isinstance(chosen, (list, tuple)):
        chosen = chosen[0] if chosen else None
    if not chosen:
        # Cancelling a file dialog is not an error and must not read as one.
        state.message = "no file chosen"
        return ""
    if not isinstance(chosen, str):
        state.error = f"the file chooser returned something unusable: {type(chosen).__name__}"
        return ""
    state.open_export(chosen)
    return ""


def _act_inspect(state: "PageState", form: dict) -> str:
    state.tab = "commands"
    state.run_inspect(form.get("zip", "").strip(), as_json=form.get("json") == "1")
    return ""


def _act_tree(state: "PageState", form: dict) -> str:
    state.tab = "commands"
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
    state.tab = "preview"
    state.inspector_tab = "dependencies" if form.get("panel") == "dependencies" else "preview"
    state.set_preview(form.get("key", ""))
    return "inspector"


def _act_filter(state: "PageState", form: dict) -> str:
    # Keep the clear button distinct from the text field in form data.
    if form.get("clear-filter"):
        state.set_filter("")
        return ""
    state.set_filter(form.get("filter", ""))
    return ""


def _act_build(state: "PageState", form: dict) -> str:
    # Keep readiness and the written bundle report on the review screen.
    state.tab = "review"
    state.build(form.get("out", ""))
    return ""


def _act_corpus_check(state: "PageState", form: dict) -> str:
    state.tab = "commands"
    state.run_corpus_check(form.get("dir", "").strip())
    return ""


def _act_diagnostics(state: "PageState", form: dict) -> str:
    # No tab change, deliberately. What this produces is a file and a banner,
    # and the banner renders above the panels, so there is nothing on any one
    # panel to land on. Moving someone here anyway would be a rule invented to
    # match the shape of the other actions rather than to help anyone.
    state.save_diagnostics(form.get("out", ""))
    return ""


def _act_run(state: "PageState", form: dict) -> str:
    state.tab = "commands"
    cmd = form.get("cmd", "")
    if cmd in COMMANDS:
        state.message = state.command_line(cmd)
    else:
        state.error = f"unknown command {cmd}"
    return ""


def _act_inspector_tab(state: "PageState", form: dict) -> str:
    panel = form.get("panel", "preview")
    if panel in ("preview", "dependencies", "details"):
        state.inspector_tab = panel
    else:
        state.error = "unknown inspector panel"
    return "inspector"


def _act_expand_preview(state: "PageState", form: dict) -> str:
    state.preview_expanded = form.get("expanded") == "1"
    state.tab = "preview"
    return "inspector"


def _act_category(state: "PageState", form: dict) -> str:
    kind = form.get("kind", "all")
    allowed = {"all", "selected"}
    if state.graph:
        allowed.update(state.graph.counts())
    if kind not in allowed:
        state.error = "unknown content type"
        return ""
    state.inventory_kind = kind
    state.tab = "preview"
    return ""


def _act_select_shown(state: "PageState", _form: dict) -> str:
    state.select_shown()
    return ""


def _act_remove_pick(state: "PageState", form: dict) -> str:
    state.remove_pick(form.get("key", ""))
    state.tab = "review"
    return ""


ACTIONS = {
    "/inspector-tab": _act_inspector_tab,
    "/expand-preview": _act_expand_preview,
    "/category": _act_category,
    "/select-shown": _act_select_shown,
    "/remove-pick": _act_remove_pick,
    "/settings": _act_settings,
    "/open": _act_open,
    "/connect": lambda state, form: state.connect_source(form),
    "/pick-export": _act_pick_export,
    "/tab": _act_tab,
    "/disclose": _act_disclose,
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


def run_desktop(zip_path: Optional[str] = None, corpus_cli: Optional[str] = None,
                log_cli: Optional[str] = None, log_level_cli: Optional[str] = None,
                log_format_cli: Optional[str] = None) -> int:
    """Open the native window without a local HTTP server."""
    from . import desktop

    state = PageState(None, corpus_cli, log_cli, log_level_cli, log_format_cli)
    print("vcfcf-migrator ui: opening a window (close it to stop)", flush=True)
    try:
        desktop.run(state, initial_zip=zip_path)
    finally:
        state.close_source()
        state.log.finish(what='page', failed=sys.exc_info()[1])
        state.log.close()
    return 0
