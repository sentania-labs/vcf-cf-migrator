"""Control contracts at their actual native bridge, persistence and TLS boundaries."""
import json
import re
from pathlib import Path
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vcfcf_migrator import desktop, settings, source
from pages import save_to, visible
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
    def form():
        return dict(address=address, username='synthetic-user', password='synthetic-password',
                    export_password='unused-export-password', auth_source='Local')
    bridge = desktop.Bridge(state)
    state.screen = 'connect'
    before = state.log.snapshot()
    # The server's certificate is not one this computer knows: that is told
    # apart from every other failure, and the two ways past it are offered.
    typed = form()
    page = visible(bridge.test_connection(typed)['html'])
    assert not typed and state.conn['tested'] == 'cert'
    assert 'This server uses a certificate your computer does not recognise.' in page
    assert 'Use my company certificate file…' in page and 'Connect without checking' in page
    state.dialog = lambda kind, current: {'path': str(cert)} if kind == 'ca' else {}
    dispatch(state, '/conn-cert-file', {})
    assert state.conn['cert'] == {'mode': 'file', 'path': str(cert)} and state.conn['tested'] is None
    assert 'Using the certificate file ca.pem.' in visible(state.render())
    typed = form()
    page = visible(bridge.test_connection(typed)['html'])
    assert not typed and state.conn['tested'] == 'ok'
    assert 'Signed in fine. Nothing was exported.' in page
    dispatch(state, '/conn-cert-undo', {})
    dispatch(state, '/conn-cert-skip', {})
    assert 'Connecting without checking the certificate.' in visible(state.render())
    bridge.test_connection(form())
    assert state.conn['tested'] == 'ok'
    # Nothing typed reached the log, the settings or the page.
    assert [e for e in state.log.snapshot() if e not in before and e.get('event') == 'page.action'
            and e.get('fields')] == []
    assert 'synthetic-password' not in str(state.log.snapshot()) + state.render()
    assert not settings.settings_path().exists()


def test_the_sign_in_choices_come_from_the_server(state, tls_server):
    address, cert, _ = tls_server
    bridge = desktop.Bridge(state)
    failed = bridge.login_sources(address)
    assert state.conn['lookup'] == 'failed' and 'Could not reach that address yet' in failed['address_hint']
    state.conn['cert'] = {'mode': 'file', 'path': str(cert)}
    found = bridge.login_sources(address)
    assert state.conn['sources'] == ['Directory A', 'SSO / B']
    assert 'A local account on VCF Operations' in found['options']
    assert 'An account from the company directory Directory A' in found['options']
    assert 'value="SSO / B"' in found['options'] and 'Found it' in found['address_hint']
    state.screen = 'connect'
    assert 'An account from the company directory SSO / B' in state.render()


def test_a_wrong_password_and_a_typo_say_what_to_do(state, monkeypatch):
    class Refuses:
        base = 'https://operations.example/suite-api/api'
        def __init__(self, *args, **kwargs):
            pass
        def acquire(self, *args):
            raise source.SignInRefused('refused')
    monkeypatch.setattr(source, 'Client', Refuses)
    form = dict(address='https://operations.example', username='u', password='typo',
                export_password='e', export_password2='e')
    dispatch(state, '/connect', dict(form))
    page = state.render()
    assert state.screen == 'connect' and state.conn['error'] == ('auth', 'operations.example')
    assert ('Could not sign in. The user name or password was not accepted by operations.example. '
            'Nothing was exported. Type the password again.') in visible(page)
    # Only the password is asked for again; the rest of the form is put back.
    assert 'data-fresh' in page.split('id="c-pw"')[1].split('>')[0]
    assert 'data-fresh' not in page.split('id="c-epw"')[1].split('>')[0]
    dispatch(state, '/connect', dict(form, export_password2='different'))
    assert state.conn['error'] == ('other', 'The two export passwords do not match. Type them again.')
    dispatch(state, '/conn-edited', {})
    assert state.conn['error'] is None


