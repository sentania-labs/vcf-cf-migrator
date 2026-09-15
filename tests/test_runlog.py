"""The log's own contract: levels, phases, the rendering, and the exclusions.

The exclusion list is a correctness requirement, not a style note, so most of
this file is about what a log refuses to say. The rule the tests are written
against is that the *logging layer* enforces it: a call site is allowed to be
careless, and the layer still has to produce a line with nothing excluded in
it. Every test here therefore logs something a careful author would not, and
asserts the layer caught it.
"""
from __future__ import annotations

import io
import json

import pytest

from make_export_fixture import (
    OWNER,
    OWNER_2,
    PERSON_DISPLAY_NAME,
    PERSON_MAIL,
    PERSON_USER_NAME,
    SECRET_CIPHER_TEXT,
    SECRET_TOKEN,
)
from vcfcf_migrator import runlog


@pytest.fixture
def log():
    stream = io.StringIO()
    log = runlog.Log(stream=stream, level="debug")
    previous = runlog.set_current(log)
    yield log
    runlog.set_current(previous)


def events(log) -> list:
    return [json.loads(line) for line in log.stream.getvalue().splitlines() if line]


def text_of(log) -> str:
    return log.stream.getvalue()


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------

def test_every_event_is_one_json_object_on_one_line(log):
    runlog.info("one.thing", kind="view", name="[Fixture] Cluster List")
    runlog.detail("another.thing", kind="dashboard")
    for event in events(log):
        assert set(("t", "lvl", "phase", "event")) <= set(event)
        assert isinstance(event["t"], float)


def test_a_level_below_the_threshold_writes_nothing():
    stream = io.StringIO()
    log = runlog.Log(stream=stream, level="warn")
    log.detail("not.written", kind="view")
    log.warn("written", kind="view")
    assert [json.loads(line)["event"] for line in stream.getvalue().splitlines()] == ["written"]


def test_a_log_with_no_stream_is_a_working_no_op():
    log = runlog.Log()
    assert log.on is False
    log.info("nothing", kind="view")
    assert log.written() == 0


def test_a_phase_reports_its_counts_and_how_long_it_took(log):
    with log.phase("build", out="bundle.zip"):
        log.count("members_written", 3)
        log.count("members_written")
    end = [e for e in events(log) if e["event"] == "phase.end"][0]
    assert end["phase"] == "build"
    assert end["members_written"] == 4
    assert isinstance(end["ms"], float)


def test_a_phase_that_raises_says_so_and_re_raises(log):
    with pytest.raises(ValueError):
        with log.phase("read"):
            raise ValueError("no")
    end = [e for e in events(log) if e["event"] == "phase.end"][0]
    assert end["lvl"] == "error" and end["failed"] == "ValueError"


def test_the_text_format_and_the_renderer_agree():
    stream = io.StringIO()
    log = runlog.Log(stream=stream, level="debug", fmt="text")
    log.info("thing.happened", kind="view", name="[Fixture] Cluster List")
    line = stream.getvalue().strip()
    assert "thing.happened" in line and "kind=view" in line

    jsonl = io.StringIO()
    other = runlog.Log(stream=jsonl, level="debug")
    other.info("thing.happened", kind="view", name="[Fixture] Cluster List")
    rendered = runlog.render_log(jsonl.getvalue().splitlines()).strip()
    # The two differ only in the timestamp, which is wall-clock dependent.
    assert rendered.split(None, 1)[1] == line.split(None, 1)[1]


def test_render_log_passes_through_a_line_that_is_not_json():
    assert "half a line" in runlog.render_log(["half a line", '{"event":"x","t":0.0}'])


# ---------------------------------------------------------------------------
# The exclusion list, enforced by the layer
# ---------------------------------------------------------------------------

def test_a_credential_is_excluded_by_the_key_whatever_it_holds(log):
    runlog.info("careless", password=SECRET_CIPHER_TEXT, authToken=SECRET_TOKEN,
                encryptedValue="anything at all", apiKey="also this")
    body = text_of(log)
    for secret in (SECRET_CIPHER_TEXT, SECRET_TOKEN, "anything at all", "also this"):
        assert secret not in body
    assert body.count(runlog.EXCLUDED_CREDENTIAL) == 4


def test_a_metric_value_is_excluded_by_the_key(log):
    runlog.info("careless", value=99.5, values=[1, 2, 3], samples=[4])
    assert "99.5" not in text_of(log)
    assert text_of(log).count(runlog.EXCLUDED_VALUE) == 3


