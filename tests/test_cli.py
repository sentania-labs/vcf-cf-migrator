"""CLI contract: version, inspect, the floor, the M3 stubs."""
from __future__ import annotations

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
from vcfcf_migrator.cli import main
from vcfcf_migrator.export_reader import read_export


def test_version_prints_both_versions(capsys):
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert f"vcfcf-migrator {__version__}" in out
    assert f"vcfcf_core {vcfcf_core.__version__}" in out
    assert re.search(r"vcfcf-migrator \d+\.\d+", out)


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
    assert "source version: not declared" in out
    assert "not declared (--source-version)" in out
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
    assert doc["source_version"] is None
    assert doc["counts"]["view"] == 2


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


@pytest.mark.parametrize("declared", ["8.9.0", "8.9", "7.5.0"])
def test_inspect_refuses_a_declared_version_below_the_floor(export_zip, capsys, declared):
    assert main(["--source-version", declared, "inspect", str(export_zip)]) == 1
    err = capsys.readouterr().err
    assert "refused" in err and declared in err and "floor 8.10" in err


@pytest.mark.parametrize("declared", ["8.10", "8.10.0", "8.18.7", "9.0.2"])
def test_inspect_accepts_a_declared_version_at_or_above_the_floor(export_zip, capsys, declared):
    assert main(["--source-version", declared, "inspect", str(export_zip)]) == 0
    out = capsys.readouterr().out
    assert f"source version: {declared} (declared; floor 8.10 passed)" in out
    assert "not declared" not in out


@pytest.mark.parametrize("declared", ["1", "eight", "8.", "v8.10", "8.10.1.2"])
def test_inspect_rejects_a_malformed_declaration_as_usage(export_zip, capsys, declared):
    assert main(["--source-version", declared, "inspect", str(export_zip)]) == 2
    assert "not major.minor[.patch]" in capsys.readouterr().err


def test_declared_version_comes_from_env_and_settings_too(export_zip, capsys, monkeypatch):
    from vcfcf_migrator import settings

    settings.save_settings({"source_version": "8.9.0"})
    assert main(["inspect", str(export_zip)]) == 1
    monkeypatch.setenv("VCFCF_MIGRATOR_SOURCE_VERSION", "8.18.7")
    assert main(["inspect", str(export_zip)]) == 0
    assert "source version: 8.18.7" in capsys.readouterr().out


def test_a_manifest_format_version_integer_is_never_a_product_version(tmp_path, capsys):
    """Review W1: a bare {"version": 1} in configuration.json must not be
    refused as VCF Operations 1. The reader no longer sniffs at all."""
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
    assert "source version: not declared" in capsys.readouterr().out


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
    from make_export_fixture import DASHBOARD_ID, OWNER, OWNER_2

    assert main(["inspect", "--json", str(export_zip)]) == 0
    doc = json.loads(capsys.readouterr().out)
    dashes = [(i["uuid"], i["source"]) for i in doc["items"] if i["kind"] == "dashboard"]
    assert sorted(dashes) == sorted([(DASHBOARD_ID, f"dashboards/{OWNER}"), (DASHBOARD_ID, f"dashboards/{OWNER_2}")])
    assert doc["counts"]["dashboard"] == doc["manifest"]["dashboards"] == 2


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

    from make_export_fixture import RULE_ID, RULE_ID_2

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
        assert got == {("[Fixture] Cluster rule", RULE_ID), ("[Fixture] Host rule", RULE_ID_2)}, zip_path


def test_inspect_refuses_a_zip_that_is_not_a_content_export(tmp_path, capsys):
    """Review N5: no marker and no configuration.json is not an export."""
    import io
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


@pytest.mark.parametrize("argv", [["tree", "x.zip"], ["build", "x.zip"], ["corpus-check"]])
def test_stubs_exit_2_without_a_traceback(argv, capsys):
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert "not implemented in M3, see spec" in captured.err
    assert "Traceback" not in captured.err


def test_no_command_prints_help(capsys):
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().out


def test_stub_exit_code_survives_the_console_script(tmp_path):
    """The installed entry point, not just main(): no traceback on exit 2."""
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "vcfcf_migrator", "tree", "x.zip"],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 2
    assert "Traceback" not in r.stderr
