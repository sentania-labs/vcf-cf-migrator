"""CLI contract: version, inspect, the floor, the M3 stubs."""
from __future__ import annotations

import json
import re

import pytest

import vcfcf_core
from make_export_fixture import EXPECTED_CARRIED, EXPECTED_ITEMS
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
    assert "version: undetermined (continuing)" in out
    assert "marker: 1757800000000000000L.v1 (format v1, owner b58a71ee" in out
    assert "carried, not inspected: 1" in out
    for name in EXPECTED_CARRIED:
        assert name in out


def test_inspect_json_carries_the_same_items(export_zip, capsys):
    assert main(["inspect", "--json", str(export_zip)]) == 0
    doc = json.loads(capsys.readouterr().out)
    got = {(i["kind"], i["name"], i["uuid"]) for i in doc["items"]}
    assert got == EXPECTED_ITEMS
    assert set(doc["carried"]) == EXPECTED_CARRIED
    assert doc["version"] is None
    assert doc["counts"]["view"] == 2


def test_read_export_uses_the_core_readers_for_dashboards_and_supermetrics(export_zip):
    export = read_export(export_zip)
    sources = {(i.kind, i.source) for i in export.items}
    assert ("dashboard", "dashboards/") in sources
    assert ("supermetric", "supermetrics.json") in sources
    assert ("view", "views.zip") in sources
    assert ("report", "Reports.zip") in sources


def test_inspect_refuses_below_the_floor(old_export_zip, capsys):
    assert main(["inspect", str(old_export_zip)]) == 1
    err = capsys.readouterr().err
    assert "refused" in err and "8.5.0" in err and "floor is 8.10" in err


def test_inspect_accepts_a_versioned_export_at_or_above_the_floor(tmp_path, capsys):
    from make_export_fixture import build_export_zip

    path = tmp_path / "v.zip"
    path.write_bytes(build_export_zip(version="8.10.2"))
    assert main(["inspect", str(path)]) == 0
    assert "version: 8.10.2 (from configuration.json)" in capsys.readouterr().out


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
