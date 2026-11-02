"""Control contracts at their actual native bridge, persistence and TLS boundaries."""
import json
from pathlib import Path
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vcfcf_migrator import desktop, settings, source
from vcfcf_migrator.ui import PageState, dispatch
from make_export_fixture import EMPTY_SM_ID, build_export_zip


@pytest.fixture
def state(config_dir, export_zip):
    page = PageState(str(export_zip))
    yield page
    page.close_source()
    page.log.close()


@pytest.fixture
def tls_server(tmp_path):
    """An actual untrusted HTTPS endpoint, with an invented API and credentials."""
    cert, key = tmp_path / 'ca.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
                    '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            requests.append((self.command, self.path, json.loads(body) if body else None))
            response = {'sources': [{'name': 'Directory A'}, {'name': 'SSO / B'}]}
            if self.path.endswith('/acquire'):
                response = {'token': 'synthetic-session'}
            if self.path.endswith('/content/operations/export'):
                if self.command == 'POST':
                    response = {'scope': 'CUSTOM', 'contentTypes': list(source.CONTENT_TYPES)}
                elif any(method == 'POST' and path == self.path for method, path, _ in requests):
                    response = {'state': 'FINISHED', 'id': 'synthetic-export', 'startTime': 1}
                else:
                    response = {'state': 'NOT_INITIALIZED'}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(build_export_zip() if self.path.endswith('/export/zip')
                             else json.dumps(response).encode())

        do_GET = do_POST = answer

    server = ThreadingHTTPServer(('localhost', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f'https://localhost:{server.server_port}', cert, requests
    server.shutdown()
    server.server_close()
    worker.join()


def test_tls_policy_against_untrusted_server(tls_server, tmp_path):
    address, cert, requests = tls_server
    with pytest.raises(source.SourceError, match='Certificate verification failed.*trusted CA'):
        source.Client(address).login_sources()
    for ca, ignore in ((str(cert), False), ('', True), (str(tmp_path / 'missing'), True)):
        client = source.Client(address, ca_file=ca, ignore_ssl=ignore)
        assert client.login_sources() == ['Directory A', 'SSO / B']
        client.test_connection('synthetic-user', 'synthetic-password', 'Directory A')
        assert client.token is None
    assert all('/content/' not in path for _, path, _ in requests)
    assert [data['authSource'] for _, path, data in requests if path.endswith('/acquire')] == ['Directory A'] * 3
    for ca in (tmp_path / 'missing', tmp_path / 'bad.pem'):
        if ca.name == 'bad.pem':
            ca.write_text('not a certificate')
        with pytest.raises(source.SourceError, match='bundle could not be read.*valid PEM'):
            source.Client(address, ca_file=str(ca)).login_sources()


@pytest.mark.parametrize('name, expected', [('Local', None), (' local ', None), ('', None),
                                            ('SSO / B', 'SSO / B'), (' Directory A ', ' Directory A '),
                                            ('Directory  MixedCase', 'Directory  MixedCase')])
def test_login_source_mapping_and_test_never_exports(tls_server, name, expected):
    address, cert, requests = tls_server
    client = source.Client(address, ca_file=str(cert))
    client.test_connection('synthetic-user', 'synthetic-password', name)
    assert [path.rsplit('/', 1)[-1] for _, path, _ in requests] == ['acquire', 'current', 'release']
    assert requests[0][2].get('authSource') == expected
    assert client.token is None


def test_bridge_test_clears_fields_without_persisting_or_logging(state, tls_server, config_dir):
    address, cert, _ = tls_server
    form = dict(address=address, ca_file=str(cert), username='synthetic-user',
                password='synthetic-password', export_password='unused-export-password', auth_source='Local')
    before = state.log.snapshot()
    assert desktop.Bridge(state).test_connection(form) == 'Connection succeeded. No export was started.'
    assert not form
    assert state.log.snapshot() == before
    assert not settings.settings_path().exists()


@pytest.mark.parametrize('policy', ['trusted-ca', 'ignore', 'ignore-with-unreadable-ca'])
def test_tls_policy_used_through_complete_export(tls_server, tmp_path, policy):
    address, cert, requests = tls_server
    ca = str(cert) if policy == 'trusted-ca' else (
        str(tmp_path / 'unreadable.pem') if policy == 'ignore-with-unreadable-ca' else '')
    client = source.Client(address, ca_file=ca, ignore_ssl=policy != 'trusted-ca')
    snapshot = client.acquire('synthetic-user', 'synthetic-password', 'SSO / B', 'synthetic-export-password')
    try:
        assert snapshot.path.read_bytes() == build_export_zip()
        assert requests[0][2]['authSource'] == 'SSO / B'
        assert sum(method == 'POST' and path.endswith('/content/operations/export')
                   for method, path, _ in requests) == 1
        assert requests[-1][1].endswith('/release')
        assert client.token is None
    finally:
        snapshot.close()


def test_source_replacement_confirmed_only_after_valid_load(state, export_zip, tmp_path):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    graph, picked = state.graph, list(state.picked)
    prompts = []
    state.confirm = lambda title, message: prompts.append((title, message)) or False
    state.open_export(str(tmp_path / 'missing.zip'))
    assert not prompts
    state.open_export(str(export_zip))
    assert len(prompts) == 1
    assert state.graph is graph and state.picked == picked
    state.confirm = lambda *args: True
    state.open_export(str(export_zip))
    assert not state.picked and state.graph is not graph


@pytest.mark.parametrize('current,chosen,expected', [
    ('suggested.zip', ('/tmp/chosen file',), {'path': '/tmp/chosen file.zip', 'confirmed': False}),
    ('suggested.zip', '/tmp/chosen.ZIP', {'path': '/tmp/chosen.ZIP', 'confirmed': True}),
    ('suggested.zip', None, {'path': ''}), ('suggested.zip', (), {'path': ''}),
])
def test_native_save_as_normalizes_platform_returns(state, current, chosen, expected):
    import webview
    calls = []
    class Window:
        def create_file_dialog(self, *args, **kwargs):
            calls.append((args, kwargs))
            return chosen
    bridge = desktop.Bridge(state)
    bridge._window = Window()
    assert bridge.pick_path('bundle', current) == expected
    args, kwargs = calls[0]
    assert args == (webview.SAVE_DIALOG,)
    assert kwargs['save_filename'] == 'suggested.zip'
    assert Path(kwargs['directory']).is_absolute()
    assert kwargs['file_types'] == ('Zip files (*.zip)',)


def test_picker_failure_is_actionable_and_ca_path_retains_extension(state):
    class Window:
        def create_file_dialog(self, *args, **kwargs):
            return ('/tmp/private-ca.crt',)
    bridge = desktop.Bridge(state)
    bridge._window = Window()
    assert bridge.pick_path('ca')['path'] == '/tmp/private-ca.crt'
    assert bridge.pick_path('unknown')['error']
    bridge._window.create_file_dialog = lambda *a, **k: (_ for _ in ()).throw(OSError('private path'))
    assert bridge.pick_path('bundle') == {'error': 'The file picker could not open. Enter the path manually.'}


def test_show_in_folder_receives_only_the_completed_destination(state, tmp_path):
    calls = []
    state.show_in_folder = lambda path: calls.append(path) or True
    bridge = desktop.Bridge(state)
    bridge.act('/show-in-folder', {'path': '/unrelated/file'})
    assert not calls
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    state.build(str(tmp_path / 'chosen bundle'))
    bridge.act('/show-in-folder', {'path': '/unrelated/file'})
    assert calls == [str(tmp_path / 'chosen bundle.zip')]
    state.show_in_folder = lambda path: False
    bridge.act('/show-in-folder')
    assert 'folder could not be opened' in state.error
    assert state.built_path == calls[0]


def test_save_path_extension_full_destination_and_overwrite(state, tmp_path, export_zip):
    from vcfcf_migrator.export_reader import read_export
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    state.build(str(tmp_path / 'my bundle'))
    output = tmp_path / 'my bundle.zip'
    assert state.built_path == str(output.resolve())
    assert read_export(output).counts() == {'supermetric': 1}
    content = output.read_bytes()
    state.confirm = lambda *args: False
    state.build(str(output))
    assert output.read_bytes() == content and 'cancelled' in state.message
    original = export_zip.read_bytes()
    state.build(str(export_zip))
    assert export_zip.read_bytes() == original
    assert 'source export cannot be overwritten' in state.error
    state.save_diagnostics(str(tmp_path / 'diagnostics'))
    assert (tmp_path / 'diagnostics.jsonl').exists()


def test_theme_default_persistence_validation_and_corpus_compatibility(config_dir):
    settings.save_settings({'corpus_dir': '/data/exports'})
    state = PageState()
    assert state.theme == 'light'
    dispatch(state, '/settings', {'theme': 'dark'})
    second = PageState()
    assert second.theme == 'dark'
    assert settings.load_settings() == {'corpus_dir': '/data/exports', 'theme': 'dark'}
    dispatch(state, '/settings', {'theme': 'invalid'})
    assert state.error and settings.load_settings()['theme'] == 'dark'
    dispatch(second, '/settings', {'theme': 'light'})
    third = PageState()
    assert third.theme == 'light'
    for item in (state, second, third):
        item.log.close()


@pytest.mark.parametrize('value', ['', '   ', '/'])
def test_blank_or_nameless_destination_falls_back_to_the_default(state, value):
    default = str(Path('fallback-bundle').resolve()) + '.zip'
    assert state.output_path(value, '.zip', 'fallback-bundle') == default


def test_replace_confirmation_only_for_typed_paths(state, tmp_path):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    prompts = []
    state.confirm = lambda title, message: prompts.append(title) or True
    target = tmp_path / 'existing.jsonl'
    target.write_text('old')
    state.save_diagnostics(str(target), True)
    assert not prompts
    state.save_diagnostics(str(target))
    assert prompts == ['Replace file?']
    assert state.diagnostics_out == str(target)


@pytest.mark.parametrize('kind, extension', [('bundle', '.zip'), ('diagnostics', '.jsonl')])
@pytest.mark.parametrize('blank', ['', '   '])
def test_native_save_dialog_starts_at_the_real_default(state, kind, extension, blank):
    calls = []
    class Window:
        def create_file_dialog(self, *args, **kwargs):
            calls.append(kwargs)
            return None
    bridge = desktop.Bridge(state)
    bridge._window = Window()
    default = Path(state.default_out() if kind == 'bundle' else state.default_diagnostics_out()).resolve()
    assert bridge.pick_path(kind, blank) == {'path': ''}
    assert (Path(calls[0]['directory']), calls[0]['save_filename']) == (default.parent, default.name)


def test_native_save_dialog_starts_at_the_live_source_default(state, tmp_path, monkeypatch):
    monkeypatch.setattr(PageState, 'download_directory', staticmethod(lambda: tmp_path))
    state.source_snapshot = object()
    calls = []
    class Window:
        def create_file_dialog(self, *args, **kwargs):
            calls.append(kwargs)
            return None
    bridge = desktop.Bridge(state)
    bridge._window = Window()
    bridge.pick_path('bundle', ' ')
    bridge.pick_path('diagnostics', '')
    assert [(Path(c['directory']), c['save_filename']) for c in calls] == [
        (tmp_path.resolve(), 'bundle.zip'), (tmp_path.resolve(), 'vcfcf-migrator-diagnostics.jsonl')]
    state.source_snapshot = None


@pytest.mark.parametrize('kind, extension', [('bundle', '.zip'), ('diagnostics', '.jsonl')])
def test_overwrite_is_confirmed_exactly_once_for_the_real_destination(state, tmp_path, kind, extension):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    prompts = []
    state.confirm = lambda title, message: prompts.append(message) or True
    run = state.build if kind == 'bundle' else state.save_diagnostics
    existing = tmp_path / ('report' + extension)
    existing.write_text('old')

    def pick(chosen):
        class Window:
            def create_file_dialog(self, *args, **kwargs):
                return (chosen,)
        bridge = desktop.Bridge(state)
        bridge._window = Window()
        return bridge.pick_path(kind, '')

    suffixless = pick(str(tmp_path / 'report'))
    assert suffixless == {'path': str(existing.resolve()), 'confirmed': False}
    run(suffixless['path'])
    assert len(prompts) == 1 and str(existing.resolve()) in prompts[0]

    prompts.clear()
    exact = pick(str(existing))
    assert exact == {'path': str(existing.resolve()), 'confirmed': True}
    run(exact['path'], True)
    assert not prompts

    run(str(existing))
    assert len(prompts) == 1
