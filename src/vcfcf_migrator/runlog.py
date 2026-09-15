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
from collections import deque
from typing import Deque, Dict, Iterable, List, Optional, Sequence, TextIO

LEVELS: Dict[str, int] = {"error": 40, "warn": 30, "info": 20, "detail": 10, "debug": 5}
LEVEL_NAMES = ("error", "warn", "info", "detail", "debug")
DEFAULT_LEVEL = "detail"
FORMATS = ("jsonl", "text")
DEFAULT_FORMAT = "jsonl"

ENV_LOG = "VCFCF_MIGRATOR_LOG"
ENV_LOG_LEVEL = "VCFCF_MIGRATOR_LOG_LEVEL"
ENV_LOG_FORMAT = "VCFCF_MIGRATOR_LOG_FORMAT"

class Prose(str):
    """A sentence this module wrote, marked as such by the call site.

    It is scanned for uuids and mail addresses like everything else, and never
    for person values, because a literal in this package carries nobody's name.

    **Why a type and not a set of key names.** The first version of this
    exemption was ``{"reason", "says", "detail"}``, which fourteen call sites
    reuse for ``str(e)``: the exemption then covered arbitrary exception text,
    and an exception whose message was a person's name went verbatim into the
    log and into the diagnostics file. Deleting the exemption closed that hole
    and opened a worse one: the reader harvests the word ``member`` as a person
    out of an ``authsources.json`` LDAP mapping, and one ``corpus-check`` at
    debug then wrote 282 mangled sentences, including "policies.xml is in the
    export but is a [excluded:person] this tool never carries into a bundle".
    An admin asking why a member did not come across was told about somebody's
    name. So the exemption is a property of the value: a literal this package
    wrote is ``prose``, a value computed from anything else is data and is
    scanned, and the two can no longer be confused by a keyword name.

    ``COMMON_WORDS`` and the left boundary do not substitute for this. They are
    a heuristic over a person table this tool fills from whatever an instance's
    identity configuration happens to contain, and that table overlaps this
    package's own vocabulary.
    """

    __slots__ = ()


def prose(text) -> Prose:
    """Mark a sentence this package wrote. See ``Prose``."""
    return text if isinstance(text, Prose) else Prose("" if text is None else str(text))


# The one line at the top of every log saying what classes of thing are in it,
# so an admin can decide before sending it on.
CONTENTS = prose(
    "this log carries content identity (kind, uuid, name, metric and property keys), "
    "export member names, counts, timings and the reason for every decision, plus the "
    "file paths you gave the tool, which on your machine may carry your own user name; "
    "it carries no credentials, no export password, no encrypted values, no user names, "
    "display names, mail addresses or user and owner uuids out of the export (a "
    "dashboard's owner appears as owner-1, owner-2, stable within this run, so a member "
    "named dashboards/owner-1 here is dashboards/<that uuid> in the zip you hold), and "
    "no metric or mock values"
)

# The events a diagnostics file is built from, which the in-memory buffer keeps
# whatever else it has to drop.
HEAD_EVENTS = ("log.contents", "run.start", "input.fingerprint")

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
# The keys whose value is a count even though the key names a credential class:
# an export's configuration.json says how many auth sources and how many
# certificates it holds. Named, because inferring "this is a count because it
# is a number" let a numeric credential through.
COUNT_FIELDS = frozenset({"authsources", "authsourcecount", "certificates",
                          "certificatecount", "credentials", "credentialcount",
                          "tokens", "tokencount", "keystores", "passwords"})

VALUE_KEY_RE = re.compile(
    r"^(value|values|sample|samples|series|datapoint|datapoints|mock|mocks|"
    r"reading|readings|observation|observations)$", re.I)

# Both spellings an account id is written in: the canonical hyphenated form and
# the compact 32 hex digits VCF Operations uses in several places. Matching only
# the first let a compact account uuid inside content through the allow-list
# rule on every command.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|\b[0-9a-fA-F]{32}\b")


def _spellings(value: str) -> List[str]:
    """The spellings of one person value that need their own alternative.

    ``re.I`` folds most case differences, but not the ones that change length:
    Turkish dotted capital I casefolds to two codepoints, and a document can
    carry either spelling. Each variant is its own alternative in the one
    pattern rather than a second lookup table, so there is still nothing for a
    second definition of case to disagree with.
    """
    out = [value]
    for variant in (value.casefold(), value.lower(), value.upper()):
        if variant and variant not in out:
            out.append(variant)
    return out


