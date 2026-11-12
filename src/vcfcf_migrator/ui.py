"""Native-window state and actions: six steps from an export to a bundle.

The window walks one job in order: open an export, choose content, pick a
dashboard owner, review, save, import on the target. ``PageState`` holds
where the person is and what they have chosen; every button is a registered
action that changes that state, and the page is drawn again from it.

Anything that takes longer than a moment (reading an export, downloading one,
writing a bundle) runs as a job on its own thread, so the window can show what
is happening and take a Cancel. Nothing is swapped in until the job's result
is whole, which is what makes cancelling, and failing, change nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
import html as _html
import re
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

import vcfcf_core

from vcfcf_migrator import __version__
from vcfcf_migrator import bundle as _bundle
from vcfcf_migrator import graph as _graph
from vcfcf_migrator import insight as _insight
from vcfcf_migrator.rawdoc import RawDocError
from vcfcf_migrator import runlog as _runlog
from vcfcf_migrator import selection as _selection
from vcfcf_migrator import settings as _settings
from vcfcf_migrator import source as _source
from vcfcf_migrator import tasks as _tasks
from vcfcf_migrator.export_reader import NotAnExport, read_members
from vcfcf_migrator.graph import object_label
from vcfcf_migrator.wording import listing, plural

STEPS = ('open', 'choose', 'owner', 'review', 'save', 'target')
STEP_NAMES = {'open': 'Open an export', 'choose': 'Choose content', 'owner': 'Dashboard owner',
              'review': 'Review', 'save': 'Save', 'target': 'Import on the target'}
# How many rows the list draws at once. A larger export is narrowed by typing.
LIST_LIMIT = 300
ISSUES_PAGE = 'https://github.com/sentania-labs/vcf-cf-migrator/issues/new'
# What the source says it is doing -> which of the four stages is shown.
DOWNLOAD_STAGES = ('Signing in', 'Asking VCF Operations to prepare the export',
                   'Downloading the export', 'Reading what is inside')
_STAGE_OF = {'Authenticating': 0, 'Requesting content export': 1, 'Waiting for Operations export': 1,
             'Downloading content export': 2, 'Loading downloaded content': 3}


def e(value) -> str:
    return _html.escape('' if value is None else str(value))


def serialized(method):
    """One state mutation or snapshot at a time, including direct callers."""
    @wraps(method)
    def call(state, *args, **kwargs):
        with state.action_lock:
            return method(state, *args, **kwargs)
    return call


@dataclass
class Note:
    """Something an action has to say, shown beside what it is about."""
    kind: str      # ok, info, warn, bad
    html: str

    @property
    def text(self) -> str:
        return _html.unescape(re.sub(r'<[^>]+>', '', self.html))


class JobCancelled(Exception):
    """Raised inside a job's work when the person has cancelled it."""


class Job:
    """Work in progress the window is showing: reading, downloading, saving."""

    def __init__(self, kind: str, name: str):
        self.kind = kind
        self.name = name
        self.stage = 0
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def check(self) -> None:
        if self.cancel.is_set():
            raise JobCancelled()


@dataclass
class Arrival:
    """An export that has been read successfully and not yet swapped in."""
    path: str
    name: str
    members: object
    graph: object
    insight: object
    snapshot: object = None
    address: str = ''
    host: str = ''                      # for a downloaded export: the server it came from
    fetched: Optional[datetime] = None  # and when


_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def fetched_name(host: str, when: datetime, seconds: bool = False) -> str:
    """What a downloaded export is called: the server and when it was fetched.

    The server alone names two downloads from one place the same, and then
    "Switch to X? Switching closes X" cannot be answered.
    """
    clock = when.strftime('%H:%M:%S' if seconds else '%H:%M')
    return f'{host}, fetched {when.day} {_MONTHS[when.month - 1]} {when.year} {clock}'


@dataclass
class Issue:
    """One line of the review list, with what its detail panel needs."""
    id: str
    group: str                     # must, kept, know, more
    title: str
    pick: str = ''
    hard: list = field(default_factory=list)        # [(node, gap)]
    stock: list = field(default_factory=list)       # [node]
    unfinished: list = field(default_factory=list)  # [node]
    catches: list = field(default_factory=list)     # [(node, catch)]
    share: object = None                            # a dashboard node
    task: object = None                             # a Task
    merge: str = ''                                 # what combining owners changes