def test_a_person_field_never_reaches_the_line(log):
    runlog.info("careless", userName=PERSON_USER_NAME, displayName=PERSON_DISPLAY_NAME,
                mailAddress=PERSON_MAIL, userId=OWNER)
    body = text_of(log)
    for person in (PERSON_USER_NAME, PERSON_DISPLAY_NAME, PERSON_MAIL, OWNER):
        assert person not in body
    assert runlog.EXCLUDED_PERSON in body


def test_an_owner_is_a_stable_pseudonym_within_the_run(log):
    runlog.info("first", owner=OWNER)
    runlog.info("second", owner=OWNER_2)
    runlog.info("third", owner=OWNER)
    got = [e["owner"] for e in events(log)]
    assert got == ["owner-1", "owner-2", "owner-1"]
    assert OWNER not in text_of(log) and OWNER_2 not in text_of(log)


def test_a_person_value_is_taken_out_of_free_text_too(log):
    runlog.person(PERSON_DISPLAY_NAME)
    runlog.owner(OWNER)
    runlog.info("careless", note=f"dashboard owned by {PERSON_DISPLAY_NAME} ({OWNER})")
    note = events(log)[0]["note"]
    assert PERSON_DISPLAY_NAME not in note and OWNER not in note
    assert runlog.EXCLUDED_PERSON in note and "owner-1" in note


def test_a_person_in_any_case_is_excluded(log):
    """A name is written three ways in one export's widget titles, and a
    case-sensitive pattern let two of them through at the default level."""
    runlog.person("Marguerite Thornbury")
    runlog.person("MTHORNBURY")
    runlog.info("careless", name="MARGUERITE THORNBURY dashboard")
    runlog.info("careless", name="marguerite thornbury alert")
    runlog.info("careless", name="MTHORNBURYS VMs")
    body = text_of(log)
    for spelling in ("MARGUERITE", "marguerite", "Thornbury", "THORNBURY",
                     "MTHORNBURY", "mthornbury"):
        assert spelling not in body, spelling


def test_a_compact_uuid_is_excluded_like_a_hyphenated_one(log):
    runlog.info("careless", note="account 0c44e115dc214ea58a5601c22f18325b here")
    assert "0c44e115dc214ea58a5601c22f18325b" not in text_of(log)
    assert runlog.EXCLUDED_ID in text_of(log)


def test_a_content_uuid_is_allowed_in_either_spelling(log):
    runlog.content_id("2d7b8c1e-4f11-4c7a-9a55-0c1f2e3d4a5b")
    runlog.info("ref", note="2d7b8c1e4f114c7a9a550c1f2e3d4a5b and "
                            "2d7b8c1e-4f11-4c7a-9a55-0c1f2e3d4a5b")
    assert runlog.EXCLUDED_ID not in text_of(log)


def test_a_word_that_is_also_a_person_is_never_taught(log):
    """The built-in account is called admin, and substituting it rewrote nine
    real content names and the tool's own sentences on one export."""
    runlog.person("admin")
    runlog.info("careless", name="DX O2 Webhook Notification - admin (WebhookPlugin)",
                reason="carrying it would be the tool deciding for the admin")
    event = events(log)[0]
    assert event["name"].endswith("- admin (WebhookPlugin)")
    assert event["reason"].endswith("deciding for the admin")


def test_no_field_name_exempts_a_value_from_the_person_rules(log):
    """There was an exemption for reason, says and detail, on the premise that
    they carry sentences this module wrote. Fourteen call sites pass
    ``detail=str(e)``, so it covered arbitrary exception text, and an exception
    whose message was a person's name went into the log and into the
    diagnostics file verbatim."""
    runlog.person("Thornbury")
    runlog.info("careless", reason="Thornbury asked for it",
                detail="KeyError: 'thornbury'", says="Thornbury", name="Thornbury")
    event = events(log)[0]
    for key in ("reason", "detail", "says", "name"):
        assert "hornbury" not in event[key], (key, event[key])
        assert runlog.EXCLUDED_PERSON in event[key]


def test_the_tools_own_sentences_survive_because_common_words_are_never_taught(log):
    """What keeps the tool's English intact is a property of the value, not of
    the key it arrived under."""
    runlog.person("admin")
    runlog.info("careless", reason="carrying it would be the tool deciding for the admin")
    assert events(log)[0]["reason"].endswith("deciding for the admin")


