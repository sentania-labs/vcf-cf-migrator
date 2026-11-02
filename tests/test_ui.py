"""The ui page: served in-process, checked over HTTP."""
from __future__ import annotations


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











def test_page_shows_help_versions_and_theme(server):
    status, body = _post(server, '/tab', {'tab': 'settings'})
    assert status == 'rendered'
    assert f"vcfcf-migrator {__version__}" in body
    assert f"vcfcf_core {vcfcf_core.__version__}" in body
    assert 'Help &amp; diagnostics' in body
    assert "id='theme'" in body


@pytest.mark.parametrize('path', ['/inspect', '/tree', '/corpus-check', '/apply-lines', '/run'])
def test_command_line_tools_cannot_be_invoked_through_desktop(server, path):
    graph = server._state.graph
    _, body = _post(server, path, {})
    assert 'no page action' in body
    assert server._state.graph is graph


def test_corpus_config_and_environment_remain_supported(config_dir, monkeypatch):
    settings.save_settings({'corpus_dir': '/data/exports'})
    assert str(settings.corpus_dir()[0]) == '/data/exports'
    monkeypatch.setenv('VCFCF_MIGRATOR_CORPUS', '/env/corpus')
    assert str(settings.corpus_dir()[0]) == '/env/corpus'
    assert str(settings.corpus_dir('/cli/corpus')[0]) == '/cli/corpus'