class PageState:
    """What the window shows, and every action it can take. One per window."""

    def __init__(self, zip_path: Optional[str] = None, corpus_cli: Optional[str] = None,
                 log_cli: Optional[str] = None,
                 log_level_cli: Optional[str] = None, log_format_cli: Optional[str] = None):
        # The page always keeps its events in memory, whether or not a file is
        # asked for, because the support report has to be answerable after the
        # run that went wrong rather than only before it.
        self.action_lock = threading.RLock()
        self.log = _runlog.NULL
        self.corpus_cli = corpus_cli
        self.log_cli = log_cli
        self.log_level_cli = log_level_cli
        self.log_format_cli = log_format_cli
        # Supplied by the native window before the first render. Without a
        # window (a test) the defaults make every dialog "not available".
        self.file_picker = None            # () -> path of an export to open
        self.dialog = None                 # (kind, current) -> {'path':..., 'confirmed':...}
        self.show_in_folder = None         # (path) -> bool
        self.open_url = None               # (url) -> bool
        # Jobs run on their own thread once a window is driving the page.
        self.background = False
        self.job: Optional[Job] = None
        self.version = 0                   # bumped whenever the page would draw differently
        self.theme = _settings.load_settings().get('theme', 'light')
        if self.theme not in ('light', 'dark'):
            self.theme = 'light'
        self.update_tag: Optional[str] = None
        self.update_checked = False
        self.update_known = False          # the check reached the release page and got an answer

        # The export that is open.
        self.zip_path = ''
        self.source_name = ''
        self.source_address = ''
        self.source_snapshot = None
        self.source_host = ''              # a downloaded export's server and fetch time:
        self.source_fetched: Optional[datetime] = None   # together they are its name
        self.clock = datetime.now
        self.members = None
        self.graph = None
        self.insight: Optional[_insight.Insight] = None
        # Where the person is.
        self.screen = 'open'
        self.max_step = 0
        self.back = 'open'
        self.parked_note: Optional[Note] = None
        self.note: Optional[Note] = None
        self.undo: Optional[dict] = None
        self.ask: Optional[dict] = None
        self.open_error: Optional[dict] = None
        self.want = ''
        self.typed_path = False            # the file window could not open; offer a path box
        self.conn: Dict[str, object] = {'tested': None, 'tested_text': '', 'cert': None,
                                        'error': None, 'sources': [], 'lookup': None}
        # Choosing.
        self.picked: List[str] = []
        self.selection: Optional[_selection.Selection] = None
        self.filter_kind = 'all'
        self.filter_text = ''
        self.focus_key = ''
        self.reveal = False
        self.preview_key = ''
        # Owner and review.
        self.owner_mode = 'admin'
        self.target_user = 'admin'
        self.decided: Dict[str, bool] = {}
        self.issue = ''
        # Saving.
        self.save_name = 'bundle.zip'
        self.save_folder = self.download_directory()
        self.save_state = 'ready'          # ready, exists, error, saved
        self.save_problem = ''
        self.save_confirmed = ''           # a path the computer's own Save window already confirmed
        self.save_left: List[str] = []     # files a failed save wrote before it stopped
        self.built_path = ''
        self.build_report = ''
        self.stale = False
        self.doc = ''
        # On the target.
        self.ticks: Dict[str, bool] = {}
        self.task = ''
        self.finished = False
        # Help.
        self.gloss = False
        self.report_saved = ''
        self._memo: Dict[str, tuple] = {}
        self.open_log()
        if zip_path:
            self.open_export(zip_path)

    # -- the log -----------------------------------------------------------

    def open_log(self) -> None:
        """Open (or re-open) this page's log from the current settings.

        **The redactor carries over.** It holds everything harvested from the
        export that is open: the owners, their pseudonyms and every person in
        the export's user documents. A fresh one knows none of that, so a log
        opened after a setting changed would log the names the previous one
        excluded, and the support report would carry them too.
        """
        destination, self.log_from = _runlog.resolve_destination(self.log_cli)
        level, self.log_level_from = _runlog.resolve_level(self.log_level_cli)
        fmt, _fmt_from = _runlog.resolve_format(self.log_format_cli)
        old = self.log
        try:
            replacement = _runlog.open_log(destination, level=level, fmt=fmt,
                                           keep_events=True,
                                           redactor=old.redactor if old is not _runlog.NULL else None)
        except (_runlog.BadLogSetting, OSError) as exc:
            self.note = Note('bad', f'<b>The log file could not be opened.</b> {e(exc)}')
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

    def support_report_text(self) -> str:
        """The run header, the input fingerprint, every log event and the
        bundle's manifest, with names swapped for stand-ins."""
        return _runlog.diagnostics_document(
            self.log.snapshot(), header=self._last_event("run.start"),
            source=self._last_event("input.fingerprint"),
            bundle=self._last_event("output.fingerprint"), retention=self.log.retention())

    @serialized
    def save_support_report(self, out_path: Optional[str] = None) -> None:
        """One click: straight into Downloads under a dated name.

        The name carries the date and nothing else, so it cannot give away
        what the export is called. If Downloads cannot be written, the
        computer's own Save window is the way out. *out_path* names the file
        outright, for a caller that already knows where it should go.
        """
        folder = self.download_directory()
        stamp = datetime.now().strftime('%Y-%m-%d-%H%M%S')
        target = folder / f'support-report-{stamp}.txt'
        number = 2
        while target.exists():
            target = folder / f'support-report-{stamp}-{number}.txt'
            number += 1
        if out_path:
            target = Path(out_path)
            folder = target.parent
        document = self.support_report_text()
        try:
            target.write_text(document, encoding='utf-8')
        except OSError as exc:
            _runlog.error("output.unwritable", path=str(target), reason=str(exc))
            chosen = self.dialog('report', str(target)) if self.dialog else {}
            path = (chosen or {}).get('path')
            if not path:
                self.note = Note('bad', f'<b>The support report could not be saved in {e(folder.name)}.</b> '
                                        'That folder does not allow new files. Nothing was written.')
                return
            try:
                target = Path(path)
                target.write_text(document, encoding='utf-8')
            except OSError as second:
                _runlog.error("output.unwritable", path=str(target), reason=str(second))
                self.note = Note('bad', '<b>The support report could not be saved there either.</b> '
                                        'Choose a folder you can save files in.')
                return
        self.report_saved = str(target)

    # -- jobs --------------------------------------------------------------

    def _start(self, kind: str, name: str, work: Callable[[Job], Callable[[], None]],
               failed: Callable[[BaseException], None]) -> None:
        """Run *work* as the job the window is showing.

        *work* does everything slow without touching the state and returns a
        function that swaps the result in. That function runs under the action
        lock, and only if the job was not cancelled meanwhile.
        """
        job = Job(kind, name)
        self.job = job
        self.version += 1

        def run() -> None:
            _runlog.set_current(self.log)
            try:
                commit = work(job)
            except BaseException as exc:  # noqa: BLE001 - reported on the page
                error = exc
                commit = lambda: failed(error)  # noqa: E731
            with self.action_lock:
                try:
                    if self.job is job and not job.cancel.is_set():
                        self.job = None
                        commit()
                    elif getattr(job, 'discard', None):
                        job.discard()
                except Exception as exc:  # noqa: BLE001 - the window must not sit on a working screen
                    _runlog.error("page.job_failed", job=kind, failure=type(exc).__name__)
                    self.note = Note('bad', '<b>That did not finish.</b> The app hit a problem it did not '
                                            f'expect ({e(type(exc).__name__)}). Nothing was changed. If it '
                                            'keeps happening, save a support report from Help and settings.')
                finally:
                    if self.job is job:
                        self.job = None
                    self.version += 1
                    job.done.set()

        if self.background:
            job.thread = threading.Thread(target=run, name=f'migrator-{kind}', daemon=True)
            job.thread.start()
        else:
            run()

    @serialized
    def cancel_job(self) -> None:
        job = self.job
        if job is None or job.kind == 'save':
            return
        job.cancel.set()
        self.job = None
        self.version += 1
        if job.kind == 'download':
            self.screen = 'connect'
            self.note = Note('info', '<b>Cancelled.</b> Nothing was downloaded. If the source had already '
                                     'started preparing an export, give it a minute to finish before '
                                     'trying again.')
        else:
            self.note = Note('info', '<b>Cancelled.</b> Nothing was changed.')

    def _set_stage(self, job: Job, stage: int) -> None:
        if stage != job.stage:
            job.stage = stage
            self.version += 1

    # -- loading -----------------------------------------------------------

    @serialized
    def open_export(self, zip_path: str) -> None:
        """Read the export and build the graph, then swap.

        Nothing is cleared until the replacement parses. The reading happens
        into locals, and the state changes only once there is something to
        change it to, so a wrong file leaves the open export and every choice
        exactly as they were.
        """
        zip_path = (zip_path or '').strip()
        if not zip_path:
            return
        name = Path(zip_path).name
        self._drop_question()
        self.open_error = None
        self.note = None
        self.screen = 'open'

        def work(job: Job):
            arrival = self._read(job, zip_path, name)
            return lambda: self._arrive(arrival)

        self._start('read', name, work, lambda exc: self._open_failed(exc, name))

    def _read(self, job: Job, zip_path: str, name: str, snapshot=None, address: str = '') -> Arrival:
        members = read_members(zip_path)
        job.check()
        graph = _graph.build_graph(members.data)
        job.check()
        return Arrival(str(zip_path), name, members, graph, _insight.build(graph), snapshot, address)

    def _open_failed(self, exc: BaseException, name: str) -> None:
        self.screen = 'open'
        if isinstance(exc, JobCancelled):
            return
        if isinstance(exc, NotAnExport):
            self.open_error = {'kind': exc.kind, 'name': name,
                               'declared': getattr(exc, 'declared', 0), 'found': getattr(exc, 'found', 0)}
        elif isinstance(exc, RawDocError):
            self.open_error = {'kind': 'unreadable_inside', 'name': name}
        else:
            _runlog.error("page.open_failed", failure=type(exc).__name__)
            self.open_error = {'kind': 'unexpected', 'name': name, 'detail': type(exc).__name__}

    def _arrive(self, new: Arrival) -> None:
        """A good export is in hand. Refuse an empty one, ask before replacing
        work in progress, and otherwise open it."""
        if not new.graph.nodes:
            if new.snapshot is not None:
                new.snapshot.close()
            self.screen = 'open'
            self.open_error = {'kind': 'empty', 'name': new.name}
            return
        if self.graph is not None and self.picked:
            self.screen = 'open'
            self.ask = {'kind': 'switch', 'new': new, 'name': new.name,
                        'same': (new.snapshot is None and self.source_snapshot is None
                                 and Path(new.path).resolve() == Path(self.zip_path).resolve())}
            return
        self._adopt(new, keep=False)

    def _adopt(self, new: Arrival, keep: bool) -> None:
        old_graph, old_picked, had_answers = self.graph, list(self.picked), bool(self.decided)
        if self.source_snapshot is not None and self.source_snapshot is not new.snapshot:
            self.close_source()
        self.zip_path, self.source_name = new.path, new.name
        self.source_snapshot, self.source_address = new.snapshot, new.address
        self.source_host, self.source_fetched = new.host, new.fetched
        self.members, self.graph, self.insight = new.members, new.graph, new.insight
        mapping: Dict[str, str] = {}
        missing = 0
        if keep and old_graph is not None:
            by_name: Dict[tuple, List[str]] = {}
            for node in new.graph.nodes.values():
                by_name.setdefault((node.kind, node.name), []).append(node.key)
            for key in old_picked:
                if key in new.graph.nodes:
                    mapping[key] = key
            for key in old_picked:
                if key in mapping:
                    continue
                old = old_graph.nodes.get(key)
                same = by_name.get((old.kind, old.name), []) if old else []
                # A name is only trusted when exactly one thing answers to it.
                if len(same) == 1 and same[0] not in mapping.values():
                    mapping[key] = same[0]
                else:
                    missing += 1
        self._set_picked(list(dict.fromkeys(mapping.values())))
        # What was answered at Review was answered about the other export. The
        # same choice can need something different here, so every "keep it in"
        # is asked again; only the ticks and the owner choice carry over.
        self.decided = {}
        if not keep:
            self.owner_mode, self.target_user = 'admin', 'admin'
        self.filter_kind, self.filter_text, self.focus_key, self.preview_key = 'all', '', '', ''
        self.issue, self.task, self.doc = '', '', ''
        self.save_state, self.save_problem, self.built_path, self.build_report = 'ready', '', '', ''
        self.stale, self.finished, self.ticks = False, False, {}
        self.want, self.undo, self.open_error, self.ask = '', None, None, None
        self.save_folder = self.download_directory()
        self.save_name = self.default_save_name()
        self.save_confirmed = ''
        self.screen, self.max_step = 'choose', 1
        count = len(new.graph.nodes)
        if new.snapshot is not None:
            # The sign-in is over. The next one, wherever it goes, verifies
            # the server's certificate again unless it is asked not to.
            self.conn['cert'], self.conn['tested'] = None, None
        text = f'<b>Opened {e(new.name)}.</b> It holds {plural(count, "thing")} you can move.'
        if mapping:
            text += (f' Your {plural(len(mapping), "choice was", "choices were")} ticked again.')
        if missing:
            text += (f' {plural(missing, "choice was", "choices were")} not found in this export and '
                     f'{"was" if missing == 1 else "were"} left out.')
        if keep and had_answers and mapping:
            text += ' What you decided at Review is asked again, because this is a different export.'
        unfinished = len(new.graph.hollow_dashboards())
        if unfinished:
            text += (f' {plural(unfinished, "dashboard")} never finished loading on the source; '
                     f'{"it is" if unfinished == 1 else "they are"} marked and left out unless you tick '
                     f'{"it" if unfinished == 1 else "them"}. <button class="linkbtn" data-act="/filter" '
                     f'data-f="unf">Show {"it" if unfinished == 1 else f"those {unfinished}"}</button>')
        if new.snapshot is not None:
            text += (' The downloaded export is kept only while the app is open, so save your bundle '
                     'before you close it.')
        self.note = Note('ok', text)

    def _drop_question(self) -> None:
        """Forget a question that is on screen. An export that was read and
        is waiting for "Switch?" is let go, with its temporary files."""
        ask, self.ask = self.ask, None
        new = (ask or {}).get('new')
        if new is not None and new.snapshot is not None and new.snapshot is not self.source_snapshot:
            new.snapshot.close()

    def close_source(self) -> None:
        if self.source_snapshot is not None:
            self.source_snapshot.close()
            self.source_snapshot = None

    @serialized
    def connect_source(self, form: dict) -> None:
        """Sign in, ask for an export, download it and read it, as one job."""
        try:
            address = (form.get('address') or '').strip()
            username, password = form.get('username') or '', form.get('password') or ''
            auth_source = form.get('auth_source') or ''
            export_password, again = form.get('export_password') or '', form.get('export_password2')
            self.conn['error'] = None
            if not (address and username and password and export_password):
                self.conn['error'] = ('other', 'Fill in the address, user name and password, and type the '
                                               'export password twice.')
                return
            if again is not None and again != export_password:
                self.conn['error'] = ('other', 'The two export passwords do not match. Type them again.')
                return
            cert = self.certificate_choice(address)
            try:
                host = _source.endpoint(address).split('//', 1)[1].split('/', 1)[0]
                client = _source.Client(address, ca_file=cert.get('path', ''),
                                        ignore_ssl=cert.get('mode') == 'skip')
            except _source.SourceError as exc:
                self.conn['error'] = ('other', str(exc))
                return
            except Exception:
                # Never expose connection exceptions, which may contain request data.
                self.conn['error'] = ('other', 'The connection could not be set up. Check the address '
                                               'and try again.')
                return
            if self.job is not None:
                return
        finally:
            form.clear()

        secrets = {'username': username, 'password': password, 'auth_source': auth_source,
                   'export_password': export_password}
        self._drop_question()
        self.note = None
        self.screen = 'connect'

        def work(job: Job):
            client.progress = lambda stage: self._set_stage(job, _STAGE_OF.get(stage, job.stage))
            client.cancelled = job.cancel.is_set
            try:
                snapshot = client.acquire(secrets['username'], secrets['password'],
                                          secrets['auth_source'], secrets['export_password'])
            finally:
                secrets.clear()
            job.discard = snapshot.close
            self._set_stage(job, 3)
            fetched = self.clock()
            name = fetched_name(host, fetched)
            if name == self.source_name:      # the same server twice within a minute
                name = fetched_name(host, fetched, seconds=True)
            try:
                arrival = self._read(job, str(snapshot.path), name, snapshot,
                                     client.base.removesuffix("/suite-api/api"))
            except BaseException:
                snapshot.close()
                job.discard = None
                raise
            arrival.host, arrival.fetched = host, fetched
            return lambda: self._arrive(arrival)

        self._start('download', host, work, lambda exc: self._connect_failed(exc, host))

    def _connect_failed(self, exc: BaseException, host: str) -> None:
        self.screen = 'connect'
        if isinstance(exc, (JobCancelled, _source.Cancelled)):
            return
        if isinstance(exc, _source.SignInRefused):
            self.conn['error'] = ('auth', host)
        elif isinstance(exc, _source.CertificateProblem):
            self.conn['tested'], self.conn['cert'] = 'cert', None
        elif isinstance(exc, _source.SourceError):
            self.conn['error'] = ('other', str(exc))
        elif isinstance(exc, (NotAnExport, RawDocError)):
            self._open_failed(exc, host)
        else:
            # Never expose connection exceptions, which may contain request data.
            self.conn['error'] = ('other', 'The connection or the download failed. Check the export job '
                                           'on the source before trying again.')

    @staticmethod
    def _server(address: str) -> str:
        """An address in the one spelling a certificate choice is kept under."""
        try:
            return _source.endpoint(address or '')
        except _source.SourceError:
            return (address or '').strip().lower()

    def certificate_choice(self, address: str) -> dict:
        """How the certificate of the server at *address* is to be checked.

        "Use my company certificate file" and "Connect without checking" are
        answers about one server. Asked about any other address the choice is
        dropped, so a sign-in somewhere else always starts with the
        certificate being verified.
        """
        cert = self.conn.get('cert')
        if not cert:
            return {}
        if cert.get('address') != self._server(address):
            self.conn['cert'] = None
            return {}
        return cert

    @staticmethod
    def download_directory() -> Path:
        downloads = Path.home() / 'Downloads'
        return downloads if downloads.is_dir() else Path.home()

    def default_save_name(self) -> str:
        if self.source_snapshot is not None:
            # From the server's name and when the export was fetched, so two
            # downloads from one server make two files. A port's colon, or the
            # brackets of an IPv6 address, are not characters a file name can carry.
            server = self.source_host or ''
            host = re.sub(r'[^A-Za-z0-9._-]+', '-',
                          server.rsplit(':', 1)[0] if ']' not in server else server).strip('-.')
            when = self.source_fetched.strftime('-%Y-%m-%d-%H%M') if self.source_fetched else ''
            return (host or 'vcf-operations') + when + '-bundle.zip'
        if not self.zip_path:
            return 'bundle.zip'
        return Path(self.zip_path).stem + '-bundle.zip'

    def default_out(self) -> str:
        return str(self.save_folder / self.save_name)

    @staticmethod
    def output_path(value: str, extension: str, default: str = '') -> str:
        path = Path((value or '').strip() or default).expanduser()
        if not path.name:
            path = Path(default).expanduser()
        if path.suffix.lower() != extension:
            path = path.with_name(path.name + extension)
        return str(path.resolve())

    # -- the selection -----------------------------------------------------

    def _set_picked(self, keys) -> None:
        picked = list(keys)
        closed = _selection.close(self.graph, picked) if self.graph else None
        self.picked, self.selection = picked, closed

    def selected_keys(self) -> set:
        """What the person ticked by hand."""
        return set(self.picked)

    def selection_keys(self) -> set:
        """What a bundle would carry: the ticks plus everything they need."""
        return set(self.selection.keys) if self.selection else set()

    def label(self, key: str) -> str:
        """What a thing is called, on screen and in the saved files alike."""
        return object_label(self.graph, self.graph.nodes[key])

    def needed_by(self, key: str, included=None, limit: int = 4) -> List[str]:
        included = included if included is not None else self.selection_keys()
        return _insight.needed_by(self.graph, self.insight, included, self.picked, key, limit)

    def changed(self) -> None:
        """The bundle is not what it was: a save is out of date and the steps
        after this one have to be walked again."""
        if self.save_state == 'saved':
            self.stale = True
        self.save_state, self.save_problem = 'ready', ''
        self.finished = False
        here = STEPS.index(self.screen) if self.screen in STEPS else 1
        self.max_step = min(self.max_step, max(here, 1))

    def _snapshot(self) -> None:
        self.undo = {'picked': list(self.picked), 'owner_mode': self.owner_mode,
                     'decided': dict(self.decided), 'issue': self.issue}

    @serialized
    def toggle(self, key: str, on: bool) -> None:
        """Tick or untick one thing and say what that did.

        Ticking adds what it needs. Something that needs a thing the export
        does not hold can still be ticked, and says that Review will ask about
        it: nothing in an export tells a thing that was left out from one that
        comes with the product and is never exported. Unticking a choice that
        another choice still needs leaves it in the bundle, and says so.
        """
        if self.graph is None:
            return
        node = self.graph.nodes.get(key)
        if node is None:
            self.note = Note('bad', 'That is not in this export any more.')
            return
        name = e(self.label(key))
        self.focus_key = key
        self.undo = None
        if on:
            if key in self.picked:
                return
            before = self.selection_keys()
            self._set_picked(self.picked + [key])
            added = [k for k in self.selection.keys if k not in before and k != key]
            mine = _insight.reach(self.graph, key)
            stock = [self.graph.nodes[k] for k in mine if self.graph.nodes[k].stock_or_derived]
            unfinished = [self.graph.nodes[k] for k in mine if self.graph.nodes[k].hollow]
            text = f'<b>Added {name}.</b> '
            if node.hollow:
                text += ('<b>It never finished loading on the source</b>, so what it needs is unknown '
                         'and it arrives empty.')
            elif added:
                text += (f'{plural(len(added), "thing")} it needs '
                         f'{"was" if len(added) == 1 else "were"} added too.')
            elif key in before:
                text += 'It was already in the bundle because something else you chose needs it.'
            else:
                text += ('Everything it needs was already in.' if self.graph.edges.get(key)
                         else 'It needs nothing else.')
            others = [n for n in unfinished if n.key != key]
            if others:
                text += (f' <b>{e(self.label(others[0].key))}, which it uses, never finished loading on '
                         'the source</b>, so it arrives empty.')
            if stock:
                text += (' <b>' + ('It' if stock[0].key == key else e(self.label(stock[0].key))
                                   + ', which it uses,')
                         + ' would overwrite the target’s own copy.</b>')
            missing = [gap for k in mine for gap in _insight.hard_gaps(self.graph, k)]
            if missing:
                text += (f' <b>It needs {e(_insight.missing_phrase(missing[0]))}'
                         + (f' and {plural(len(missing) - 1, "other thing")}' if len(missing) > 1 else '')
                         + ', which '
                         + ('is' if len(missing) == 1 else 'are') + ' not in this export.</b>')
            if stock or unfinished or missing:
                text += ' You will be asked at the Review step whether to keep it.'
            if any(twin in self.selection.keys for twin in self.insight.twins.get(key, ())):
                text += ' You now have both copies of this dashboard. The next step asks which to keep.'
            self.note = Note('warn' if stock or unfinished or missing else 'ok', text)
            self.changed()
            return
        if key not in self.picked:
            return
        before = self.selection_keys()
        self._set_picked(k for k in self.picked if k != key)
        self.decided.pop('i-' + key, None)
        after = self.selection_keys()
        gone = [k for k in before if k not in after and k != key]
        text = f'<b>Left out {name}.</b> '
        if key in after:
            text += f'It stays in the bundle because {e(self._names(self.needed_by(key)))} needs it.'
        elif gone:
            text += (f'{plural(len(gone), "thing")} only it needed '
                     f'{"was" if len(gone) == 1 else "were"} left out too.')
        self.note = Note('info', text)
        self.changed()

    def _names(self, keys, limit: int = 3) -> str:
        return _insight.names(self.graph, keys, limit)

    def visible_nodes(self):
        """The rows the list is showing: the chosen part, narrowed by name."""
        if self.graph is None:
            return []
        needle = self.filter_text.strip().lower()
        nodes = self.graph.ordered()
        if self.filter_kind == 'in':
            included = self.selection_keys()
            nodes = [n for n in nodes if n.key in included]
        elif self.filter_kind == 'unf':
            nodes = [n for n in nodes if n.hollow]
        elif self.filter_kind != 'all':
            nodes = [n for n in nodes if n.kind == self.filter_kind]
        if needle:
            nodes = [n for n in nodes if needle in n.name.lower()]
        return nodes

    def addable(self, nodes=None):
        """What "Add all of these" would add: everything shown that is not in
        the bundle yet and needs no decision."""
        included = self.selection_keys()
        nodes = self.visible_nodes() if nodes is None else nodes
        return [n for n in nodes if n.key not in included and not self.insight.needs_decision(n.key)]

    @serialized
    def add_shown(self) -> None:
        if self.graph is None:
            return
        shown = self.visible_nodes()
        add = self.addable(shown)
        if not add:
            return
        self._snapshot()
        self._set_picked(self.picked + [n.key for n in add])
        included = self.selection_keys()
        rest = [n for n in shown if n.key not in included]
        blocked = sum(1 for n in rest if n.key in self.insight.blocked)
        copies = sum(1 for n in rest if n.key not in self.insight.blocked and n.key in self.insight.twins)
        warned = len(rest) - blocked - copies
        text = f'<b>Added {plural(len(add), "thing")}.</b>'
        parts = []
        if warned:
            parts.append(f'{plural(warned, "thing")} that '
                         f'{"carries" if warned == 1 else "carry"} a warning')
        if copies:
            parts.append(f'{plural(copies, "copy", "copies")} of a dashboard that has more than one')
        if blocked:
            parts.append(f'{plural(blocked, "thing")} that '
                         f'{"needs" if blocked == 1 else "need"} something this export does not have')
        if parts:
            text += (' Not added, because each needs a decision from you: ' + listing(parts)
                     + '. Tick those yourself if you want them.')
        self.note = Note('ok', text)
        self.changed()

    @serialized
    def clear(self) -> None:
        self._set_picked([])
        self.decided = {}
        self.undo = None
        self.filter_kind = 'all'
        self.note = Note('info', '<b>Your bundle is empty again.</b>')
        self.changed()

    @serialized
    def undo_last(self) -> None:
        if not self.undo:
            return
        saved, self.undo = self.undo, None
        self._set_picked(saved['picked'])
        self.owner_mode, self.decided = saved['owner_mode'], saved['decided']
        # Back to the thing that was being looked at when it was left out.
        self.issue = saved.get('issue', self.issue)
        self.note = Note('info', '<b>Undone.</b> Your bundle is as it was.')
        self.changed()

    @serialized
    def leave_out(self, key: str) -> None:
        """Take one of the person's choices out, from the review step."""
        if key not in self.picked:
            return
        self._snapshot()
        name = e(self.label(key))
        before = self.selection_keys()
        self._set_picked(k for k in self.picked if k != key)
        self.decided.pop('i-' + key, None)
        after = self.selection_keys()
        gone = [k for k in before if k not in after and k != key]
        text = f'<b>Left out {name}.</b> '
        if key in after:
            text += f'It is still in the bundle because {e(self._names(self.needed_by(key)))} needs it.'
        elif gone:
            text += (f'{plural(len(gone), "thing")} only it needed went with it: '
                     f'{e(self._names(gone, 6))}.')
        self.note = Note('ok', text)
        self.changed()
        if not self.picked:
            self.screen = 'choose'
            self.undo = None
            self.note = Note('info', '<b>Your bundle is empty.</b> Tick something to carry on.')

    # -- owner -------------------------------------------------------------

    def dashboards(self) -> List[_graph.Node]:
        """The dashboards in the bundle that belong to someone."""
        if not self.selection:
            return []
        return [self.graph.nodes[k] for k in self.selection.keys
                if self.graph.nodes[k].kind == 'dashboard' and self.graph.nodes[k].owner]

    def copies_chosen(self) -> List[List[_graph.Node]]:
        """Each set of copies of one dashboard that are in the bundle together."""
        groups: Dict[str, List[_graph.Node]] = {}
        for node in self.dashboards():
            groups.setdefault(node.ident, []).append(node)
        return [nodes for nodes in groups.values() if len({n.owner for n in nodes}) > 1]

    def _merge(self) -> tuple:
        """What putting the chosen dashboards under one owner would run into:
        (refusals, notices). Worked out once per selection, because it
        rebuilds the dashboard files."""
        if not self.selection or self.owner_mode == 'source':
            return [], []
        cached = self._memo.get('merge')
        if cached and cached[0] is self.selection:
            return cached[1]
        result = _bundle.merge_notices(self.graph, self.selection, False)
        self._memo['merge'] = (self.selection, result)
        return result

    def merge_refusals(self) -> List[str]:
        """Why the chosen dashboards cannot be put under one owner, if they cannot."""
        return self._merge()[0]

    def owner_question(self) -> str:
        """What has to be answered before the owner choice: 'copies', 'merge' or ''."""
        if self.owner_mode == 'source':
            return ''
        if self.copies_chosen():
            return 'copies'
        if self.merge_refusals():
            return 'merge'
        return ''

    def _others_of(self, key: str) -> List[str]:
        return [n.key for group in self.copies_chosen() for n in group
                if any(m.key == key for m in group) and n.key != key]

    def can_keep_copy(self, key: str) -> bool:
        """Whether the other copies can be left out by unticking them alone.
        They cannot when something else that was chosen brings them along."""
        others = set(self._others_of(key))
        if not others:
            return False
        seen = set()
        for pick in self.picked:
            if pick not in others:
                seen.update(_insight.reach(self.graph, pick))
        return not (others & seen)

    @serialized
    def set_owner(self, mode: str, both: bool = False) -> None:
        if mode not in ('admin', 'source'):
            self.note = Note('bad', 'Choose one of the two owners.')
            return
        if mode == self.owner_mode and not both:
            return
        self.owner_mode, self.target_user = mode, 'admin'
        self.undo = None
        self.note = (Note('info', '<b>Both copies are kept</b>, so "The same user as on the source" is '
                                  'selected below. Choosing a different owner means picking one copy.')
                     if both else None)
        self.changed()

    @serialized
    def keep_copy(self, key: str) -> None:
        """Keep one copy of a dashboard and leave the others out."""
        if not self.can_keep_copy(key):
            return
        others = self._others_of(key)
        self._snapshot()
        self._set_picked(k for k in self.picked if k not in others)
        for other in others:
            self.decided.pop('i-' + other, None)
        names = listing([self.graph.owner_names.get(self.graph.nodes[k].owner, 'Unknown owner')
                         for k in others])
        self.note = Note('ok', f'<b>The copy of {e(self.graph.nodes[key].name)} owned by {e(names)} was '
                               'left out of the bundle.</b> The other copy stays in.')
        self.changed()

    # -- review ------------------------------------------------------------

    def tasks(self) -> List[_tasks.Task]:
        """What there will be to do on the target for the bundle as it stands."""
        if not self.selection or not self.selection.keys:
            return []
        key = (self.owner_mode, self.target_user, self.save_name, str(self.save_folder),
               self.source_snapshot is not None, tuple(sorted(self.decided)))
        cached = self._memo.get('tasks')
        if cached and cached[0] is self.selection and cached[1] == key:
            return cached[2]
        result = _tasks.build_tasks(self.graph, self.selection, self.owner_mode == 'source',
                                    self.target_user, self.save_name, self.save_folder.name,
                                    self.source_snapshot is not None, self._confirmed())
        self._memo['tasks'] = (self.selection, key, result)
        return result

    def _confirmed(self) -> list:
        """Like ``accepted_missing``, without going through the review list,
        which is itself built from the tasks."""
        out = []
        for pick in self.picked:
            if self.decided.get('i-' + pick) and pick in self.insight.blocked:
                for key in _insight.reach(self.graph, pick):
                    out.extend(_insight.hard_gaps(self.graph, key))
        return list(dict.fromkeys(out))

    def issues(self) -> List[Issue]:
        """The review list, as data: what must be answered, what was answered,
        what is only good to know, and the two reference lines."""
        if not self.selection or not self.selection.keys:
            return []
        key = (self.owner_mode, self.save_name, str(self.save_folder), tuple(sorted(self.decided)))
        cached = self._memo.get('issues')
        if cached and cached[0] is self.selection and cached[1] == key:
            return cached[2]
        graph, ins = self.graph, self.insight
        must: List[Issue] = []
        know: List[Issue] = []
        for pick in self.picked:
            if not (pick in ins.blocked or pick in ins.stock or pick in ins.unfinished or pick in ins.soft):
                continue
            nodes = [graph.nodes[k] for k in _insight.reach(graph, pick)]
            hard = [(n, gap) for n in nodes for gap in _insight.hard_gaps(graph, n.key)]
            stock = [n for n in nodes if n.stock_or_derived]
            unfinished = [n for n in nodes if n.hollow]
            catches = [(n, c) for n in nodes for c in _insight.catches(graph, n)]
            name = self.label(pick)

            def via(node, _pick=pick):
                return '' if node.key == _pick else f'brings {self.label(node.key)}, which '
            if hard:
                head = 'needs something this export does not have'
            elif stock:
                head = via(stock[0]) + 'would overwrite the target’s copy'
            elif unfinished:
                head = via(unfinished[0]) + 'never finished loading on the source'
            elif catches:
                head = catches[0][1].badge[:1].lower() + catches[0][1].badge[1:]
            else:
                continue
            issue = Issue('i-' + pick, 'must', f'{name}: {head}', pick, hard, stock, unfinished, catches)
            if hard or stock or unfinished:
                if self.decided.get(issue.id):
                    issue.group = 'kept'
                must.append(issue)
            else:
                issue.group = 'know'
                know.append(issue)
        if self.owner_mode != 'source':
            for node in self.dashboards():
                groups = _tasks.directory_groups(graph, node)
                if groups:
                    know.append(Issue('share-' + node.key, 'know',
                                      f'{self.label(node.key)}: sharing with {listing(groups)} is not carried',
                                      share=node))
            notices: Dict[tuple, int] = {}
            for kind, ident, owner, _key in self._merge()[1]:
                notices[(kind, ident, owner)] = notices.get((kind, ident, owner), 0) + 1
            for number, ((kind, ident, owner), count) in enumerate(notices.items()):
                node = graph.nodes.get(f'dashboard:{ident}@{owner}')
                name = self.label(node.key) if node else 'A dashboard'
                if kind == 'unresolved':
                    head = ('one widget may stay empty under a shared owner' if count == 1 else
                            f'{count} widgets may stay empty under a shared owner')
                else:
                    head = ('one of its texts is renamed inside the bundle' if count == 1 else
                            f'{count} of its texts are renamed inside the bundle')
                know.append(Issue(f'merge-{number}', 'know', f'{name}: {head}', merge=kind))
        tasks = self.tasks()
        for task in tasks:
            if task.know:
                know.append(Issue('k-' + task.id, 'know', task.know, task=task))
        rows = ([i for i in must if i.group == 'must'] + [i for i in must if i.group == 'kept'] + know
                + [Issue('todo', 'more', f'{plural(len(tasks), "thing")} to do on the target after import'),
                   Issue('contents', 'more', f'What is in the bundle ({len(self.selection.keys)})')])
        self._memo['issues'] = (self.selection, key, rows)
        return rows

    def open_issues(self) -> List[Issue]:
        return [i for i in self.issues() if i.group == 'must']

    @serialized
    def keep_in(self, issue_id: str) -> None:
        issue = next((i for i in self.issues() if i.id == issue_id), None)
        if issue is None or issue.group != 'must':
            return
        self.decided[issue_id] = True
        self.note, self.undo = None, None
        remaining = self.open_issues()
        self.issue = remaining[0].id if remaining else issue_id
        if self.save_state == 'saved':
            self.stale = True
        self.save_state = 'ready'

    # -- saving ------------------------------------------------------------

    def accepted_missing(self) -> list:
        """The missing dependencies the person kept in, having said each
        already exists on the target. Only these get past the build's gate."""
        return list(dict.fromkeys(gap for issue in self.issues() if issue.group == 'kept'
                                  for _node, gap in issue.hard))

    def kept_lines(self) -> List[str]:
        out = []
        for issue in self.issues():
            if issue.group != 'kept':
                continue
            name = self.label(issue.pick)
            for node, gap in issue.hard:
                out.append(f'{name}: {self.label(node.key)} needs {_insight.missing_phrase(gap)}, which is '
                           'not in the bundle. You confirmed that it already exists on the target.')
            if issue.stock:
                out.append(f'{name}: overwrites {self.label(issue.stock[0].key)}, which comes with '
                           'VCF Operations, on the target.')
            if issue.unfinished:
                out.append(f'{name}: {self.label(issue.unfinished[0].key)} never finished loading on '
                           'the source and arrives empty.')
        return out

    def report_blocks(self) -> List[_tasks.Block]:
        included = self.selection_keys()
        stem = Path(self.save_name).stem
        return _tasks.report_blocks(
            self.graph, self.selection, self.tasks(), self.save_name, self.source_name,
            self.owner_mode == 'source', self.target_user, self.kept_lines(),
            lambda key: self._names(self.needed_by(key, included)), stem + '-checklist.txt',
            bool(self.accepted_missing()))

    def save_files(self) -> List[Path]:
        out = self.save_folder / self.save_name
        return [out, out.with_name(out.stem + '-report.txt'), out.with_name(out.stem + '-checklist.txt')]

    @serialized
    def choose_save_place(self) -> None:
        """The computer's own Save window, for a different name or folder."""
        if not self.dialog:
            self.note = Note('bad', 'This window cannot open the Save window.')
            return
        chosen = self.dialog('bundle', self.default_out()) or {}
        if chosen.get('error'):
            self.note = Note('bad', e(chosen['error']))
            return
        if not chosen.get('path'):
            return
        path = Path(self.output_path(chosen['path'], '.zip', self.default_out()))
        self.save_name, self.save_folder = path.name, path.parent
        # Some Save windows ask "replace?" themselves. When this one did, the
        # page does not ask a second time for the same file.
        self.save_confirmed = str(path) if chosen.get('confirmed') else ''
        self.save_state, self.save_problem = 'ready', ''

    @serialized
    def save(self, force: bool = False) -> None:
        """Write the bundle, its report and its checklist for what is chosen.

        The bundle is the one the command line writes for the same selection.
        Anything that still needs an answer at Review refuses the save. A
        missing dependency is let through only where the person answered for
        it there, one choice at a time.
        """
        if self.graph is None or not (self.selection and self.selection.keys):
            return
        if self.open_issues() or self.owner_question():
            self.screen = 'review' if self.open_issues() else 'owner'
            return
        files = self.save_files()
        out = files[0]
        if self.zip_path and out.resolve() == Path(self.zip_path).resolve():
            self.save_state = 'error'
            self.save_problem = 'same'
            return
        existing = [f for f in files if f.exists()]
        # A Save window that asked "replace?" asked about the bundle. That
        # covers the bundle and the two files that belong to it, and nothing
        # when the bundle was not there to ask about.
        agreed = self.save_confirmed == str(out) and out.exists()
        if existing and not force and not agreed:
            self.save_state, self.save_problem = 'exists', existing[0].name
            return
        self.save_state, self.save_problem = 'ready', ''
        self.save_left = []
        before = {f: (f.stat().st_mtime_ns if f.exists() else None) for f in files}
        selection, graph, members = self.selection, self.graph, self.members
        report_head = _tasks.blocks_text(self.report_blocks())
        owner_mode, target_user = self.owner_mode, self.target_user
        from_source = self.source_snapshot is not None
        accepted = self.accepted_missing()

        def work(job: Job):
            result = _bundle.build_bundle(members.data, members.order, graph, selection, str(out),
                                          marker=members.marker, directories=members.directories,
                                          directory_order=members.directory_order,
                                          target_user=target_user, keep_ownership=owner_mode == 'source',
                                          from_source=from_source, accepted_missing=accepted)
            technical = (_selection.render(graph, selection, target_user, owner_mode == 'source',
                                           accepted)
                         + _bundle.render(result))
            if result.support_details:
                technical += '\nSupport details\n' + '\n'.join(result.support_details) + '\n'
            report = (report_head + '\n\nTechnical detail, for support\n'
                      + '=============================\n' + technical)
            files[1].write_text(report, encoding='utf-8')
            return lambda: self._saved(result, technical)

        def failed(exc: BaseException) -> None:
            # Which of the three files this attempt wrote before it stopped.
            self.save_left = [f.name for f in files
                              if f.exists() and f.stat().st_mtime_ns != before[f]]
            self._save_failed(exc)

        self._start('save', out.name, work, failed)

    def _saved(self, result, technical: str) -> None:
        self.built_path = str(Path(result.path).resolve())
        self.build_report = technical
        self.save_state, self.save_problem, self.stale = 'saved', '', False
        self.save_confirmed = ''
        self.ticks, self.task, self.finished = {}, '', False
        self.screen = 'save'
        self.max_step = max(self.max_step, STEPS.index('save'))

    def _save_failed(self, exc: BaseException) -> None:
        self.screen = 'save'
        self.save_state = 'error'
        if isinstance(exc, PermissionError):
            self.save_problem = 'denied'
        elif isinstance(exc, OSError):
            _runlog.error("output.unwritable", path=str(self.save_folder), reason=str(exc))
            self.save_problem = 'os:' + (exc.strerror or type(exc).__name__)
        elif isinstance(exc, _selection.MissingDependency):
            self.save_state, self.screen = 'ready', 'review'
        else:
            _runlog.error("build.failed", failure=type(exc).__name__, detail=str(exc))
            self.save_problem = 'build:' + str(exc)

    # -- settings ----------------------------------------------------------

    @serialized
    def save_setting(self, form: dict) -> None:
        if 'theme' in form:
            value = form['theme']
            if value in ('light', 'dark'):
                _settings.save_settings({'theme': value})
                self.theme = value
            return
        if 'log_on' in form:
            if form['log_on'] == '1':
                current = _settings.load_settings().get('log_file')
                value = current or str(self.download_directory() / 'vcf-content-migrator.log')
            else:
                value = ''
            _settings.save_settings({'log_file': value})
            self.open_log()

    @serialized
    def render(self) -> str:
        """Measure page generation without claiming the session has ended."""
        from vcfcf_migrator import uipage
        with self.log.phase('render'):
            return uipage.render(self)


