"""Issue #31: a selection that depends on content the export does not carry
cannot be built.

Scott, 2026-10-05: "there should be no override - a missing dependency breaks
the bundle - it's something we need to guard against."

Scott, 2026-11-12, settling issue 64 (an export never carries some of the
definitions that come with the product, and nothing in an export says which):
"block on building a bundle with missing dependencies, expose a 'I know what
I'm doing, I've validated this is OOTB/whatever' and then let them proceed to
build the bundle". So the default is still a refusal, on the command line and
in the window. The window alone offers a way past it, and never as a switch:
each choice with a missing dependency is answered on the review step, the
answer is written in the report, and the checklist tells the person to
confirm it on the target.

A missing dependency is content the bundle would have to carry for the
selection to work on the target: a dashboard, view, super metric, alert,
symptom, recommendation or report the export does not hold, and the outbound
setting or payload template a notification rule names. A custom group's
policy is the other case: the tool never carries one and every instance has
its own, so it is listed as referenced but not carried and the build goes
ahead. What decides between the two is ``graph.is_missing_dependency``, keyed
by the kind the reference was parsed as, never by the shape of an identifier.

Fixture only. The fixture leaves objects out on purpose (a view no widget can
find, a super metric at the end of a formula chain, an alert and a webhook
endpoint a rule names), so its select-all is the refusal case and
``CLEAN_SELECTION`` is the build case.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request
import zipfile

import pytest

import ci_checks
from make_export_fixture import (
    ABSENT_ALERT_ID,
    ABSENT_SM_ID,
    ABSENT_SM_NAME,
    ABSENT_VIEW_ID,
    CLEAN_SELECTION,
    DASHBOARD_ID,
    EMPTY_DASHBOARD_ID,
    EXPECTED_CLEAN_ITEMS,
    GROUP_NAME,
    GROUP_POLICY_ID,
    OWNER,
    OWNER_2,
    VIEW_IDS,
    build_export_zip,
)
from vcfcf_migrator import graph as _graph
from vcfcf_migrator import runlog as _runlog
from vcfcf_migrator import selection as _selection
from vcfcf_migrator.cli import build_parser, main
from vcfcf_migrator.export_reader import read_export, read_members
from pages import save_to, visible
from vcfcf_migrator.ui import PageState, dispatch

DASH = f"dashboard:{DASHBOARD_ID}@{OWNER}"
DASH_CLEAN = f"dashboard:{EMPTY_DASHBOARD_ID}@{OWNER_2}"
GROUP = f"customgroup:{GROUP_NAME}"

# What the fixture's seven missing edges are, by category, and which way each
# goes. The counts are the ones the report, the CHANGELOG and the design
# record quote; a fixture change that moves one fails here first.
REFUSED_ON_THE_FIXTURE = {"supermetric": 2, "alert": 1, "outboundsetting": 1}
NOT_CARRIED_ON_THE_FIXTURE = {"policy": 1, "view": 4}


@pytest.fixture
def graph(export_zip):
    return _graph.build_graph(read_members(export_zip).data)


def _lines(tmp_path, lines):
    path = tmp_path / "picks.txt"
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def _run(argv, log_path, level="detail"):
    """Run the command line with a jsonl log and return (exit code, events)."""
    code = main(["--log", str(log_path), "--log-level", level, *argv])
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    return code, events


# ---------------------------------------------------------------------------
# The classification: from the parsed reference, never from the identifier
# ---------------------------------------------------------------------------

def test_every_fixture_gap_is_classified_and_the_counts_are_the_documented_ones(graph):
    refused = {}
    not_carried = {}
    for gap in graph.missing:
        bucket = refused if _graph.is_missing_dependency(gap) else not_carried
        bucket[gap.kind] = bucket.get(gap.kind, 0) + 1
    assert refused == REFUSED_ON_THE_FIXTURE
    assert not_carried == NOT_CARRIED_ON_THE_FIXTURE
    assert len(graph.missing) == sum(REFUSED_ON_THE_FIXTURE.values()) + sum(
        NOT_CARRIED_ON_THE_FIXTURE.values())


def test_a_policy_is_the_only_kind_that_is_not_a_dependency(graph):
    """The table is keyed by kind, and a policy is in it because the existing
    MissingEdge reason already says why: policies.xml is a member the tool
    never carries, and every instance has policies of its own."""
    assert set(_graph.NOT_A_DEPENDENCY) == {"policy"}
    policy = next(g for g in graph.missing if g.kind == "policy")
    assert policy.ident == GROUP_POLICY_ID
    assert not _graph.is_missing_dependency(policy)
    assert "never carries" in _graph.missing_reason(policy)
    assert "check the group policy assignment" in _graph.missing_reason(policy)


@pytest.mark.parametrize("kind", ["supermetric", "alert", "outboundsetting"])
def test_a_refused_kind_says_why_the_classification_stops_there(graph, kind):
    """For a view or a super metric the honest position is that nothing in an
    export tells built-in content apart from custom content left out of it,
    and the reason line says so rather than hiding behind "not in this
    export". For an outbound setting the reason is different and also said."""
    gap = next(g for g in graph.missing if g.kind == kind)
    assert _graph.is_missing_dependency(gap)
    reason = _graph.missing_reason(gap)
    assert reason.startswith("it is not in this export")
    if kind == "outboundsetting":
        assert "admin's own endpoint configuration" in reason
    else:
        assert "ships with the product or a management pack" in reason
        assert "refused rather than guessed at" in reason


