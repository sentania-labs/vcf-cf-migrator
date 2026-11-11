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
                assert any('b' in note and 'tokens were rewritten' in note for note in result.notes)
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
