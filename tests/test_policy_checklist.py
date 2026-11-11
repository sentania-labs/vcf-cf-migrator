"""Policy enablement is reported without carrying policy content."""
import json
import zipfile

from vcfcf_migrator import bundle, graph, selection


SM = '11111111-1111-4111-8111-111111111111'


def test_policy_enablement_checklist_and_dashboard_notice(tmp_path):
    members = {'dashboard/dashboard.json': json.dumps({'dashboards': [
        {'id': 'd', 'name': 'Dashboard', 'widgets': [{'id': 'w', 'config': {
            'metricKey': 'Super Metric|sm_' + SM}}]}]}).encode(),
        'supermetrics.json': json.dumps({SM: {'name': 'Metric', 'formula': '',
            'resourceKinds': [{'adapterKindKey': 'Adapter', 'resourceKindKey': 'Thing'}]}}).encode(),
        'customgroups.json': json.dumps({'customGroups': [
            {'name': 'Group', 'policy': 'p1', 'resourceKind': 'Type'}]}).encode(),
        'policies.xml': (b'<PolicyContent><Policies><Policy id="p1" name="Production">'
                         b'<SuperMetric enabled="true" id="' + SM.encode() + b'"/>'
                         b'</Policy><Policy id="p2" name="Disabled"><SuperMetric enabled="false" id="'
                         + SM.encode() + b'"/></Policy></Policies></PolicyContent>')}
    g = graph.build_graph(members)
    picked = selection.close(g, ['dashboard:d', 'customgroup:Group'])
    assert g.metric_policies[SM] == ['p1']
    assert any(m.kind == 'policy' and m.ident == 'p1' for m in g.missing_for('supermetric:' + SM))
    # Ownerless dashboards still need a policy notice.
    from vcfcf_migrator.ui import PageState
    from vcfcf_migrator.workspace import review
    state = PageState()
    state.graph, state.selection, state.picked = g, picked, ['dashboard:d']
    state.name_counts = {}
    assert 'Uses 1 super metric that must be enabled' in review(state)
    out = tmp_path / 'bundle.zip'
    result = bundle.build_bundle(members, list(members), g, picked, out)
    with zipfile.ZipFile(out) as z:
        assert 'policies.xml' not in z.namelist()
    checklist = out.with_name('bundle-post-import.txt').read_text()
    assert 'Metric' in checklist and 'Adapter / Thing' in checklist
    assert 'Production' in checklist and 'Disabled' not in checklist
    assert 'Custom group: Group' in checklist and 'Source policy: Production' in checklist
    assert checklist == result.checklist
    assert checklist.rstrip() in bundle.render(result)
