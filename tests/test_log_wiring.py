"""What a run actually logs, over the committed fixture.

The corpus tier of this is ``tests/test_log_redaction_corpus.py``, which runs
the same walk over the admin's real exports and asserts nothing excluded is in
it. This file is the committed tier: it proves the events exist, that they
carry the reason as well as the decision, that the three swallowed failures
are loud, and that a failed run explains itself from the log alone.
"""
from __future__ import annotations

import json
import zipfile

import pytest

from make_export_fixture import (
    DASHBOARD_ID,
    OWNER,
    PERSON_DISPLAY_NAME,
    PERSON_MAIL,
    PERSON_USER_NAME,
    SECRET_CIPHER_TEXT,
    SECRET_TOKEN,
    build_export_zip,
)
from vcfcf_migrator import runlog
from vcfcf_migrator.cli import main
from vcfcf_migrator.ui import PageState

def _fixture_excluded():
    """Every person and every secret the fixture defines, taken from the module
    rather than listed here: a hand-picked six of nine is the drift the corpus
    file's own docstring warns about, in the tier CI actually runs."""
    import make_export_fixture as fixture

    values = []
    for name in dir(fixture):
        if name.startswith(("PERSON_", "SECRET_")) or name in ("OWNER", "OWNER_2"):
            value = getattr(fixture, name)
            if isinstance(value, str) and value:
                values.append(value)
    return tuple(values)


EXCLUDED_FROM_THE_FIXTURE = _fixture_excluded()


def run(argv, log_path, level="detail"):
    """One command with a log, returning the exit code and the events."""
    code = main(list(argv) + ["--log", str(log_path), "--log-level", level])
    events = [json.loads(line) for line in
              log_path.read_text(encoding="utf-8").splitlines() if line]
    return code, events


def codes(events):
    return [e["event"] for e in events]


def of(events, code):
    return [e for e in events if e["event"] == code]


@pytest.fixture
def state(config_dir, export_zip):
    return PageState(str(export_zip))


@pytest.fixture
def built(tmp_path, export_zip, config_dir):
    log = tmp_path / "run.jsonl"
    out = tmp_path / "bundle.zip"
    code, events = run(["--source-version", "9.0.2", "build", str(export_zip),
                        "--select-all", "--out", str(out)], log)
    assert code == 0, events[-3:]
    return events, out


# ---------------------------------------------------------------------------
# The run header
# ---------------------------------------------------------------------------

def test_the_header_says_what_the_run_was_asked_to_do(built):
    events, _out = built
    assert codes(events)[0] == "log.contents"
    header = of(events, "run.start")[0]
    assert header["tool"] and header["core"] and header["python"] and header["platform"]
    assert "build" in header["argv"] and "--select-all" in header["argv"]
    assert header["source_version"] == "9.0.2"
    assert header["source_version_from"] == "command line"
    assert header["corpus_dir"] and header["corpus_from"]


def test_the_input_is_fingerprinted_so_an_export_can_be_identified(built):
    events, _out = built
    fingerprint = of(events, "input.fingerprint")[0]
    assert len(fingerprint["sha256"]) == 64
    assert fingerprint["bytes"] > 0 and fingerprint["members"] > 10
    assert "configuration.json" in fingerprint["member_names"]
    assert fingerprint["manifest"]["dashboards"] == 4


def test_the_output_is_fingerprinted_member_by_member(built):
    events, out = built
    fingerprint = of(events, "output.fingerprint")[0]
    assert fingerprint["members"] == len(zipfile.ZipFile(out).namelist())
    assert len(fingerprint["zip_sha256"]) == 64
    for entry in fingerprint["files"]:
        assert entry["bytes"] >= 0 and len(entry["sha256"]) == 64
    assert {f["member"] for f in fingerprint["files"]} >= {"configuration.json"}


# ---------------------------------------------------------------------------
# Decisions, each with its reason
# ---------------------------------------------------------------------------

def test_every_decision_carries_the_object_it_concerns_and_why(built):
    events, _out = built
    resolved = of(events, "ref.resolved")[0]
    assert resolved["kind"] and resolved["to_kind"] and resolved["via"]
    assert resolved["spelling"] in ("uuid", "name")
    rebuilt = of(events, "container.rebuilt")[0]
    assert rebuilt["member"] and "copied byte for byte" in rebuilt["reason"]


def test_closure_says_what_it_added_and_what_needed_it(tmp_path, export_zip, config_dir):
    picks = tmp_path / "picks.txt"
    picks.write_text(f"dashboard:{DASHBOARD_ID}@{OWNER}\n", encoding="utf-8")
    log = tmp_path / "closure.jsonl"
    code, events = run(["--source-version", "9.0.2", "build", str(export_zip),
                        "--select", str(picks), "--out", str(tmp_path / "subset.zip")], log)
    assert code == 0
    added = of(events, "closure.added")
    assert added, codes(events)
    assert all(a["required_by_name"] and "depends on it" in a["reason"] for a in added)
    closed = of(events, "selection.closed")[0]
    assert closed["picked"] == 1 and closed["carried"] > 1


