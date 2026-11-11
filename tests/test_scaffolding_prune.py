"""Selection controls container scaffolding and localization."""
import io
import json
import zipfile

from vcfcf_migrator import bundle, graph, selection


def test_selected_dashboard_prunes_only_known_scaffolding(tmp_path):
    tokens = ['resource:id:0_::_', 'resource:id:1_::_']
    kinds = ['resourceKind:id:0_::_', 'resourceKind:id:1_::_']
    dashboards = [{'id': f'dash-{i}', 'name': f'Dashboard {i}', 'widgets': [{'id': f'w-{i}',
                   'config': {'resourceId': tokens[i], 'kind': kinds[i]}}]} for i in range(2)]
    entries = {'resource': [{'internalId': tokens[i], 'name': f'Group {i}',
                             'adapterKindKey': 'Container', 'resourceKindKey': 'Function'} for i in range(2)],
               'resourceKind': [{'internalId': kinds[i], 'resourceKindKey': f'Kind {i}',
                                 'adapterKindKey': 'Example'} for i in range(2)]}
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, 'w') as z:
        z.writestr('dashboard/dashboard.json', json.dumps({'entries': entries, 'dashboards': dashboards}))
        z.writestr('dashboard/resources/resources.properties',
                   'dash-0.title=Selected\ndash-1.title=Other\nshared=Unknown\n')
    members = {'dashboards/a': nested.getvalue(),
               'customgroups.json': json.dumps({'customGroups': [
                   {'name': 'Group 0', 'resourceKind': 'Type0'},
                   {'name': 'Group 1', 'resourceKind': 'Type1'}],
                   'customGroupTypes': [{'resourceKind': 'Type0'}, {'resourceKind': 'Type1'}]}).encode()}
    g = graph.build_graph(members)
    picked = selection.close(g, ['dashboard:dash-0@a'])
    out = tmp_path / 'bundle.zip'
    result = bundle.build_bundle(members, list(members), g, picked, out)
    with zipfile.ZipFile(out) as z:
        groups = json.loads(z.read('customgroups.json'))
        assert groups['customGroupTypes'] == [{'resourceKind': 'Type0'}]
        with zipfile.ZipFile(io.BytesIO(z.read('dashboards/a'))) as inner:
            doc = json.loads(inner.read('dashboard/dashboard.json'))
            assert [e['internalId'] for e in doc['entries']['resource']] == [tokens[0]]
            assert [e['internalId'] for e in doc['entries']['resourceKind']] == [kinds[0]]
            assert inner.read('dashboard/resources/resources.properties') == b'dash-0.title=Selected\nshared=Unknown\n'
    assert any('pruned 1 unused custom group types' in n for n in result.notes)
    assert any('kept unattributed localization keys: shared' in n for n in result.notes)


def test_view_localization_uses_explicit_keys_and_retains_unknown():
    from vcfcf_migrator.containers import XmlElementContainer
    data = (b'<Views><ViewDef id="view-a"><Title localizationKey="title.a">A</Title></ViewDef>'
            b'<ViewDef id="view-b"><Title localizationKey="title.b">B</Title></ViewDef></Views>')
    c = XmlElementContainer('views.zip', data, 'ViewDef', 'view', inner='content.xml',
                            name_path='Title', siblings={'resources/content.properties':
                                b'title.a=Selected\ntitle.b=Other\nshared=Keep\n'})
    with zipfile.ZipFile(io.BytesIO(c.rebuild([0]))) as z:
        assert z.read('resources/content.properties') == b'title.a=Selected\nshared=Keep\n'
    assert any('kept unattributed localization keys: shared' in note for note in c.notes)


def test_unknown_group_type_is_not_guessed():
    from vcfcf_migrator.containers import CustomGroupsContainer
    c = CustomGroupsContainer('customgroups.json', json.dumps({'customGroups': [{'name': 'Group'}],
                                'customGroupTypes': [{'resourceKind': 'Custom'}]}).encode())
    assert json.loads(c.rebuild([0]))['customGroupTypes'] == [{'resourceKind': 'Custom'}]
    assert any('no type' in note for note in c.notes)


def test_localization_key_matched_only_by_another_entry_name_is_kept():
    from vcfcf_migrator.containers import XmlElementContainer
    data = (b'<Views><ViewDef id="view-a"><Title>A</Title></ViewDef>'
            b'<ViewDef id="view-b"><Title localizationKey="title.b">Memory</Title></ViewDef></Views>')
    c = XmlElementContainer('views.zip', data, 'ViewDef', 'view', inner='content.xml',
                            name_path='Title', siblings={'resources/content.properties':
                                b'title.b=Other\nMemory.title=Keep\n'})
    with zipfile.ZipFile(io.BytesIO(c.rebuild([0]))) as z:
        assert z.read('resources/content.properties') == b'Memory.title=Keep\n'
    assert any('kept unattributed localization keys: Memory.title' in note for note in c.notes)


def test_properties_split_only_on_real_line_terminators():
    from vcfcf_migrator.containers import XmlElementContainer
    data = (b'<Views><ViewDef id="view-a"><Title localizationKey="title.a">A</Title></ViewDef>'
            b'<ViewDef id="view-b"><Title localizationKey="title.b">B</Title></ViewDef></Views>')
    props = 'title.b=Ząb\ntitle.a=Keep\n'.encode('utf-8')
    c = XmlElementContainer('views.zip', data, 'ViewDef', 'view', inner='content.xml',
                            name_path='Title', siblings={'resources/content.properties': props})
    with zipfile.ZipFile(io.BytesIO(c.rebuild([0]))) as z:
        assert z.read('resources/content.properties') == b'title.a=Keep\n'
    assert not any('kept unattributed' in note for note in c.notes)