def test_a_download_shows_its_stages_and_can_be_cancelled(state, monkeypatch):
    import threading
    from vcfcf_migrator.ui import DOWNLOAD_STAGES
    started, release = threading.Event(), threading.Event()

    class Slow:
        base = 'https://operations.example/suite-api/api'
        def __init__(self, *args, **kwargs):
            self.progress = lambda stage: None
            self.cancelled = lambda: False
        def acquire(self, *args):
            self.progress('Authenticating')
            self.progress('Requesting content export')
            started.set()
            assert release.wait(5)
            if self.cancelled():
                raise source.Cancelled('cancelled')
            raise AssertionError('the download was not cancelled')
    monkeypatch.setattr(source, 'Client', Slow)
    state.background = True
    before = state.graph
    dispatch(state, '/connect', dict(address='https://operations.example', username='u', password='p',
                                     export_password='e', export_password2='e'))
    assert started.wait(5)
    job = state.job
    assert job is not None and job.kind == 'download' and job.stage == 1
    page = visible(state.render())
    assert 'Getting your content' in page and all(stage in page for stage in DOWNLOAD_STAGES)
    assert 'Cancelling leaves the source untouched.' in page
    dispatch(state, '/cancel-job', {})
    assert state.job is None and state.screen == 'connect' and 'Nothing was downloaded' in state.note.text
    release.set()
    assert job.done.wait(5)
    assert state.graph is before and state.source_snapshot is None


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


def test_switching_export_is_asked_only_after_a_good_read_and_choices_are_ticked_again(
        state, export_zip, tmp_path):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    state.toggle('customgroup:[Fixture] Prod Clusters', True)
    graph, picked = state.graph, list(state.picked)
    state.open_export(str(tmp_path / 'missing.zip'))
    assert state.ask is None and state.open_error['kind'] == 'unreadable'
    assert state.graph is graph and state.picked == picked
    # The same file again: asked, in the page, and saying no changes nothing.
    state.open_export(str(export_zip))
    assert state.ask['kind'] == 'switch' and state.ask['same'] and state.graph is graph
    page = visible(state.render())
    assert 'Read fixture-export.zip again?' in page
    assert 'Your 2 choices are ticked again wherever the same things exist in it.' in page
    dispatch(state, '/ask-no', {})
    assert state.ask is None and state.graph is graph and state.picked == picked and state.screen == 'choose'
    # A different export that lacks one of the choices: the one that is there
    # is ticked again, and the other is said to be missing.
    other = tmp_path / 'other-export.zip'
    other.write_bytes(build_export_zip(without=('customgroups.json',)))
    state.open_export(str(other))
    assert 'Switch to other-export.zip?' in visible(state.render())
    assert 'Switching closes fixture-export.zip.' in visible(state.render())
    dispatch(state, '/ask-yes', {})
    assert state.graph is not graph and state.zip_path == str(other)
    assert state.picked == ['supermetric:' + EMPTY_SM_ID]
    assert 'Your 1 choice was ticked again.' in state.note.text
    assert '1 choice was not found in this export and was left out.' in state.note.text
    # With nothing chosen there is nothing to ask about.
    state.clear()
    state.open_export(str(export_zip))
    assert state.ask is None and state.zip_path == str(export_zip)


