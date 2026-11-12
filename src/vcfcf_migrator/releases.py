"""A best-effort check of the project's public stable release."""

import json
import re
import urllib.request

LATEST_API = 'https://api.github.com/repos/sentania-labs/vcf-cf-migrator/releases/latest'
RELEASE_PAGE = 'https://github.com/sentania-labs/vcf-cf-migrator/releases/latest'
MAX_RESPONSE = 256 * 1024


def stable_version(value):
    """Compare release numbers numerically; development builds stay quiet."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value)
    return tuple(int(part) for part in match.groups()) if match else None


def release_status(current):
    """``(tag, reached)``: a validated newer tag or None, and whether the
    release page actually answered. Only an answer lets the app say "that is
    the latest version". No credentials, content, or retries."""
    installed = stable_version(current)
    if installed is None:
        return None, False
    try:
        request = urllib.request.Request(LATEST_API, headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'vcfcf-migrator-update-check',
            'X-GitHub-Api-Version': '2022-11-28',
        })
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            return None, False
        release = json.loads(raw)
        if not isinstance(release, dict):
            return None, False
        if release.get('draft') is not False or release.get('prerelease') is not False:
            return None, False
        latest = stable_version(release.get('tag_name'))
        if latest is None:
            return None, False
        if latest > installed:
            return 'v' + '.'.join(str(part) for part in latest), True
        return None, True
    except Exception:
        # Offline, rate limits, certificate errors and invalid responses are
        # irrelevant to the user's content work. Do not log response bodies.
        return None, False


def newer_release(current):
    """Return a validated tag or None. No credentials, content, or retries."""
    return release_status(current)[0]