def test_an_exception_message_carrying_a_person_is_redacted(log):
    runlog.person("Marguerite Thornbury")
    try:
        raise KeyError("Marguerite Thornbury")
    except KeyError as e:
        runlog.error("run.crashed", failure=type(e).__name__, detail=str(e))
    assert "Marguerite" not in text_of(log) and "Thornbury" not in text_of(log)


def test_a_logging_failure_never_takes_the_command_down(log, monkeypatch):
    """The invariant this layer states and now enforces. A redactor defect used
    to raise inside a logging call, so a preview died with a traceback at the
    default level: the tool worked without --log and crashed with it."""
    def explode(_self, _value):
        raise RuntimeError("the redactor gave up")

    monkeypatch.setattr(runlog.Redactor, "text", explode)
    runlog.info("thing.happened", name="anything")  # must not raise
    failures = [e for e in events(log) if e["event"] == "log.failed"]
    assert failures and failures[0]["for_event"] == "thing.happened"
    assert "anything" not in text_of(log)


def test_a_sentence_this_package_wrote_is_never_person_substituted(log):
    """The exemption is a property of the value. The reader harvests the word
    "member" as a person out of an LDAP mapping in authsources.json, and
    without this one corpus-check wrote 282 mangled sentences, including the
    answer to "why did policies.xml not come across"."""
    runlog.person("member")
    runlog.info("member.not_carried", member="policies.xml",
                reason=runlog.prose("policies.xml is in the export but is a member this "
                                    "tool never carries into a bundle"),
                name="member of staff dashboard")
    event = events(log)[0]
    assert event["reason"].endswith("is a member this tool never carries into a bundle")
    # And the same word as data, out of a document, is still excluded.
    assert runlog.EXCLUDED_PERSON in event["name"]


def test_an_exception_message_is_not_prose_however_it_is_keyed(log):
    runlog.person("Thornbury")
    try:
        raise KeyError("Thornbury")
    except KeyError as e:
        runlog.error("run.crashed", detail=str(e), reason=str(e),
                     says=str(e), note=str(e))
    event = events(log)[0]
    for key in ("detail", "reason", "says", "note"):
        assert "Thornbury" not in event[key], key


def test_every_literal_reason_in_the_package_is_marked_as_prose():
    """The gate against this coming back. A literal reason a call site passes
    is a sentence this package wrote; anything computed is data. An unwrapped
    literal reads as data and gets rewritten by somebody else's name."""
    import ast
    from pathlib import Path

    from vcfcf_migrator import runlog as module

    package = Path(module.__file__).parent
    unwrapped = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id in ("runlog", "_runlog")):
                continue
            for keyword in node.keywords or []:
                if keyword.arg != "reason":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    unwrapped.append(f"{path.name}:{value.lineno}")
    assert unwrapped == [], unwrapped


def test_the_spellings_a_person_value_needs_are_generated(log):
    """``_spellings`` is load-bearing and was ungated: re.I covers the Turkish
    and long-s cases on its own, so the only work this function does is the one
    where case folding changes length. German sharp s is that case."""
    assert "weiss hausmeister" in [s.lower() for s in runlog._spellings("Wei\u00df Hausmeister")]
    runlog.person("Wei\u00df Hausmeister")
    runlog.info("widget.classified", title="WEISS HAUSMEISTER dashboard")
    runlog.info("widget.classified", title="Wei\u00df Hausmeister dashboard")
    for event in events(log):
        assert runlog.EXCLUDED_PERSON in event["title"], event["title"]


def test_the_buffer_survives_a_batch_drop_and_stays_cheap():
    """The head is kept by what an event is, and trimming does not rebuild the
    buffer: the first fix was O(n) per event past the cap, which made a long
    page session look like a hang."""
    import time

    log = runlog.Log(level="debug")
    log.events = []
    log.event_cap = 200
    log.header(["inspect", "export.zip"], tool_version="0.0.0", core_version="0.1.0")
    log.info("input.fingerprint", sha256="a" * 64, members=3)
    # A batch of events far larger than the cap, in one go.
    for index in range(5000):
        log.detail("noise", index=index)
    kept = {e["event"] for e in log.events}
    for head in runlog.HEAD_EVENTS:
        assert head in kept, head
    assert len(log.events) <= log.event_cap + len(runlog.HEAD_EVENTS)

    # The cost is a function of the cap, so the timing runs at the shipped one:
    # the O(n) version measured 1.67 ms per event there, against 0.0018 ms
    # while filling. A smaller cap hides it, which is why the first version of
    # this test passed against the defect.
    big = runlog.Log(level="debug")
    big.events = []
    for index in range(big.event_cap + 200):
        big.detail("noise", index=index)
    start = time.perf_counter()
    for index in range(2000):
        big.detail("noise", index=index)
    per_event_ms = (time.perf_counter() - start) * 1000 / 2000
    assert per_event_ms < 0.05, per_event_ms