def test_the_classification_reads_the_kind_the_reference_was_parsed_as():
    """A gap is classified by what the field it was read from says the target
    is, not by what the identifier looks like. The same uuid is a missing
    dependency as a view and not one as a policy."""
    same = GROUP_POLICY_ID
    assert _graph.is_missing_dependency(_graph.MissingEdge("x", "view", same, "via"))
    assert not _graph.is_missing_dependency(_graph.MissingEdge("x", "policy", same, "via"))
    assert _graph.is_missing_dependency(
        _graph.MissingEdge("x", "supermetric", ABSENT_SM_NAME, "via"))


def test_tree_says_which_gaps_a_build_refuses(graph, export_zip, capsys):
    text = _graph.render_tree(graph)
    assert "of which 4 are missing dependencies: a build refuses" in text
    assert text.count("MISSING DEPENDENCY ") == 4
    assert text.count("referenced but not carried: policy") == 1
    assert main(["tree", "--json", str(export_zip)]) == 0
    doc = json.loads(capsys.readouterr().out)
    flags = {(m["kind"], m["ident"]): m["missing_dependency"] for m in doc["missing"]}
    assert flags[("policy", GROUP_POLICY_ID)] is False
    assert flags[("view", ABSENT_VIEW_ID)] is False
    assert all(m["reason"] for m in doc["missing"])


# ---------------------------------------------------------------------------
# AC1: the command refuses, exits non-zero, writes nothing, names every one
# ---------------------------------------------------------------------------

def test_select_all_of_the_fixture_is_refused_and_names_every_missing_dependency(
        tmp_path, export_zip, capsys):
    out = tmp_path / "bundle.zip"
    assert main(["build", str(export_zip), "--select-all", "--out", str(out)]) == 1
    assert not out.exists()
    err = capsys.readouterr().err
    assert "refused: the selection depends on 4 objects this export does not carry" in err
    assert err.rstrip().endswith("no bundle written")
    # The report names affected objects without exposing implementation IDs.
    assert '[Fixture] VM List' in err and '[Fixture] Cluster rule' in err
    assert 'required super metric' in err and 'required outbound setting' in err
    assert ABSENT_SM_ID not in err
    assert GROUP_POLICY_ID not in err