# ---------------------------------------------------------------------------
# Registered actions are the only entry points exposed by the native bridge.

# What may still be pressed while a job is on screen.
DURING_A_JOB = ('/cancel-job', '/escape', '/redraw', '/find')
# What may be pressed while a question is on screen: its two answers.
WHILE_ASKING = ('/ask-yes', '/ask-no', '/escape', '/redraw')
# Actions that are not worth a line in the support report each time.
QUIET = ('/find', '/redraw', '/conn-edited')


@serialized
def dispatch(state: "PageState", path: str, form: dict) -> str:
    """Run a registered native-window action and return the element to focus.

    Raises KeyError for an action that does not exist.
    """
    action = ACTIONS[path]
    if state.job is not None and path not in DURING_A_JOB:
        return ''
    if state.ask is not None and path not in WHILE_ASKING:
        return ''
    # Content actions share the CLI event log. Never log connection fields.
    _runlog.set_current(state.log)
    if path in QUIET:
        return action(state, form) or ''
    if path not in KEEPS_NOTE:
        state.note = None
    _runlog.info("page.action", action=path,
                 fields={} if path.startswith('/conn') else dict(form))
    state.version += 1
    with state.log.phase("page", action=path):
        return action(state, form) or ''


def _index(step: str) -> int:
    return STEPS.index(step)


