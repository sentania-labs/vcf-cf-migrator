"""The exclusion list, proved over the admin's own exports.

This is the corpus tier of the log's contract, the sibling of
``corpus-check``: it runs a full run over every zip in the corpus directory at
the loudest level, then walks every event the run produced and asserts that no
excluded value appears in any of them. Skipped when there is no corpus, so CI
runs the committed tier (``tests/test_log_wiring.py``) and this runs on the
workstation before a PR opens.

**The needles are generated from the exports, not remembered.** That is the
same rule the corpus leak scan follows, and for the same reason: the one value
that reached the public repo was one nobody had thought to put on a list, and
the first hand-written version of that scan missed a real display name the
generated version caught. So this file walks every member of every zip, nested
zips included, and harvests every value sitting under a key that names a
person or a credential.

**And the harvest is deliberately not the redactor's.** The key list below is
written here, in the test's own words, rather than imported from
``runlog``. A test that asks the redactor which values it considers people and
then checks it excluded those values proves nothing. This one decides for
itself and holds the log to it.

Nothing here prints a needle. A failure names the class, the member and the
event code, never the value: a test that leaks on failure is a leak.
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path

import pytest

from vcfcf_migrator import bundle as _bundle
from vcfcf_migrator import corpus_check as _corpus_check
from vcfcf_migrator import graph as _graph
from vcfcf_migrator import preview as _preview
from vcfcf_migrator import runlog
from vcfcf_migrator import selection as _selection
from vcfcf_migrator.export_reader import read_export, read_members

# Keys whose value is a person, in this test's own words.
PERSON_KEYS = re.compile(
    r"^(user|userid|username|user_name|userkey|owner|ownerid|owneruserid|owneruuid|"
    r"createdby|modifiedby|lastmodifiedby|displayname|display_name|fullname|name_first|"
    r"firstname|lastname|givenname|surname|emailaddress|email|mail|mailaddress|"
    r"principal|account|accountid|login|loginname)$", re.I)
# Keys whose value is a credential or an encrypted blob.
SECRET_KEYS = re.compile(
    r"pass|pwd|secret|credential|token|cipher|encrypt|auth|apikey|api_key|privatekey|"
    r"signature|keystore", re.I)
UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
MAIL = re.compile(r"[\w.%+-]+@[\w-]+\.[\w.-]*[A-Za-z]{2,}")
# Values too short or too generic to be evidence of anything: "admin" as a
# user name is also a word that appears in content titles, and a needle that
# matches content would fail this test for the wrong reason.
COMMON = {"admin", "root", "system", "local", "user", "users", "all", "everyone",
          "true", "false", "none", "null", "default", "administrator", "vcops",
          "automation", "public", "unknown", ""}


# Fields holding a path the admin gave the tool, rather than anything read out
# of an export. They are scanned for nothing: the log carries them on purpose
# (the tool cannot be diagnosed without knowing which file it was pointed at),
# the contents line says so, and on this workstation the corpus happens to sit
# under a directory whose name is also a user name on the source instance. A
# home directory is not an export leak, and a test that cannot tell the two
# apart fails for the wrong reason.
PATH_FIELDS = {"path", "cwd", "argv", "out", "zip", "dir", "file", "corpus_dir"}


def without_paths(event: dict) -> dict:
    return {k: v for k, v in event.items() if k not in PATH_FIELDS}


def corpus_dir():
    return Path(os.environ.get("VCFCF_MIGRATOR_CORPUS") or "corpus")


def corpus_zips():
    directory = corpus_dir()
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix.lower() == ".zip")


pytestmark = pytest.mark.skipif(
    not corpus_zips(),
    reason="no corpus directory; the committed tier is tests/test_log_wiring.py")


# ---------------------------------------------------------------------------
# Harvesting the needles
# ---------------------------------------------------------------------------

def _walk(doc, found: dict, depth: int = 0) -> None:
    if depth > 14:
        return
    if isinstance(doc, dict):
        for key, value in doc.items():
            name = str(key)
            if isinstance(value, str) and value.strip():
                if PERSON_KEYS.match(name):
                    found.setdefault("person", set()).add(value.strip())
                elif SECRET_KEYS.search(name):
                    found.setdefault("credential", set()).add(value.strip())
            _walk(value, found, depth + 1)
    elif isinstance(doc, list):
        for item in doc:
            _walk(item, found, depth + 1)


def _members(data: bytes, depth: int = 0):
    """Every member of a zip, and of any zip inside it."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                raw = zf.read(name)
                yield name, raw
                if depth < 2 and zipfile.is_zipfile(io.BytesIO(raw)):
                    for inner, inner_raw in _members(raw, depth + 1):
                        yield f"{name}!{inner}", inner_raw
    except (zipfile.BadZipFile, OSError):
        return