def test_a_subset_with_one_missing_dependency_is_refused_too(tmp_path, export_zip, capsys):
    """The VM List view names one super metric the export does not carry; one
    is enough, and the refusal names that one and nothing else."""
    out = tmp_path / "bundle.zip"
    picks = _lines(tmp_path, [f"view:{VIEW_IDS[1]}"])
    assert main(["build", str(export_zip), "--select", str(picks), "--out", str(out)]) == 1
    assert not out.exists()
    err = capsys.readouterr().err
    assert "depends on 1 object this export does not carry" in err
    assert "[Fixture] VM List" in err and "required super metric" in err
    assert ABSENT_SM_ID not in err


def test_a_refused_build_with_json_says_so_on_stdout_too(tmp_path, export_zip, capsys):
    out = tmp_path / "bundle.zip"
    assert main(["build", "--json", str(export_zip), "--select-all", "--out", str(out)]) == 1
    assert not out.exists()
    doc = json.loads(capsys.readouterr().out)
    assert "depends on 4 objects" in doc["refused"]
    assert len(doc["selection"]["missing_dependencies"]) == 4
    assert sorted(m["kind"] for m in doc["selection"]["not_carried"]) == ["policy", "view", "view", "view", "view"]
    assert all(m["missing_dependency"] for m in doc["selection"]["missing_dependencies"])


def test_a_refusal_leaves_no_file_even_where_one_was(tmp_path, export_zip):
    """Refusing happens before the writer opens anything, so a bundle from an
    earlier, clean build at the same path is left exactly as it was."""
    out = tmp_path / "bundle.zip"
    picks = _lines(tmp_path, CLEAN_SELECTION)
    assert main(["build", str(export_zip), "--select", str(picks), "--out", str(out)]) == 0
    before = out.read_bytes()
    assert main(["build", str(export_zip), "--select-all", "--out", str(out)]) == 1
    assert out.read_bytes() == before


def test_the_refusal_is_in_the_log_with_every_dependency_and_a_non_zero_exit(
        tmp_path, export_zip, config_dir):
    code, events = _run(["build", str(export_zip), "--select-all",
                         "--out", str(tmp_path / "bundle.zip")], tmp_path / "run.jsonl")
    assert code == 1
    codes = [e["event"] for e in events]
    assert "output.fingerprint" not in codes
    refused = [e for e in events if e["event"] == "build.refused"]
    assert len(refused) == 1 and refused[0]["missing_dependencies"] == 4
    assert refused[0]["lvl"] == "error"
    named = [e for e in events if e["event"] == "build.missing_dependency"]
    assert len(named) == 4 and all(e["reason"] and e["wants"] for e in named)
    assert {e["ident"] for e in named} >= {ABSENT_SM_ID, ABSENT_ALERT_ID}
    # The closure said it earlier too, at warn, so the page's log carries it
    # before any build is attempted.
    assert [e for e in events if e["event"] == "closure.missing_dependency"]
    assert events[-1]["event"] == "run.end" and events[-1]["exit"] == 1
    # The same log satisfies the check the workflows run on the installed binary.
    assert "named 4 missing dependencies" in ci_checks.check_refusal(
        (tmp_path / "run.jsonl").read_text(encoding="utf-8"))


def test_build_has_no_flag_that_gets_past_the_gate():
    """No override on the command line, by the operator's decision. Any flag
    whose name suggests one is a usage error, and the parser's own option
    list carries no such word."""
    parser = build_parser()
    build = next(a for a in parser._actions if isinstance(a, type(parser._subparsers._actions[-1]))
                 ).choices["build"]
    words = " ".join(opt for action in build._actions for opt in action.option_strings)
    for forbidden in ("force", "override", "ignore", "allow", "skip", "unsafe", "anyway"):
        assert forbidden not in words.lower(), words
    for flag in ("--force", "--ignore-missing", "--allow-missing-dependencies", "--no-check"):
        with pytest.raises(SystemExit) as e:
            build.parse_args(["x.zip", "--select-all", "--out", "o.zip", flag])
        assert e.value.code == 2