def can_go(state: "PageState", to: str) -> bool:
    """Whether the step may be entered from where things stand."""
    if to == 'open':
        return True
    if state.graph is None or to not in STEPS:
        return False
    if to == 'choose':
        return True
    if not state.picked:
        return False
    if to == 'owner':
        return bool(state.dashboards())
    if state.owner_question():
        return False
    if to == 'review':
        return True
    if state.open_issues():
        return False
    if to == 'save':
        return True
    return state.save_state == 'saved'


def _act_go(state: "PageState", form: dict) -> str:
    to = form.get('to', '')
    state.undo = None
    if to == 'open':
        state.screen, state.open_error, state.want = 'open', None, ''
        return ''
    if to == 'connect':
        state.screen = 'connect'
        return 'c-addr'
    if not can_go(state, to):
        return ''
    state.screen = to
    state.max_step = max(state.max_step, _index(to))
    if to == 'review':
        # Arrive on whatever is waiting for an answer, not on what was last read.
        waiting = state.open_issues()
        if waiting:
            state.issue = waiting[0].id
    return ''


def _act_open(state: "PageState", form: dict) -> str:
    state.open_export(form.get("zip", "").strip())
    return ''


def _act_pick_export(state: "PageState", _form: dict) -> str:
    """Open the machine's own file window and read whatever comes back.

    This goes through the action table like every other button rather than
    being a special case in the window's bridge, so it is logged the same way
    and there is still exactly one place a page action runs.
    """
    if state.file_picker is None:
        state.typed_path = True
        return 'zip'
    try:
        chosen = state.file_picker()
    except Exception:  # noqa: BLE001 - a broken file window must not take the page down
        state.typed_path = True
        return 'zip'
    # A file dialog hands back a sequence even when it was told to allow one
    # file, and an unwrapped tuple reaching open_export raises a TypeError.
    if isinstance(chosen, (list, tuple)):
        chosen = chosen[0] if chosen else None
    if not chosen:
        # Cancelling a file window is not an error and must not read as one.
        return ''
    if not isinstance(chosen, str):
        state.typed_path = True
        return 'zip'
    state.open_export(chosen)
    return ''


