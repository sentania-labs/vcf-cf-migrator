"""Acquire a private Operations export snapshot with session-only credentials.

The API exposes the latest export, not an operation-specific download URL.
Require a new operation after acceptance and an unchanged ID before and after
its download. The account must not be used for another export concurrently.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import socket
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

CONTENT_TYPES = (
    'DASHBOARDS', 'VIEW_DEFINITIONS', 'SUPER_METRICS', 'CUSTOM_GROUPS',
    'SYMPTOM_DEFINITIONS', 'ALERT_DEFINITIONS', 'RECOMMENDATION_DEFINITIONS',
    'REPORT_DEFINITIONS', 'NOTIFICATION_RULES', 'OUTBOUND_SETTINGS', 'PAYLOAD_TEMPLATES',
)
MAX_DOWNLOAD = 256 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024


class SourceError(Exception):
    """A safe message that contains no response body, URL, or credentials."""


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Snapshot:
    def __init__(self):
        self._directory = tempfile.TemporaryDirectory(prefix='vcfcf-source-')
        self.path = Path(self._directory.name) / 'operations-export.zip'

    def close(self):
        self._directory.cleanup()


def endpoint(value):
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise SourceError('Enter a valid HTTPS Operations address.') from None
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path.rstrip('/') not in ('', '/suite-api', '/suite-api/api')):
        raise SourceError('Use the HTTPS Operations address without credentials, query, or page path.')
    return urllib.parse.urlunsplit(('https', parsed.netloc, '/suite-api/api', '', ''))


def validate_size(path):
    """Bound expanded content across nested Operations containers before parsing."""
    remaining = MAX_EXPANDED

    def check(archive, depth):
        nonlocal remaining
        members = archive.infolist()
        remaining -= sum(member.file_size for member in members)
        if remaining < 0:
            raise SourceError('The export exceeds the expanded-size limit. Use a smaller source export.')
        for member in members:
            if member.is_dir():
                continue
            with archive.open(member) as stream:
                prefix = stream.read(4)
                if prefix not in (b'PK\x03\x04', b'PK\x05\x06'):
                    continue
                if depth >= 4:
                    raise SourceError('The export contains too many nested archives. Use a manual export.')
                payload = prefix + stream.read()
            with zipfile.ZipFile(io.BytesIO(payload)) as nested:
                check(nested, depth + 1)

    with zipfile.ZipFile(path) as archive:
        check(archive, 0)


class Client:
    def __init__(self, address, ca_file='', opener=None, progress=None,
                 timeout=300, poll_interval=1):
        self.base = endpoint(address)
        self.token = None
        self.progress = progress or (lambda text: None)
        self.timeout = timeout
        self.poll_interval = poll_interval
        try:
            context = ssl.create_default_context(cafile=ca_file or None)
        except (OSError, ssl.SSLError):
            raise SourceError('The trusted CA file could not be read. Choose a PEM CA certificate.') from None
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context), NoRedirects())

    def _request(self, path, data=None, extra_headers=None):
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'vRealizeOpsToken ' + self.token
        headers.update(extra_headers or {})
        request = urllib.request.Request(self.base + path, headers=headers,
                                         data=json.dumps(data).encode() if data is not None else None)
        try:
            return self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            messages = {
                401: 'Authentication failed or the session expired. Check credentials and authority source.',
                403: 'Operations refused the request. Check export permissions and whether another content job is running.',
                404: 'This Operations endpoint does not offer the required content export API.',
                429: 'Operations is busy. Wait before connecting again.',
            }
            raise SourceError(messages.get(code, 'Operations could not complete the request. No automatic export retry was made.')) from None
        except (urllib.error.URLError, ssl.SSLError, OSError, socket.timeout):
            raise SourceError('Connection failed. Check the address, trusted CA, network, and source availability. If export had started, check its status before retrying.') from None

    def _json(self, path, data=None, extra_headers=None):
        try:
            with self._request(path, data, extra_headers) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('oversized response')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('unexpected response')
            return value
        except SourceError:
            raise
        except (ValueError, OSError, UnicodeError):
            raise SourceError('Operations returned an unreadable response. The current inventory is unchanged.') from None

    def _status(self):
        status = self._json('/content/operations/export')
        if status.get('state') not in ('NOT_INITIALIZED', 'INITIALIZED', 'RUNNING', 'FAILED', 'FINISHED'):
            raise SourceError('Operations returned an unknown export state. Use a manually downloaded ZIP.')
        return status

    @staticmethod
    def _identity(status):
        ident, started = status.get('id'), status.get('startTime')
        if not isinstance(ident, str) or not ident or not isinstance(started, int) or isinstance(started, bool):
            raise SourceError('Operations did not identify the export reliably. Use a manually downloaded ZIP.')
        return ident, started

    def acquire(self, username, password, auth_source='', export_password=''):
        snapshot = None
        try:
            self.progress('Authenticating')
            credentials = {'username': username, 'password': password}
            if auth_source:
                credentials['authSource'] = auth_source
            try:
                result = self._json('/auth/token/acquire', credentials)
            finally:
                credentials.clear()
            token = result.get('token')
            if not isinstance(token, str) or not token:
                raise SourceError('Operations did not return a usable session token.')
            self.token = token
            result.clear()
            previous = self._status()
            if previous['state'] in ('INITIALIZED', 'RUNNING'):
                raise SourceError('Another export is running. Let it finish before connecting.')
            previous_id = self._identity(previous) if previous['state'] != 'NOT_INITIALIZED' else None
            self.progress('Requesting content export')
            extra = {'EncryptionPassword': export_password} if export_password else {}
            try:
                accepted = self._json('/content/operations/export',
                                      {'scope': 'CUSTOM', 'contentTypes': list(CONTENT_TYPES)}, extra)
            finally:
                extra.clear()
            types = accepted.get('contentTypes')
            if (accepted.get('scope') != 'CUSTOM' or not isinstance(types, list)
                    or not all(isinstance(item, str) for item in types)
                    or set(types) != set(CONTENT_TYPES)):
                raise SourceError('Operations did not accept the requested content scope. Use a manual export.')
            deadline = time.monotonic() + self.timeout
            identity = None
            self.progress('Waiting for Operations export')
            while time.monotonic() < deadline:
                current = self._status()
                if current['state'] == 'NOT_INITIALIZED':
                    time.sleep(self.poll_interval)
                    continue
                candidate = self._identity(current)
                if identity is None:
                    if previous_id and (candidate[0] == previous_id[0] or candidate[1] <= previous_id[1]):
                        time.sleep(self.poll_interval)
                        continue
                    identity = candidate
                if candidate != identity:
                    raise SourceError('Another export replaced this operation. Nothing was loaded; try again with exclusive use of this account.')
                if current['state'] == 'FAILED' or current.get('errorCode', 'NONE') != 'NONE' or current.get('errorMessages'):
                    raise SourceError('Operations could not export the content. Check the source content job and export password.')
                if current['state'] == 'FINISHED':
                    break
                time.sleep(self.poll_interval)
            else:
                raise SourceError('Export timed out. The source job may still be running; check it before retrying.')
            self.progress('Downloading content export')
            snapshot = Snapshot()
            try:
                with self._request('/content/operations/export/zip') as response, snapshot.path.open('xb') as target:
                    snapshot.path.chmod(0o600)
                    count = 0
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > MAX_DOWNLOAD:
                            raise SourceError('The export exceeds the 256 MiB download limit. Download it manually instead.')
                        target.write(chunk)
                after = self._status()
                if self._identity(after) != identity or after['state'] != 'FINISHED':
                    raise SourceError('The export changed during download. Nothing was loaded.')
                validate_size(snapshot.path)
                self.progress('Loading downloaded content')
            except SourceError:
                raise
            except (OSError, ValueError, zipfile.BadZipFile):
                raise SourceError('The export download was incomplete or invalid. The current inventory is unchanged.') from None
            return snapshot
        except Exception:
            if snapshot is not None:
                snapshot.close()
            raise
        finally:
            self.token = None