def test_the_window_has_no_switch_past_the_gate_only_an_answer_per_choice(config_dir, export_zip):
    state = PageState(str(export_zip))
    state.toggle(DASH, on=True)
    dispatch(state, '/go', {'to': 'review'})
    page = state.render()
    main_part = page.split('<main>')[1].split('</main>')[0]
    # Nothing to tick and no setting: the one way on is the answer beside the
    # problem it is about, and the safe answer is the filled button.
    assert 'type="checkbox"' not in main_part
    assert re.search(r'lat-btn--primary[^>]*data-act="/leave"', main_part)
    assert not re.search(r'lat-btn--primary[^>]*data-act="/keep-in"', main_part)
    assert ('I have checked this already exists on the target. Keep it in anyway' in main_part)
    assert re.search(r'id="next"[^>]*disabled', page)
    state.log.close()


# ---------------------------------------------------------------------------
# AC2: only policy gaps, the bundle is written and they are listed
# ---------------------------------------------------------------------------

def test_a_selection_whose_only_gap_is_a_policy_builds_and_lists_it(tmp_path, export_zip,
                                                                     capsys):
    out = tmp_path / "bundle.zip"
    picks = _lines(tmp_path, [GROUP])
    assert main(["build", str(export_zip), "--select", str(picks), "--out", str(out)]) == 0
    assert out.exists() and read_export(out).counts() == {"customgroup": 1}
    report = capsys.readouterr().out
    assert "References to check: 1" in report
    assert GROUP_NAME in report and "check the policy assignment" in report
    assert "missing dependencies" not in report


def test_the_clean_selection_closes_with_no_missing_dependency_and_is_maximal(graph):
    """Every object the fixture can build is in CLEAN_SELECTION, and nothing
    in it depends on anything absent. Both directions, so a fixture change
    that frees or breaks an object fails here until the list follows."""
    keys = _selection.resolve(graph, list(CLEAN_SELECTION))
    closed = _selection.close(graph, keys)
    assert closed.missing_dependencies() == []
    assert [g.kind for g in closed.not_carried()] == ["policy"]
    carried = {(n.kind, n.name, n.uuid) for n in _selection.selected_nodes(graph, closed)}
    assert carried == EXPECTED_CLEAN_ITEMS
    for node in graph.ordered():
        own = _selection.close(graph, [node.key])
        if node.key in closed.keys:
            assert own.missing_dependencies() == [], node.label()
        else:
            assert own.missing_dependencies(), f"{node.label()} builds and is not listed"