def _normal_id(value: str) -> str:
    """One spelling for the allow-list: lowercased, hyphens dropped, so the
    same identifier written either way is the same key."""
    return str(value).strip().lower().replace("-", "")
_MAIL_RE = re.compile(r"[\w.%+-]+@[\w-]+\.[\w.-]*[A-Za-z]{2,}")
# A person value shorter than this is not taught at all: it cannot be told from
# an ordinary word, and replacing it would mangle the admin's own content. A
# user name of three characters is therefore not excluded, which is stated in
# the spec's exclusion list rather than only here. Uuids are matched whatever
# their length.
_MIN_PERSON_LEN = 4

# Person values that are also ordinary words. An instance's built-in account is
# called "admin", and substituting it everywhere rewrote nine real content names
# on one corpus export ("... admin alert" became "... [excluded:person] alert")
# and the tool's own sentences with them. A log that cannot be paired with what
# VCF Operations says about a named object has lost the thing it exists for, so
# these are never taught. The corpus test used to keep this list; it belongs
# here, where the behaviour is.
COMMON_WORDS = frozenset({
    "admin", "administrator", "root", "system", "local", "user", "users",
    "guest", "all", "everyone", "true", "false", "none", "null", "default",
    "unknown", "public", "service", "operator", "owner", "account", "automation",
    "vcops", "vmware", "support", "test", "demo", "domain", "group", "groups",
})

# Fields holding a path the admin typed. They are logged as given, everywhere,
# because the tool cannot be diagnosed without knowing which file it was pointed
# at, and because the alternative was one event showing a path verbatim (the
# header, written before anything is harvested) and another showing the same
# path redacted. The log's contents line names them as a class it carries, and
# so does the README. The list lives here rather than in a test, so the
# exemption is the layer's and cannot drift.
PATH_FIELDS = frozenset({"path", "cwd", "argv", "out", "zip", "dir", "file",
                         "corpus_dir", "log_file"})