def test_a_reference_that_resolves_to_nothing_says_which_spelling_it_used(built):
    events, _out = built
    missing = of(events, "ref.missing")
    assert missing, codes(events)
    assert all(m["spelling"] in ("uuid", "name") and m["reason"] for m in missing)


def test_the_phases_carry_their_counts_and_their_timings(built):
    events, _out = built
    ends = {e["phase"]: e for e in of(events, "phase.end")}
    assert {"command", "read", "graph", "select", "build"} <= set(ends)
    assert ends["graph"]["nodes"] > 0 and ends["graph"]["edges"] > 0
    assert ends["build"]["members_written"] > 0
    assert all(isinstance(e["ms"], float) for e in ends.values())


def test_a_member_this_tool_does_not_understand_is_named_not_dropped_quietly(built):
    events, _out = built
    skipped = of(events, "member.not_carried")
    assert any(e["member"] == "policies.xml" for e in skipped), codes(events)


# ---------------------------------------------------------------------------
# Refusals and swallowed failures
# ---------------------------------------------------------------------------

def test_a_build_with_no_declared_source_version_says_why_it_refused(tmp_path, export_zip,
                                                                     config_dir):
    log = tmp_path / "refused.jsonl"
    code, events = run(["build", str(export_zip), "--select-all",
                        "--out", str(tmp_path / "no.zip")], log)
    assert code == 1
    refusal = of(events, "command.refused")[0]
    assert "no source version declared" in refusal["reason"]
    assert of(events, "run.end")[0]["exit"] == 1


def test_a_selection_naming_something_absent_is_refused_in_the_log(tmp_path, export_zip,
                                                                   config_dir):
    picks = tmp_path / "picks.txt"
    picks.write_text("dashboard:11111111-2222-4333-8444-555555555555\n", encoding="utf-8")
    log = tmp_path / "bad-selection.jsonl"
    code, events = run(["--source-version", "9.0.2", "build", str(export_zip),
                        "--select", str(picks), "--out", str(tmp_path / "no.zip")], log)
    assert code == 1
    refusal = of(events, "selection.refused")[0]
    assert "does not carry" in refusal["reason"]
    # The uuid the admin typed is not a content identifier of this export, so
    # the line names the refusal without repeating an identifier the log
    # cannot vouch for.
    assert refusal["lines"]


def test_an_unwritable_bundle_path_is_in_the_log(tmp_path, export_zip, config_dir):
    log = tmp_path / "unwritable.jsonl"
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory", encoding="utf-8")
    code, events = run(["--source-version", "9.0.2", "build", str(export_zip),
                        "--select-all", "--out", str(blocked / "bundle.zip")], log)
    assert code == 1
    failure = of(events, "bundle.unwritable")[0]
    assert str(blocked) in failure["path"] and failure["reason"]


def test_an_unreadable_zip_is_in_the_log(tmp_path, config_dir):
    bad = tmp_path / "not-an-export.zip"
    bad.write_bytes(b"this is not a zip")
    log = tmp_path / "bad-zip.jsonl"
    code, events = run(["inspect", str(bad)], log)
    assert code == 1
    failure = of(events, "input.not_a_zip")[0]
    assert "does not open as a zip" in failure["reason"]


def test_markup_that_does_not_parse_is_quiet_on_the_page_and_loud_in_the_log(
        tmp_path, config_dir, monkeypatch):
    """The one failure this tool swallows on purpose. The page still renders,
    and the log says what happened."""
    from vcfcf_migrator import preview

    stream = tmp_path / "swallowed.jsonl"
    log = runlog.open_log(str(stream), level="warn")
    previous = runlog.set_current(log)

    class Exploding:
        def feed(self, _markup):
            raise RuntimeError("the parser gave up")

        def close(self):
            pass

    monkeypatch.setattr(preview, "_TextOnly", Exploding)
    try:
        assert preview._strip_tags("<p>words</p>") == "<p>words</p>"
    finally:
        runlog.set_current(previous)
        log.close()
    events = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines()]
    swallowed = [e for e in events if e["event"] == "swallowed.markup_unparsed"][0]
    assert swallowed["failure"] == "RuntimeError"
    assert "did not parse" in swallowed["reason"]


