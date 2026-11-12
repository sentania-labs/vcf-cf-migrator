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
    from vcfcf_migrator import insight
    from vcfcf_migrator.ui import PageState
    state = PageState()
    state.graph, state.insight = g, insight.build(g)
    state._set_picked(['dashboard:d', 'customgroup:Group'])
    assert '1 super metric arrives switched off' in [i.title for i in state.issues()]
    state.log.close()
    out = tmp_path / 'bundle.zip'
    result = bundle.build_bundle(members, list(members), g, picked, out)
    with zipfile.ZipFile(out) as z:
        assert 'policies.xml' not in z.namelist()
    # One checklist beside the bundle: the tasks, in plain words.
    assert not out.with_name('bundle-post-import.txt').exists()
    checklist = out.with_name('bundle-checklist.txt').read_text()
    assert checklist == result.checklist
    assert 'Switch on 1 super metric' in checklist and 'Tick Metric.' in checklist
    assert 'On the source, Metric was switched on in the policy Production.' in checklist
    assert 'Group used the policy Production on the source.' in checklist
    assert 'Disabled' not in checklist
    # The technical detail stays with the report, for support.
    detail = result.policy_detail
    assert 'Adapter / Thing' in detail and 'Disabled' not in detail
    assert 'Custom group: Group' in detail and 'Source policy: Production' in detail
    assert detail.rstrip() in bundle.render(result)


def _checklist(policies):
    members = {'supermetrics.json': json.dumps({SM: {'name': 'Metric', 'formula': '',
                                                     'resourceKinds': []}}).encode()}
    if policies is not None:
        members['policies.xml'] = policies
    g = graph.build_graph(members)
    return bundle.post_import_checklist(g, selection.close(g, ['supermetric:' + SM]))


def test_checklist_never_claims_no_policy_enables_a_metric():
    parsed_but_empty = _checklist(b'<PolicyContent><Policies><Policy id="p1" name="Production"/>'
                                  b'</Policies></PolicyContent>')
    assert 'not found in policies.xml; verify on the source instance' in parsed_but_empty
    assert 'none found' not in parsed_but_empty
    unreadable = _checklist(b'<Unexpected><Thing/></Unexpected>')
    assert 'policy file could not be read as expected' in unreadable
    assert 'source policy data unavailable' in _checklist(None)
