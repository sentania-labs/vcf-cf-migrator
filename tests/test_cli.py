"""CLI contract: version, inspect, the M3 stubs."""
from __future__ import annotations

import argparse
import json
import re

import pytest

import vcfcf_core
from make_export_fixture import (
    EXPECTED_CARRIED,
    EXPECTED_DASHBOARD_LISTINGS,
    EXPECTED_ITEMS,
    MEMBER_FOR_KIND,
    build_export_zip,
)
from vcfcf_migrator import __version__
from vcfcf_migrator.cli import build_parser, main
from vcfcf_migrator.export_reader import read_export


def test_version_prints_both_versions(capsys):
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert f"vcfcf-migrator {__version__}" in out
    assert f"vcfcf_core {vcfcf_core.__version__}" in out
    assert re.search(r"vcfcf-migrator \d+\.\d+", out)


@pytest.mark.parametrize("command", ["inspect", "tree", "build", "preview",
                                    "corpus-check", "ui", "version"])
def test_every_subcommand_takes_the_shared_options_after_it(command):
    """A shared option typed after the subcommand has to parse, and argparse
    refuses an option a subparser does not have. ``ui x.zip --log run.log``
    failed with "unrecognized arguments" until the shared parent carried the
    corpus flag as well as the log ones."""
    parser = build_parser()
    subparsers = [a for a in parser._actions
                  if isinstance(a, argparse._SubParsersAction)][0]
    options = set()
    for action in subparsers.choices[command]._actions:
        options.update(action.option_strings)
    for flag in ("--corpus", "--log", "--log-level", "--log-format"):
        assert flag in options, (command, flag)


def test_the_readme_headline_command_parses(export_zip):
    args = build_parser().parse_args(["ui", str(export_zip)])
    assert args.command == "ui" and args.zip == str(export_zip)


def test_inspect_lists_every_item(export_zip, capsys):
    assert main(["inspect", str(export_zip)]) == 0
    out = capsys.readouterr().out
    for kind, name, uuid in EXPECTED_ITEMS:
        line = next((l for l in out.splitlines() if l.strip().startswith(kind + " ") and name in l), None)
        assert line is not None, (kind, name, out)
        if uuid:
            assert uuid in line
        else:
            assert "(no uuid)" in line
    assert "source version" not in out.lower()
    assert "marker: 1757800000000000000L.v1 (format v1, owner aaaa1111" in out
    assert "carried, not inspected: 1" in out
    for name in EXPECTED_CARRIED:
        assert name in out


def test_inspect_json_carries_the_same_items(export_zip, capsys):
    assert main(["inspect", "--json", str(export_zip)]) == 0
    doc = json.loads(capsys.readouterr().out)
    got = {(i["kind"], i["name"], i["uuid"]) for i in doc["items"]}
    assert got == EXPECTED_ITEMS
    assert doc["counts"]["dashboard"] == EXPECTED_DASHBOARD_LISTINGS
    assert set(doc["carried"]) == EXPECTED_CARRIED
    assert "source_version" not in doc
    # Derived from the fixture's own expectations rather than typed, so a
    # fixture that grows one view does not fail here for the wrong reason.
    assert doc["counts"]["view"] == sum(1 for kind, _n, _u in EXPECTED_ITEMS if kind == "view")


def test_read_export_uses_the_core_readers_for_dashboards_and_supermetrics(export_zip):
    from make_export_fixture import OWNER

    export = read_export(export_zip)
    sources = {(i.kind, i.source) for i in export.items}
    assert ("dashboard", f"dashboards/{OWNER}") in sources
    assert ("supermetric", "supermetrics.json") in sources
    assert ("view", "views.zip") in sources
    assert ("report", "reports.zip") in sources
    assert ("symptom", "symptomdefs.xml") in sources
    assert ("recommendation", "recommendationdefs.xml") in sources
    assert ("outboundsetting", "outboundsettings.json") in sources