def test_a_choice_is_matched_by_name_when_its_identity_changed(state, tmp_path):
    from vcfcf_migrator.ui import Arrival
    from vcfcf_migrator import insight
    from vcfcf_migrator.graph import Graph, Node
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    renamed = Graph()
    for node in (Node('supermetric', 'new-id', '[Fixture] SM Empty', 'new-id', 'supermetrics.json', 0),
                 Node('supermetric', 'other', 'Something else', 'other', 'supermetrics.json', 1)):
        renamed.nodes[node.key] = node
    state._adopt(Arrival(str(tmp_path / 'renamed.zip'), 'renamed.zip', None, renamed,
                         insight.build(renamed)), keep=True)
    assert state.picked == ['supermetric:new-id']
    # Two things with the same name: a name is not enough to pick one.
    twice = Graph()
    for index in (1, 2):
        node = Node('supermetric', f'dup-{index}', '[Fixture] SM Empty', f'dup-{index}', 'supermetrics.json', index)
        twice.nodes[node.key] = node
    state._adopt(Arrival(str(tmp_path / 'twice.zip'), 'twice.zip', None, twice, insight.build(twice)),
                 keep=True)
    assert state.picked == [] and '1 choice was not found' in state.note.text


def test_an_export_with_nothing_in_it_is_refused_and_changes_nothing(state, tmp_path):
    import io
    import zipfile
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    empty = tmp_path / 'empty-export.zip'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('1757800000000000000L.v1', 'owner')
        archive.writestr('configuration.json', json.dumps({'type': 'CUSTOM'}))
    empty.write_bytes(buffer.getvalue())
    before = state.graph
    state.open_export(str(empty))
    assert state.graph is before and state.picked and state.ask is None
    assert state.open_error == {'kind': 'empty', 'name': 'empty-export.zip'}
    page = visible(state.render())
    assert 'That export has nothing in it to move' in page
    assert 'fixture-export.zip is still open and your 1 choice is untouched.' in page
    assert 'Export again on the source and tick the content you want before exporting.' in page


@pytest.mark.parametrize('content, kind, title', [
    (b'PK\x03\x04 cut off', 'not_zip', 'That file could not be opened'),
    (None, 'unreadable', 'That file could not be opened'),
    ('other', 'not_export', 'That ZIP is not a content export'),
])
def test_each_wrong_file_gets_its_own_plain_card(state, tmp_path, content, kind, title):
    import io
    import zipfile
    path = tmp_path / 'wrong.zip'
    if content == 'other':
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('beach.txt', 'not an export')
        path.write_bytes(buffer.getvalue())
    elif content is not None:
        path.write_bytes(content)
    state.open_export(str(path))
    assert state.open_error['kind'] == kind and state.screen == 'open'
    page = visible(state.render())
    assert title in page and 'What to do:' in page and 'wrong.zip' in page
    # The folder it came from is nobody's business on screen.
    assert str(tmp_path) not in page
    # Going back is one click and nothing was lost.
    dispatch(state, '/go', {'to': 'choose'})
    assert state.screen == 'choose' and state.open_error is not None
    dispatch(state, '/go', {'to': 'open'})
    assert state.open_error is None


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


def test_picker_failure_is_said_and_ca_path_retains_extension(state):
    class Window:
        def create_file_dialog(self, *args, **kwargs):
            return ('/tmp/private-ca.crt',)
    bridge = desktop.Bridge(state)
    bridge._window = Window()
    assert bridge.pick_path('ca')['path'] == '/tmp/private-ca.crt'
    assert bridge.pick_path('unknown')['error']
    bridge._window.create_file_dialog = lambda *a, **k: (_ for _ in ()).throw(OSError('private path'))
    assert bridge.pick_path('bundle') == {'error': 'The file window could not be opened.'}
    state.dialog = bridge.pick_path
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    state.choose_save_place()
    assert state.note.text == 'The file window could not be opened.'


def test_show_in_folder_receives_only_what_the_app_saved(state, tmp_path):
    calls = []
    state.show_in_folder = lambda path: calls.append(path) or True
    bridge = desktop.Bridge(state)
    bridge.act('/show-in-folder', {'path': '/unrelated/file', 'which': 'bundle'})
    assert not calls
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    save_to(state, tmp_path / 'chosen bundle.zip')
    bridge.act('/show-in-folder', {'path': '/unrelated/file', 'which': 'bundle'})
    assert calls == [str(tmp_path / 'chosen bundle.zip')]
    state.show_in_folder = lambda path: False
    bridge.act('/show-in-folder', {'which': 'bundle'})
    assert 'The folder could not be opened.' in state.note.text
    assert state.built_path == calls[0]


