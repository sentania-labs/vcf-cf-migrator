"""The ui page: served in-process, checked over HTTP."""
from __future__ import annotations

import html
import json

import pytest

import vcfcf_core
from vcfcf_migrator import __version__
from vcfcf_migrator import settings
from vcfcf_migrator.ui import PageState
from vcfcf_migrator.desktop import Bridge

@pytest.fixture
def server(config_dir, export_zip):
    state = PageState(str(export_zip))
    yield Bridge(state)
    state.log.close()

def _post(bridge, path, form):
    return "rendered", bridge.act(path, form)["html"]

def _get(bridge, path="/"):
    return "rendered", bridge._state.render()











def test_page_shows_versions_settings_and_listing(server):
    # The settings live on their own panel now; getting there is a post like
    # everything else, which is also what proves the tab strip works.
    status, body = _post(server, "/tab", {"tab": "settings"})
    assert status == "rendered"
    assert f"vcfcf-migrator {__version__}" in body
    assert f"vcfcf_core {vcfcf_core.__version__}" in body
    assert "id='corpus_dir'" in body and "value='corpus'" in body
    assert "current value from: default" in body
    assert "Help &amp; diagnostics" in body
    # The command buttons are on their own panel, so this has to go there to
    # see them. Splitting the assertion is the point of the change.
    _, commands = _post(server, "/tab", {"tab": "commands"})
    for cmd in ("tree", "preview", "build", "corpus-check"):
        assert f"value='{cmd}'" in commands
        assert f"show the {cmd} command" in commands
    assert "id='zip'" in body




def test_saving_corpus_dir_persists_to_the_settings_file(server, config_dir):
    # Saving a setting leaves you on the panel you saved it from.
    _post(server, "/tab", {"tab": "settings"})
    status, body = _post(server, "/settings", {"corpus_dir": "/data/exports"})
    assert status == "rendered"
    assert "corpus directory saved to" in body
    saved = json.loads((config_dir / "settings.json").read_text())
    assert saved == {"corpus_dir": "/data/exports"}
    assert "value='/data/exports'" in body
    assert "current value from: settings file" in body
    assert settings.corpus_dir()[0].as_posix() == "/data/exports"




def test_environment_overrides_the_saved_setting(server, config_dir, monkeypatch):
    _post(server, "/tab", {"tab": "settings"})
    _post(server, "/settings", {"corpus_dir": "/data/exports"})
    monkeypatch.setenv("VCFCF_MIGRATOR_CORPUS", "/env/corpus")
    _, body = _get(server)
    assert "value='/env/corpus'" in body
    assert "environment (VCFCF_MIGRATOR_CORPUS)" in body


def test_inspect_form_and_json_toggle(server, export_zip):
    _, body = _post(server, "/inspect", {"zip": str(export_zip), "json": "1"})
    assert '"kind": "dashboard"' in html.unescape(body)
    # The other half of the toggle: the same listing as the lines a person
    # reads. This asserted on the string "checked" until 2026-09-15, which was
    # matching the word inside "the 8.10 floor was not checked" rather than
    # anything about the toggle, and passed for the wrong reason.
    _, body = _post(server, "/inspect", {"zip": str(export_zip)})
    assert "items: " in html.unescape(body) and '"kind"' not in html.unescape(body)
    _, body = _post(server, "/inspect", {"zip": str(export_zip.parent / "missing.zip")})
    assert "is not a zip file" in body or "cannot read" in body


def test_the_buttons_hand_back_the_equivalent_command_line(server, export_zip):
    """The page does these itself now; the command line is for the admin who
    wants to script what they just did by hand."""
    _, body = _post(server, "/inspect", {"zip": str(export_zip)})
    _, body = _post(server, "/run", {"cmd": "tree"})
    assert f"vcfcf-migrator tree {export_zip}" in html.unescape(body)
    _, body = _post(server, "/run", {"cmd": "build"})
    assert "--select &lt;picks.txt&gt; --out" in body
    assert "-bundle.zip" in body
    _, body = _post(server, "/run", {"cmd": "corpus-check"})
    assert "vcfcf-migrator corpus-check" in body




@pytest.mark.parametrize("path,expected", [
    ("/tmp/plain.zip", "vcfcf-migrator tree /tmp/plain.zip"),
    ("/tmp/My Export.zip", "vcfcf-migrator tree '/tmp/My Export.zip'"),
    (r"C:\Users\a b\export.zip", "vcfcf-migrator tree 'C:\\Users\\a b\\export.zip'"),
])
def test_the_command_the_page_hands_back_is_one_argument(path, expected, monkeypatch):
    """A path with a space in it is ordinary on every OS this ships for, and
    unquoted it is two arguments, so the command the button offers does not
    run. The quoting follows the platform the page runs on, since that is the
    shell the line will be pasted into: this asserts the POSIX form, and
    ``tests/test_ui_selection.py`` asserts the Windows one.
    """
    import os

    from vcfcf_migrator.ui import PageState

    monkeypatch.setattr(os, "name", "posix")
    state = PageState(path, corpus_cli=None)
    assert state.command_line("tree") == expected


def test_a_quoted_command_survives_a_shell_split(monkeypatch):
    import os
    import shlex

    from vcfcf_migrator.ui import PageState

    monkeypatch.setattr(os, "name", "posix")
    state = PageState("/tmp/My Export.zip", corpus_cli=None)
    parts = shlex.split(state.command_line("build"))
    assert "/tmp/My Export.zip" in parts
    assert parts[:2] == ["vcfcf-migrator", "build"]