def test_a_manifest_format_version_integer_is_never_a_product_version(tmp_path, capsys):
    """Review W1: a bare {"version": 1} in configuration.json must not be
    read as a product version. The reader does not look for one at all."""
    import io
    import zipfile

    src = zipfile.ZipFile(io.BytesIO(build_export_zip()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in src.namelist():
            data = src.read(n)
            if n == "configuration.json":
                data = json.dumps({"version": 1, "type": "CUSTOM"}).encode()
            z.writestr(n, data)
    path = tmp_path / "fmt.zip"
    path.write_bytes(out.getvalue())
    assert main(["inspect", str(path)]) == 0
    out = capsys.readouterr().out
    # The manifest's own keys are echoed as the export wrote them, so
    # "version=1" may appear there; what must not appear is the tool saying
    # anything about a source version of its own.
    assert "source version" not in out.lower()


@pytest.mark.parametrize("drop", [
    ["reports.zip", "customgroups.json"],
    ["symptomdefs.xml", "alertdefs.xml", "recommendationdefs.xml"],
    ["notificationrules.json", "payloadtemplates.json", "outboundsettings.json", "supermetrics.json"],
])
def test_inspect_tolerates_missing_optional_members(tmp_path, capsys, drop):
    """Review W5: an export carrying fewer content types lists fewer items,
    exits 0, and never tracebacks."""
    path = tmp_path / "partial.zip"
    path.write_bytes(build_export_zip(without=drop))
    assert main(["inspect", "--json", str(path)]) == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    doc = json.loads(captured.out)
    kinds = {i["kind"] for i in doc["items"]}
    for member in drop:
        for kind, owner in MEMBER_FOR_KIND.items():
            if owner == member:
                assert kind not in kinds, (kind, member)
    assert "dashboard" in kinds and "view" in kinds


def test_the_same_template_in_two_members_is_listed_once(tmp_path, capsys):
    """A full export embeds the payload template in notificationrules.json
    too; the listing must not show it twice."""
    import io
    import zipfile

    from make_export_fixture import TEMPLATE_ID

    src = zipfile.ZipFile(io.BytesIO(build_export_zip()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in src.namelist():
            data = src.read(n)
            if n == "notificationrules.json":
                doc = json.loads(data)
                doc["NotificationRules"]["notificationTemplateDataSet"] = [{"NotificationTemplateData": {
                    "id": TEMPLATE_ID, "Name": "[Fixture] Cluster template"}}]
                data = json.dumps(doc).encode()
            z.writestr(n, data)
    path = tmp_path / "dup.zip"
    path.write_bytes(out.getvalue())
    assert main(["inspect", "--json", str(path)]) == 0
    doc = json.loads(capsys.readouterr().out)
    # The fixture's two payload templates, with the embedded copy of the
    # first collapsed rather than listed a third time.
    assert doc["counts"]["notificationtemplate"] == 2


def test_a_dashboard_shared_by_two_owners_lists_once_per_owner(export_zip, capsys):
    """Five dashboards on a real 9.x export sit under two owners with the
    same uuid; dashboardsByOwner counts each, so the listing must too."""
    from make_export_fixture import (
        DASHBOARD_ID,
        DASHBOARD_ID_2,
        EMPTY_DASHBOARD_ID,
        EXPECTED_DASHBOARD_LISTINGS,
        OWNER,
        OWNER_2,
    )

    assert main(["inspect", "--json", str(export_zip)]) == 0
    doc = json.loads(capsys.readouterr().out)
    dashes = [(i["uuid"], i["source"]) for i in doc["items"] if i["kind"] == "dashboard"]
    assert sorted(dashes) == sorted([
        (DASHBOARD_ID, f"dashboards/{OWNER}"),
        (DASHBOARD_ID, f"dashboards/{OWNER_2}"),
        (DASHBOARD_ID_2, f"dashboards/{OWNER_2}"),
        (EMPTY_DASHBOARD_ID, f"dashboards/{OWNER_2}"),
    ])
    assert (doc["counts"]["dashboard"] == doc["manifest"]["dashboards"]
            == EXPECTED_DASHBOARD_LISTINGS)


def test_payload_templates_read_both_nestings(tmp_path, capsys):
    """A list under NotificationTemplateData (the fixture) or one dict per
    entry: both list every template."""
    import io
    import zipfile

    from make_export_fixture import TEMPLATE_ID, TEMPLATE_ID_2

    src = zipfile.ZipFile(io.BytesIO(build_export_zip()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in src.namelist():
            data = src.read(n)
            if n == "payloadtemplates.json":
                doc = json.loads(data)
                tpls = doc["NotificationTemplate"]["notificationTemplateData"][0]["NotificationTemplateData"]
                doc["NotificationTemplate"]["notificationTemplateData"] = [{"NotificationTemplateData": t} for t in tpls]
                data = json.dumps(doc).encode()
            z.writestr(n, data)
    per_entry = tmp_path / "per-entry.zip"
    per_entry.write_bytes(out.getvalue())
    as_list = tmp_path / "as-list.zip"
    as_list.write_bytes(build_export_zip())
    for zip_path in (per_entry, as_list):
        assert main(["inspect", "--json", str(zip_path)]) == 0
        doc = json.loads(capsys.readouterr().out)
        got = {(i["name"], i["uuid"]) for i in doc["items"] if i["kind"] == "notificationtemplate"}
        assert got == {("[Fixture] Cluster template", TEMPLATE_ID), ("[Fixture] Host template", TEMPLATE_ID_2)}, zip_path


def test_notification_rules_read_both_nestings(tmp_path, capsys):
    """9.1.1 puts the list of rules under one NotificationRule key (the
    fixture's shape); 8.x carries one rule dict per entry. Both list every
    rule."""
    import io
    import zipfile

    from make_export_fixture import EMPTY_RULE_ID, RULE_ID, RULE_ID_2

    src = zipfile.ZipFile(io.BytesIO(build_export_zip()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in src.namelist():
            data = src.read(n)
            if n == "notificationrules.json":
                doc = json.loads(data)
                rules = doc["NotificationRules"]["notificationRules"][0]["NotificationRule"]
                doc["NotificationRules"]["notificationRules"] = [{"NotificationRule": r} for r in rules]
                data = json.dumps(doc).encode()
            z.writestr(n, data)
    path = tmp_path / "eightx.zip"
    path.write_bytes(out.getvalue())
    as_list = tmp_path / "as-list.zip"
    as_list.write_bytes(build_export_zip())
    for zip_path in (path, as_list):
        assert main(["inspect", "--json", str(zip_path)]) == 0
        doc = json.loads(capsys.readouterr().out)
        got = {(i["name"], i["uuid"]) for i in doc["items"] if i["kind"] == "notificationrule"}
        assert got == {("[Fixture] Cluster rule", RULE_ID),
                       ("[Fixture] Host rule", RULE_ID_2),
                       ("[Fixture] Rule with no conditions", EMPTY_RULE_ID)}, zip_path


def test_inspect_refuses_a_zip_that_is_not_a_content_export(tmp_path, capsys):
    """Review N5: no marker and no configuration.json is not an export."""
    import zipfile

    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w"):
        pass
    assert main(["inspect", str(empty)]) == 1
    assert "not a content export" in capsys.readouterr().err

    junk = tmp_path / "junk.zip"
    with zipfile.ZipFile(junk, "w") as z:
        z.writestr("readme.txt", "hello")
        z.writestr("supermetrics.json", "{}")
    assert main(["inspect", str(junk)]) == 1
    assert "not a content export" in capsys.readouterr().err

    marker_only = tmp_path / "marker.zip"
    with zipfile.ZipFile(marker_only, "w") as z:
        z.writestr("1757800000000000000L.v1", "owner")
    assert main(["inspect", str(marker_only)]) == 0


def test_inspect_rejects_a_non_zip(tmp_path, capsys):
    path = tmp_path / "nope.zip"
    path.write_text("hello")
    assert main(["inspect", str(path)]) == 1
    assert "not a zip file" in capsys.readouterr().err
    assert main(["inspect", str(tmp_path / "missing.zip")]) == 1


@pytest.mark.parametrize("argv,code", [
    (["tree", "x.zip"], 1),                          # unreadable input
    (["build", "x.zip", "--out", "o.zip"], 2),       # neither --select nor --select-all
    (["corpus-check", "nowhere"], 1),                # no such directory
])
def test_bad_input_exits_without_a_traceback(argv, code, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(argv) == code
    assert "Traceback" not in capsys.readouterr().err


def test_no_command_prints_help(capsys):
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().out


def test_exit_code_survives_the_console_script(tmp_path):
    """The installed entry point, not just main(): no traceback on a refusal."""
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "vcfcf_migrator", "tree", "x.zip"],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 1
    assert "Traceback" not in r.stderr


def test_the_ci_checker_agrees_with_the_fixture(export_zip, capsys, tmp_path):
    """CI asserts the console script's listing against the fixture's own
    expectations rather than a number typed into the workflow, which is how a
    hand-copied 16 survived the fixture growing to 20 while the suite stayed
    green. This test is what keeps the checker honest locally: it fails here
    before it fails in CI."""
    import ci_checks

    assert main(["inspect", "--json", str(export_zip)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert "listing matches the fixture" in ci_checks.check_listing(doc)

    out = tmp_path / "bundle.zip"
    assert main(["build", str(export_zip), "--select-all", "--out", str(out)]) == 0
    capsys.readouterr()
    assert main(["inspect", "--json", str(out)]) == 0
    built = json.loads(capsys.readouterr().out)
    assert "no unreadable member carried" in ci_checks.check_listing(built, bundle=True)


def test_the_ci_checker_notices_a_listing_that_drifted(export_zip, capsys):
    import ci_checks

    assert main(["inspect", "--json", str(export_zip)]) == 0
    doc = json.loads(capsys.readouterr().out)
    doc["items"] = doc["items"][:-1]
    with pytest.raises(AssertionError) as e:
        ci_checks.check_listing(doc)
    assert "does not match the fixture" in str(e.value)


def test_inspect_reports_navigation_links_pointing_outside_the_export(tmp_path, capsys):
    """A dashboard navigation is a link to another dashboard. Every target in
    the corpus resolves to nothing in any export, so those links do not land
    after an import, and the listing is where an admin sees it before deciding
    what to carry. Whether such a target is a dependency the bundle should
    chase is M5's question (migrator issue #3)."""
    import io
    import zipfile

    src = zipfile.ZipFile(io.BytesIO(build_export_zip()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name in src.namelist():
            data = src.read(name)
            if name.endswith("/"):
                # A zip directory entry, which every real export carries and
                # which is not a nested zip: copied across untouched.
                z.writestr(name, data)
                continue
            if name.startswith("dashboards/"):
                inner = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(data)) as dash_zip:
                    with zipfile.ZipFile(inner, "w") as w:
                        for member in dash_zip.namelist():
                            body = dash_zip.read(member)
                            if member.endswith("dashboard.json"):
                                doc = json.loads(body)
                                first = doc["dashboards"][0]
                                widget = str(first["widgets"][0].get("id") or "w1")
                                first["widgets"][0]["id"] = widget
                                first["dashboardNavigations"] = {
                                    widget: [
                                        # one target inside the document, one
                                        # naming something it does not carry
                                        {"id": widget, "widgets": []},
                                        {"id": "a-dashboard-not-in-this-export",
                                         "widgets": []},
                                    ]}
                                body = json.dumps(doc).encode()
                            w.writestr(member, body)
                data = inner.getvalue()
            z.writestr(name, data)
    path = tmp_path / "with-navigations.zip"
    path.write_bytes(out.getvalue())

    assert main(["inspect", str(path)]) == 0
    text = capsys.readouterr().out
    assert "dashboard navigation links pointing outside this export: 2" in text
    assert main(["inspect", "--json", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["navigation_gaps"] == 2