def test_a_document_that_does_not_parse_is_logged_before_the_refusal(tmp_path, config_dir):
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as z:
        z.writestr("1757800000000000000L.v1", OWNER)
        z.writestr("configuration.json", json.dumps({"type": "CUSTOM", "superMetrics": 1}))
        z.writestr("supermetrics.json", "{not json at all")
    log = tmp_path / "broken.jsonl"
    code, events = run(["--source-version", "9.0.2", "tree", str(broken)], log)
    # The member is unreadable, so the tool carries it rather than reading it;
    # the log says the member was not understood rather than staying silent.
    assert code == 0
    assert of(events, "member.unknown"), codes(events)


# ---------------------------------------------------------------------------
# The exclusion list, over a whole run of the committed fixture
# ---------------------------------------------------------------------------

def test_no_excluded_value_from_the_fixture_reaches_any_event(tmp_path, export_zip,
                                                              config_dir):
    log = tmp_path / "everything.jsonl"
    code, _events = run(["--source-version", "9.0.2", "build", str(export_zip),
                         "--select-all", "--out", str(tmp_path / "bundle.zip")], log,
                        level="debug")
    assert code == 0
    body = log.read_text(encoding="utf-8")
    for needle in EXCLUDED_FROM_THE_FIXTURE:
        assert needle not in body, needle
    assert "owner-1" in body and "owner-2" in body
    # Content identity is in scope, and is what makes the log worth having.
    assert DASHBOARD_ID in body and "[Fixture] Cluster Overview" in body


def test_a_preview_of_one_owners_copy_keeps_the_owner_out_of_the_log(tmp_path, export_zip,
                                                                     config_dir):
    log = tmp_path / "preview.jsonl"
    code, events = run(["preview", str(export_zip), f"dashboard:{DASHBOARD_ID}@{OWNER}",
                        "--out", str(tmp_path / "p.html")], log)
    assert code == 0
    body = log.read_text(encoding="utf-8")
    assert OWNER not in body and "owner-1" in body
    classified = of(events, "widget.classified")
    assert classified and all("widget_type" in e for e in classified)
    assert of(events, "preview.built")


def test_the_log_carries_the_widget_verdicts_the_page_shows(tmp_path, export_zip,
                                                            config_dir):
    log = tmp_path / "verdicts.jsonl"
    code, events = run(["preview", str(export_zip), f"dashboard:{DASHBOARD_ID}@{OWNER}",
                        "--out", str(tmp_path / "p.html")], log)
    assert code == 0
    states = {e["state"] for e in of(events, "widget.classified")}
    assert {"drawn", "empty"} <= states
    assert any(e.get("code") for e in of(events, "widget.classified"))


# ---------------------------------------------------------------------------
# log-render
# ---------------------------------------------------------------------------

def test_log_render_turns_a_captured_log_into_lines_a_person_reads(tmp_path, export_zip,
                                                                   config_dir, capsys):
    log = tmp_path / "render.jsonl"
    run(["inspect", str(export_zip)], log)
    capsys.readouterr()
    assert main(["log-render", str(log)]) == 0
    out = capsys.readouterr().out
    assert "run.start" in out and "input.fingerprint" in out
    assert not out.lstrip().startswith("{")


def test_log_render_says_so_when_the_file_is_not_there(tmp_path, capsys, config_dir):
    assert main(["log-render", str(tmp_path / "absent.jsonl")]) == 1
    assert "cannot read" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def test_the_page_saves_one_diagnostics_file_that_names_its_contents(tmp_path, export_zip,
                                                                     config_dir):
    state = PageState(str(export_zip))
    state.select_all()
    state.source_version_cli = "9.0.2"
    state.build(str(tmp_path / "bundle.zip"))
    out = tmp_path / "diagnostics.jsonl"
    state.save_diagnostics(str(out))
    assert state.error == "", state.error
    lines = out.read_text(encoding="utf-8").splitlines()
    head = json.loads(lines[0])
    assert head["kind"] == "vcfcf-migrator-diagnostics"
    assert head["run"]["tool"] and head["source"]["sha256"] and head["bundle"]["members"]
    assert head["events"] == len(lines) - 1
    for needle in EXCLUDED_FROM_THE_FIXTURE:
        assert needle not in out.read_text(encoding="utf-8"), needle


def test_a_log_setting_change_keeps_what_the_page_learned_about_people(
        tmp_path, export_zip, config_dir):
    """The page re-opens its log whenever a setting changes. A fresh redactor
    knows nothing about the export that is still open, so the names it had been
    excluding started appearing, in the log and in the diagnostics file the
    README tells a customer to mail."""
    state = PageState(str(export_zip))
    before = state.log.redactor.owners_seen()
    assert before > 0
    target = tmp_path / "after-change.jsonl"
    state.save_setting({"log_file": str(target)})
    state.save_setting({"log_level": "debug"})
    state.run_tree()
    state.select_all()
    out = tmp_path / "diag.jsonl"
    state.save_diagnostics(str(out))
    state.log.close()
    body = target.read_text(encoding="utf-8") + out.read_text(encoding="utf-8")
    for needle in EXCLUDED_FROM_THE_FIXTURE:
        assert needle not in body, needle
    assert state.log.redactor.owners_seen() >= before