def test_a_person_in_a_case_pair_python_and_the_regex_disagree_about(log):
    """re.I and str.lower() are two definitions of case: they agree on ASCII
    and disagree on 77 pairs, and the disagreement raised KeyError inside a
    logging call. The pattern now names its own replacement."""
    runlog.person("Ilkay Yilmaz")
    runlog.person("Sabine Strauss")
    for title in ("dashboard by \u0130LKAY YILMAZ", "dashboard by ilkay yilmaz",
                  "report by \u017fabine \u017ftrauss", "report by SABINE STRAUSS"):
        runlog.info("widget.classified", title=title)
    for event in events(log):
        assert runlog.EXCLUDED_PERSON in event["title"], event["title"]
    assert not [e for e in events(log) if e["event"] == "log.failed"]


def test_a_file_path_is_logged_as_the_admin_typed_it(log):
    runlog.person("Thornbury")
    runlog.info("careless", path="/home/thornbury/exports/Thornbury-export.zip",
                out="/tmp/Thornbury-bundle.zip")
    event = events(log)[0]
    assert event["path"] == "/home/thornbury/exports/Thornbury-export.zip"
    assert event["out"] == "/tmp/Thornbury-bundle.zip"


def test_a_mail_address_is_excluded_even_when_nobody_taught_it(log):
    runlog.info("careless", note="mail to someone.else@example.invalid failed")
    assert "someone.else@example.invalid" not in text_of(log)
    assert runlog.EXCLUDED_MAIL in text_of(log)


def test_a_uuid_nobody_declared_to_be_content_is_excluded(log):
    stranger = "9f8e7d6c-5b4a-4938-8271-605f4e3d2c1b"
    runlog.info("careless", note=f"something about {stranger}")
    assert stranger not in text_of(log)
    assert runlog.EXCLUDED_ID in text_of(log)


def test_a_content_uuid_is_allowed_through_because_that_is_the_point(log):
    uuid = "6e8310ed-1753-45a4-aacc-7f1025c03d11"
    runlog.content_id(uuid)
    runlog.info("ref.resolved", to_uuid=uuid, to_name="[Fixture] Cluster List")
    assert uuid in text_of(log)
    assert "[Fixture] Cluster List" in text_of(log)


def test_the_exclusions_reach_inside_lists_and_dicts(log):
    runlog.info("careless", things=[{"password": "x", "note": "someone@example.invalid"}])
    body = text_of(log)
    assert "someone@example.invalid" not in body and '"x"' not in body


def test_a_count_of_owners_is_a_number_and_survives(log):
    runlog.info("owners.carried", owner_count=3)
    assert events(log)[0]["owner_count"] == 3


def test_the_argument_vector_teaches_the_owner_it_carries(log):
    argv = ["preview", "/tmp/export.zip",
            f"dashboard:2d7b8c1e-4f11-4c7a-9a55-0c1f2e3d4a5b@{OWNER}"]
    log.header(argv, tool_version="0.0.0", core_version="0.1.0")
    body = text_of(log)
    assert OWNER not in body
    assert "owner-1" in body
    assert "2d7b8c1e-4f11-4c7a-9a55-0c1f2e3d4a5b" in body
    assert "/tmp/export.zip" in body


def test_the_header_says_what_classes_of_thing_the_log_holds(log):
    log.header(["version"], tool_version="0.0.0", core_version="0.1.0")
    first = events(log)[0]
    assert first["event"] == "log.contents"
    assert "no credentials" in first["says"]
    assert "owner-1" in first["says"]
    header = events(log)[1]
    assert header["event"] == "run.start"
    for key in ("tool", "core", "python", "platform", "argv", "cwd"):
        assert key in header