def test_bundles_go_to_downloads_with_a_report_and_a_checklist_beside_them(
        config_dir, export_zip, tmp_path, monkeypatch):
    from vcfcf_migrator.export_reader import read_export
    home = tmp_path / 'home'
    (home / 'Downloads').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    state = PageState(str(export_zip))
    # Not beside the export any more: Downloads, whichever way the export came in.
    assert state.save_folder == home / 'Downloads' and state.save_name == 'fixture-export-bundle.zip'
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    state.screen = 'save'
    page = visible(state.render())
    assert 'Save as fixture-export-bundle.zip' in page and 'In folder Downloads' in page
    assert 'What is inside 1 thing: 1 super metric' in page
    state.save()
    assert state.save_state == 'saved'
    saved = sorted(p.name for p in (home / 'Downloads').iterdir())
    assert saved == ['fixture-export-bundle-checklist.txt', 'fixture-export-bundle-report.txt',
                     'fixture-export-bundle.zip']
    assert read_export(home / 'Downloads' / 'fixture-export-bundle.zip').counts() == {'supermetric': 1}
    page = visible(state.render())
    assert 'Bundle saved' in page and 'Three files were saved in Downloads' in page
    assert 'Read the report' in page and 'Read the checklist' in page
    state.log.close()


def test_an_existing_file_is_asked_about_in_the_page_and_never_replaced_silently(state, tmp_path):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    output = tmp_path / 'my bundle.zip'
    save_to(state, output)
    content = output.read_bytes()
    stamp = output.stat().st_mtime_ns
    save_to(state, output)
    assert state.save_state == 'exists' and state.save_problem == 'my bundle.zip'
    assert output.read_bytes() == content and output.stat().st_mtime_ns == stamp
    state.screen = 'save'
    page = state.render()
    assert (f'{tmp_path.name} already has a file called my bundle.zip. Replacing it overwrites the old '
            'file, its report and its checklist for good.') in visible(page)
    # The safe answer is the filled button, and Save itself is off until one is picked.
    assert re.search(r'lat-btn--primary[^>]*data-act="/pick-save"', page)
    assert not re.search(r'lat-btn--primary[^>]*data-force="1"', page)
    assert re.search(r'id="save"[^>]*disabled', page)
    dispatch(state, '/save', {'force': '1'})
    assert state.save_state == 'saved' and output.exists()


def test_a_report_or_checklist_already_there_counts_as_an_existing_file(state, tmp_path):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    (tmp_path / 'fresh-report.txt').write_text('someone else wrote this')
    save_to(state, tmp_path / 'fresh.zip')
    assert state.save_state == 'exists' and state.save_problem == 'fresh-report.txt'
    assert not (tmp_path / 'fresh.zip').exists()
    assert (tmp_path / 'fresh-report.txt').read_text() == 'someone else wrote this'


def test_the_export_itself_is_never_overwritten(state, export_zip):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    original = export_zip.read_bytes()
    save_to(state, export_zip, force=True)
    assert export_zip.read_bytes() == original
    assert (state.save_state, state.save_problem) == ('error', 'same')
    state.screen = 'save'
    assert 'That would overwrite the export you opened.' in visible(state.render())


def test_a_folder_that_refuses_new_files_says_so_and_offers_another(state, tmp_path):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    locked = tmp_path / 'locked'
    locked.mkdir()
    locked.chmod(0o500)
    try:
        save_to(state, locked / 'bundle.zip')
        assert (state.save_state, state.save_problem) == ('error', 'denied')
        state.screen = 'save'
        page = visible(state.render())
        assert ('Could not save in locked. That folder does not allow new files. Nothing was written. '
                'Choose a different folder, then save again.') in page
        assert not list(locked.iterdir())
    finally:
        locked.chmod(0o700)
    chosen = tmp_path / 'elsewhere' / 'renamed'
    state.dialog = lambda kind, current: {'path': str(chosen), 'confirmed': False}
    dispatch(state, '/pick-save', {})
    assert (state.save_name, state.save_folder, state.save_state) == ('renamed.zip', tmp_path / 'elsewhere', 'ready')
    dispatch(state, '/save', {})
    assert state.save_state == 'saved' and (tmp_path / 'elsewhere' / 'renamed.zip').exists()


