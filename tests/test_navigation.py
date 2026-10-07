"""Real navigation structure, with invented dashboard and widget identifiers."""

import copy
import io
import json
import zipfile

import pytest

from vcfcf_migrator import bundle, graph, selection, preview
from vcfcf_migrator.cli import main
from vcfcf_migrator.export_reader import read_export, read_members
from vcfcf_migrator.ui import PageState


def dashboard(ident, destination=None, receivers=None):
    doc = {'id': ident, 'name': 'Dashboard ' + ident,
           'widgets': [{'id': ident + '-widget', 'type': 'TextDisplay',
                        'config': {'text': 'Synthetic navigation example'}}]}
    if destination:
        doc['dashboardNavigations'] = {ident + '-widget': [{
            'id': destination,
            'widgets': [{'id': w, 'interactionType': 'resourceId'} for w in
                        (receivers if receivers is not None else [destination + '-widget'])]}]}
    return doc


def export_file(tmp_path, owners):
    path = tmp_path / 'navigation.zip'
    with zipfile.ZipFile(path, 'w') as outer:
        outer.writestr('123L.v1', 'owner-a')
        outer.writestr('configuration.json', json.dumps({'dashboards': sum(map(len, owners.values()))}))
        for owner, docs in owners.items():
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as inner:
                inner.writestr('dashboard/dashboard.json', json.dumps({'dashboards': docs}))
            outer.writestr('dashboards/' + owner, data.getvalue())
    return path


def load(path):
    members = read_members(path)
    return members, graph.build_graph(members.data)


def pick(g, ident):
    return selection.close(g, [next(n.key for n in g.nodes.values() if n.uuid == ident)])


def test_cross_owner_cycle_closes_and_preserves_navigation_on_readback(tmp_path):
    a, b = dashboard('a', 'b'), dashboard('b', 'a')
    path = export_file(tmp_path, {'owner-a': [a], 'owner-b': [b, dashboard('unrelated')]})
    members, g = load(path)
    chosen = pick(g, 'a')
    assert {g.nodes[k].uuid for k in chosen.keys} == {'a', 'b'}
    assert not chosen.missing_dependencies()
    assert read_export(path).navigation_gaps == 0
    out = tmp_path / 'bundle.zip'
    bundle.build_bundle(members.data, members.order, g, chosen, out, marker=members.marker)
    _, reread = load(out)
    assert {n.uuid for n in reread.nodes.values()} == {'a', 'b'}
    assert not reread.missing
    for node in reread.nodes.values():
        assert json.loads(preview.raw_document(reread, node)) == {'a': a, 'b': b}[node.uuid]


def test_uuid_cannot_resolve_to_a_dashboard_with_that_name(tmp_path):
    other = dashboard('other'); other['name'] = 'absent'
    path = export_file(tmp_path, {'owner-a': [dashboard('a', 'absent'), other]})
    _, g = load(path)
    chosen = pick(g, 'a')
    assert len(chosen.keys) == 1
    assert [(m.kind, m.ident) for m in chosen.missing_dependencies()] == [('dashboard', 'absent')]
    assert read_export(path).navigation_gaps == 1


def test_missing_dashboard_blocks_cli_and_ui_without_touching_output(tmp_path, config_dir, capsys):
    path = export_file(tmp_path, {'owner-a': [dashboard('a', 'missing'), dashboard('safe')]})
    out = tmp_path / 'bundle.zip'; out.write_bytes(b'keep this existing file')
    picks = tmp_path / 'picks.txt'; picks.write_text('a\n')
    assert main(['build', str(path), '--select', str(picks), '--out', str(out)]) != 0
    output = capsys.readouterr()
    assert 'missing' in output.out + output.err
    assert out.read_bytes() == b'keep this existing file'
    state = PageState(str(path))
    try:
        key = next(n.key for n in state.graph.nodes.values() if n.uuid == 'a')
        state.toggle(key, on=True)
        assert state.selection.missing_dependencies()
        state.tab = 'review'
        assert 'missing' in state.render().lower()
        state.build(str(out))
        assert state.error
        with pytest.raises(selection.MissingDependency):
            bundle.build_bundle(state.members.data, state.members.order,
                                state.graph, state.selection, out)
        assert out.read_bytes() == b'keep this existing file'
        assert not pick(state.graph, 'safe').missing_dependencies()
    finally:
        state.log.close()


def test_preview_does_not_label_present_dashboard_as_missing(tmp_path):
    path = export_file(tmp_path, {'owner-a': [dashboard('a', 'b')],
                                  'owner-b': [dashboard('b')]})
    _, g = load(path)
    node = next(n for n in g.nodes.values() if n.uuid == 'a')
    rendered = preview.build(g, node)
    assert not any('navigation target' in note for note in rendered.notes)


