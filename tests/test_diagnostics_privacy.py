"""Shared diagnostics must be safe even when old events contain raw identity."""
import json

import pytest

from vcfcf_migrator import runlog
from vcfcf_migrator.ui import PageState


@pytest.mark.parametrize('level', runlog.LEVEL_NAMES)
def test_saved_diagnostics_remove_identity_from_every_surface(tmp_path, export_zip, level):
    state = PageState(str(export_zip), log_level_cli=level)
    needles = ['Ava Example', 'ava-login', 'Li', 'Zoë Example',
               'ava@example.invalid', 'AVA-WORKSTATION', '192.0.2.44',
               '/Users/ava-login/private.zip', r'C:\Users\ava-login\private.zip',
               '/home/ava-login/private.zip', 'secret-canary-987', '8877665544']
    # Exercise the final boundary directly, including retained legacy events.
    payload = {n: n for n in needles}
    payload['numericUserId'] = 8877665544
    event = {'event': 'input.fingerprint', 't': 1.25, 'bytes': 1024,
             'members': 7, 'path': needles[7], 'manifest': payload,
             'name': needles[0], 'uuid': needles[1], 'detail': ' '.join(needles),
             'owner': needles[2], 'argv': needles, 'platform': needles[5]}
    state.log.events = [{'event': 'run.start', 'tool': '0.4.0', 'cwd': needles[7]}, event,
                        {'event': 'output.fingerprint', 'files': [payload]}]
    path = tmp_path / 'shared.jsonl'
    state.save_diagnostics(str(path))
    assert path.is_file()
    body = path.read_text()
    for needle in needles:
        assert needle not in body, 'a planted identity reached shared diagnostics'
    rows = [json.loads(line) for line in body.splitlines()]
    assert rows[0]['privacy'] == 'anonymized'
    assert rows[0]['version'] == 2
    assert rows[0]['run']['tool'] == '0.4.0'
    assert rows[0]['source']['bytes'] == 1024
    assert rows[2]['t'] == 1.25
    assert rows[0]['source']['path'] == rows[2]['path']
    assert 'private.zip' not in body


def test_new_fields_and_event_names_fail_closed():
    marker = 'Unregistered Person'
    doc = runlog.diagnostics_document([
        {'event': marker, 'phase': marker, 'lvl': marker, marker: marker,
         'counts': {marker: 8877665544}, 'platform': marker,
         'tool': marker, 'bytes': marker, 'name': marker, 'reason': marker}])
    assert marker not in doc
    assert '8877665544' not in doc


def test_report_labels_preserve_relationships_without_a_mapping():
    source = {'event': 'node.found', 'kind': 'dashboard', 'name': 'Private Name',
              'uuid': 'private-id'}
    target = {'event': 'ref.resolved', 'kind': 'report', 'to_kind': 'dashboard',
              'to_name': 'Private Name', 'to_uuid': 'private-id'}
    doc = runlog.diagnostics_document([source, target], source=source)
    head, one, two = map(json.loads, doc.splitlines())
    assert one['name'] == two['to_name'] == head['source']['name']
    assert one['uuid'] == two['to_uuid']
    assert one['name'] != one['uuid']
    assert 'Private Name' not in doc and 'private-id' not in doc
    assert one['kind'] == 'dashboard' and two['kind'] == 'report'


def test_dynamic_strings_cannot_masquerade_as_safe_versions_or_counts():
    doc = runlog.diagnostics_document([{'event': 'run.start', 'tool': '0.4.0-Ava',
        'core': 'Ava', 'python': '3.12.1 Ava', 'platform': 'macOS-26.1-Ava',
        'bytes': {'name': 'Ava'}, 'members': ['Ava'], 'owner': 8877665544}])
    assert 'Ava' not in doc and '8877665544' not in doc


def test_manifest_unknown_numeric_fields_are_not_mistaken_for_measurements():
    doc = runlog.diagnostics_document([{'event': 'input.fingerprint',
        'manifest': {'bytes': 8877665544, 'dashboards': 13}}])
    assert '8877665544' not in doc
    assert json.loads(doc.splitlines()[1])['manifest']['dashboards'] == 13


def test_report_is_safe_after_settings_change_truncation_and_failure(tmp_path, export_zip):
    state = PageState(str(export_zip))
    state.log.event_cap = 8
    for i in range(20):
        state.log.info('page.action', path='/Users/ava-login/private.zip', index=i)
    state.save_setting({'log_level': 'debug'})
    state.open_export('/home/ava-login/nonexistent.zip')
    out = tmp_path / 'report.jsonl'
    state.save_diagnostics(str(out))
    body = out.read_text()
    assert 'ava-login' not in body and 'nonexistent.zip' not in body
    assert json.loads(body.splitlines()[0])['privacy'] == 'anonymized'


@pytest.mark.parametrize('payload', [
    {'unregistered': {'files': [{'bytes': 8877665544}]}},
    {'unregistered': {'counts': {'dashboard': 8877665544}}},
    {'unregistered': {'manifest': {'dashboards': 8877665544}}},
    {'fields': {'manifest': {'dashboards': 8877665544}}},
    {'bytes': {'files': [{'bytes': 8877665544}]}},
])
def test_untrusted_subtrees_cannot_reenable_metadata_exceptions(payload):
    assert '8877665544' not in runlog.diagnostics_document([payload])


def test_large_untrusted_integers_do_not_prevent_diagnostics():
    doc = runlog.diagnostics_document([{'manifest': {'dashboards': 10 ** 400}}])
    assert json.loads(doc.splitlines()[0])['privacy'] == 'anonymized'


def test_default_attachment_name_does_not_reuse_source_identity():
    state = PageState()
    state.zip_path = '/private/Ava-Example-export.zip'
    assert state.default_diagnostics_out() == '/private/vcfcf-migrator-diagnostics.jsonl'
