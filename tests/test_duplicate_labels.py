"""Same-named objects and unnamed widgets stay distinguishable without raw ids."""
import io
import json
import zipfile
from collections import Counter

from vcfcf_migrator import graph
from vcfcf_migrator.graph import dashboard_binding_lines
from vcfcf_migrator.ui import PageState
from vcfcf_migrator.workspace import display_name, identity


def _state(members):
    g = graph.build_graph(members)
    state = PageState()
    state.graph = g
    state.name_counts = Counter((n.kind, n.name) for n in g.nodes.values())
    return state, g


def test_same_named_views_get_distinct_labels_and_unique_names_stay_plain():
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as z:
        z.writestr('content.xml', b'<Views>'
                   b'<ViewDef id="aaaa1111-0000-0000-0000-00000000a41f"><Title>CPU Overview</Title></ViewDef>'
                   b'<ViewDef id="bbbb2222-0000-0000-0000-00000000c7e2"><Title>CPU Overview</Title></ViewDef>'
                   b'<ViewDef id="cccc3333-0000-0000-0000-000000000001"><Title>Memory</Title></ViewDef>'
                   b'</Views>')
    state, g = _state({'views.zip': raw.getvalue()})
    views = {n.key: n for n in g.nodes.values() if n.kind == 'view'}
    assert len(views) == 3
    cpu = [display_name(state, n) for n in views.values() if n.name == 'CPU Overview']
    assert len(set(cpu)) == 2
    assert 'CPU Overview (view · id …a41f)' in cpu and 'CPU Overview (view · id …c7e2)' in cpu
    memory = [n for n in views.values() if n.name == 'Memory'][0]
    assert display_name(state, memory) == 'Memory'
    assert identity(state, memory) == 'view'
    rows = [identity(state, n) for n in views.values() if n.name == 'CPU Overview']
    assert len(set(rows)) == 2


def test_unnamed_widgets_are_told_apart_by_type_and_position():
    resources = [{'internalId': 'resource:id:0_::_', 'name': 'Cluster One',
                  'adapterKindKey': 'VMWARE', 'resourceKindKey': 'Cluster'},
                 {'internalId': 'resource:id:1_::_', 'name': 'Cluster Two',
                  'adapterKindKey': 'VMWARE', 'resourceKindKey': 'Cluster'}]
    widgets = [{'id': 'a', 'type': 'Scoreboard', 'config': {'scope': 'resource:id:0_::_'}},
               {'id': 'b', 'type': 'TextDisplay', 'config': {}},
               {'id': 'c', 'type': 'Scoreboard', 'config': {'scope': 'resource:id:1_::_'}}]
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as z:
        z.writestr('dashboard/dashboard.json', json.dumps({
            'entries': {'resource': resources},
            'dashboards': [{'id': 'd', 'name': 'Dash', 'widgets': widgets}]}))
    g = graph.build_graph({'dashboards/a': raw.getvalue()})
    lines = dashboard_binding_lines(g, 'dashboard:d@a')
    assert any(l.startswith('Widget Scoreboard, 1st widget:') and 'Cluster One' in l for l in lines)
    assert any(l.startswith('Widget Scoreboard, 3rd widget:') and 'Cluster Two' in l for l in lines)
    assert not any('Unnamed widget' in l for l in lines)
