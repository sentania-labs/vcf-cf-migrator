"""Exercise owner choices and warnings through the desktop bridge and CLI."""
import io
import json
import zipfile

from pages import save_to, visible
from vcfcf_migrator.cli import main
from vcfcf_migrator.desktop import Bridge
from vcfcf_migrator.ui import PageState, can_go


def make_source(tmp_path, same=False):
    path = tmp_path / 'source.zip'
    users = [{'userId': 'owner-secret-1', 'userName': 'Alice', 'userSourceId': 'directory-1'},
             {'userId': 'owner-secret-2', 'userName': 'Bob', 'userSourceId': 'directory-1'}]
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('configuration.json', json.dumps({'type': 'CUSTOM', 'dashboards': 2}))
        z.writestr('usermappings.json', json.dumps({'sources': [{'id': 'directory-1'}], 'users': users}))
        for i in (1, 2):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as inner:
                inner.writestr('dashboard/dashboard.json', json.dumps({'entries': {}, 'dashboards': [
                    {'id': 'same' if same else f'd{i}', 'name': 'Overview' if same else f'Dashboard {i}', 'widgets': []}]}))
            z.writestr(f'dashboards/owner-secret-{i}', buf.getvalue())
            if i == 1:
                z.writestr('dashboardsharings/owner-secret-1', json.dumps([
                    {'sourceType': 'ACTIVE_DIRECTORY', 'groupName': 'Directory team',
                     'dashboards': [{'dashboardId': 'same' if same else 'd1'}]}]))
    return path


def choose(bridge, key):
    return bridge.act('/select', {'key': key, 'on': '1'})['html']


def go(bridge, step):
    return bridge.act('/go', {'to': step})['html']


def test_the_owner_step_offers_two_choices_and_each_is_the_real_build(tmp_path, config_dir):
    state = PageState(str(make_source(tmp_path)))
    bridge = Bridge(state)
    choose(bridge, 'dashboard:d1@owner-secret-1')
    html = go(bridge, 'owner')
    page = visible(html)
    assert 'Who should own this dashboard on the target?' in page
    # Two choices and nothing else: no third option, no list to pick from, no name to type.
    assert html.count('name="own"') == 2 and '<select' not in html
    assert '<input class="lat-input"' not in html.split('<main>')[1].split('</main>')[0]
    assert 'The built-in admin user Safest' in page and 'The same user as on the source May fail' in page
    # The warning is on the choice itself, before it is picked.
    assert state.owner_mode == 'admin'
    assert ('Warning: The import, or the owner mapping, may fail if one of these users does not exist on '
            'the target. A dashboard whose owner or group is missing there is skipped. This app works '
            'from a file and cannot check the target for you.') in page
    # What admin does, per dashboard.
    assert 'Dashboard 1 admin Sharing not carried You share it with Directory team again after import' in page
    assert 'Directory team is a group from your company’s staff directory.' in page
    assert 'owner-secret-1' not in page
    out = tmp_path / 'admin.zip'
    save_to(state, out)
    with zipfile.ZipFile(out) as z:
        mapping = json.loads(z.read('usermappings.json'))
        assert mapping['users'][0]['userName'] == 'admin' and mapping['sources'] == []
    assert 'Share Dashboard 1 with Directory team again' in out.with_name('admin-checklist.txt').read_text()

    # The same user as on the source: applies on click, and the table says where it can fail.
    state.screen = 'owner'
    page = visible(bridge.act('/owner', {'mode': 'source'})['html'])
    assert state.owner_mode == 'source'
    assert 'Dashboard 1 Alice Skipped if Alice is not on the target' in page
    assert 'Shared with Directory team Whole dashboard skipped if the target has no such group' in page
    review = visible(go(bridge, 'review'))
    assert 'The import may fail if a dashboard owner is not on the target' in review
    out = tmp_path / 'source-owned.zip'
    save_to(state, out)
    with zipfile.ZipFile(out) as z:
        mapping = json.loads(z.read('usermappings.json'))
        assert mapping['users'][0]['userSourceId'] == 'directory-1'
        assert mapping['sources'] == [{'id': 'directory-1'}]
        assert json.loads(z.read('dashboardsharings/owner-secret-1'))[0]['sourceType'] == 'ACTIVE_DIRECTORY'
    checklist = out.with_name('source-owned-checklist.txt').read_text()
    assert checklist.index('First, make sure these users exist on the target: Alice') < checklist.index('Import source-owned.zip')
    assert 'Look for the group Directory team there as well.' in checklist
    state.log.close()


