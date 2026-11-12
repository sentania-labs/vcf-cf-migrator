"""Widget bindings report target requirements without blocking a build."""
import io
import json
import zipfile

from pages import page_over
from vcfcf_migrator import bundle, graph, selection


def test_widget_bindings_report_objects_kinds_and_worlds(tmp_path):
    resources = [
        {'internalId': 'resource:id:0_::_', 'name': 'Selected Group',
         'adapterKindKey': 'Container', 'resourceKindKey': 'Function'},
        {'internalId': 'resource:id:1_::_', 'name': 'Cluster One',
         'adapterKindKey': 'VMWARE', 'resourceKindKey': 'Cluster'},
        {'internalId': 'resource:id:2_::_', 'name': 'Example World',
         'adapterKindKey': 'Example', 'resourceKindKey': 'exampleworld'},
        {'internalId': 'resource:id:3_::_', 'name': 'Unrelated Group',
         'adapterKindKey': 'Container', 'resourceKindKey': 'Function'},
        {'internalId': 'resource:id:4_::_', 'name': 'External Container',
         'adapterKindKey': 'Container', 'resourceKindKey': 'Function'}]
    kinds = [{'internalId': 'resourceKind:id:0_::_', 'adapterKindKey': 'OtherAdapter',
              'resourceKindKey': 'Project'}]
    adapters = [{'internalId': 'adapterKind:id:0_::_', 'adapterKindKey': 'ThirdAdapter'}]
    widgets = [
        {'id': 'group', 'name': 'Group widget', 'config': {'scope': 'resource:id:0_::_'}},
        {'id': 'object', 'name': 'Object widget', 'config': {'scope': 'resource:id:1_::_'}},
        {'id': 'world', 'name': 'World widget', 'config': {'scope': 'resource:id:2_::_'}},
        {'id': 'kind', 'name': 'Kind widget', 'config': {'kind': 'resourceKind:id:0_::_'}},
        {'id': 'adapter', 'name': 'Adapter widget', 'config': {'kind': 'adapterKind:id:0_::_'}},
        {'id': 'missing', 'name': 'External widget', 'config': {'scope': 'resource:id:4_::_'}}]
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as z:
        z.writestr('dashboard/dashboard.json', json.dumps({'entries': {'resource': resources,
                   'resourceKind': kinds, 'adapterKind': adapters},
                   'dashboards': [{'id': 'd', 'name': 'Dashboard', 'widgets': widgets}]}))
    members = {'dashboards/a': raw.getvalue(), 'customgroups.json': json.dumps({'customGroups': [
        {'name': 'Selected Group', 'resourceKind': 'Function'},
        {'name': 'Unrelated Group', 'resourceKind': 'Function'}]}).encode()}
    g = graph.build_graph(members)
    picked = selection.close(g, ['dashboard:d@a'])
    assert not picked.missing_dependencies()
    assert 'customgroup:Selected Group' in picked.keys
    assert 'customgroup:Unrelated Group' not in picked.keys
    text = selection.render(g, picked)
    assert 'Widget Object widget: VMWARE / Cluster / Cluster One will not resolve' in text
    assert 'Widget Kind widget: requires OtherAdapter / Project' in text
    assert 'Widget Adapter widget: requires ThirdAdapter' in text
    assert 'looks like a world object (inferred)' in text and 'at least one collecting instance' in text
    assert 'External Container' in text and 'Unrelated Group' not in text
    assert 'Target presence has not been checked' in text
    out = tmp_path / 'bundle.zip'
    result = bundle.build_bundle(members, list(members), g, picked, out)
    assert 'Widget Kind widget' in bundle.render(result)
    # On screen the same facts are one task for the target, in plain words.
    state = page_over(g, ['dashboard:d@a'])
    look = next(task for task in state.tasks() if task.id == 'look')
    assert 'vCenter (the system that manages your virtual machines)' in look.why
    assert 'the system that the adapter "OtherAdapter" reads from' in look.why
    assert any('the widget "Object widget" shows one particular object, "Cluster One"' in step
               for step in look.steps)
    assert not any('World widget' in step or 'Group widget' in step for step in look.steps)
    # A group the dashboard shows that the export does not hold is good to know, not a refusal.
    assert [issue.title for issue in state.issues() if issue.group == 'know'][0] == (
        'Dashboard: needs a group on the target')
    state.log.close()