def test_navigation_gap_count_is_per_entry_across_owner_containers(tmp_path):
    a = dashboard('a', 'absent', [])
    a['dashboardNavigations']['a-widget'].append({'id': 'b', 'widgets': []})
    path = export_file(tmp_path, {'owner-a': [a], 'owner-b': [dashboard('b')]})
    assert read_export(path).navigation_gaps == 1


def test_later_duplicate_dashboard_cannot_hide_a_missing_dependency(tmp_path):
    path = export_file(tmp_path, {'owner-a': [dashboard('a'), dashboard('a', 'absent')]})
    members, g = load(path)
    chosen = pick(g, 'a')
    assert any(m.kind == 'dashboard' and m.ident == 'absent' for m in chosen.missing_dependencies())
    with pytest.raises(selection.MissingDependency):
        bundle.build_bundle(members.data, members.order, g, chosen, tmp_path / 'refused.zip')
    assert not (tmp_path / 'refused.zip').exists()


def test_later_duplicate_dashboard_adds_its_resolvable_dependency(tmp_path):
    path = export_file(tmp_path, {'owner-a': [dashboard('a'), dashboard('a', 'b'), dashboard('b')]})
    members, g = load(path)
    chosen = pick(g, 'a')
    assert {g.nodes[k].uuid for k in chosen.keys} == {'a', 'b'}
    assert not chosen.missing_dependencies()
    out = tmp_path / 'carried.zip'
    bundle.build_bundle(members.data, members.order, g, chosen, out)
    with zipfile.ZipFile(out) as outer:
        with zipfile.ZipFile(io.BytesIO(outer.read('dashboards/owner-a'))) as inner:
            docs = json.loads(inner.read('dashboard/dashboard.json'))['dashboards']
    assert [d['id'] for d in docs] == ['a', 'a', 'b']
    assert docs[1]['dashboardNavigations']['a-widget'][0]['id'] == 'b'


def test_missing_widget_in_duplicate_destination_blocks(tmp_path):
    bad = dashboard('b'); bad['widgets'] = []
    path = export_file(tmp_path, {'owner-a': [dashboard('a', 'b'), dashboard('b'), bad]})
    _, g = load(path)
    assert any(m.kind == 'dashboard widget' for m in pick(g, 'a').missing_dependencies())


def test_linked_duplicate_destination_keeps_its_other_dependencies(tmp_path):
    other = dashboard('b')
    other['widgets'][0]['config']['viewDefinitionId'] = 'missing-view'
    path = export_file(tmp_path, {'owner-a': [dashboard('a', 'b'), dashboard('b'), other]})
    _, g = load(path)
    assert any(m.kind == 'view' and m.ident == 'missing-view'
               for m in pick(g, 'a').missing_dependencies())


@pytest.mark.parametrize('breakage', ['source', 'destination', 'second-owner'])
def test_missing_widget_blocks_without_claiming_missing_dashboard(tmp_path, breakage):
    a, b = dashboard('a', 'b'), dashboard('b')
    owners = {'owner-a': [a], 'owner-b': [b]}
    if breakage == 'source':
        a['widgets'] = []
    elif breakage == 'destination':
        b['widgets'] = []
    else:
        bad_copy = copy.deepcopy(b); bad_copy['widgets'] = []
        owners['owner-c'] = [bad_copy]
    path = export_file(tmp_path, owners)
    _, g = load(path)
    gaps = pick(g, 'a').missing_dependencies()
    assert len(gaps) == 1 and gaps[0].kind == 'dashboard widget'
    assert read_export(path).navigation_gaps == 0


def test_nested_widget_self_link_and_empty_receiver_list(tmp_path):
    a = dashboard('a', 'a')
    a['widgets'] = [{'id': 'tab', 'config': {'widgets': a['widgets']}}]
    b = dashboard('b', 'a', [])
    path = export_file(tmp_path, {'owner-a': [a, b]})
    _, g = load(path)
    assert not pick(g, 'b').missing_dependencies()
    assert len(pick(g, 'b').keys) == 2
    assert read_export(path).navigation_gaps == 0


@pytest.mark.parametrize('nav', [[], {'a-widget': {}}, {'a-widget': [{}]},
                               {'a-widget': [{'id': 'b', 'widgets': [{}]}]}])
def test_malformed_navigation_cannot_silently_pass_build(tmp_path, nav):
    a = dashboard('a'); a['dashboardNavigations'] = nav
    path = export_file(tmp_path, {'owner-a': [a, dashboard('b')]})
    _, g = load(path)
    assert any(m.kind == 'dashboard navigation' for m in pick(g, 'a').missing_dependencies())