def test_the_clean_selection_builds_and_the_ci_checker_agrees(tmp_path, export_zip, capsys):
    out = tmp_path / "bundle.zip"
    picks = tmp_path / "picks.txt"
    picks.write_text(ci_checks.clean_selection(), encoding="utf-8")
    assert picks.read_text(encoding="utf-8").splitlines() == list(CLEAN_SELECTION)
    assert main(["build", str(export_zip), "--select", str(picks), "--out", str(out)]) == 0
    report = capsys.readouterr().out
    assert "References to check: 1" in report and "missing dependencies" not in report
    assert main(["inspect", "--json", str(out)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert "listing matches the fixture" in ci_checks.check_listing(doc, bundle=True)
    assert main(["build", str(export_zip), "--select-all", "--out", str(tmp_path / "x.zip")]) == 1


def test_the_ci_checker_notices_a_refusal_that_did_not_happen(tmp_path, export_zip, config_dir):
    code, _events = _run(["build", str(export_zip), "--select",
                          str(_lines(tmp_path, CLEAN_SELECTION)),
                          "--out", str(tmp_path / "bundle.zip")], tmp_path / "clean.jsonl")
    assert code == 0
    with pytest.raises(AssertionError):
        ci_checks.check_refusal((tmp_path / "clean.jsonl").read_text(encoding="utf-8"))


def test_corpus_check_still_round_trips_and_counts_what_a_build_would_refuse(tmp_path, capsys):
    """The regression tier's select-all goes to a scratch bundle that is
    deleted, to check the container rebuild on real exports; it is not a way
    to obtain a bundle. The line says what a build would refuse."""
    (tmp_path / "a.zip").write_bytes(build_export_zip())
    assert main(["corpus-check", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.startswith("ok       a.zip"))
    assert "documents byte-identical" in line
    assert "4 missing dependencies a build would refuse" in line
    assert "5 edges referenced but not carried" in line
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.zip"]


# ---------------------------------------------------------------------------
# AC3: the page shows the refusal and writes nothing
# ---------------------------------------------------------------------------

def test_the_page_refuses_a_save_with_an_unanswered_missing_dependency_and_writes_nothing(
        config_dir, export_zip, tmp_path):
    state = PageState(str(export_zip))
    state.toggle(DASH, on=True)
    # Ticking is allowed, and says at once that Review will ask.
    assert DASH in state.picked
    assert 'not in this export' in state.note.text
    assert 'You will be asked at the Review step whether to keep it.' in state.note.text
    out = tmp_path / "page.zip"
    save_to(state, out)
    assert not out.exists() and state.save_state != 'saved'
    assert state.screen == 'review'
    issue, = state.open_issues()
    assert issue.title.endswith('needs something this export does not have')
    assert len(issue.hard) == len(state.selection.missing_dependencies()) > 0
    state.issue = issue.id
    page = visible(state.render())
    assert '1 thing to decide before you can save' in page
    # Every missing dependency of this choice is named, in words, with the
    # path from the choice to it. No id is shown for any of them.
    assert f'the super metric "{ABSENT_SM_NAME}", which is not in this export' in page
    for ident in (ABSENT_VIEW_ID, ABSENT_SM_ID, GROUP_POLICY_ID):
        assert ident not in page
    state.log.close()


def test_the_page_says_so_before_review(config_dir, export_zip):
    state = PageState(str(export_zip))
    key = f"view:{VIEW_IDS[1]}"
    dispatch(state, '/focus', {'key': key})
    page = visible(state.render())
    assert 'Needs something not in this export' in page          # on the row
    assert 'Needs something that is not in this export.' in page  # in its panel
    assert 'Review will ask you to confirm' in page
    assert ABSENT_SM_ID not in page
    # It is kept out of a bulk add, like everything else that needs a decision.
    state.add_shown()
    assert key not in state.picked
    assert 'that need something this export does not have' in state.note.text
    state.log.close()


def test_keeping_it_in_is_explicit_and_is_written_down(config_dir, export_zip, tmp_path):
    state = PageState(str(export_zip))
    state.toggle(DASH, on=True)
    issue, = state.open_issues()
    missing = len(issue.hard)
    state.keep_in(issue.id)
    assert not state.open_issues()
    assert state.accepted_missing() == [gap for _node, gap in issue.hard]
    state.screen = 'review'
    page = visible(state.render())
    assert 'Ready to save' in page
    assert 'except 1 thing you confirmed is already on the target' in page
    out = tmp_path / "kept.zip"
    save_to(state, out)
    assert out.exists() and state.save_state == 'saved'
    report = out.with_name('kept-report.txt').read_text(encoding='utf-8')
    assert 'Kept in against the usual advice' in report
    assert report.count('You confirmed that it already exists on the target.') == missing
    assert f'missing dependencies kept in by decision: {missing}' in report
    checklist = out.with_name('kept-checklist.txt').read_text(encoding='utf-8')
    assert checklist.count('Confirm that ') == missing
    assert 'You kept it in because you had checked that the target already has it.' in checklist
    # And on the last screen, as tasks to tick.
    assert sum(1 for task in state.tasks() if task.id.startswith('confirm-')) == missing
    state.log.close()


def test_leaving_it_out_is_the_recommended_answer_and_needs_no_override(config_dir, export_zip,
                                                                       tmp_path):
    state = PageState(str(export_zip))
    state.toggle(DASH_CLEAN, on=True)
    state.toggle(DASH, on=True)
    issue, = state.open_issues()
    state.leave_out(issue.pick)
    assert not state.open_issues() and state.picked == [DASH_CLEAN]
    assert 'Left out' in state.note.text and state.undo
    out = tmp_path / "left.zip"
    save_to(state, out)
    assert out.exists() and not state.accepted_missing()
    assert 'Confirm that ' not in out.with_name('left-checklist.txt').read_text(encoding='utf-8')
    assert 'Kept in against the usual advice' not in out.with_name('left-report.txt').read_text(encoding='utf-8')
    state.log.close()


def test_the_page_saves_a_clean_selection_and_lists_the_policy(config_dir, export_zip, tmp_path):
    state = PageState(str(export_zip))
    state.toggle(DASH_CLEAN, on=True)
    state.toggle(GROUP, on=True)
    assert not state.open_issues()
    assert 'The group arrives without a policy of its own' in [i.title for i in state.issues()]
    out = tmp_path / "clean.zip"
    save_to(state, out)
    assert state.save_state == 'saved' and state.built_path == str(out)
    assert "References to check: 1" in state.build_report
    assert zipfile.ZipFile(out).namelist()
    state.log.close()


def test_the_page_and_the_command_go_through_one_gate(config_dir, export_zip, tmp_path,
                                                      monkeypatch):
    """Both ways in call ``refuse_missing_dependencies``; a second gate in
    either would be a second set of rules to drift. Only the window ever
    passes it anything, and only what was answered for."""
    calls = []
    real = _selection.refuse_missing_dependencies

    def spy(graph, selection, accepted=()):
        calls.append((len(selection.keys), len(list(accepted))))
        return real(graph, selection, accepted)

    from vcfcf_migrator import bundle as _bundle
    monkeypatch.setattr(_bundle, "refuse_missing_dependencies", spy)
    monkeypatch.setattr(_selection, "refuse_missing_dependencies", spy)
    state = PageState(str(export_zip))
    state.toggle(DASH, on=True)
    carried = len(state.selection.keys)
    save_to(state, tmp_path / "a.zip")
    assert calls == []                      # refused before the writer: unanswered at Review
    picks = _lines(tmp_path, [DASH])
    assert main(["build", str(export_zip), "--select", str(picks),
                 "--out", str(tmp_path / "b.zip")]) == 1
    assert calls and all(accepted == 0 for _keys, accepted in calls)
    assert not (tmp_path / "a.zip").exists() and not (tmp_path / "b.zip").exists()
    calls.clear()
    issue, = state.open_issues()
    state.keep_in(issue.id)
    save_to(state, tmp_path / "a.zip")
    assert calls == [(carried, len(issue.hard))] and (tmp_path / "a.zip").exists()
    state.log.close()
    _runlog.set_current(None)


def test_the_writer_lets_through_only_what_was_answered_for(graph, export_zip, tmp_path):
    from vcfcf_migrator.bundle import build_bundle
    members = read_members(export_zip)
    picked = _selection.select_all(graph)
    gaps = picked.missing_dependencies()
    assert len(gaps) == sum(REFUSED_ON_THE_FIXTURE.values()) > 1
    out = tmp_path / 'partly.zip'
    with pytest.raises(_selection.MissingDependency) as refused:
        build_bundle(members.data, members.order, graph, picked, out, keep_ownership=True,
                     accepted_missing=gaps[:1])
    assert refused.value.gaps == gaps[1:] and not out.exists()
    result = build_bundle(members.data, members.order, graph, picked, out, keep_ownership=True,
                          accepted_missing=gaps)
    assert out.exists()
    assert sum(1 for task in result.tasks if task.id.startswith('confirm-')) == len(gaps)


def test_shared_writer_refuses_without_touching_existing_output(graph, export_zip, tmp_path):
    from vcfcf_migrator.bundle import build_bundle
    members = read_members(export_zip)
    target = tmp_path / 'existing.zip'
    target.write_bytes(b'keep the existing file')
    with pytest.raises(_selection.MissingDependency):
        build_bundle(members.data, members.order, graph, _selection.select_all(graph), target)
    assert target.read_bytes() == b'keep the existing file'