def _act_cancel_job(state: "PageState", _form: dict) -> str:
    state.cancel_job()
    return ''


def _act_ask(answer: bool):
    def act(state: "PageState", _form: dict) -> str:
        ask, state.ask = state.ask, None
        if not ask:
            return ''
        if ask['kind'] == 'clear':
            if answer:
                state.clear()
            return ''
        new = ask['new']
        if answer:
            state._adopt(new, keep=True)
            return ''
        if new.snapshot is not None and new.snapshot is not state.source_snapshot:
            new.snapshot.close()
        state.screen = 'choose'
        return ''
    return act


def _act_filter(state: "PageState", form: dict) -> str:
    kind = form.get('f', 'all')
    allowed = {'all', 'in', 'unf'}
    if state.graph:
        allowed.update(state.graph.counts())
    if kind not in allowed or state.graph is None:
        return ''
    state.filter_kind = kind
    if kind == 'unf':
        state.filter_text = ''
    elif 'q' in form:
        state.filter_text = form['q'].strip()
    state.screen = 'choose'
    state.max_step = max(state.max_step, 1)
    return ''


def _act_find(state: "PageState", form: dict) -> str:
    """The search box, as it is typed in. Only the list is drawn again."""
    state.filter_text = (form.get('q') or '').strip()
    return ''


