"""The run log: one line per event, machine readable, with a human rendering.

Scott set the bar for v0.1.0: "very robust logs which when paired with an
input and output bundle and pointers from ops tell a whole story, but the
logs itself and the ops error(s) is enough to diagnose. No secrets not
confidential data but solid verbose logs, we can dial it back around 0.3 or
0.4". So a support case is a log plus whatever VCF Operations said, and that
pair has to be enough to say what the tool did and why. The bundles and the
source export make the story complete; they are not required to reach a
diagnosis.

**What goes in.** Content identity: kind, uuid and name for every object, plus
metric and property keys, member names, counts, timings and every decision
with the object it concerns and the reason for it.

**What never goes in, and this is the harder half.** Credentials of any kind,
the export's encryption password and the encrypted values it carries; people,
meaning user names, display names, mail addresses and user or owner uuids; and
metric or mock values. A dashboard's owner appears as a stable per-run
pseudonym, ``owner-1``, ``owner-2``, so multi-owner behaviour stays legible.

**The exclusion list is enforced here, not at the call sites.** A rule a
caller can forget is not a rule, and there are several hundred call sites. So
every event goes through ``Redactor`` on its way to the stream, and three
things happen to it:

1. a field whose *key* names a credential, a person or a metric value is
   replaced by an exclusion marker whatever its content is;
2. every string is scanned for values the redactor has been taught are people
   (harvested from the export's own marker, owner member names, usermappings
   and users documents), and each is replaced by its pseudonym or by an
   exclusion marker;
3. every uuid-shaped token that has not been declared a *content* identifier
   is replaced by ``[excluded:id]``. Identifiers reach the log only through
   the reader and the graph, which know they came from a content document, so
   a caller who logs a user uuid by accident writes an exclusion marker
   rather than the uuid.

The argument vector is the one place the tool teaches the redactor from the
command line: ``preview <zip> dashboard:<uuid>@<owner>`` carries an owner uuid
the admin typed, so the ``@owner`` half is registered as a person (and prints
as ``owner-1``) and the identifier half as content, before the header is
written.

File paths are logged as the admin typed them. They are not export content,
the tool cannot do anything without them, and the contents line says they are
there so an admin can decide before sending the file.

**The format.** One JSON object per line (``.jsonl``): greppable, machine
readable, appendable and safe to truncate. ``--log-format text`` writes the
human rendering instead, and ``vcfcf-migrator log-render FILE`` turns a
captured jsonl log into that same text, so nothing has to be logged twice to
be readable.

**Levels.** ``error`` the failure that ended the command, ``warn`` every
refusal and every swallowed failure, ``info`` the run header, the per-phase
counts and timings and the fingerprints, ``detail`` every decision with its
object and reason, ``debug`` the per-reference and per-widget noise behind
those decisions. The default when a log is asked for is ``detail``, which is
deliberately verbose for the early releases.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, TextIO

LEVELS: Dict[str, int] = {"error": 40, "warn": 30, "info": 20, "detail": 10, "debug": 5}
LEVEL_NAMES = ("error", "warn", "info", "detail", "debug")
DEFAULT_LEVEL = "detail"
FORMATS = ("jsonl", "text")
DEFAULT_FORMAT = "jsonl"

ENV_LOG = "VCFCF_MIGRATOR_LOG"
ENV_LOG_LEVEL = "VCFCF_MIGRATOR_LOG_LEVEL"
ENV_LOG_FORMAT = "VCFCF_MIGRATOR_LOG_FORMAT"

# The one line at the top of every log saying what classes of thing are in it,
# so an admin can decide before sending it on.
CONTENTS = (
    "this log carries content identity (kind, uuid, name, metric and property keys), "
    "export member names, counts, timings and the reason for every decision, plus the "
    "file paths you gave the tool; it carries no credentials, no export password, no "
    "encrypted values, no user names, display names, mail addresses or user and owner "
    "uuids (owners appear as owner-1, owner-2, stable within this run), and no metric "
    "or mock values"
)

EXCLUDED_CREDENTIAL = "[excluded:credential]"
EXCLUDED_PERSON = "[excluded:person]"
EXCLUDED_MAIL = "[excluded:mail]"
EXCLUDED_VALUE = "[excluded:metric value]"
EXCLUDED_ID = "[excluded:id]"

# Keys whose value is excluded whatever it holds. Matched as a substring of
# the lowercased key, so "servicePassword" and "pluginAuthToken" are both
# caught without anyone enumerating them.
SECRET_KEY_RE = re.compile(
    r"pass|pwd|secret|credential|token|cipher|encrypt|auth|apikey|api_key|"
    r"privatekey|private_key|signature|salt|keystore|certificate", re.I)
PERSON_KEY_RE = re.compile(
    r"user|account|login|mail|displayname|display_name|fullname|full_name|"
    r"firstname|first_name|lastname|last_name|givenname|given_name|surname|principal",
    re.I)
# An owner key is a person key whose value is pseudonymised rather than
# dropped: multi-owner behaviour has to stay legible.
OWNER_KEY_RE = re.compile(r"^owners?$|_owners?$|^owner_", re.I)
VALUE_KEY_RE = re.compile(
    r"^(value|values|sample|samples|series|datapoint|datapoints|mock|mocks|"
    r"reading|readings|observation|observations)$", re.I)

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_MAIL_RE = re.compile(r"[\w.%+-]+@[\w-]+\.[\w.-]*[A-Za-z]{2,}")
# A person value shorter than this is not replaced by substring: "admin" inside
# "Administrative Overview" would mangle the admin's own content, and a name
# that short carries nothing anyway. Uuids are matched whatever their length.
_MIN_PERSON_LEN = 4


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

class Redactor:
    """What the log is allowed to say, and what it turns everything else into.

    It is taught three things as a run goes on: *owners* (pseudonymised),
    *people* (excluded outright) and *content identifiers* (allowed through).
    Nothing else that is uuid-shaped survives. Teaching happens in the reader
    and the graph, where the tool knows which document a value came out of.
    """

    def __init__(self) -> None:
        self._owners: Dict[str, str] = {}      # value -> owner-N
        self._people: List[str] = []           # values excluded outright
        self._allowed: set = set()             # lowercased content identifiers
        self._pattern: Optional[re.Pattern] = None
        self._replacements: Dict[str, str] = {}

    # -- teaching ----------------------------------------------------------

    def owner(self, value) -> str:
        """The stable per-run pseudonym for one owner, assigned in first-seen
        order. There is no table on disk: the mapping lives for the length of
        the run and dies with the process, which is what keeps it from being a
        leak of its own."""
        text = str(value or "").strip()
        if not text:
            return ""
        if text not in self._owners:
            self._owners[text] = f"owner-{len(self._owners) + 1}"
            self._pattern = None
        return self._owners[text]

    def person(self, value) -> None:
        """Teach one value that is a person and is not an owner: a user name,
        a display name, a mail address."""
        text = str(value or "").strip()
        if not text or text in self._people or text in self._owners:
            return
        if len(text) < _MIN_PERSON_LEN and not _UUID_RE.fullmatch(text):
            return
        self._people.append(text)
        self._pattern = None

    def content_id(self, value) -> None:
        """Teach one identifier that came out of a content document, so it is
        allowed to appear. Anything uuid-shaped that was never taught here is
        excluded, which is what makes an accidental user uuid impossible."""
        text = str(value or "").strip().lower()
        if not text:
            return
        self._allowed.add(text)
        # An export writes several identifiers with the uuid inside a longer
        # string ("AlertDefinition-<uuid>", "Super Metric|sm_<uuid>"). The uuid
        # in them came out of the same content document, so it is allowed too;
        # without this the log printed AlertDefinition-[excluded:id].
        for token in _UUID_RE.findall(text):
            self._allowed.add(token.lower())

    def content_ids(self, values: Iterable) -> None:
        for value in values:
            self.content_id(value)

    def learn_argv(self, argv: Sequence[str]) -> None:
        """The one teaching pass driven by the command line.

        ``dashboard:<uuid>@<owner>`` is how the admin names one owner's copy
        of a dashboard, so the owner half is a person the admin typed and the
        identifier half is content. Without this the run header could not
        print the argument vector at all.
        """
        for arg in argv or ():
            text = str(arg)
            head, sep, tail = text.rpartition("@")
            if sep and _UUID_RE.fullmatch(tail.strip()):
                self.owner(tail.strip())
                text = head
            for token in _UUID_RE.findall(text):
                self.content_id(token)

    # -- applying ----------------------------------------------------------

    def _compiled(self):
        if self._pattern is None:
            self._replacements = {}
            for value, pseudonym in self._owners.items():
                self._replacements[value] = pseudonym
            for value in self._people:
                self._replacements[value] = EXCLUDED_PERSON
            if self._replacements:
                # Longest first, so a value that contains another is replaced
                # whole rather than half.
                parts = sorted(self._replacements, key=len, reverse=True)
                self._pattern = re.compile("|".join(re.escape(p) for p in parts))
            else:
                self._pattern = re.compile(r"(?!x)x")  # matches nothing
        return self._pattern

    def text(self, value: str) -> str:
        """One string, with everything excluded taken out of it."""
        out = self._compiled().sub(lambda m: self._replacements[m.group(0)], value)
        out = _MAIL_RE.sub(EXCLUDED_MAIL, out)
        return _UUID_RE.sub(
            lambda m: m.group(0) if m.group(0).lower() in self._allowed else EXCLUDED_ID,
            out)

    def field(self, key: str, value):
        """One event field, keyed, which is where the key rules apply."""
        name = str(key)
        if SECRET_KEY_RE.search(name):
            # A number under such a key is a count, not a credential: an
            # export's manifest counts its auth sources, and excluding the
            # count says nothing about a secret and loses a fact.
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            return EXCLUDED_CREDENTIAL
        if VALUE_KEY_RE.match(name):
            return EXCLUDED_VALUE
        if OWNER_KEY_RE.search(name):
            return self._owner_field(value)
        if PERSON_KEY_RE.search(name):
            return self._person_field(value)
        return self.value(value)

    def _owner_field(self, value):
        if isinstance(value, str):
            return self.owner(value)
        if isinstance(value, (list, tuple)):
            return [self._owner_field(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self._owner_field(v) for k, v in value.items()}
        return value  # a count of owners is a number, and a number is not a person

    def _person_field(self, value):
        if isinstance(value, str):
            return self._owners.get(value.strip(), EXCLUDED_PERSON)
        if isinstance(value, (list, tuple)):
            return [self._person_field(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self._person_field(v) for k, v in value.items()}
        return value

    def value(self, value):
        """Any value, scanned rather than keyed: strings inside lists and
        dicts get the same treatment as a string at the top."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, (list, tuple)):
            return [self.value(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self.field(str(k), v) for k, v in value.items()}
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return self.text(str(value))

    def owners_seen(self) -> int:
        return len(self._owners)


# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------

@dataclass
class _Phase:
    name: str
    started: float
    counts: Dict[str, int] = field(default_factory=dict)


class Log:
    """One run's log. A ``Log`` with no stream is a working no-op, so call
    sites never test whether logging is on."""

    def __init__(self, stream: Optional[TextIO] = None, level: str = DEFAULT_LEVEL,
                 fmt: str = DEFAULT_FORMAT, redactor: Optional[Redactor] = None,
                 path: Optional[str] = None, clock=time.monotonic) -> None:
        self.stream = stream
        self.level = level if level in LEVELS else DEFAULT_LEVEL
        self.threshold = LEVELS[self.level]
        self.fmt = fmt if fmt in FORMATS else DEFAULT_FORMAT
        self.redactor = redactor or Redactor()
        self.path = path
        self._clock = clock
        self._t0 = clock()
        self._phases: List[_Phase] = []
        self._written = 0
        self._by_level: Dict[str, int] = {}
        # Set to a list to keep events in memory as well as on the stream:
        # the page's diagnostics action needs them without re-reading a file
        # it may not have been asked to write. Capped, because the page is a
        # long-lived process and a select-all over a large export is tens of
        # thousands of events; the oldest go first and the drop is recorded.
        self.events: Optional[List[dict]] = None
        self.event_cap = 40000
        self.dropped = 0

    # -- state -------------------------------------------------------------

    @property
    def on(self) -> bool:
        return self.stream is not None or self.events is not None

    def enabled(self, level: str) -> bool:
        return self.on and LEVELS.get(level, 0) >= self.threshold

    def written(self) -> int:
        return self._written

    def counts_by_level(self) -> Dict[str, int]:
        return dict(self._by_level)

    # -- emitting ----------------------------------------------------------

    def emit(self, level: str, code: str, /, **fields) -> None:
        # Positional-only, so a call site may log a field called ``level``,
        # ``code`` or ``name`` without colliding with the method's own
        # parameters. A logging call that raises is worse than a missing line.
        if not self.enabled(level):
            return
        event = {
            "t": round(self._clock() - self._t0, 4),
            "lvl": level,
            "phase": self._phases[-1].name if self._phases else "run",
            "event": str(code),
        }
        for key, value in fields.items():
            if value is None:
                continue
            event[str(key)] = self.redactor.field(str(key), value)
        self._written += 1
        self._by_level[level] = self._by_level.get(level, 0) + 1
        if self.events is not None:
            self.events.append(event)
            while len(self.events) > self.event_cap:
                self.events.pop(0)
                self.dropped += 1
        if self.stream is None:
            return
        line = (render_event(event) if self.fmt == "text"
                else json.dumps(event, ensure_ascii=False))
        self.stream.write(line + "\n")

    def error(self, code: str, /, **fields) -> None:
        self.emit("error", code, **fields)

    def warn(self, code: str, /, **fields) -> None:
        self.emit("warn", code, **fields)

    def info(self, code: str, /, **fields) -> None:
        self.emit("info", code, **fields)

    def detail(self, code: str, /, **fields) -> None:
        self.emit("detail", code, **fields)

    def debug(self, code: str, /, **fields) -> None:
        self.emit("debug", code, **fields)

    # -- phases ------------------------------------------------------------

    @contextmanager
    def phase(self, name: str, /, **fields):
        """A phase, with its counts and how long it took.

        The counts are collected on the phase itself rather than by the
        caller, so "a slow or wrong run can be located without a rerun" does
        not depend on anyone remembering to add up.
        """
        phase = _Phase(name=name, started=self._clock())
        self._phases.append(phase)
        self.info("phase.start", **fields)
        failed = None
        try:
            yield phase
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            failed = exc
            raise
        finally:
            elapsed = round((self._clock() - phase.started) * 1000, 1)
            self.emit("error" if failed is not None else "info", "phase.end",
                      ms=elapsed,
                      failed=(type(failed).__name__ if failed is not None else None),
                      **{k: v for k, v in phase.counts.items()})
            self._phases.pop()

    def count(self, key: str, by: int = 1) -> None:
        """Add to the current phase's counter, if there is one."""
        if self._phases:
            counts = self._phases[-1].counts
            counts[key] = counts.get(key, 0) + by

    # -- the run header ----------------------------------------------------

    def header(self, argv: Sequence[str], tool_version: str, core_version: str,
               source_version=None, source_version_from: str = "",
               corpus_dir=None, corpus_from: str = "") -> None:
        """Everything about this run that does not come from the export.

        Written before anything is read, because a run that cannot read its
        input still has to say what it was asked to do.
        """
        if not self.on:
            return
        self.redactor.learn_argv(argv)
        self.info("log.contents", says=CONTENTS, level=self.level, format=self.fmt,
                  file=self.path)
        self.info("run.start",
                  started=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  tool=tool_version, core=core_version,
                  python=platform.python_version(),
                  python_build=sys.version.split()[0] + (" (frozen)" if getattr(sys, "frozen", False) else ""),
                  platform=platform.platform(),
                  argv=list(argv),
                  cwd=str(Path.cwd()),
                  source_version=source_version,
                  source_version_from=source_version_from or None,
                  corpus_dir=str(corpus_dir) if corpus_dir else None,
                  corpus_from=corpus_from or None)

    def finish(self, code: int, what: str = "") -> None:
        self.info("run.end", exit=code, what=what or None,
                  events=self._written, by_level=self.counts_by_level(),
                  owners_seen=self.redactor.owners_seen())

    def close(self) -> None:
        if self.stream is not None and self.stream not in (sys.stdout, sys.stderr):
            try:
                self.stream.close()
            except OSError:
                pass
        self.stream = None


NULL = Log()

# The current run's log. A module global rather than a parameter threaded
# through every signature: the reference walk, the container rebuild and the
# widget classification are five and six calls deep, and a parameter that deep
# is a parameter somebody forgets. The page's server is threaded and shares
# one page state, so one global is also what the page wants.
_CURRENT: Log = NULL


def current() -> Log:
    return _CURRENT


def set_current(log: Optional[Log]) -> Log:
    global _CURRENT
    previous = _CURRENT
    _CURRENT = log or NULL
    return previous


def emit(level: str, code: str, /, **fields) -> None:
    _CURRENT.emit(level, code, **fields)


def error(code: str, /, **fields) -> None:
    _CURRENT.emit("error", code, **fields)


def warn(code: str, /, **fields) -> None:
    _CURRENT.emit("warn", code, **fields)


def info(code: str, /, **fields) -> None:
    _CURRENT.emit("info", code, **fields)


def detail(code: str, /, **fields) -> None:
    _CURRENT.emit("detail", code, **fields)


def debug(code: str, /, **fields) -> None:
    _CURRENT.emit("debug", code, **fields)


def count(key: str, by: int = 1) -> None:
    _CURRENT.count(key, by)


def phase(name: str, /, **fields):
    return _CURRENT.phase(name, **fields)


def owner(value) -> str:
    """The current run's pseudonym for one owner, for a caller that needs the
    pseudonym in a string of its own."""
    return _CURRENT.redactor.owner(value)


def content_id(value) -> None:
    _CURRENT.redactor.content_id(value)


def content_ids(values: Iterable) -> None:
    _CURRENT.redactor.content_ids(values)


def person(value) -> None:
    _CURRENT.redactor.person(value)


# ---------------------------------------------------------------------------
# Opening one
# ---------------------------------------------------------------------------

def resolve_level(cli_value: Optional[str] = None):
    """Level, and where it came from: flag, environment, settings, default."""
    from vcfcf_migrator import settings as _settings

    if cli_value:
        return str(cli_value).strip().lower(), "command line"
    env = os.environ.get(ENV_LOG_LEVEL)
    if env:
        return env.strip().lower(), f"environment ({ENV_LOG_LEVEL})"
    saved = _settings.load_settings().get("log_level")
    if saved:
        return str(saved).strip().lower(), "settings file"
    return DEFAULT_LEVEL, "default"


def resolve_destination(cli_value: Optional[str] = None):
    """Log file, and where it came from; ``(None, ...)`` when nothing asks for
    one, which is the default: a log is written when it is asked for."""
    from vcfcf_migrator import settings as _settings

    if cli_value:
        return str(cli_value), "command line"
    env = os.environ.get(ENV_LOG)
    if env:
        return env, f"environment ({ENV_LOG})"
    saved = _settings.load_settings().get("log_file")
    if saved:
        return str(saved), "settings file"
    return None, "not set"


def resolve_format(cli_value: Optional[str] = None):
    from vcfcf_migrator import settings as _settings

    if cli_value:
        return str(cli_value).strip().lower(), "command line"
    env = os.environ.get(ENV_LOG_FORMAT)
    if env:
        return env.strip().lower(), f"environment ({ENV_LOG_FORMAT})"
    saved = _settings.load_settings().get("log_format")
    if saved:
        return str(saved).strip().lower(), "settings file"
    return DEFAULT_FORMAT, "default"


class BadLogSetting(ValueError):
    """A level or a format that is not one of the known ones."""


def open_log(destination: Optional[str], level: str = DEFAULT_LEVEL,
             fmt: str = DEFAULT_FORMAT, keep_events: bool = False) -> Log:
    """Open the log for one run. ``-`` writes to stderr, so a run can be
    watched without a file; anything else is a path, appended to."""
    if level not in LEVELS:
        raise BadLogSetting(
            f"log level {level!r} is not one of {', '.join(LEVEL_NAMES)}")
    if fmt not in FORMATS:
        raise BadLogSetting(f"log format {fmt!r} is not one of {', '.join(FORMATS)}")
    stream: Optional[TextIO] = None
    path = None
    if destination == "-":
        stream, path = sys.stderr, "-"
    elif destination:
        target = Path(destination)
        if target.parent and str(target.parent):
            target.parent.mkdir(parents=True, exist_ok=True)
        stream = open(target, "a", encoding="utf-8")
        path = str(target)
    log = Log(stream=stream, level=level, fmt=fmt, path=path)
    if keep_events:
        log.events = []
    return log


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def input_fingerprint(path, data: bytes, member_names: Sequence[str],
                      manifest: Optional[dict] = None) -> dict:
    """Enough to tell two exports apart, and to identify one later, without
    shipping it: size, hash, member count, member names and manifest counts.

    Member names carry the owner uuid on a real export
    (``dashboards/<owner>``); the redactor turns those into the run's
    pseudonyms on the way out, so the shape of the export stays readable and
    the owner does not appear.
    """
    manifest_counts = {}
    for key, value in (manifest or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            manifest_counts[str(key)] = value
        elif isinstance(value, list):
            manifest_counts[str(key)] = len(value)
        elif isinstance(value, str):
            manifest_counts[str(key)] = value
    return {
        "path": str(path),
        "bytes": len(data),
        "sha256": sha256(data),
        "members": len(member_names),
        "member_names": list(member_names),
        "manifest": manifest_counts,
    }


def output_fingerprint(written: Dict[str, bytes], order: Sequence[str]) -> dict:
    """What the build actually wrote: every member, its size and its hash, so
    "what did it put in the bundle" is answerable from the log alone."""
    return {
        "members": len(order),
        "bytes": sum(len(written[name]) for name in order if name in written),
        "files": [{"member": name, "bytes": len(written[name]),
                   "sha256": sha256(written[name])}
                  for name in order if name in written],
    }


# ---------------------------------------------------------------------------
# The human rendering
# ---------------------------------------------------------------------------

def render_event(event: dict) -> str:
    """One event as a line a person reads."""
    head = (f"{event.get('t', 0):9.3f} {str(event.get('lvl', '')):6s} "
            f"{str(event.get('phase', '')):9s} {event.get('event', '')}")
    rest = []
    for key, value in event.items():
        if key in ("t", "lvl", "phase", "event"):
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=isinstance(value, dict))
        rest.append(f"{key}={value}")
    return head + ("  " + " ".join(rest) if rest else "")


def render_log(lines: Iterable[str]) -> str:
    """A captured jsonl log as text. A line that is not JSON is passed
    through rather than dropped: a truncated log is still a log."""
    out = []
    for line in lines:
        text = line.rstrip("\n")
        if not text:
            continue
        try:
            event = json.loads(text)
        except ValueError:
            out.append(text)
            continue
        out.append(render_event(event) if isinstance(event, dict) else text)
    return "\n".join(out) + ("\n" if out else "")


# ---------------------------------------------------------------------------
# Diagnostics: one file, ready to attach to a mail
# ---------------------------------------------------------------------------

DIAGNOSTICS_CONTENTS = (
    "the run header, the input export's fingerprint, every log event and the bundle's "
    "manifest: content names, uuids and metric keys, no people and no credentials"
)


def diagnostics_document(events: Sequence[dict], header: Optional[dict] = None,
                         source: Optional[dict] = None,
                         bundle: Optional[dict] = None) -> str:
    """The diagnostics file: one head line saying what it is and what is in it,
    then the events, one JSON object per line.

    One file rather than an archive, on purpose. It goes on a mail, and a mail
    gateway that strips a zip does not strip a text attachment; an admin can
    read it before sending it without unpacking anything; and the same reader
    renders it, so nothing has to be learned to open it.
    """
    head = {
        "kind": "vcfcf-migrator-diagnostics",
        "version": 1,
        "written": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "contains": DIAGNOSTICS_CONTENTS,
        "contents_line": CONTENTS,
        "events": len(events),
    }
    if header:
        head["run"] = header
    if source:
        head["source"] = source
    if bundle:
        head["bundle"] = bundle
    lines = [json.dumps(head, ensure_ascii=False)]
    lines += [json.dumps(event, ensure_ascii=False) for event in events]
    return "\n".join(lines) + "\n"