def needles(path: Path) -> dict:
    """Every value in one export that the log is not allowed to carry,
    harvested from the export itself."""
    found: dict = {}
    data = path.read_bytes()
    for name, raw in _members(data):
        head, _sep, tail = name.partition("/")
        if head in ("dashboards", "dashboardsharings") and tail and UUID.fullmatch(tail):
            found.setdefault("person", set()).add(tail)
        if re.fullmatch(r"\d+L\.v\d+", Path(name).name):
            found.setdefault("person", set()).add(
                raw.decode("utf-8", "replace").strip())
            continue
        if name.lower().endswith(".json"):
            try:
                _walk(json.loads(raw), found)
            except ValueError:
                continue
        else:
            for match in MAIL.findall(raw.decode("utf-8", "replace")):
                found.setdefault("person", set()).add(match)
    for klass in list(found):
        found[klass] = {v for v in found[klass]
                        if v.lower() not in COMMON and len(v) >= 4}
    return found


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def full_run(path: Path, declared, out_dir: Path) -> list:
    """Everything the tool does to an export, with the loudest log, returning
    the events it produced: inspect, tree, a preview of every object and a
    select-all build."""
    log = runlog.open_log(None, level="debug", keep_events=True)
    log.event_cap = 10_000_000
    previous = runlog.set_current(log)
    try:
        log.header(["build", str(path), "--select-all"], tool_version="test",
                   core_version="test", source_version=declared,
                   source_version_from="versions.json")
        read_export(path, source_version=declared)
        members = read_members(path, source_version=declared)
        graph = _graph.build_graph(members.data)
        for node in graph.ordered():
            try:
                _preview.render_page(graph, node)
            except _preview.PreviewError:
                continue
        picked = _selection.select_all(graph)
        _bundle.build_bundle(members.data, members.order, graph, picked,
                             out_dir / (path.stem + "-bundle.zip"),
                             marker=members.marker, directories=members.directories,
                             directory_order=members.directory_order)
        log.finish(0, what="build")
    finally:
        runlog.set_current(previous)
    return log.events or []


@pytest.mark.parametrize("path", corpus_zips(), ids=lambda p: p.stem)
def test_no_excluded_value_reaches_any_event_of_a_full_run(path, tmp_path, config_dir):
    declared = _corpus_check.read_versions(corpus_dir()).get(path.name, "9.0.2")
    events = full_run(path, declared, tmp_path)
    assert events, "the run produced no events"
    body = "\n".join(json.dumps(without_paths(event), ensure_ascii=False)
                     for event in events)

    harvest = needles(path)
    assert harvest.get("person"), "this export carries no people, so it proves nothing"

    # Uuid-shaped needles are checked by set membership against the uuids that
    # actually appear in the log, which is one pass rather than one pass per
    # needle; the rest go through a chunked alternation for the same reason.
    in_log = {token.lower() for token in UUID.findall(body)}
    for klass, values in sorted(harvest.items()):
        uuids = {v.lower() for v in values if UUID.fullmatch(v)}
        leaked = uuids & in_log
        assert not leaked, (
            f"{len(leaked)} {klass} uuid(s) from {path.name} reached the log "
            f"(values deliberately not printed)")
        words = sorted(v for v in values if not UUID.fullmatch(v))
        for start in range(0, len(words), 200):
            chunk = words[start:start + 200]
            pattern = re.compile("|".join(re.escape(w) for w in chunk))
            hits = {m.group(0) for m in pattern.finditer(body)}
            assert not hits, (
                f"{len(hits)} {klass} value(s) from {path.name} reached the log "
                f"(values deliberately not printed)")

    # A log that excludes everything is also a log that says nothing, so the
    # same run has to prove it kept what makes it diagnostic.
    assert "[excluded:" not in json.dumps(
        [e for e in events if e["event"] == "run.start"]), "the header lost itself"
    names = {e.get("name") for e in events if e.get("event") == "node.found"}
    assert len([n for n in names if n]) > 5, "the log carries no content names"
    fingerprints = [e for e in events if e["event"] == "input.fingerprint"]
    assert fingerprints and len(fingerprints[0]["sha256"]) == 64


@pytest.mark.parametrize("path", corpus_zips(), ids=lambda p: p.stem)
def test_owners_are_pseudonyms_and_the_member_names_carrying_them_are_rewritten(
        path, tmp_path, config_dir):
    declared = _corpus_check.read_versions(corpus_dir()).get(path.name, "9.0.2")
    events = full_run(path, declared, tmp_path)
    fingerprint = [e for e in events if e["event"] == "input.fingerprint"][0]
    dashboards = [n for n in fingerprint["member_names"] if n.startswith("dashboards/")]
    if not dashboards:
        pytest.skip("this export names no owner in a member name")
    # dashboards/<owner uuid> becomes dashboards/owner-N, so the shape of the
    # export stays readable and the account does not appear.
    assert all(re.fullmatch(r"dashboards/owner-\d+", n) for n in dashboards), dashboards
    owners = {n.split("/", 1)[1] for n in dashboards}
    end = [e for e in events if e["event"] == "run.end"][0]
    assert end["owners_seen"] >= len(owners)