def test_a_replace_the_save_window_already_confirmed_is_not_asked_twice(state, tmp_path):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    existing = tmp_path / 'report.zip'
    existing.write_text('old')
    state.dialog = lambda kind, current: {'path': str(existing), 'confirmed': True}
    dispatch(state, '/pick-save', {})
    dispatch(state, '/save', {})
    assert state.save_state == 'saved'
    # Once only: the next save of the same name is asked about again.
    dispatch(state, '/save', {})
    assert state.save_state == 'exists'
    # A name the window completed with .zip was not confirmed by it.
    state.dialog = lambda kind, current: {'path': str(existing), 'confirmed': False}
    dispatch(state, '/pick-save', {})
    dispatch(state, '/save', {})
    assert state.save_state == 'exists'


def test_changing_the_choices_after_a_save_says_the_saved_files_are_out_of_date(state, tmp_path):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    save_to(state, tmp_path / 'first.zip')
    assert state.save_state == 'saved' and not state.stale
    state.screen = 'choose'
    state.toggle('customgroup:[Fixture] Prod Clusters', True)
    assert state.stale and state.save_state == 'ready'
    assert 'You changed your choices after saving.' in visible(state.render())
    state.screen = 'save'
    assert 'You changed your choices after the last save.' in visible(state.render())
    save_to(state, tmp_path / 'first.zip', force=True)
    assert state.save_state == 'saved' and not state.stale


def test_theme_default_persistence_validation_and_corpus_compatibility(config_dir):
    settings.save_settings({'corpus_dir': '/data/exports'})
    state = PageState()
    assert state.theme == 'light'
    dispatch(state, '/theme', {'choice': 'dark'})
    # Applies at once, with no second button to press.
    page = state.render()
    assert state.theme == 'dark' and '<body data-theme="dark"' in page
    # Only the page carries the theme attribute. A control that carried it
    # would be drawn in the theme it offers rather than the one in force.
    state.screen = 'help'
    assert 'data-theme' not in state.render().split('<main>')[1].split('</main>')[0]
    second = PageState()
    assert second.theme == 'dark'
    assert settings.load_settings() == {'corpus_dir': '/data/exports', 'theme': 'dark'}
    dispatch(state, '/theme', {'choice': 'invalid'})
    assert state.theme == 'dark' and settings.load_settings()['theme'] == 'dark'
    dispatch(second, '/theme', {'choice': 'light'})
    third = PageState()
    assert third.theme == 'light'
    for item in (state, second, third):
        item.log.close()


@pytest.mark.parametrize('value', ['', '   ', '/'])
def test_blank_or_nameless_destination_falls_back_to_the_default(state, value):
    default = str(Path('fallback-bundle').resolve()) + '.zip'
    assert state.output_path(value, '.zip', 'fallback-bundle') == default


@pytest.mark.parametrize('kind, name', [('bundle', None), ('report', 'support-report.txt'),
                                        ('log', 'vcf-content-migrator.log')])
@pytest.mark.parametrize('blank', ['', '   '])
def test_native_save_dialog_starts_at_the_real_default(state, kind, name, blank, tmp_path, monkeypatch):
    monkeypatch.setattr(PageState, 'download_directory', staticmethod(lambda: tmp_path))
    state.save_folder = tmp_path
    calls = []
    class Window:
        def create_file_dialog(self, *args, **kwargs):
            calls.append(kwargs)
            return None
    bridge = desktop.Bridge(state)
    bridge._window = Window()
    assert bridge.pick_path(kind, blank) == {'path': ''}
    assert (Path(calls[0]['directory']), calls[0]['save_filename']) == (
        tmp_path.resolve(), name or state.save_name)