def test_the_window_no_longer_sends_one_named_user_and_the_program_still_can(tmp_path, config_dir):
    from vcfcf_migrator import bundle, selection
    state = PageState(str(make_source(tmp_path)))
    bridge = Bridge(state)
    choose(bridge, 'dashboard:d1@owner-secret-1')
    go(bridge, 'owner')
    # Whatever a page sends, the window takes only the two choices it shows.
    bridge.act('/owner', {'mode': 'other', 'target_user': 'target-user', 'owner_mode': 'other'})
    assert (state.owner_mode, state.target_user) == ('admin', 'admin')
    assert 'target-user' not in state.render()
    # The capability is kept for later: the writer still maps to a named user.
    out = tmp_path / 'named.zip'
    picked = selection.close(state.graph, ['dashboard:d1@owner-secret-1'])
    assert any('target-user' in line for line in selection.ownership_messages(state.graph, picked, 'target-user'))
    result = bundle.build_bundle(state.members.data, state.members.order, state.graph, picked, out,
                                 target_user='target-user')
    with zipfile.ZipFile(out) as z:
        assert json.loads(z.read('usermappings.json'))['users'][0]['userName'] == 'target-user'
    assert result.tasks[0].title == 'First, make sure the user target-user exists on the target'
    state.log.close()


def test_cli_owner_choices_warn_and_write_requested_mapping(tmp_path, capsys):
    source = make_source(tmp_path)
    picks = tmp_path / 'picks.txt'
    picks.write_text('dashboard:d1@owner-secret-1\n')
    for args, expected in [(['--target-owner', 'target-user'], 'target-user'),
                           (['--keep-directory-ownership'], 'Alice')]:
        out = tmp_path / (expected + '.zip')
        assert main(['build', str(source), '--select', str(picks), '--out', str(out), *args]) == 0
        output = capsys.readouterr()
        assert 'will be skipped unless' in output.err and 'Dashboard 1' in output.err
        with zipfile.ZipFile(out) as z:
            assert json.loads(z.read('usermappings.json'))['users'][0]['userName'] == expected


def test_two_copies_are_asked_about_first_and_one_can_be_kept(tmp_path, config_dir):
    state = PageState(str(make_source(tmp_path, same=True)))
    bridge = Bridge(state)
    choose(bridge, 'dashboard:same@owner-secret-1')
    html = choose(bridge, 'dashboard:same@owner-secret-2')
    assert 'You now have both copies of this dashboard. The next step asks which to keep.' in visible(html)
    html = go(bridge, 'owner')
    page = visible(html)
    assert 'First: which copy of Overview do you want?' in page
    assert 'The copy owned by Alice' in page and 'The copy owned by Bob' in page
    assert 'Both, under their original owners' in page and 'Shared with Directory team' in page
    assert 'owner-secret-1' not in page and 'owner-secret-2' not in page
    # The owner question itself, and Review, wait for this answer.
    assert 'Who should own' not in page and 'Next: review' not in page
    assert not can_go(state, 'review')
    go(bridge, 'review')
    assert state.screen == 'owner'
    page = visible(bridge.act('/keep-copy', {'key': 'dashboard:same@owner-secret-1'})['html'])
    assert state.picked == ['dashboard:same@owner-secret-1']
    assert 'The copy of Overview owned by Bob was left out of the bundle. The other copy stays in. Undo' in page
    assert 'Who should own this dashboard on the target?' in page and can_go(state, 'review')
    # Undo brings the question back.
    bridge.act('/undo', {})
    assert len(state.picked) == 2 and state.owner_question() == 'copies'
    state.log.close()


