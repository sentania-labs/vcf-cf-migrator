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


def newer_release(current):
    """Return a validated tag or None. No credentials, content, or retries."""
    installed = stable_version(current)
    if installed is None:
        return None
    try:
        request = urllib.request.Request(LATEST_API, headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'vcfcf-migrator-update-check',
            'X-GitHub-Api-Version': '2022-11-28',
        })
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            return None
        release = json.loads(raw)
        if not isinstance(release, dict):
            return None
        if release.get('draft') is not False or release.get('prerelease') is not False:
            return None
        tag = release.get('tag_name')
        latest = stable_version(tag)
        if latest is not None and latest > installed:
            return 'v' + '.'.join(str(part) for part in latest)
    except Exception:
        # Offline, rate limits, certificate errors and invalid responses are
        # irrelevant to the user's content work. Do not log response bodies.
        pass
    return None
