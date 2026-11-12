"""Ownership translation and file-local token collision regression tests."""
import io
import json
import zipfile

from vcfcf_migrator import bundle, graph, selection


def archive(owner, token, name):
    body = {'entries': {'resource': [{'internalId': token, 'name': name,
                                     'adapterKindKey': 'Container', 'resourceKindKey': 'Function'}]},
            'dashboards': [{'id': owner, 'name': owner, 'widgets': [{'id': 'w', 'config': {
                'metric': {'resourceMetrics': [{'resourceId': token, 'resourceName': name}]}}}]}]}
    raw = json.dumps(body).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('dashboard/dashboard.json', raw)
    return buf.getvalue()


def test_owner_merge_keeps_bindings_and_only_rewrites_collisions(tmp_path):
    for second_token in ('resource:id:0_::_', 'resource:id:1_::_'):
        members = {'dashboards/a': archive('a', 'resource:id:0_::_', 'Group A'),
                   'dashboards/b': archive('b', second_token, 'Group B'),
                   'usermappings.json': json.dumps({'sources': [{'id': 'directory'}], 'users': [
                       {'userId': 'a', 'userName': 'someone', 'userSourceId': 'directory'},
                       {'userId': 'b', 'userName': 'someone-else', 'userSourceId': 'directory'}]}).encode(),
                   'customgroups.json': json.dumps({'customGroups': [{'name': 'Group A'}, {'name': 'Group B'}],
                                                    'customGroupTypes': []}).encode()}
        g = graph.build_graph(members)
        picked = selection.select_all(g)
        out = tmp_path / 'bundle.zip'
        result = bundle.build_bundle(members, list(members), g, picked, out)
        with zipfile.ZipFile(out) as z:
            assert json.loads(z.read('usermappings.json')) == {
                'sources': [], 'users': [{'userId': 'a', 'userName': 'admin'}]}
            assert 'dashboards/b' not in z.namelist()
            built = {'dashboards/a': z.read('dashboards/a'), 'customgroups.json': z.read('customgroups.json')}
            merged = graph.build_graph(built)
            assert {merged.nodes[k].ident for k in merged.edges['dashboard:a@a']} == {'Group A'}
            assert {merged.nodes[k].ident for k in merged.edges['dashboard:b@a']} == {'Group B'}
            originals = {e.ident: e.raw for c in g.containers for e in c.entries() if e.kind == 'dashboard'}
            actual = {e.ident: e.raw for c in merged.containers for e in c.entries() if e.kind == 'dashboard'}
            assert actual['a'] == originals['a']
            if second_token.endswith('0_::_'):
                assert actual['b'] != originals['b']
                assert any('b' in note and 'Resource references were adjusted' in note for note in result.notes)
            else:
                assert actual['b'] == originals['b']


def test_single_directory_owner_mapping_and_dashboard_bytes(tmp_path):
    source = archive('a', 'resource:id:0_::_', 'Group A')
    members = {'dashboards/a': source,
               'usermappings.json': json.dumps({'sources': [{'id': 'directory'}], 'users': [
                   {'userId': 'a', 'userName': 'person', 'userSourceId': 'directory'}]}).encode(),
               'customgroups.json': json.dumps({'customGroups': [{'name': 'Group A'}],
                                                'customGroupTypes': []}).encode()}
    g = graph.build_graph(members)
    picked = selection.select_all(g)
    assert 'a' in g.directory_owners
    assert 'Directory-owned dashboards: a' in selection.render(g, picked)
    out = tmp_path / 'single.zip'
    bundle.build_bundle(members, list(members), g, picked, out)
    with zipfile.ZipFile(out) as z:
        assert json.loads(z.read('usermappings.json')) == {
            'sources': [], 'users': [{'userId': 'a', 'userName': 'admin'}]}
        with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(
                io.BytesIO(z.read('dashboards/a'))) as actual:
            original_doc = json.loads(original.read('dashboard/dashboard.json'))
            actual_doc = json.loads(actual.read('dashboard/dashboard.json'))
            assert actual_doc['dashboards'] == original_doc['dashboards']
            original_container = graph._containers.DashboardsContainer('dashboards/a', source, owner='a')
            actual_container = graph._containers.DashboardsContainer('dashboards/a', z.read('dashboards/a'), owner='a')
            assert original_container.entries()[0].raw == actual_container.entries()[0].raw