def test_a_copy_that_comes_with_something_else_cannot_be_dropped_on_its_own(tmp_path, config_dir):
    path = make_source(tmp_path, same=True)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, 'w') as z:
        z.writestr('content.xml', '<Content><Reports><ReportDef id="r"><Title>Both copies report</Title>'
                   '<Sections><Section><ContentType>Dashboard</ContentType><ContentKey>same</ContentKey>'
                   '</Section></Sections></ReportDef></Reports></Content>')
    with zipfile.ZipFile(path, 'a') as z:
        z.writestr('reports.zip', inner.getvalue())
    state = PageState(str(path))
    bridge = Bridge(state)
    choose(bridge, 'report:r')
    page = visible(go(bridge, 'owner'))
    assert 'First: which copy of Overview do you want?' in page
    assert 'Keep this copy' not in page
    assert 'The other copy comes along with Both copies report.' in page
    bridge.act('/keep-copy', {'key': 'dashboard:same@owner-secret-1'})
    assert state.owner_question() == 'copies'
    bridge.act('/owner', {'mode': 'source', 'both': '1'})
    assert state.owner_question() == '' and 'Both copies are kept' in state.note.text
    state.log.close()


def test_a_super_metric_and_a_deleted_view_are_good_to_know_in_plain_words(tmp_path, config_dir):
    sm = '11111111-1111-4111-8111-111111111111'
    source = tmp_path / 'warnings.zip'
    dashboard = {'id': 'dashboard-private-id', 'name': 'Summary', 'widgets': [
        {'id': 'widget-private-id', 'name': 'Capacity view',
         'config': {'viewDefinitionId': 'view-private-id'}}]}
    with zipfile.ZipFile(source, 'w') as z:
        z.writestr('configuration.json', json.dumps({'type': 'ALL', 'dashboards': 1, 'views': 1}))
        z.writestr('dashboard/dashboard.json', json.dumps({'dashboards': [dashboard]}))
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, 'w') as inner:
            inner.writestr('content.xml', '<Views><ViewDef id="present"><Title>Present</Title></ViewDef></Views>')
        z.writestr('views.zip', nested.getvalue())
        z.writestr('supermetrics.json', json.dumps({sm: {'name': 'Demand', 'formula': '', 'resourceKinds': []}}))
    state = PageState(str(source))
    bridge = Bridge(state)
    html = choose(bridge, 'dashboard:dashboard-private-id')
    assert 'One widget will be empty' in visible(html)     # on the row, before Review
    choose(bridge, 'supermetric:' + sm)
    # No owner to choose: the dashboard in this export belongs to nobody.
    assert 'Dashboard owner (not needed, no dashboards chosen)' in visible(state.render())
    page = visible(go(bridge, 'review'))
    assert 'Ready to save' in page and 'Good to know, nothing to decide' in page
    assert 'Summary: one widget will be empty' in page
    assert '1 super metric arrives switched off' in page
    page = visible(bridge.act('/issue', {'id': 'i-dashboard:dashboard-private-id'})['html'])
    assert ('Its widget "Capacity view" shows a view that is not in this export, most likely because '
            'the view was deleted on the source.') in page
    for private in ('viewDefinitionId', 'dashboard-private-id', 'view-private-id', 'widget-private-id'):
        assert private not in page
    out = tmp_path / 'warnings-out.zip'
    save_to(state, out)
    page = visible(state.render())
    assert out.exists() and 'Bundle saved' in page
    checklist = out.with_name('warnings-out-checklist.txt').read_text()
    assert 'Switch on 1 super metric' in checklist and 'Tick Demand.' in checklist
    assert 'Expect one empty widget on Summary' in checklist and 'Capacity view' in checklist
    for private in ('viewDefinitionId', 'dashboard-private-id', 'view-private-id'):
        assert private not in page and private not in checklist
    state.log.close()


def test_a_private_dashboard_is_said_to_stay_private(tmp_path, config_dir):
    state = PageState(str(make_source(tmp_path)))
    bridge = Bridge(state)
    choose(bridge, 'dashboard:d2@owner-secret-2')
    page = visible(go(bridge, 'owner'))
    assert 'Dashboard 2 admin Private, as on the source' in page
    out = tmp_path / 'private.zip'
    save_to(state, out)
    assert out.exists()
    report = out.with_name('private-report.txt').read_text()
    assert 'Dashboards will belong to the built-in admin user on the target.' in report
    assert 'These dashboards will be private' in report        # the technical section, for support
    assert 'owner-secret-2' not in visible(state.render())
    state.log.close()