def _act_show_all(state: "PageState", _form: dict) -> str:
    state.filter_kind, state.filter_text = 'all', ''
    return ''


def _act_focus(state: "PageState", form: dict) -> str:
    key = form.get('key', '')
    if state.graph is None or key not in state.graph.nodes:
        return ''
    if 'q' in form:
        state.filter_text = form['q'].strip()
    state.focus_key = key
    state.screen = 'choose'
    if form.get('reveal'):
        if not any(n.key == key for n in state.visible_nodes()[:LIST_LIMIT]):
            state.filter_kind, state.filter_text = state.graph.nodes[key].kind, ''
        if not any(n.key == key for n in state.visible_nodes()[:LIST_LIMIT]):
            # Too far down a long list to be drawn: narrow the list to it.
            state.filter_text = state.graph.nodes[key].name
        state.reveal = True
    return ''


def _act_select(state: "PageState", form: dict) -> str:
    if 'q' in form:
        state.filter_text = form['q'].strip()
    key = form.get("key", "")
    state.toggle(key, on=form.get("on", "1") == "1")
    from vcfcf_migrator import uipage
    return 'tick-' + uipage.anchor(key)


def _act_add_shown(state: "PageState", form: dict) -> str:
    if 'q' in form:
        state.filter_text = form['q'].strip()
    state.add_shown()
    return ''


