"""Live source acquisition, operation identity, cleanup, and privacy."""
import io
import json
import ssl
import zipfile
import urllib.error
from pathlib import Path

import pytest

from vcfcf_migrator import source
from vcfcf_migrator.desktop import Bridge
from vcfcf_migrator.ui import PageState
from fixtures.make_export_fixture import build_export_zip


class Reply(io.BytesIO):
    pass


class Opener:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Reply(reply if isinstance(reply, bytes) else json.dumps(reply).encode())


def status(ident='new', start=2, state='FINISHED'):
    return dict(id=ident, startTime=start, state=state, errorCode='NONE')


def replies():
    return [dict(token='test-token'), status('old', 1),
            dict(scope='CUSTOM', contentTypes=list(source.CONTENT_TYPES)),
            status(), build_export_zip(), status()]


def client(responses):
    opener = Opener(responses)
    return source.Client('https://operations.example', opener=opener, poll_interval=0, timeout=.02), opener


def test_download_scope_auth_and_cleanup():
    c, opener = client(replies())
    snapshot = c.acquire('user', 'private-password', 'local', 'Export1!password')
    path = snapshot.path
    try:
        assert path.read_bytes() == build_export_zip()
        assert path.stat().st_mode & 0o777 == 0o600
        assert c.token is None
        assert json.loads(opener.requests[0].data)['authSource'] == 'local'
        assert opener.requests[2].get_header('Encryptionpassword') == 'Export1!password'
        assert all(r.get_header('Authorization') == 'vRealizeOpsToken test-token' for r in opener.requests[1:])
    finally:
        snapshot.close()
    assert not path.exists()


@pytest.mark.parametrize('url', ['http://operations.example', 'https://user:pass@operations.example',
                                'https://operations.example/a', 'https://operations.example/?token=x',
                                'https://operations.example/#x', 'https://operations.example:bad'])
def test_address_validation(url):
    with pytest.raises(source.SourceError):
        source.endpoint(url)


def test_no_redirects():
    assert source.NoRedirects().redirect_request(None, None, 302, '', {}, 'https://elsewhere.example') is None


def test_running_export_is_not_replaced():
    c, opener = client([dict(token='test'), status(state='RUNNING')])
    with pytest.raises(source.SourceError, match='Another export is running'):
        c.acquire('user', 'pass')
    assert len(opener.requests) == 2
    assert c.token is None


@pytest.mark.parametrize('position,replacement', [
    (2, dict(scope='CUSTOM', contentTypes=[{}])),
    (3, status(state='FAILED')),
    (3, dict(state='FINISHED')),
    (4, b'not a zip'),
    (5, status('other', 3)),
])
def test_bad_operation_or_download_preserves_privacy(position, replacement, monkeypatch, tmp_path):
    values = replies()
    values[position] = replacement
    monkeypatch.setattr(source.tempfile, 'tempdir', str(tmp_path))
    c, _ = client(values)
    with pytest.raises(source.SourceError) as error:
        c.acquire('user', 'private-password')
    assert 'private-password' not in str(error.value)
    assert c.token is None
    assert not list(tmp_path.iterdir())


def test_replaced_running_operation_is_refused():
    values = replies()[:3] + [status(state='RUNNING'), status('replacement', 3)]
    c, opener = client(values)
    with pytest.raises(source.SourceError, match='replaced'):
        c.acquire('user', 'password')
    assert not any(r.full_url.endswith('/zip') for r in opener.requests)


def test_stale_finished_export_is_not_downloaded():
    class Stale(Opener):
        def open(self, request, timeout):
            if len(self.requests) >= 3:
                self.requests.append(request)
                return Reply(json.dumps(status('old', 1)).encode())
            return super().open(request, timeout)
    opener = Stale(replies()[:3])
    c = source.Client('https://operations.example', opener=opener, poll_interval=0, timeout=.01)
    with pytest.raises(source.SourceError, match='timed out'):
        c.acquire('user', 'password')
    assert not any(r.full_url.endswith('/zip') for r in opener.requests)


def test_tls_failure_is_sanitized():
    c, _ = client([urllib.error.URLError(ssl.SSLError('private-password'))])
    with pytest.raises(source.SourceError) as error:
        c.acquire('user', 'private-password')
    assert 'private-password' not in str(error.value)


def test_download_limit_cleans_up(monkeypatch, tmp_path):
    monkeypatch.setattr(source, 'MAX_DOWNLOAD', 3)
    monkeypatch.setattr(source.tempfile, 'tempdir', str(tmp_path))
    c, _ = client(replies())
    with pytest.raises(source.SourceError, match='download limit'):
        c.acquire('user', 'password')
    assert not list(tmp_path.iterdir())


def test_native_action_keeps_secrets_out_of_logs_and_preserves_inventory(monkeypatch, tmp_path):
    monkeypatch.setenv('VCFCF_MIGRATOR_CONFIG_DIR', str(tmp_path / 'config'))
    export = tmp_path / 'fixture.zip'
    export.write_bytes(build_export_zip())
    log = tmp_path / 'private.jsonl'
    state = PageState(str(export), log_cli=str(log), log_level_cli='debug')
    old = state.graph
    values = dict(address='https://private-host.example', username='private-user',
                  password='private-password', export_password='private-export-password')
    class Broken:
        def __init__(self, *args, **kwargs):
            raise ValueError('private-password')
    monkeypatch.setattr(source, 'Client', Broken)
    result = Bridge(state).act('/connect', values)
    assert state.graph is old
    state.save_diagnostics(str(tmp_path / 'shared.jsonl'))
    state.log.close()
    combined = result['html'] + log.read_text() + (tmp_path / 'shared.jsonl').read_text()
    assert all(value not in combined for value in values.values())


def test_snapshot_lifetime_and_output_location(monkeypatch, tmp_path):
    monkeypatch.setenv('VCFCF_MIGRATOR_CONFIG_DIR', str(tmp_path / 'config'))
    state = PageState()
    c, _ = client(replies())
    monkeypatch.setattr(source, 'Client', lambda *a, **k: c)
    form = dict(address='https://operations.example', username='user', password='pass', export_password='pass')
    state.connect_source(form)
    assert not form
    assert not state.error
    downloaded = Path(state.zip_path)
    assert downloaded.exists()
    assert state.default_out() == 'bundle.zip'
    assert state.default_diagnostics_out() == 'vcfcf-migrator-diagnostics.jsonl'
    state.open_export(str(downloaded.parent / '.' / downloaded.name))
    assert downloaded.exists()
    assert state.source_snapshot is not None
    assert state.default_out() == 'bundle.zip'
    state.open_export('missing.zip')
    assert downloaded.exists()
    export = tmp_path / 'fixture.zip'
    export.write_bytes(build_export_zip())
    state.open_export(str(export))
    assert not downloaded.exists()
    assert state.source_snapshot is None
    state.log.close()


def test_nested_export_budget_includes_dashboard_owner_containers(monkeypatch, tmp_path):
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('dashboard.json', b'x' * 10000)
    outer = tmp_path / 'source.zip'
    with zipfile.ZipFile(outer, 'w') as archive:
        archive.writestr('dashboards/owner', nested.getvalue())
    monkeypatch.setattr(source, 'MAX_EXPANDED', 1024)
    with pytest.raises(source.SourceError, match='expanded-size limit'):
        source.validate_size(outer)