# The subset logged untouched. ``argv`` is not in it: an admin names one owner's
# copy of a dashboard as ``kind:uuid@owner`` on the command line, so the vector
# carries an account uuid and goes through the ordinary rules, which turn that
# half into the run's pseudonym. Everything else here is a file path and nothing
# else, and is written as given so the same path reads the same way in the
# header and in every later event.
VERBATIM_FIELDS = PATH_FIELDS - {"argv"}

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
        if text.lower() in COMMON_WORDS and not _UUID_RE.fullmatch(text):
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
        self._allowed.add(_normal_id(text))
        # An export writes several identifiers with the uuid inside a longer
        # string ("AlertDefinition-<uuid>", "Super Metric|sm_<uuid>"). The uuid
        # in them came out of the same content document, so it is allowed too;
        # without this the log printed AlertDefinition-[excluded:id].
        for token in _UUID_RE.findall(text):
            self._allowed.add(_normal_id(token))

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
        """The person pattern, case-insensitive and word-bounded.

        Case first: a name declared as "Marguerite Thornbury" is written
        "MARGUERITE THORNBURY" in one widget title and "marguerite thornbury" in
        the next, and a case-sensitive pattern let both through at the default
        level. The replacement comes from the group that matched, so there is
        no second key and nothing to disagree about: keying by the lowercased
        value is what raised KeyError on every pair ``re.I`` and ``str.lower``
        fold differently.

        Boundaries second: without them a person value is replaced inside any
        longer word, which rewrote content names and the tool's own sentences.
        A uuid keeps its own boundary rule, since it can sit inside a longer
        identifier the export wrote ("Condition_<uuid>").
        """
        if self._pattern is None:
            # **The pattern names its own replacement.** The previous version
            # matched with ``re.I`` and then looked the replacement up in a
            # dict keyed by ``str.lower()``, which are two different
            # definitions of case: they agree on ASCII and disagree on 77 pairs
            # of characters, so a widget title containing Turkish dotted I, or
            # a long s, raised KeyError inside a logging call and took the
            # command down. Two pieces of code agreeing by coincidence rather
            # than by sharing one definition is the shape this codebase keeps
            # producing, so there is now one expression: each alternative is a
            # named group, and the group that matched says what to write.
            self._replacements = {}
            values = []
            for value, pseudonym in self._owners.items():
                values.extend((spelling, pseudonym) for spelling in _spellings(value))
            for value in self._people:
                values.extend((spelling, EXCLUDED_PERSON) for spelling in _spellings(value))
            if values:
                # Longest first, so a value that contains another is replaced
                # whole rather than half.
                values.sort(key=lambda pair: len(pair[0]), reverse=True)
                pieces = []
                for index, (part, replacement) in enumerate(values):
                    group = f"p{index}"
                    self._replacements[group] = replacement
                    escaped = f"(?P<{group}>{re.escape(part)})"
                    # The boundary is on the left only. A person value that
                    # *starts* a longer token is still that person: a corpus
                    # export carries the display name "Brock" and the login
                    # built from it, and requiring a boundary on both sides let
                    # the login through. A value that merely ends inside
                    # another word is left alone, which is what stops an
                    # ordinary word being half-replaced.
                    pieces.append(escaped if _UUID_RE.fullmatch(part)
                                  else r"(?<![\w-])" + escaped)
                self._pattern = re.compile("|".join(pieces), re.I)
            else:
                self._pattern = re.compile(r"(?!x)x")  # matches nothing
        return self._pattern

    def text(self, value: str, people: bool = True) -> str:
        """One string, with everything excluded taken out of it.

        *people* is false only for a ``Prose`` value: see that class.
        """
        out = self._compiled().sub(self._replacement_for, value) if people else value
        out = _MAIL_RE.sub(EXCLUDED_MAIL, out)
        return _UUID_RE.sub(
            lambda m: m.group(0) if _normal_id(m.group(0)) in self._allowed
            else EXCLUDED_ID, out)

    def _replacement_for(self, match) -> str:
        """What the group that matched says to write. No second lookup, so
        there is nothing for a second definition of case to disagree with."""
        for group, replacement in self._replacements.items():
            if match.group(group) is not None:
                return replacement
        return EXCLUDED_PERSON

    def field(self, key: str, value):
        """One event field, keyed, which is where the key rules apply."""
        name = str(key)
        if name in VERBATIM_FIELDS:
            # Logged as given: see PATH_FIELDS and VERBATIM_FIELDS.
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value]
            return value if isinstance(value, (int, float, bool)) else str(value)
        if SECRET_KEY_RE.search(name):
            # The exception is for *named* counts, not for numbers. It used to
            # be "an int under such a key is a count", which is a hole shaped
            # like a type: ``password=1234`` and a numeric token were written
            # verbatim. An export's manifest counts its auth sources, and those
            # keys are known, so they are listed rather than inferred.
            if name.lower() in COUNT_FIELDS and isinstance(value, int) \
                    and not isinstance(value, bool):
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
            # The *keys* are the owners here: ``{owner uuid: dashboard count}``
            # is how a caller says what each owner carried, and leaving keys
            # alone wrote the uuid the field exists to hide.
            return {self.owner(k) if isinstance(k, str) and not str(k).isdigit()
                    else str(k): self.value(v) for k, v in value.items()}
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
        dicts get the same treatment as a string at the top.

        A dict's *keys* are scanned too. They were not, and one caller was
        writing an owner uuid as a key until it hand-wrote the pseudonym
        itself, which is exactly the "a rule a caller can forget is not a rule"
        this layer exists to avoid.
        """
        if isinstance(value, str):
            return self.text(value, people=not isinstance(value, Prose))
        if isinstance(value, (list, tuple)):
            return [self.value(v) for v in value]
        if isinstance(value, dict):
            return {self.text(str(k)): self.field(str(k), v) for k, v in value.items()}
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
        self._head: List[dict] = []
        self._tail: Optional[Deque[dict]] = None
        self.event_cap = 40000
        self.dropped = 0
        self._broken = 0
        self.stream_failure: Optional[str] = None

    # -- state -------------------------------------------------------------

    @property
    def events(self) -> Optional[List[dict]]:
        """The events kept in memory, head first. Built on read, which is rare
        (the diagnostics file), rather than maintained on write, which is not."""
        if self._tail is None:
            return None
        return self._head + list(self._tail)

    @events.setter
    def events(self, value) -> None:
        """Take a list of events, partitioned the way ``_keep`` partitions one.

        It has to dedup, and the page is why: it hands the events of the
        previous log to the new one every time a setting is saved, so a session
        that saved fifty times grew a head of 101 events that ``_trim`` would
        never drop. Worse, the header it kept was the oldest one, so after a
        truncation the log claimed a run header it no longer described. The
        last ``run.start`` is the one this log is running under, so that is the
        one kept.
        """
        if value is None:
            self._head, self._tail = [], None
            return
        head: Dict[str, dict] = {}
        tail = deque()
        for event in value:
            name = event.get("event")
            if name in HEAD_EVENTS:
                head[name] = event  # last one wins: it describes this log
            else:
                tail.append(event)
        self._head = [head[name] for name in HEAD_EVENTS if name in head]
        self._tail = tail

    def _keep(self, event: dict) -> None:
        """Hold one event in memory, in the head list or the tail deque.

        Which list it goes in is decided by what the event *is*, once, here.
        The version that decided by where an event sat kept the fingerprint
        only because events happened to pop one at a time.
        """
        if self._tail is None:
            return
        name = event.get("event")
        if name in HEAD_EVENTS:
            # A head event replaces its slot rather than being turned away as a
            # duplicate, which is the same last-one-wins rule the setter uses.
            # The page carries the previous log's events into a new log and
            # *then* writes the new header, so first-one-wins left the fresh
            # run.start in the tail and kept a header describing the run before
            # the setting changed: after a truncation the log stated a source
            # version the run was not using.
            for index, existing in enumerate(self._head):
                if existing.get("event") == name:
                    self._head[index] = event
                    break
            else:
                self._head.append(event)
        else:
            self._tail.append(event)
        self._trim()

    @property
    def on(self) -> bool:
        return self.stream is not None or self._tail is not None

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
        try:
            self._emit(level, code, **fields)
        except Exception as failure:  # noqa: BLE001 - see below
            # The invariant, enforced rather than asserted: nothing about
            # logging may end a command. A redactor defect took a preview down
            # with a traceback at the default level, so the tool worked without
            # --log and crashed with it, which is the release's headline
            # feature breaking the tool. A failure here is recorded as an
            # event of its own, with no value from the failed event in it: the
            # event that could not be redacted is exactly the one that must not
            # be written.
            self._failed(level, code, failure)

    def _failed(self, level: str, code: str, failure: BaseException) -> None:
        """Record that an event could not be written, without ever raising.

        This is the path that runs when something is already wrong, so it is
        the path that must be surest. The destination can go bad *after* it was
        opened: a full disk, a broken pipe on stderr, a removed file. The write
        here used to be unguarded, so a failing stream took the command down
        through the very code written to stop that happening.

        On a stream failure the stream is dropped, the in-memory record is
        kept, and the command carries on. A log that stops is a diagnosis; a
        command that stops because its log stopped is a defect.
        """
        self._broken += 1
        if self._broken > 50 or (self.stream is None and self._tail is None):
            return
        event = {"t": round(self._clock() - self._t0, 4), "lvl": "error",
                 "phase": self._phases[-1].name if self._phases else "run",
                 "event": "log.failed", "for_event": str(code), "for_level": level,
                 "failure": type(failure).__name__,
                 "reason": "this event could not be written safely, so it was dropped "
                           "rather than written unredacted; the failure is the log's, "
                           "not the command's"}
        try:
            self._keep(event)
        except Exception:  # noqa: BLE001 - nothing here may reach the caller
            pass
        if self.stream is None:
            return
        try:
            line = (render_event(event) if self.fmt == "text"
                    else json.dumps(event, ensure_ascii=False))
            self.stream.write(line + "\n")
            self.stream.flush()
        except Exception as second:  # noqa: BLE001 - the destination is gone
            self._drop_stream(second)

    def _drop_stream(self, failure: BaseException) -> None:
        """Stop writing to a destination that has gone bad, and say so in the
        events still held in memory, which is the one place left to say it."""
        self.stream = None
        self.stream_failure = type(failure).__name__
        try:
            self._keep({"t": round(self._clock() - self._t0, 4), "lvl": "error",
                        "phase": self._phases[-1].name if self._phases else "run",
                        "event": "log.destination_lost",
                        "failure": type(failure).__name__,
                        "reason": "the log's destination stopped accepting writes, so "
                                  "the rest of this run is not in the file; the command "
                                  "is unaffected"})
        except Exception:  # noqa: BLE001
            pass

    def _emit(self, level: str, code: str, /, **fields) -> None:
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
        self._keep(event)
        if self.stream is None:
            return
        line = (render_event(event) if self.fmt == "text"
                else json.dumps(event, ensure_ascii=False))
        try:
            self.stream.write(line + "\n")
            # Flushed per event, not per buffer. The page runs until someone stops
        # it, usually with Ctrl-C, and a log whose last events are still in a
        # buffer when that happens loses exactly the part a support case is
            # about. Measured at 0.0012 ms per event, which a run does not
            # notice, and the difference between a log that ends in run.end
            # after a SIGKILL and one that ends mid-story.
            self.stream.flush()
        except Exception as failure:  # noqa: BLE001 - the destination is gone
            self._drop_stream(failure)

    def _trim(self) -> None:
        """Hold the buffer at its cap, oldest first, but never the head.

        The oldest events are the contents line, the run header and the input
        fingerprint, and those are exactly what the diagnostics file looks up
        to describe itself: dropping them first turned a long session's
        diagnostics into events with nothing saying what they are about. And a
        drop is a thing the tool keeps quiet about in its output, so it is
        loud here the first time it happens.

        **The head is a list of its own, and the rest is a deque.** The first
        version of this rebuilt both lists on every event once the cap was
        reached, which is O(n) per event: measured at the shipped cap of 40000,
        0.0018 ms per event while filling and 1.67 ms after, so a page session
        that crossed the cap looked like a hang. Nothing is rebuilt now; the
        deque drops from its left.
        """
        if self._tail is None or len(self._head) + len(self._tail) <= self.event_cap:
            return
        first_drop = self.dropped == 0
        while len(self._head) + len(self._tail) > self.event_cap and self._tail:
            self._tail.popleft()
            self.dropped += 1
        if first_drop:
            self.warn("log.truncated", cap=self.event_cap, dropped=self.dropped,
                      reason=prose(
                          "this session has written more events than the page keeps in "
                          "memory, so the oldest are gone from the diagnostics file; the "
                          "contents line, the run header and the input fingerprint are "
                          "kept whatever else goes"))

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

    def finish(self, code: Optional[int] = None, what: str = "",
               failed: Optional[BaseException] = None) -> None:
        """The end of the run, with the code the process will actually exit
        with.

        The code comes from here rather than from each caller's own variable.
        Three separate defects in this family have been fixed one at a time: a
        hardcoded 0 in the CLI, the same hardcoded 0 in the census, and a
        crash path reporting the initialised 2 while Python exits 1. An
        uncaught exception exits 1, so that is what this reports, whatever the
        caller was holding.
        """
        if failed is not None:
            code = 1
        self.info("run.end", exit=0 if code is None else int(code),
                  what=what or None, failed=(type(failed).__name__ if failed else None),
                  events=self._written, by_level=self.counts_by_level(),
                  dropped=self.dropped or None, broken=self._broken or None,
                  destination_lost=self.stream_failure,
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
             fmt: str = DEFAULT_FORMAT, keep_events: bool = False,
             redactor: Optional[Redactor] = None) -> Log:
    """Open the log for one run. ``-`` writes to stderr, so a run can be
    watched without a file; anything else is a path, appended to.

    *redactor* carries one forward: the page re-opens its log whenever a
    setting changes, and a fresh redactor would have forgotten every person it
    had harvested from the export that is still open.
    """
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
    log = Log(stream=stream, level=level, fmt=fmt, path=path, redactor=redactor)
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
    # Directory entries are members of the zip and are named here: they are
    # the reason a bundle is accepted at all, so "what did it write" has to
    # include them.
    return {
        "members": len(order),
        "bytes": sum(len(written.get(name, b"")) for name in order),
        "files": [{"member": name, "bytes": len(written[name]),
                   "sha256": sha256(written[name])}
                  for name in order if name in written],
        "directory_entries": [name for name in order if name.endswith("/")],
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

DIAGNOSTICS_CONTENTS = prose(
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
