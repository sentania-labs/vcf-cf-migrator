"""Update checks must be quiet, bounded, and independent of content work."""

import io
import json
import threading

import pytest

from vcfcf_migrator import desktop, releases


def response(monkeypatch, **values):
    payload = {'tag_name': 'v0.5.2', 'draft': False, 'prerelease': False}
    payload.update(values)
    calls = []

    def open_url(request, timeout):
        calls.append((request, timeout))
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(releases.urllib.request, 'urlopen', open_url)
    return calls


@pytest.mark.parametrize('installed,tag,want', [
    ('0.5.1', 'v0.5.2', 'v0.5.2'),
    ('0.5.9', 'v0.5.10', 'v0.5.10'),
    ('0.5.2', 'v0.5.2', None),
    ('0.6.0', 'v0.5.2', None),
    ('0.5.1', 'v0.5.2rc1', None),
    ('0.5.1', '<script>bad</script>', None),
    ('0.5.2.dev1+g123', 'v0.5.2', None),
    ('unknown', 'v0.5.2', None),
])
def test_only_newer_stable_releases(monkeypatch, installed, tag, want):
    response(monkeypatch, tag_name=tag)
    assert releases.newer_release(installed) == want


@pytest.mark.parametrize('flags', [{'draft': True}, {'prerelease': True}, {'draft': None}])
def test_unpublished_and_prerelease_responses_stay_quiet(monkeypatch, flags):
    response(monkeypatch, **flags)
    assert releases.newer_release('0.5.1') is None


def test_request_is_public_bounded_and_has_no_user_data(monkeypatch):
    calls = response(monkeypatch, html_url='https://untrusted.invalid/')
    assert releases.newer_release('0.5.1') == 'v0.5.2'
    request, timeout = calls[0]
    assert request.full_url == releases.LATEST_API
    assert request.data is None
    assert timeout == 5
    assert set(k.lower() for k in request.headers) == {
        'accept', 'user-agent', 'x-github-api-version'}


@pytest.mark.parametrize('payload', [b'not JSON', b'[]', b'x' * (releases.MAX_RESPONSE + 1)])
def test_unusable_response_is_silent(monkeypatch, payload):
    monkeypatch.setattr(releases.urllib.request, 'urlopen',
                        lambda *a, **kw: io.BytesIO(payload))
    assert releases.newer_release('0.5.1') is None


def test_offline_is_silent(monkeypatch):
    def offline(*args, **kwargs):
        raise TimeoutError('private proxy detail')
    monkeypatch.setattr(releases.urllib.request, 'urlopen', offline)
    assert releases.newer_release('0.5.1') is None


def test_slow_check_does_not_take_the_content_lock_and_runs_once(monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    calls = []

    def check(version):
        calls.append(version)
        entered.set()
        assert finish.wait(5)
        return 'v0.5.2'

    monkeypatch.setattr(releases, 'newer_release', check)
    state = type('State', (), {'action_lock': threading.RLock()})()
    bridge = desktop.Bridge(state)
    worker = threading.Thread(target=bridge.check_release)
    worker.start()
    try:
        assert entered.wait(2)
        assert state.action_lock.acquire(timeout=1)
        state.action_lock.release()
    finally:
        finish.set()
        worker.join(2)
    assert bridge.check_release() == 'v0.5.2'
    assert len(calls) == 1


def test_release_click_only_opens_the_project_page(monkeypatch):
    opened = []
    monkeypatch.setattr(desktop.webbrowser, 'open', lambda url, new: opened.append((url, new)) or True)
    assert desktop.Bridge(None).open_release()
    assert opened == [(releases.RELEASE_PAGE, 2)]
