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

EXCLUDED_FROM_THE_FIXTURE = (OWNER, PERSON_USER_NAME, PERSON_DISPLAY_NAME, PERSON_MAIL,
                             SECRET_CIPHER_TEXT, SECRET_TOKEN)


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
