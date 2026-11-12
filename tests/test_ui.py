"""The ui page: served in-process, checked over HTTP."""
from __future__ import annotations


import pytest

import vcfcf_core
from vcfcf_migrator import __version__
from vcfcf_migrator import settings
from pages import visible
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











def test_help_shows_the_version_the_settings_and_a_way_back(server):
    status, body = _post(server, '/help', {})
    assert status == 'rendered'
    text = visible(body)
    assert f"You have version {__version__}." in text
    # The version is one plain line here and nowhere in the header.
    assert __version__ not in body.split('<header')[1].split('</header>')[0]
    assert 'Help and settings' in text and '← Back to where I was' in text
    assert 'id="theme-light"' in body and 'id="theme-dark"' in body
    assert 'How this works' in text and 'What the words mean' in text
    # Nothing is folded away.
    assert '<details' not in body
    _, body = _post(server, '/back', {})
    assert server._state.screen == 'choose'


def test_the_glossary_explains_every_kind_of_content_in_one_sentence(server):
    from vcfcf_migrator.wording import KINDS
    _post(server, '/help', {})
    _, body = _post(server, '/gloss', {})
    text = visible(body)
    for one, _many, what in KINDS.values():
        assert one in text and what in text
    for word in ('Bundle', 'Widget', 'Policy', 'Adapter', 'Never finished loading'):
        assert word in text
    _, body = _post(server, '/escape', {})
    assert not server._state.gloss and server._state.screen == 'help'


def test_the_command_line_still_reports_the_library_and_window_versions():
    from vcfcf_migrator.cli import version_lines
    lines = version_lines()
    assert lines[0] == f"vcfcf-migrator {__version__}"
    assert f"vcfcf_core {vcfcf_core.__version__}" in lines[1]


@pytest.mark.parametrize('path', ['/inspect', '/tree', '/corpus-check', '/apply-lines', '/run'])
def test_command_line_tools_cannot_be_invoked_through_desktop(server, path):
    graph = server._state.graph
    _, body = _post(server, path, {})
    assert 'has no action called' in visible(body)
    assert server._state.graph is graph


def test_corpus_config_and_environment_remain_supported(config_dir, monkeypatch):
    settings.save_settings({'corpus_dir': '/data/exports'})
    assert str(settings.corpus_dir()[0]) == '/data/exports'
    monkeypatch.setenv('VCFCF_MIGRATOR_CORPUS', '/env/corpus')
    assert str(settings.corpus_dir()[0]) == '/env/corpus'
    assert str(settings.corpus_dir('/cli/corpus')[0]) == '/cli/corpus'