def _act_clear(state: "PageState", _form: dict) -> str:
    if state.picked:
        state.ask = {'kind': 'clear'}
    return ''


def _act_undo(state: "PageState", _form: dict) -> str:
    state.undo_last()
    return ''


def _act_preview(state: "PageState", form: dict) -> str:
    key = form.get('key', '')
    if state.graph is not None and key in state.graph.nodes:
        state.preview_key = key
    return 'close-preview'


def _act_close_preview(state: "PageState", _form: dict) -> str:
    state.preview_key = ''
    return ''


def _act_owner(state: "PageState", form: dict) -> str:
    # The window offers the built-in admin user or the same user as on the
    # source. Giving every dashboard to one named user is still something the
    # program can do (``--target-owner`` on the command line); the window does
    # not send it.
    state.set_owner(form.get('mode', ''), both=form.get('both') == '1')
    return 'own-' + form.get('mode', '')


def _act_keep_copy(state: "PageState", form: dict) -> str:
    state.keep_copy(form.get('key', ''))
    return ''


def _act_issue(state: "PageState", form: dict) -> str:
    state.issue = form.get('id', '')
    return 'issue-' + state.issue


def _act_leave(state: "PageState", form: dict) -> str:
    state.leave_out(form.get('key', ''))
    return ''