# -- what an independent review of this change found ---------------------------

def test_an_answer_given_at_review_never_follows_the_choice_into_another_export(
        state, export_zip, tmp_path):
    """The same choice can need something different in another export. An
    answer that came along would approve a gap nobody was ever shown."""
    from make_export_fixture import DASHBOARD_ID, OWNER
    dash = f'dashboard:{DASHBOARD_ID}@{OWNER}'
    state.toggle(dash, True)
    issue, = state.open_issues()
    state.keep_in(issue.id)
    assert not state.open_issues() and state.accepted_missing()
    for name in ('other.zip', 'fixture-export.zip'):          # a different export, then the same one re-read
        path = export_zip if name == 'fixture-export.zip' else tmp_path / name
        if path != export_zip:
            path.write_bytes(build_export_zip())
        state.open_export(str(path))
        dispatch(state, '/ask-yes', {})
        assert state.picked == [dash] and state.decided == {}
        assert [i.id for i in state.open_issues()] == [issue.id]
        assert state.accepted_missing() == []
        if name == 'other.zip':
            assert 'What you decided at Review is asked again' in state.note.text
        save_to(state, tmp_path / f'after-{name}')
        assert not (tmp_path / f'after-{name}').exists() and state.screen == 'review'
        state.keep_in(issue.id)


def test_a_save_that_stops_part_way_says_which_files_it_left(state, tmp_path):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    folder = tmp_path / 'out'
    folder.mkdir()
    out = folder / 'bundle.zip'
    stuck = folder / 'bundle-report.txt'
    stuck.mkdir()                       # the report cannot be written over a folder
    save_to(state, out, force=True)
    assert state.save_state == 'error' and state.save_problem.startswith('os:')
    assert state.save_left == ['bundle.zip', 'bundle-checklist.txt']
    state.screen = 'save'
    page = visible(state.render())
    assert 'Nothing was written.' not in page
    assert ('It stopped part-way: bundle.zip and bundle-checklist.txt were written and the rest was not, '
            'so do not use what is there.') in page


def test_a_downloaded_export_gets_a_file_name_a_computer_accepts(state):
    state.source_snapshot = object()
    for address, name in (('ops.example.com:8443', 'ops.example.com-bundle.zip'),
                          ('ops.example.com', 'ops.example.com-bundle.zip'),
                          ('[fd00::5]', 'fd00-5-bundle.zip'), ('', 'vcf-operations-bundle.zip')):
        state.source_name = address
        assert state.default_save_name() == name
        assert not set(state.default_save_name()) & set(':[]/\\')
    state.source_snapshot = None


def test_a_replace_agreed_for_a_bundle_that_is_not_there_does_not_cover_its_report(state, tmp_path):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    (tmp_path / 'x-report.txt').write_text('someone else wrote this')
    state.dialog = lambda kind, current: {'path': str(tmp_path / 'x.zip'), 'confirmed': True}
    dispatch(state, '/pick-save', {})
    dispatch(state, '/save', {})
    assert (state.save_state, state.save_problem) == ('exists', 'x-report.txt')
    assert (tmp_path / 'x-report.txt').read_text() == 'someone else wrote this'


def test_help_does_not_show_what_another_screen_said_and_gives_it_back(state):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    said = state.note
    dispatch(state, '/add-shown', {})
    assert state.undo
    dispatch(state, '/help', {})
    page = visible(state.render())
    assert state.note is None and 'Added' not in page and 'Undo' not in page
    dispatch(state, '/back', {})
    assert state.note is not None and state.note is not said and 'Added' in state.note.text