def test_directory_sharing_is_removed_and_reported(tmp_path):
    members = {'dashboards/a': archive('a', 'resource:id:0_::_', 'Group A'),
               'dashboardsharings/a': json.dumps([
                   {'sourceType': 'ACTIVE_DIRECTORY', 'groupName': 'directory group',
                    'dashboards': [{'dashboardId': 'a'}]},
                   {'sourceType': 'LOCAL', 'groupName': 'local group',
                    'dashboards': [{'dashboardId': 'a'}]}]).encode(),
               'customgroups.json': json.dumps({'customGroups': [{'name': 'Group A'}]}).encode()}
    g = graph.build_graph(members)
    picked = selection.select_all(g)
    assert 'Directory sharing will be removed for: a' in selection.render(g, picked)
    out = tmp_path / 'out.zip'
    result = bundle.build_bundle(members, list(members), g, picked, out)
    with zipfile.ZipFile(out) as z:
        groups = json.loads(z.read('dashboardsharings/a'))
        assert [group['groupName'] for group in groups] == ['local group']
    assert any('Directory sharing removed for: a' in note for note in result.notes)
    assert any('admin on the target (1 dashboard)' in note for note in result.notes)


def test_inconsistent_owner_scaffolding_is_rejected():
    import pytest
    with pytest.raises(ValueError, match='owner files'):
        bundle._validate_dashboard_owners({
            'usermappings.json': b'{"sources":[],"users":[{"userId":"a","userName":"admin"}]}',
            'dashboards/b': b'wrong owner'}, {'a': 1})


def test_directory_sharing_note_needs_a_selected_dashboard_and_tolerates_junk(tmp_path):
    for sharing in (b'null', b'7', json.dumps([
            {'sourceType': 'ACTIVE_DIRECTORY', 'groupName': 'g',
             'dashboards': [{'dashboardId': 'unselected'}]}]).encode()):
        members = {'dashboards/a': archive('a', 'resource:id:0_::_', 'Group A'),
                   'dashboardsharings/a': sharing,
                   'customgroups.json': json.dumps({'customGroups': [{'name': 'Group A'}]}).encode()}
        g = graph.build_graph(members)
        picked = selection.select_all(g)
        result = bundle.build_bundle(members, list(members), g, picked, tmp_path / 'out.zip')
        assert not any('Directory sharing' in note for note in result.notes)
        with zipfile.ZipFile(tmp_path / 'out.zip') as z:
            assert json.loads(z.read('dashboardsharings/a')) == []


def test_sharing_group_without_a_source_type_is_kept_not_stripped(tmp_path):
    members = {'dashboards/a': archive('a', 'resource:id:0_::_', 'Group A'),
               'dashboardsharings/a': json.dumps([
                   {'groupName': 'untyped', 'dashboards': [{'dashboardId': 'a'}]},
                   {'sourceType': 'LDAP', 'groupName': 'directory group',
                    'dashboards': [{'dashboardId': 'a'}]}]).encode(),
               'customgroups.json': json.dumps({'customGroups': [{'name': 'Group A'}]}).encode()}
    g = graph.build_graph(members)
    assert g.directory_shared_dashboards == {('a', 'a')}
    picked = selection.select_all(g)
    bundle.build_bundle(members, list(members), g, picked, tmp_path / 'out.zip')
    with zipfile.ZipFile(tmp_path / 'out.zip') as z:
        assert [x['groupName'] for x in json.loads(z.read('dashboardsharings/a'))] == ['untyped']


def test_ownership_source_is_not_assumed_local_when_the_export_does_not_say():
    base = {'dashboards/a': archive('a', 'resource:id:0_::_', 'Group A'),
            'customgroups.json': json.dumps({'customGroups': [{'name': 'Group A'}]}).encode()}
    silent = dict(base, **{'usermappings.json': json.dumps(
        {'sources': [], 'users': [{'userId': 'a', 'userName': 'someone'}]}).encode()})
    g = graph.build_graph(silent)
    assert 'Ownership source could not be determined' in selection.render(g, selection.select_all(g))
    stated = dict(base, **{'usermappings.json': json.dumps({'sources': [{'id': 'directory'}], 'users': [
        {'userId': 'a', 'userName': 'someone', 'userSourceId': 'directory'}]}).encode()})
    g = graph.build_graph(stated)
    assert 'could not be determined' not in selection.render(g, selection.select_all(g))


def test_same_dashboard_id_under_two_owners_is_refused_and_nothing_is_written(tmp_path):
    import pytest
    members = {'dashboards/a': archive('same', 'resource:id:0_::_', 'Group A'),
               'dashboards/b': archive('same', 'resource:id:0_::_', 'Group A'),
               'customgroups.json': json.dumps({'customGroups': [{'name': 'Group A'}]}).encode()}
    g = graph.build_graph(members)
    picked = selection.select_all(g)
    out = tmp_path / 'out.zip'
    with pytest.raises(ValueError, match='same ID; choose one copy'):
        bundle.build_bundle(members, list(members), g, picked, out)
    assert not out.exists()
    one = selection.close(g, ['dashboard:same@a'])
    bundle.build_bundle(members, list(members), g, one, out)
    assert out.exists()


