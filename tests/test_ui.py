"""The ui page: served in-process, checked over HTTP."""
from __future__ import annotations

import html
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

import vcfcf_core
from vcfcf_migrator import __version__
from vcfcf_migrator import settings
from vcfcf_migrator.ui import make_server


@pytest.fixture
def server(config_dir, export_zip):
    srv = make_server(zip_path=str(export_zip), port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def _url(srv, path="/"):
    return f"http://127.0.0.1:{srv.server_address[1]}{path}"


def _get(srv, path="/"):
    with urllib.request.urlopen(_url(srv, path)) as r:
        return r.status, r.read().decode("utf-8")


def _post(srv, path, form, headers=None):
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(_url(srv, path), data=data, method="POST", headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return r.status, r.read().decode("utf-8")


def test_page_shows_versions_settings_and_listing(server):
    status, body = _get(server)
    assert status == 200
    assert f"vcfcf-migrator {__version__}" in body
    assert f"vcfcf_core {vcfcf_core.__version__}" in body
    assert "id='corpus_dir'" in body and "value='corpus'" in body
    assert "current value from: default" in body
    assert "id='source_version'" in body and "current value from: not declared" in body
    assert "[Fixture] Cluster Overview" in body
    assert "[Fixture] SM 2" in body
    for cmd in ("tree", "build", "corpus-check"):
        assert f"value='{cmd}'" in body
        assert f"Show the {cmd} command" in body
    assert "id='zip'" in body


def test_server_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_saving_corpus_dir_persists_to_the_settings_file(server, config_dir):
    status, body = _post(server, "/settings", {"corpus_dir": "/data/exports"})
    assert status == 200
    assert "corpus directory saved to" in body
    saved = json.loads((config_dir / "settings.json").read_text())
    assert saved == {"corpus_dir": "/data/exports"}
    assert "value='/data/exports'" in body
    assert "current value from: settings file" in body
    assert settings.corpus_dir()[0].as_posix() == "/data/exports"


def test_saving_source_version_persists_and_rechecks_the_listing(server, config_dir):
    status, body = _post(server, "/settings", {"source_version": "8.18.7"})
    assert status == 200
    assert "source version 8.18.7 saved to" in body
    assert json.loads((config_dir / "settings.json").read_text()) == {"source_version": "8.18.7"}
    assert "value='8.18.7'" in body
    assert "source version: 8.18.7 (declared; floor 8.10 passed)" in body
    assert settings.source_version() == ("8.18.7", f"settings file ({config_dir / 'settings.json'})")


def test_saving_a_source_version_below_the_floor_is_refused_and_not_saved(server, config_dir):
    _, body = _post(server, "/settings", {"source_version": "8.9.0"})
    assert "refused: declared source version 8.9.0 is below the floor 8.10" in body
    assert not (config_dir / "settings.json").exists()
    _, body = _post(server, "/settings", {"source_version": "eight"})
    assert "not major.minor[.patch]" in body
    assert not (config_dir / "settings.json").exists()


def test_foreign_origin_post_is_refused_with_403(server, config_dir):
    """Review W2: a page on another origin must not be able to post here."""
    port = server.server_address[1]
    for headers in ({"Origin": "http://evil.example"},
                    {"Origin": "http://127.0.0.1:1"},
                    {"Origin": f"http://localhost:{port + 1}"},
                    {"Origin": "null"},
                    {"Host": "evil.example"}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(server, "/settings", {"corpus_dir": "/pwned"}, headers=headers)
        assert e.value.code == 403, headers
        assert f"http://127.0.0.1:{port}, http://localhost:{port}" in e.value.read().decode()
    assert not (config_dir / "settings.json").exists()
    # The page's own origin (127.0.0.1 or localhost), and a bare same-host
    # request with no Origin, pass.
    status, _ = _post(server, "/settings", {"corpus_dir": "/ok"}, headers={"Origin": f"http://127.0.0.1:{port}"})
    assert status == 200
    status, _ = _post(server, "/settings", {"corpus_dir": "/ok-localhost"}, headers={"Origin": f"http://localhost:{port}"})
    assert status == 200
    status, _ = _post(server, "/settings", {"corpus_dir": "/ok-host"}, headers={"Host": f"localhost:{port}"})
    assert status == 200
    status, _ = _post(server, "/settings", {"corpus_dir": "/ok2"})
    assert status == 200


def test_environment_overrides_the_saved_setting(server, config_dir, monkeypatch):
    _post(server, "/settings", {"corpus_dir": "/data/exports"})
    monkeypatch.setenv("VCFCF_MIGRATOR_CORPUS", "/env/corpus")
    _, body = _get(server)
    assert "value='/env/corpus'" in body
    assert "environment (VCFCF_MIGRATOR_CORPUS)" in body


def test_inspect_form_and_json_toggle(server, export_zip):
    _, body = _post(server, "/inspect", {"zip": str(export_zip), "json": "1"})
    assert '"kind": "dashboard"' in html.unescape(body)
    assert "checked" in body
    _, body = _post(server, "/inspect", {"zip": str(export_zip.parent / "missing.zip")})
    assert "is not a zip file" in body or "cannot read" in body


def test_pending_buttons_hand_back_the_command_to_run(server, export_zip):
    """tree, build and corpus-check run on the CLI today; their page controls
    land in the next PR. Until then the buttons do the one useful thing they
    can: hand back the exact command line for what the page is set to."""
    _, body = _post(server, "/inspect", {"zip": str(export_zip)})
    _, body = _post(server, "/run", {"cmd": "tree"})
    assert f"vcfcf-migrator tree {export_zip}" in html.unescape(body)
    _, body = _post(server, "/run", {"cmd": "build"})
    assert "--select &lt;picks.txt&gt; --out &lt;bundle.zip&gt;" in body
    assert "refuses without a declared source version" in body
    _, body = _post(server, "/run", {"cmd": "corpus-check"})
    assert "vcfcf-migrator corpus-check" in body


def test_unknown_path_is_404(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(server, "/nope")
    assert e.value.code == 404


@pytest.mark.parametrize("path,expected", [
    ("/tmp/plain.zip", "vcfcf-migrator tree /tmp/plain.zip"),
    ("/tmp/My Export.zip", 'vcfcf-migrator tree "/tmp/My Export.zip"'),
    (r"C:\Users\a b\export.zip", 'vcfcf-migrator tree "C:\\Users\\a b\\export.zip"'),
])
def test_the_command_the_page_hands_back_is_one_argument(path, expected):
    """A path with a space in it is ordinary on every OS this ships for, and
    unquoted it is two arguments, so the command the button offers does not
    run. Double quotes rather than shlex, which emits single quotes that
    Windows takes literally."""
    from vcfcf_migrator.ui import PageState

    state = PageState(path, corpus_cli=None, source_version_cli=None)
    assert state.command_line("tree") == expected


def test_a_quoted_command_survives_a_shell_split():
    import shlex

    from vcfcf_migrator.ui import PageState

    state = PageState("/tmp/My Export.zip", corpus_cli=None, source_version_cli="9.0.2")
    parts = shlex.split(state.command_line("build"))
    assert "/tmp/My Export.zip" in parts
    assert parts[:4] == ["vcfcf-migrator", "--source-version", "9.0.2", "build"]