def _act_keep_in(state: "PageState", form: dict) -> str:
    state.keep_in(form.get('id', ''))
    return ''


def _act_fuller(state: "PageState", form: dict) -> str:
    state.want = form.get('want', '')
    state.screen, state.open_error = 'open', None
    return ''


def _act_pick_save(state: "PageState", _form: dict) -> str:
    state.choose_save_place()
    return ''


def _act_save(state: "PageState", form: dict) -> str:
    state.save(force=form.get('force') == '1')
    return ''


def _act_doc(state: "PageState", form: dict) -> str:
    if form.get('k') in ('report', 'checklist') and state.save_state == 'saved':
        state.doc = form['k']
    return 'close-doc'


def _act_close_doc(state: "PageState", _form: dict) -> str:
    state.doc = ''
    return ''


def _act_show_in_folder(state: "PageState", form: dict) -> str:
    path = state.report_saved if form.get('which') == 'report' else state.built_path
    if path and state.show_in_folder and not state.show_in_folder(path):
        state.note = Note('bad', f'The folder could not be opened. The file is at {e(path)}.')
    return ''


def _act_task(state: "PageState", form: dict) -> str:
    state.task = form.get('id', '')
    return ''


def _act_tick(state: "PageState", form: dict) -> str:
    ident = form.get('id', '')
    tasks = state.tasks()
    if not any(t.id == ident for t in tasks):
        return ''
    state.ticks[ident] = form.get('on', '1') == '1'
    state.task = ident
    if state.ticks[ident]:
        following = next((t for t in tasks if not state.ticks.get(t.id)), None)
        if following:
            state.task = following.id
    return ''


def _act_finish(state: "PageState", form: dict) -> str:
    state.finished = form.get('on', '1') == '1'
    return ''


def _act_again(state: "PageState", _form: dict) -> str:
    """Another bundle from the export that is already open."""
    if state.graph is None:
        return ''
    state._set_picked([])
    state.decided, state.ticks, state.finished = {}, {}, False
    state.save_state, state.stale, state.built_path = 'ready', False, ''
    state.screen, state.max_step = 'choose', 1
    state.focus_key, state.filter_kind, state.filter_text = '', 'all', ''
    return ''


def _act_help(state: "PageState", _form: dict) -> str:
    if state.screen != 'help':
        state.back = state.screen
        # What the last action said belongs to the screen it was said on.
        state.parked_note, state.note = state.note, None
    state.screen = 'help'
    return ''


def _act_back(state: "PageState", _form: dict) -> str:
    state.screen, state.gloss = state.back, False
    state.note, state.parked_note = state.parked_note, None
    return ''


def _act_gloss(state: "PageState", _form: dict) -> str:
    state.gloss = not state.gloss
    return 'close-gloss' if state.gloss else ''


def _act_support_report(state: "PageState", _form: dict) -> str:
    state.save_support_report()
    return ''


def _act_report_problem(state: "PageState", _form: dict) -> str:
    # Never a URL from the page: the address is fixed here.
    if not (state.open_url and state.open_url(ISSUES_PAGE)):
        state.note = Note('info', f'Your browser could not be opened. The page is {e(ISSUES_PAGE)}')
    return ''


def _act_get_update(state: "PageState", _form: dict) -> str:
    from vcfcf_migrator import releases
    if not (state.open_url and state.open_url(releases.RELEASE_PAGE)):
        state.note = Note('info', f'Your browser could not be opened. The page is {e(releases.RELEASE_PAGE)}')
    return ''


def _act_theme(state: "PageState", form: dict) -> str:
    # The choice travels as "choice", never as a data-theme attribute: the
    # page's colours are keyed on that attribute, and a control carrying it
    # would be drawn in the theme it offers.
    state.save_setting({'theme': form.get('choice', '')})
    return 'theme-' + state.theme


def _act_log(state: "PageState", form: dict) -> str:
    state.save_setting({'log_on': form.get('on', '0')})
    return 'log-on'


def _cert_address(state: "PageState", form: dict) -> str:
    """The server a certificate choice is being made for. The buttons sit in
    the sign-in form, so everything typed there arrives with them; only the
    address is read, and the rest is dropped at once."""
    address = state._server(form.get('address', ''))
    form.clear()
    return address


def _act_conn_cert_file(state: "PageState", form: dict) -> str:
    address = _cert_address(state, form)
    chosen = state.dialog('ca', '') if state.dialog else {}
    if chosen and chosen.get('path'):
        state.conn['cert'] = {'mode': 'file', 'path': chosen['path'], 'address': address}
        state.conn['tested'] = None
    return ''


def _act_conn_cert_skip(state: "PageState", form: dict) -> str:
    state.conn['cert'] = {'mode': 'skip', 'address': _cert_address(state, form)}
    state.conn['tested'] = None
    return ''


def _act_conn_cert_undo(state: "PageState", _form: dict) -> str:
    state.conn['cert'], state.conn['tested'] = None, None
    return ''


def _act_conn_edited(state: "PageState", form: dict) -> str:
    """The form changed, so an earlier test no longer says anything about it,
    and a certificate choice made for another address no longer applies."""
    state.conn['tested'], state.conn['error'] = None, None
    if 'address' in form:
        state.certificate_choice(form.get('address', ''))
    form.clear()
    return ''


def _act_escape(state: "PageState", _form: dict) -> str:
    """Escape closes whatever is on top, and nothing else."""
    if state.ask:
        return _act_ask(False)(state, {})
    if state.preview_key:
        state.preview_key = ''
    elif state.doc:
        state.doc = ''
    elif state.gloss:
        state.gloss = False
    return ''


def _act_redraw(_state: "PageState", _form: dict) -> str:
    return ''


ACTIONS = {
    '/go': _act_go,
    '/open': _act_open,
    '/pick-export': _act_pick_export,
    '/cancel-job': _act_cancel_job,
    '/ask-yes': _act_ask(True),
    '/ask-no': _act_ask(False),
    '/connect': lambda state, form: state.connect_source(form),
    '/conn-cert-file': _act_conn_cert_file,
    '/conn-cert-skip': _act_conn_cert_skip,
    '/conn-cert-undo': _act_conn_cert_undo,
    '/conn-edited': _act_conn_edited,
    '/filter': _act_filter,
    '/find': _act_find,
    '/show-all': _act_show_all,
    '/focus': _act_focus,
    '/select': _act_select,
    '/add-shown': _act_add_shown,
    '/clear': _act_clear,
    '/undo': _act_undo,
    '/preview': _act_preview,
    '/close-preview': _act_close_preview,
    '/owner': _act_owner,
    '/keep-copy': _act_keep_copy,
    '/issue': _act_issue,
    '/leave': _act_leave,
    '/keep-in': _act_keep_in,
    '/fuller': _act_fuller,
    '/pick-save': _act_pick_save,
    '/save': _act_save,
    '/doc': _act_doc,
    '/close-doc': _act_close_doc,
    '/show-in-folder': _act_show_in_folder,
    '/task': _act_task,
    '/tick': _act_tick,
    '/finish': _act_finish,
    '/again': _act_again,
    '/help': _act_help,
    '/back': _act_back,
    '/gloss': _act_gloss,
    '/support-report': _act_support_report,
    '/report-problem': _act_report_problem,
    '/get-update': _act_get_update,
    '/theme': _act_theme,
    '/log': _act_log,
    '/escape': _act_escape,
    '/redraw': _act_redraw,
}
POST_PATHS = tuple(sorted(ACTIONS))
# Actions that leave the last message where it is: looking at something is not
# a reason to lose what the previous action said.
KEEPS_NOTE = ('/focus', '/find', '/redraw', '/preview', '/close-preview', '/issue', '/task', '/tick',
              '/doc', '/close-doc', '/gloss', '/escape', '/help', '/back', '/conn-edited',
              '/show-in-folder', '/theme', '/log', '/support-report')


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