def test_the_page_ends_its_log_so_a_truncated_one_can_be_told_apart(
        tmp_path, export_zip, config_dir):
    state = PageState(str(export_zip))
    state.save_setting({"log_file": str(tmp_path / "page.jsonl")})
    state.run_tree()
    state.render()
    ends = [e for e in state.log.events if e["event"] == "run.end"]
    assert ends, [e["event"] for e in state.log.events][-5:]


def test_the_ui_command_writes_the_log_the_flag_asks_for(tmp_path, export_zip,
                                                         config_dir):
    """``ui --log run.log`` was accepted, advertised in --help and in the
    README's one instruction for reporting a problem, and wrote four lines with
    nothing the page did in them. Two review rounds proved logging through
    preview and build and never through the page."""
    from vcfcf_migrator.ui import make_server

    target = tmp_path / "ui.jsonl"
    server = make_server(zip_path=str(export_zip), port=0, log_cli=str(target),
                         log_level_cli="debug")
    state = server.page_state
    try:
        state.select_all()
        state.run_tree()
        state.render()
    finally:
        state.log.close()
        server.server_close()
    events = [json.loads(line) for line in
              target.read_text(encoding="utf-8").splitlines() if line]
    codes = {e["event"] for e in events}
    assert len(events) > 50, len(events)
    # Not just the header: what the page actually did.
    assert {"input.fingerprint", "graph.built", "closure.picked", "run.end"} <= codes, codes
    for needle in EXCLUDED_FROM_THE_FIXTURE:
        assert needle not in target.read_text(encoding="utf-8"), needle


def test_the_page_takes_the_log_level_from_the_command_line(tmp_path, export_zip,
                                                            config_dir):
    from vcfcf_migrator.ui import PageState as State

    state = State(str(export_zip), log_cli=str(tmp_path / "quiet.jsonl"),
                  log_level_cli="error")
    _destination, _from, level, level_from = state.log_settings()
    assert level == "error" and level_from == "command line"
    state.log.close()


def test_the_header_the_page_keeps_is_the_one_the_run_is_using(tmp_path, export_zip,
                                                               config_dir):
    """Driven the way the page drives it, not by assigning a list by hand.

    ``open_log`` carries the previous log's events into the new one and then
    writes the new header. The earlier version of this test handed the buffer
    two synthetic ``run.start`` events, which the page never produces, so it
    passed while the real path kept the header from before the settings change:
    after a truncation the log stated a source version the run was not using.
    """
    state = PageState(str(export_zip))
    state.save_setting({"source_version": "9.0.2"})
    state.save_setting({"log_file": str(tmp_path / "page.jsonl")})
    state.save_setting({"log_level": "debug"})       # a second open_log
    state.run_tree()
    state.log.event_cap = 5                          # force the truncation
    for index in range(40):
        state.log.detail("noise", index=index)
    headers = [e for e in state.log.events if e["event"] == "run.start"]
    assert len(headers) == 1, headers
    assert headers[0]["source_version"] == "9.0.2", headers[0]
    kept = {e["event"] for e in state.log.events}
    for head in runlog.HEAD_EVENTS:
        assert head in kept, (head, kept)
    state.log.close()


def test_a_head_event_written_twice_keeps_one_slot():
    log = runlog.Log(level="debug")
    log.events = []
    log.info("run.start", tool="first")
    log.info("run.start", tool="second")
    headers = [e for e in log.events if e["event"] == "run.start"]
    assert len(headers) == 1 and headers[0]["tool"] == "second"


def test_the_page_has_a_control_for_the_log_file_and_the_level(state):
    page = state.render()
    assert "Run log file" in page and "Log level" in page
    for level in runlog.LEVEL_NAMES:
        assert f"<option value='{level}'" in page
    assert "never records credentials" in page


def test_the_diagnostics_button_names_what_the_file_holds(state):
    page = state.render()
    assert ("Save the run header, the export&#x27;s fingerprint, every log event and the "
            "bundle&#x27;s manifest to one file") in page


def test_saving_a_log_file_setting_turns_the_log_on(tmp_path, export_zip, config_dir):
    state = PageState(str(export_zip))
    target = tmp_path / "page.jsonl"
    state.save_setting({"log_file": str(target)})
    assert state.error == ""
    state.run_tree()
    state.log.close()
    assert target.exists() and target.read_text(encoding="utf-8").strip()


def test_a_log_level_the_tool_does_not_know_is_refused_on_the_page(state):
    state.save_setting({"log_level": "chatty"})
    assert "not one of" in state.error