def test_keep_source_refuses_missing_directory_information(tmp_path, capsys):
    source = make_source(tmp_path)
    altered = tmp_path / 'missing-source.zip'
    with zipfile.ZipFile(source) as inp, zipfile.ZipFile(altered, 'w') as out:
        for item in inp.infolist():
            data = inp.read(item)
            if item.filename == 'usermappings.json':
                doc = json.loads(data)
                doc['sources'] = []
                data = json.dumps(doc).encode()
            out.writestr(item, data)
    picks = tmp_path / 'picks.txt'
    picks.write_text('dashboard:d1@owner-secret-1\n')
    target = tmp_path / 'bad.zip'
    assert main(['build', str(altered), '--select', str(picks), '--out', str(target),
                 '--keep-directory-ownership']) == 1
    assert not target.exists()
    assert 'missing directory information' in capsys.readouterr().err


def test_cli_collision_identifies_owners_without_raw_ids(tmp_path, capsys):
    source = make_source(tmp_path, same=True)
    out = tmp_path / 'collision.zip'
    assert main(['build', str(source), '--select-all', '--out', str(out)]) == 1
    output = capsys.readouterr().err
    assert 'Overview' in output and 'Alice' in output and 'Bob' in output
    assert 'choose one copy' in output
    assert 'owner-secret-1' not in output and 'owner-secret-2' not in output
    assert not out.exists()


def test_copies_stay_distinct_when_both_are_kept_and_in_the_report(tmp_path, config_dir):
    state = PageState(str(make_source(tmp_path, same=True)))
    bridge = Bridge(state)
    choose(bridge, 'dashboard:same@owner-secret-1')
    choose(bridge, 'dashboard:same@owner-secret-2')
    go(bridge, 'owner')
    page = visible(bridge.act('/owner', {'mode': 'source', 'both': '1'})['html'])
    alice, bob = "Overview (Alice's copy)", "Overview (Bob's copy)"
    assert alice in page and bob in page
    assert 'Both copies are kept' in page and '"The same user as on the source" is selected below.' in page
    assert f'{bob} Bob Skipped if Bob is not on the target Private, as on the source' in page
    assert 'Choosing this takes you back to pick one copy of Overview.' in page
    out = tmp_path / 'both-owners.zip'
    save_to(state, out)
    assert out.exists()
    report = out.with_name('both-owners-report.txt').read_text()
    assert 'Dashboards will belong to the same users as on the source.' in report
    assert 'Source owners and directory sharing are kept for: ' + alice + ', ' + bob in report
    assert 'No sharing remains for: ' + bob in report
    state.log.close()


def test_same_named_views_are_told_apart_without_showing_an_id(tmp_path, config_dir):
    ids = ['11111111-1111-4111-8111-11111111abcd', '22222222-2222-4222-8222-22222222abcd',
           '33333333-3333-4333-8333-33333331abcd', '44444444-4444-4444-8444-44444442abcd']
    xml = ('<Views>'
           f'<ViewDef id="{ids[0]}"><Title>CPU Overview</Title><Description>Production servers</Description></ViewDef>'
           f'<ViewDef id="{ids[1]}"><Title>CPU Overview</Title><Description>Development servers</Description></ViewDef>'
           f'<ViewDef id="{ids[2]}"><Title>Capacity</Title><Description>Same description</Description></ViewDef>'
           f'<ViewDef id="{ids[3]}"><Title>Capacity</Title><Description>Same description</Description></ViewDef>'
           '</Views>')
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, 'w') as z:
        z.writestr('content.xml', xml)
    source = tmp_path / 'views.zip'
    with zipfile.ZipFile(source, 'w') as z:
        z.writestr('configuration.json', json.dumps({'type': 'ALL', 'views': 4}))
        z.writestr('views.zip', inner.getvalue())
    state = PageState(str(source))
    bridge = Bridge(state)
    listing = visible(state.render())
    production = 'CPU Overview (Production servers)'
    development = 'CPU Overview (Development servers)'
    assert production in listing and development in listing
    assert 'CPU Overview (id' not in listing
    assert 'Capacity (id …1abcd)' in listing and 'Capacity (id …2abcd)' in listing
    for ident in ids:
        choose(bridge, 'view:' + ident)
    go(bridge, 'review')
    page = visible(bridge.act('/issue', {'id': 'contents'})['html'])
    assert production in page and development in page
    out = tmp_path / 'views-out.zip'
    save_to(state, out)
    page = visible(bridge.act('/doc', {'k': 'report'})['html'])
    assert out.exists() and 'Capacity (id …1abcd)' in page and 'Capacity (id …2abcd)' in page
    assert 'Bundle report' in page and 'What is inside (4)' in page
    for ident in ids:
        assert ident not in page
    # The full ids are where support will look for them: at the end of the saved file.
    saved = (tmp_path / 'views-out-report.txt').read_text(encoding='utf-8')
    assert saved.index('Bundle report') < saved.index('Technical detail, for support')
    assert state.build_report in saved
    assert 'Support details\n' in saved
    assert ids[2] in saved and ids[3] in saved
    state.log.close()