def _same_id_members():
    return {'dashboards/a': archive('same', 'resource:id:0_::_', 'Group A'),
            'dashboards/b': archive('same', 'resource:id:0_::_', 'Group A'),
            'usermappings.json': json.dumps({'sources': [{'id': 'directory'}], 'users': [
                {'userId': 'a', 'userName': 'someone', 'userSourceId': 'directory'},
                {'userId': 'b', 'userName': 'someone-else', 'userSourceId': 'directory'}]}).encode(),
            'customgroups.json': json.dumps({'customGroups': [{'name': 'Group A'}]}).encode()}


def test_same_dashboard_id_under_two_owners_builds_when_source_owners_are_kept(tmp_path):
    members = _same_id_members()
    g = graph.build_graph(members)
    picked = selection.select_all(g)
    assert 'The build is refused' not in selection.render(g, picked, keep_ownership=True)
    assert 'The build is refused' in selection.render(g, picked)
    out = tmp_path / 'kept.zip'
    bundle.build_bundle(members, list(members), g, picked, out, keep_ownership=True)
    with zipfile.ZipFile(out) as z:
        assert {'dashboards/a', 'dashboards/b'} <= set(z.namelist())
        assert {u['userId'] for u in json.loads(z.read('usermappings.json'))['users']} == {'a', 'b'}


def test_same_dashboard_id_under_two_owners_is_refused_for_a_chosen_single_owner(tmp_path):
    import pytest
    members = _same_id_members()
    g = graph.build_graph(members)
    out = tmp_path / 'other.zip'
    with pytest.raises(ValueError, match='choose one copy'):
        bundle.build_bundle(members, list(members), g, selection.select_all(g), out, target_user='someone')
    assert not out.exists()


def _page_with_everything(g):
    from vcfcf_migrator import insight
    from vcfcf_migrator.ui import PageState
    state = PageState()
    state.graph, state.insight = g, insight.build(g)
    state._set_picked([n.key for n in g.ordered()])
    state.screen = 'owner'
    return state


def test_the_copies_question_comes_before_review_unless_the_original_owners_are_kept():
    from vcfcf_migrator.ui import can_go
    state = _page_with_everything(graph.build_graph(_same_id_members()))
    assert state.owner_question() == 'copies' and not can_go(state, 'review')
    assert 'First: which copy of' in state.render()
    state.set_owner('source', both=True)
    assert state.owner_question() == '' and can_go(state, 'review')
    state.log.close()


def _localized(owner, props):
    body = {'entries': {'resource': []},
            'dashboards': [{'id': owner, 'name': owner, 'localizationKey': 'K', 'widgets': []}]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('dashboard/dashboard.json', json.dumps(body).encode())
        for name, text in props.items():
            z.writestr(name, text.encode('latin-1'))
    return buf.getvalue()


def _members(first, second):
    return {'dashboards/a': first, 'dashboards/b': second}


def test_renamed_localization_key_reaches_a_locale_file_only_the_later_owner_has():
    merged, _, changed, notices = bundle._merge_owner_archives(
        [('dashboards/a', _localized('a', {'res.properties': 'K=a\n'})),
         ('dashboards/b', _localized('b', {'res.properties': 'K=b\n', 'res_de.properties': 'K=c\n'}))], {})
    with zipfile.ZipFile(io.BytesIO(merged)) as z:
        assert z.read('res.properties').decode() == 'K=a\nK.merged2=b\n'
        assert z.read('res_de.properties').decode() == 'K.merged2=c\n'
    assert changed == []
    assert [n[0] for n in notices] == ['localization']


def test_non_object_entries_member_is_tolerated_when_merging():
    body = {'entries': [], 'dashboards': [{'id': 'x', 'name': 'x', 'widgets': []}]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('dashboard/dashboard.json', json.dumps(body).encode())
    merged, _, _, _ = bundle._merge_owner_archives(
        [('dashboards/a', buf.getvalue()), ('dashboards/b', _localized('b', {}))], {})
    assert merged


def test_unmergeable_escaped_localization_is_a_refusal_not_a_warning():
    members = _members(_localized('a', {'res.properties': 'K=caf\\u00e9\n'}),
                       _localized('b', {'res.properties': 'K=cafe\\u00e8\n'}))
    g = graph.build_graph(members)
    picked = selection.select_all(g)
    refusals, notes = bundle.merge_preview(g, picked)
    assert refusals and 'escaped localization' in refusals[0] and notes == []
    assert 'The build is refused' in selection.render(g, picked)
    assert bundle.merge_preview(g, picked, keep_ownership=True) == ([], [])


def test_dashboards_that_cannot_share_an_owner_are_asked_about_before_review():
    from vcfcf_migrator.ui import can_go
    state = _page_with_everything(graph.build_graph(
        _members(_localized('a', {'res.properties': 'K=caf\\u00e9\n'}),
                 _localized('b', {'res.properties': 'K=cafe\\u00e8\n'}))))
    assert state.owner_question() == 'merge' and not can_go(state, 'review')
    assert 'these dashboards cannot share one owner' in state.render()
    state.set_owner('source')
    assert state.owner_question() == '' and can_go(state, 'review')
    state.log.close()
