"""The ui page: served in-process, checked over HTTP."""
from __future__ import annotations

import html
import json
import threading
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


def _post(srv, path, form):
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(_url(srv, path), data=data, method="POST")
    with urllib.request.urlopen(req) as r:
        return r.status, r.read().decode("utf-8")


def test_page_shows_versions_settings_and_listing(server):
    status, body = _get(server)
    assert status == 200
    assert f"vcfcf-migrator {__version__}" in body
    assert f"vcfcf_core {vcfcf_core.__version__}" in body
    assert "id='corpus_dir'" in body and "value='corpus'" in body
    assert "current value from: default" in body
    assert "[Fixture] Cluster Overview" in body
    assert "[Fixture] SM 2" in body
    for cmd in ("tree", "build", "corpus-check"):
        assert f"value='{cmd}'" in body
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


def test_stub_buttons_answer_like_the_cli(server):
    _, body = _post(server, "/run", {"cmd": "build"})
    assert "vcfcf-migrator build: not implemented in M3, see spec" in body


def test_unknown_path_is_404(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(server, "/nope")
    assert e.value.code == 404