def test_the_saved_report_keeps_the_technical_detail_at_its_end(tmp_path, config_dir):
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, 'w') as z:
        z.writestr('dashboard/dashboard.json', json.dumps({'entries': {
                    'resource': [{'internalId': 'resource:id:0_::_', 'name': 'Old cluster', 'adapterKindKey': 'VMWARE'}],
                    'resourceKind': [{'internalId': 'resourceKind:id:0_::_', 'adapterKindKey': 'Example', 'resourceKindKey': 'Old kind'}]},
                    'dashboards': [{'id': 'keep-dashboard', 'name': 'Keep', 'widgets': []},
                                   {'id': 'other-dashboard', 'name': 'Other', 'widgets': []}]}))
        z.writestr('dashboard/resources/resources.properties',
                   'keep-dashboard.title=Keep\nother-dashboard.title=Other\nshared=Shared\nescaped\\ name=Value\ncontinued\\\n  key=Value\n!comment\\\ncomment-next=Value\n')
    source = tmp_path / 'without-marker.zip'
    with zipfile.ZipFile(source, 'w') as z:
        z.writestr('configuration.json', json.dumps({'type': 'ALL', 'dashboards': 2}))
        z.writestr('dash-export.zip', nested.getvalue())
        z.writestr('customgroups.json', json.dumps({'customGroups': [
            {'name': 'Keep group', 'resourceKind': 'Keep type'},
            {'name': 'Other group', 'resourceKind': 'Other type'}],
            'customGroupTypes': [{'resourceKind': 'Keep type'}, {'resourceKind': 'Other type'}]}))
    state = PageState(str(source))
    bridge = Bridge(state)
    choose(bridge, 'dashboard:keep-dashboard')
    choose(bridge, 'customgroup:Keep group')
    out = tmp_path / 'subset.zip'
    save_to(state, out)
    html = bridge.act('/doc', {'k': 'report'})['html']
    page = visible(html)
    # On screen: the report a person reads, with nothing folded away and no
    # block of fixed-width text.
    assert out.exists() and '<details' not in html and '<pre' not in html
    assert 'Chosen by you (2)' in page and 'Keep' in page and 'Keep group' in page
    assert 'To do on the target' in page and 'The saved file ends with a technical section for support.' in page
    assert 'unused custom group types' not in page
    saved = (tmp_path / 'subset-report.txt').read_text(encoding='utf-8')
    technical = saved.split('Technical detail, for support')[1]
    assert state.build_report in technical
    for line in ('Localization entries kept because they could not be matched: shared',
                 'localization keys for unselected objects: other-dashboard.title',
                 'unused custom group types: Other type',
                 'unused object entries: VMWARE / Old cluster',
                 'unused resource kind entries: Example / Old kind',
                 'source export is missing its format marker'):
        assert line in technical, line
    assert 'escaped\\ name' in technical and 'continuedkey' in technical and 'comment-next' in technical
    assert '<digits>' not in saved and 'L.v1' not in saved
    assert 'dashboard/resources/resources.properties' not in saved
    state.log.close()


def test_duplicate_labels_use_available_modification_dates_in_review(tmp_path, config_dir, monkeypatch):
    import time
    monkeypatch.setenv('TZ', 'America/Chicago')
    time.tzset()
    try:
        _check_dates_in_review(tmp_path)
    finally:
        monkeypatch.undo()
        time.tzset()


def test_duplicate_labels_fall_back_to_utc_for_unknown_timezone(tmp_path, config_dir, monkeypatch):
    import time
    monkeypatch.setenv('TZ', 'Invalid/Unknown')
    time.tzset()
    try:
        _check_dates_in_review(tmp_path, ('12:00 UTC', '18:00 UTC'))
    finally:
        monkeypatch.undo()
        time.tzset()