def test_a_question_on_screen_is_answered_before_anything_else(state, export_zip, tmp_path):
    state.toggle('supermetric:' + EMPTY_SM_ID, True)
    state.open_export(str(export_zip))
    assert state.ask and state.ask['kind'] == 'switch'
    pending = state.ask['new']
    for path, form in (('/pick-export', {}), ('/select', {'key': 'customgroup:[Fixture] Web Tier', 'on': '1'}),
                       ('/go', {'to': 'choose'}), ('/help', {})):
        dispatch(state, path, form)
        assert state.ask is not None and state.ask['new'] is pending and len(state.picked) == 1
    dispatch(state, '/escape', {})
    assert state.ask is None and state.screen == 'choose'


def test_a_screen_that_cannot_be_drawn_does_not_trap_the_window(state, monkeypatch):
    from vcfcf_migrator import preview
    key = next(n.key for n in state.graph.ordered() if n.kind == 'dashboard')

    def broken(*args, **kwargs):
        raise RuntimeError('unexpected shape')
    monkeypatch.setattr(preview, 'fragment', broken)
    page = desktop.Bridge(state).act('/preview', {'key': key})['html']
    assert state.preview_key == '' and 'That could not be shown.' in visible(page)
    assert 'Find by name' in page          # the step itself is drawn again
    # The window is usable again: the next action draws normally.
    assert 'Help and settings' in visible(desktop.Bridge(state).act('/help', {})['html'])


def test_a_local_read_runs_in_the_background_and_can_be_cancelled(config_dir, export_zip):
    import threading
    state = PageState()
    state.background = True
    started, release = threading.Event(), threading.Event()
    real = state._read

    def slow(job, *args, **kwargs):
        started.set()
        assert release.wait(5)
        return real(job, *args, **kwargs)
    state._read = slow
    state.open_export(str(export_zip))
    assert started.wait(5)
    job = state.job
    assert job.kind == 'read' and 'Reading fixture-export.zip' in visible(state.render())
    dispatch(state, '/cancel-job', {})
    release.set()
    assert job.done.wait(5)
    assert state.graph is None and state.job is None and 'Nothing was changed.' in state.note.text
    # And without a cancel, the same read lands.
    started.clear()
    state.open_export(str(export_zip))
    assert state.job.done.wait(5)
    assert state.graph is not None and state.screen == 'choose' and state.job is None
    state.log.close()


def test_a_download_cancelled_after_it_arrived_lets_the_temporary_export_go(state, monkeypatch):
    import threading
    closed, arrived, release = [], threading.Event(), threading.Event()

    class Fetched:
        base = 'https://operations.example/suite-api/api'
        def __init__(self, *args, **kwargs):
            self.progress = lambda stage: None
        def acquire(self, *args):
            snapshot = source.Snapshot()
            snapshot.path.write_bytes(build_export_zip())
            real_close = snapshot.close
            snapshot.close = lambda: (closed.append(True), real_close())
            return snapshot
    monkeypatch.setattr(source, 'Client', Fetched)
    real = state._read

    def slow(job, *args, **kwargs):
        arrived.set()
        assert release.wait(5)
        return real(job, *args, **kwargs)
    state._read = slow
    state.background = True
    before = state.graph
    dispatch(state, '/connect', dict(address='https://operations.example', username='u', password='p',
                                     export_password='e', export_password2='e'))
    assert arrived.wait(5)
    job = state.job
    assert job.stage == 3                                   # reading what is inside
    dispatch(state, '/cancel-job', {})
    release.set()
    assert job.done.wait(5)
    assert closed and state.graph is before and state.source_snapshot is None


def test_a_job_whose_result_cannot_be_swapped_in_still_ends(config_dir, export_zip, monkeypatch):
    state = PageState()

    def boom(new):
        raise RuntimeError('unexpected')
    monkeypatch.setattr(state, '_arrive', boom)
    state.open_export(str(export_zip))
    assert state.job is None and state.graph is None
    assert state.note.kind == 'bad' and 'That did not finish.' in state.note.text
    state.log.close()