# ---------------------------------------------------------------------------
# Fingerprints and diagnostics
# ---------------------------------------------------------------------------

def test_an_input_fingerprint_identifies_an_export_without_shipping_it(export_zip):
    data = export_zip.read_bytes()
    fingerprint = runlog.input_fingerprint(export_zip, data, ["configuration.json"],
                                           {"dashboards": 4, "type": "CUSTOM"})
    assert fingerprint["bytes"] == len(data)
    assert len(fingerprint["sha256"]) == 64
    assert fingerprint["manifest"] == {"dashboards": 4, "type": "CUSTOM"}


def test_an_output_fingerprint_hashes_every_member():
    written = {"a.json": b"{}", "b.json": b"[]"}
    fingerprint = runlog.output_fingerprint(written, ["a.json", "b.json"])
    assert fingerprint["members"] == 2 and fingerprint["bytes"] == 4
    assert {f["member"] for f in fingerprint["files"]} == {"a.json", "b.json"}
    assert all(len(f["sha256"]) == 64 for f in fingerprint["files"])


def test_the_diagnostics_file_is_one_file_that_names_its_own_contents():
    doc = runlog.diagnostics_document([{"event": "run.start", "t": 0.0}],
                                      header={"tool": "0.0.0"},
                                      source={"sha256": "abc"},
                                      bundle={"members": 3})
    lines = doc.splitlines()
    head = json.loads(lines[0])
    assert head["kind"] == "vcfcf-migrator-diagnostics"
    assert "no people and no credentials" in head["contains"]
    assert head["run"]["tool"] == "0.0.0" and head["bundle"]["members"] == 3
    assert json.loads(lines[1])["event"] == "run.start"


def test_open_log_refuses_a_level_or_a_format_it_does_not_know(tmp_path):
    with pytest.raises(runlog.BadLogSetting):
        runlog.open_log(str(tmp_path / "x.jsonl"), level="chatty")
    with pytest.raises(runlog.BadLogSetting):
        runlog.open_log(str(tmp_path / "x.jsonl"), fmt="yaml")


def test_each_event_reaches_the_file_before_the_next_one(tmp_path):
    """The page runs until someone stops it, usually with Ctrl-C. A log whose
    last events are still in a buffer then loses the part a support case is
    about."""
    path = tmp_path / "flushed.jsonl"
    log = runlog.open_log(str(path), level="debug")
    log.info("first", index=1)
    log.info("second", index=2)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    log.close()


def test_a_dropped_event_reaches_the_file_too(tmp_path, monkeypatch):
    """The failure path flushes like the ordinary one. It is the line that says
    an event was lost, so losing it in a buffer is the same defect twice."""
    def explode(_self, _value, people=True):
        raise RuntimeError("the redactor gave up")

    path = tmp_path / "failed.jsonl"
    log = runlog.open_log(str(path), level="debug")
    monkeypatch.setattr(runlog.Redactor, "text", explode)
    log.info("thing.happened", name="anything")
    written = path.read_text(encoding="utf-8")
    assert '"log.failed"' in written
    assert "anything" not in written
    log.close()


def test_open_log_appends_so_two_runs_are_one_story(tmp_path):
    path = tmp_path / "logs" / "run.jsonl"
    for _ in range(2):
        log = runlog.open_log(str(path), level="info")
        log.info("run.end", exit=0)
        log.close()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_the_in_memory_buffer_is_capped_and_says_what_it_dropped():
    log = runlog.Log(level="debug")
    log.events = []
    log.event_cap = 5
    for index in range(9):
        log.info("thing", index=index)
    assert len(log.events) <= 5 and log.dropped >= 4
    # The drop is not silent: the page keeps the events in memory for the
    # diagnostics file, and a file quietly missing its oldest events is the
    # thing the log exists to stop.
    assert any(e["event"] == "log.truncated" for e in log.events)


def test_the_buffer_never_drops_the_events_the_diagnostics_head_is_built_from():
    log = runlog.Log(level="debug")
    log.events = []
    log.event_cap = 6
    log.header(["inspect", "export.zip"], tool_version="0.0.0", core_version="0.1.0")
    log.info("input.fingerprint", sha256="a" * 64, members=3)
    for index in range(40):
        log.detail("noise", index=index)
    kept = [e["event"] for e in log.events]
    for head in runlog.HEAD_EVENTS:
        assert head in kept, (head, kept)