def _check_dates_in_review(tmp_path, times=('06:00 CST', '12:00 CST')):
    first = '11111111-1111-4111-8111-111111111111'
    second = '22222222-2222-4222-8222-222222222222'
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, 'w') as z:
        z.writestr('content.xml', '<Views>'
                   f'<ViewDef id="{first}" lastModified="2026-01-15T12:00:00Z"><Title>CPU</Title></ViewDef>'
                   f'<ViewDef id="{second}" lastModified="2026-01-15T18:00:00Z"><Title>CPU</Title></ViewDef>'
                   '</Views>')
    source = tmp_path / 'dates.zip'
    with zipfile.ZipFile(source, 'w') as z:
        z.writestr('configuration.json', json.dumps({'type': 'ALL', 'views': 2}))
        z.writestr('views.zip', inner.getvalue())
    state = PageState(str(source))
    bridge = Bridge(state)
    choose(bridge, 'view:' + first)
    choose(bridge, 'view:' + second)
    go(bridge, 'review')
    page = visible(bridge.act('/issue', {'id': 'contents'})['html'])
    assert f'CPU (2026-01-15 {times[0]})' in page
    assert f'CPU (2026-01-15 {times[1]})' in page
    assert 'CPU (id …' not in page
    state.log.close()


def test_owner_merge_preserves_missing_binding_and_localized_values(tmp_path, config_dir, monkeypatch):
    import os
    source = tmp_path / 'two-owners.zip'
    token = 'resource:id:0_::_'
    with zipfile.ZipFile(source, 'w') as outer:
        outer.writestr('configuration.json', json.dumps({'type': 'CUSTOM', 'dashboards': 2}))
        for owner in ('a', 'b'):
            inner = io.BytesIO()
            entries = [{'internalId': token, 'name': 'Cluster A', 'adapterKindKey': 'VMWARE',
                        'resourceKindKey': 'Cluster'}] if owner == 'a' else []
            dashboard = {'id': owner, 'name': 'Dashboard ' + owner,
                         'localizationKey': 'shared.title', 'widgets': [
                             {'name': 'Cluster widget', 'config': {'resource': token}}]}
            with zipfile.ZipFile(inner, 'w') as z:
                z.writestr('dashboard/dashboard.json', json.dumps({'entries': {'resource': entries},
                                                                  'dashboards': [dashboard]}))
                z.writestr('dashboard/resources/resources.properties',
                           'shared.title=' + ('First title' if owner == 'a' else 'Second title') + '\ncommon=Same\n')
            outer.writestr('dashboards/' + owner, inner.getvalue())
    state = PageState(str(source))
    bridge = Bridge(state)
    choose(bridge, 'dashboard:a@a')
    choose(bridge, 'dashboard:b@b')
    go(bridge, 'owner')
    review = visible(go(bridge, 'review'))
    assert 'Dashboard b: one widget may stay empty under a shared owner' in review
    assert 'Dashboard b: one of its texts is renamed inside the bundle' in review
    page = visible(bridge.act('/issue', {'id': 'merge-0'})['html'])
    assert 'that tie cannot be kept, so the widget may stay empty on the target' in page
    assert 'shared.title' not in review + page          # an internal name stays in the saved file
    out = tmp_path / 'combined.zip'
    save_to(state, out)
    assert out.exists()
    report = (tmp_path / 'combined-report.txt').read_text(encoding='utf-8')
    assert 'missing widget binding will remain unresolved' in report
    assert 'conflicting localization key shared.title' in report
    with zipfile.ZipFile(out) as outer:
        with zipfile.ZipFile(io.BytesIO(outer.read('dashboards/a'))) as inner:
            doc = json.loads(inner.read('dashboard/dashboard.json'))
            props = inner.read('dashboard/resources/resources.properties').decode('latin-1')
    first, second = doc['dashboards']
    assert first['widgets'][0]['config']['resource'] == token
    unresolved = second['widgets'][0]['config']['resource']
    assert unresolved != token
    assert unresolved not in {item['internalId'] for item in doc['entries']['resource']}
    assert first['localizationKey'] == 'shared.title'
    assert second['localizationKey'] == 'shared.title.merged2'
    assert 'shared.title=First title\n' in props
    assert 'shared.title.merged2=Second title\n' in props
    assert props.count('common=Same') == 1
    evidence = os.environ.get('VCFM_UX_EVIDENCE_DIR')
    if evidence:
        from pathlib import Path
        Path(evidence, 'owner-merge-after.txt').write_text(review + '\n\n' + page, encoding='utf-8')
    state.log.close()
