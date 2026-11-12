"""Source-dangling references are reported with their evidence limits."""
import io
import json
import zipfile

from pages import page_over
from vcfcf_migrator import bundle, graph, selection


def _members(include_views=True, count=1):
    dashboard = {'id': 'd', 'name': 'Stock copy', 'adapterName': 'Example', 'widgets': [
        {'id': 'w1', 'name': 'First', 'config': {'viewDefinitionId': 'missing'}},
        {'id': 'w2', 'name': 'Second', 'config': {'viewDefinitionId': 'missing'}}]}
    members = {'dashboard/dashboard.json': json.dumps({'dashboards': [dashboard]}).encode(),
               'configuration.json': json.dumps({'type': 'ALL', 'dashboards': 1, 'views': count}).encode()}
    if include_views:
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as z:
            z.writestr('content.xml', b'<Views><ViewDef id="present"><Title>Present</Title></ViewDef></Views>')
        members['views.zip'] = raw.getvalue()
    return members


def test_view_in_complete_type_is_reported_per_widget_without_blocking(tmp_path):
    members = _members()
    g = graph.build_graph(members)
    picked = selection.close(g, ['dashboard:d'])
    assert not picked.missing_dependencies()
    assert {gap.via for gap in picked.not_carried()} == {
        'widget First viewDefinitionId', 'widget Second viewDefinitionId'}
    assert all(gap.classification == 'inferred_dangling' for gap in picked.not_carried())
    assert 'export may be incomplete' in selection.render(g, picked)
    out = tmp_path / 'bundle.zip'
    bundle.build_bundle(members, list(members), g, picked, out)
    state = page_over(g, ['dashboard:d'])
    assert 'Replaces a built-in dashboard' in state.render()
    # A dashboard that comes with the product is asked about; the two empty
    # widgets are said under the same entry and are not a reason to refuse.
    issue, = state.open_issues()
    assert issue.title == 'Stock copy: would overwrite the target’s copy' and not issue.hard
    assert [catch.badge for _node, catch in issue.catches] == ['2 widgets will be empty']
    state.screen = 'review'
    assert 'Importing yours overwrites the target’s copy for good.' in state.render()
    state.log.close()


def test_missing_or_inconsistent_view_type_blocks():
    for members in (_members(include_views=False), _members(count=2)):
        g = graph.build_graph(members)
        picked = selection.close(g, ['dashboard:d'])
        assert len(picked.missing_dependencies()) == 2
        assert all(gap.classification == 'blocking' for gap in picked.missing_dependencies())


def test_report_section_naming_missing_view_still_blocks():
    members = _members()
    del members['dashboard/dashboard.json']
    members['configuration.json'] = json.dumps({'type': 'ALL', 'views': 1, 'reports': 1}).encode()
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as z:
        z.writestr('content.xml', b'<Content><Reports><ReportDef id="r"><Title>Report</Title><Sections>'
                   b'<Section><ContentType>View</ContentType><ContentKey>missing</ContentKey></Section>'
                   b'</Sections></ReportDef></Reports></Content>')
    members['reports.zip'] = raw.getvalue()
    g = graph.build_graph(members)
    picked = selection.close(g, ['report:r'])
    gaps = picked.missing_dependencies()
    assert [gap.kind for gap in gaps] == ['view']
    assert gaps[0].classification == 'blocking'
    assert 'report section' in graph.missing_reason(gaps[0])
    assert 'widget' not in graph.missing_reason(gaps[0])
