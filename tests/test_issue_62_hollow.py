"""Dashboards the source never finished loading, and exports that lost some.

Every export here is built in the test from invented names and shapes. No
real instance content is read or copied.
"""
import io
import json
import zipfile

import pytest

from vcfcf_migrator import bundle, graph as graph_mod, selection
from vcfcf_migrator.cli import main
from vcfcf_migrator.export_reader import IncompleteExport, NotAnExport, read_export, read_members, render_text
from vcfcf_migrator.ui import PageState, dispatch
from vcfcf_migrator.workspace import inspector, inventory, review

OWNER = 'aaaa1111-0000-4000-8000-00000000000a'
FINISHED = 'f1111111-0000-4000-8000-000000000001'
HOLLOW = 'e2222222-0000-4000-8000-000000000002'
HOLLOW_BARE = 'e3333333-0000-4000-8000-000000000003'
NO_FLAG = 'd4444444-0000-4000-8000-000000000004'

INTERNAL_WORDS = ('importComplete', 'importAttempts', 'entryKeys', 'internalId', 'adapterKindKey')


def _dashboards():
    widgets = [{'id': 'w1', 'name': 'Widget one', 'config': {}}]
    return [
        {'id': FINISHED, 'name': 'Finished dashboard', 'widgets': widgets,
         'importComplete': True, 'entryKeys': None},
        {'id': HOLLOW, 'name': 'Loading dashboard', 'widgets': [{'id': 'w2', 'config': {}}],
         'importComplete': False, 'importAttempts': 3,
         'entryKeys': {'adapterKind': [{'internalId': 'adapterKind:id:0_::_',
                                        'adapterKindKey': 'ExampleSqlAdapter'}],
                       'resourceKind': [{'internalId': 'resourceKind:id:0_::_',
                                         'adapterKindKey': 'ExampleSqlAdapter',
                                         'resourceKindKey': 'Database'}],
                       'resource': [{'internalId': 'resource:id:0_::_', 'name': 'Example World',
                                     'adapterKindKey': 'Example', 'resourceKindKey': 'world'}]}},
        {'id': HOLLOW_BARE, 'name': 'Loading with no detail', 'widgets': [],
         'importComplete': False},
        {'id': NO_FLAG, 'name': 'Older export dashboard', 'widgets': widgets},
    ]


def _inner(dashboards):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('dashboard/dashboard.json', json.dumps(
            {'uuid': FINISHED, 'entries': {'resourceKind': [], 'resource': []}, 'dashboards': dashboards}))
    return buf.getvalue()


def export_bytes(dashboards=None, manifest=None, owner_member=True):
    dashboards = _dashboards() if dashboards is None else dashboards
    manifest = {'type': 'ALL', 'dashboards': len(dashboards)} if manifest is None else manifest
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('1757800000000000000L.v1', OWNER)
        z.writestr('configuration.json', json.dumps(manifest))
        z.writestr('dashboards/', '')
        z.writestr('dashboardsharings/', '')
        if owner_member:
            z.writestr(f'dashboards/{OWNER}', _inner(dashboards))
        z.writestr(f'dashboardsharings/{OWNER}', json.dumps([]))
    return out.getvalue()


@pytest.fixture
def export(tmp_path):
    path = tmp_path / 'synthetic.zip'
    path.write_bytes(export_bytes())
    return path


def _graph(path):
    return graph_mod.build_graph(read_members(path).data)


# -- per dashboard ----------------------------------------------------------

def test_graph_marks_only_dashboards_whose_flag_is_false(export):
    g = _graph(export)
    hollow = {n.name: n for n in g.hollow_dashboards()}
    assert set(hollow) == {'Loading dashboard', 'Loading with no detail'}
    node = hollow['Loading dashboard']
    assert node.import_attempts == 3
    assert node.waiting_on == ['the ExampleSqlAdapter adapter',
                               'the resource kind ExampleSqlAdapter / Database',
                               'the object Example World (Example / world)']
    assert hollow['Loading with no detail'].waiting_on == []
    # A flag that says true, and a document that carries no flag, are finished.
    assert not g.nodes[f'dashboard:{FINISHED}@{OWNER}'].hollow
    assert not g.nodes[f'dashboard:{NO_FLAG}@{OWNER}'].hollow


def test_string_false_flag_counts_as_unfinished(tmp_path):
    path = tmp_path / 'e.zip'
    path.write_bytes(export_bytes([{'id': HOLLOW, 'name': 'Text flag', 'widgets': [],
                                    'importComplete': 'false'}]))
    assert [n.name for n in _graph(path).hollow_dashboards()] == ['Text flag']


def test_inspect_reports_the_count_and_marks_each(export, capsys):
    assert main(['inspect', str(export)]) == 0
    out = capsys.readouterr().out
    assert '2 dashboards not finished loading on the source' in out
    marked = [line for line in out.splitlines() if 'not finished loading on the source]' in line]
    assert len(marked) == 2 and all('Loading' in line for line in marked)
    for word in INTERNAL_WORDS:
        assert word not in out
    assert read_export(export).hollow_count() == 2


def test_inspect_json_carries_the_count(export, capsys):
    assert main(['inspect', str(export), '--json']) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc['dashboards_not_finished_on_source'] == 2
    flags = {i['name']: i['not_finished_on_source'] for i in doc['items']}
    assert flags['Loading dashboard'] and not flags['Finished dashboard']


def test_inspect_counts_nothing_on_a_clean_export(tmp_path, capsys):
    path = tmp_path / 'clean.zip'
    path.write_bytes(export_bytes(_dashboards()[:1] + _dashboards()[3:]))
    assert main(['inspect', str(path)]) == 0
    assert 'not finished loading' not in capsys.readouterr().out


def test_tree_says_references_are_unknown_not_none(export, capsys):
    assert main(['tree', str(export)]) == 0
    out = capsys.readouterr().out
    assert '2 dashboards not finished loading on the source' in out
    assert 'references unknown' in out
    assert 'Waiting on the ExampleSqlAdapter adapter' in out
    assert 'The source tried to finish it 3 times.' in out
    for word in INTERNAL_WORDS:
        assert word not in out


# -- selection ----------------------------------------------------------------

def test_select_all_leaves_them_out_unless_named(export):
    g = _graph(export)
    picked = selection.select_all(g)
    assert not [k for k in picked.keys if g.nodes[k].hollow]
    assert len(picked.left_out) == 2
    text = selection.render(g, picked)
    assert 'Left out: 2 dashboards not finished loading on the source' in text
    assert 'Loading dashboard' in text
    named = selection.close(g, [f'dashboard:{HOLLOW}@{OWNER}'])
    assert f'dashboard:{HOLLOW}@{OWNER}' in named.keys
    text = selection.render(g, named)
    assert 'Warning: 1 dashboard not finished loading on the source' in text
    assert 'Waiting on the ExampleSqlAdapter adapter' in text
    assert selection.select_all(g, include_unfinished=True).left_out == []


def test_build_select_all_report_names_what_was_left_out(export, tmp_path, capsys):
    out = tmp_path / 'bundle.zip'
    assert main(['build', str(export), '--select-all', '--out', str(out)]) == 0
    report = capsys.readouterr().out
    assert 'Left out: 2 dashboards not finished loading on the source' in report
    carried = read_export(out)
    assert {i.name for i in carried.items} == {'Finished dashboard', 'Older export dashboard'}
    assert carried.hollow_count() == 0


def test_build_naming_one_carries_it_with_a_warning(export, tmp_path, capsys):
    out = tmp_path / 'bundle.zip'
    chosen = tmp_path / 'pick.txt'
    chosen.write_text(f'dashboard:{HOLLOW}@{OWNER}\n')
    assert main(['build', str(export), '--select', str(chosen), '--out', str(out)]) == 0
    report = capsys.readouterr().out
    assert 'Warning: 1 dashboard not finished loading on the source' in report
    assert 'will not work on the target' in report
    assert [i.name for i in read_export(out).items] == ['Loading dashboard']


def test_build_json_lists_them(export, tmp_path, capsys):
    out = tmp_path / 'bundle.zip'
    assert main(['build', str(export), '--select-all', '--out', str(out), '--json']) == 0
    doc = json.loads(capsys.readouterr().out)
    assert len(doc['selection']['left_out']) == 2


# -- the page --------------------------------------------------------------------

@pytest.fixture
def state(export):
    return PageState(str(export))


def test_picker_marks_and_explains(state):
    page = inventory(state)
    assert 'Not finished loading on the source' in page
    assert 'Waiting on the ExampleSqlAdapter adapter, the resource kind ExampleSqlAdapter / Database '\
           'and the object Example World (Example / world).' in page
    assert 'The export does not say what it was waiting on.' in page
    assert '2 dashboards not finished loading on the source' in page
    assert 'Select all 2 objects' in page
    for word in INTERNAL_WORDS:
        assert word not in page
    assert 'Opened synthetic.zip' in state.message and '2 dashboards not finished loading' in state.message


def test_select_all_and_select_shown_skip_them(state):
    state.select_all()
    assert len(state.picked) == 2
    assert not [k for k in state.picked if state.graph.nodes[k].hollow]
    assert 'left out 2 dashboards not finished loading on the source' in state.message
    state.clear()
    state.select_shown()
    assert len(state.picked) == 2
    assert 'Left out 2 dashboards' in state.message


def test_picking_one_by_name_works_and_review_warns(state):
    key = f'dashboard:{HOLLOW}@{OWNER}'
    state.toggle(key, True)
    assert key in state.picked
    assert 'not finished loading on the source' in state.message
    page = review(state)
    assert 'unfinished-review' in page
    assert '1 dashboard not finished loading on the source in this bundle' in page
    assert 'Waiting on the ExampleSqlAdapter adapter' in page
    assert 'Ready to build with warnings' in page
    for word in INTERNAL_WORDS:
        assert word not in page
    state.build(str(state.default_out()))
    assert 'Warning: 1 dashboard not finished loading on the source' in state.build_report


def test_review_is_quiet_without_them(state):
    state.toggle(f'dashboard:{FINISHED}@{OWNER}', True)
    page = review(state)
    assert 'unfinished-review' not in page and 'not finished loading' not in page


def test_inspector_never_shows_no_dependencies_for_one(state):
    state.set_preview(f'dashboard:{HOLLOW}@{OWNER}')
    for tab in ('preview', 'dependencies', 'details'):
        state.inspector_tab = tab
        page = inspector(state)
        assert 'unfinished-notice' in page
        assert 'Waiting on the ExampleSqlAdapter adapter' in page
        for word in INTERNAL_WORDS:
            assert word not in page
    state.inspector_tab = 'dependencies'
    page = inspector(state)
    assert 'References unknown' in page
    assert 'No dependencies recorded' not in page


# -- the export as a whole ---------------------------------------------------------

def _write(tmp_path, **kwargs):
    path = tmp_path / 'e.zip'
    path.write_bytes(export_bytes(**kwargs))
    return path


def test_declared_dashboards_with_no_files_is_a_failed_export(tmp_path, capsys):
    path = _write(tmp_path, dashboards=[], manifest={'type': 'ALL', 'dashboards': 12}, owner_member=False)
    for command in ('inspect', 'tree'):
        assert main([command, str(path)]) == 1
        err = capsys.readouterr().err
        assert 'looks incomplete' in err and '12 dashboards' in err
        assert 'empty or missing' in err and 'Take the export again' in err
    out = tmp_path / 'b.zip'
    assert main(['build', str(path), '--select-all', '--out', str(out)]) == 1
    assert 'looks incomplete' in capsys.readouterr().err and not out.exists()


def test_owner_file_with_no_dashboards_in_it_is_a_failed_export(tmp_path):
    path = _write(tmp_path, dashboards=[], manifest={'type': 'ALL', 'dashboards': 3})
    with pytest.raises(IncompleteExport):
        read_export(path)
    with pytest.raises(IncompleteExport):
        _graph(path)
    assert issubclass(IncompleteExport, NotAnExport)


def test_a_count_that_disagrees_is_a_failed_export(tmp_path, capsys):
    path = _write(tmp_path, dashboards=_dashboards()[:2], manifest={'type': 'ALL', 'dashboards': 5})
    assert main(['inspect', str(path)]) == 1
    assert 'says it holds 5 dashboards, but 2 could be read' in capsys.readouterr().err
    path = _write(tmp_path, dashboards=_dashboards(), manifest={'type': 'ALL', 'dashboards': 2})
    with pytest.raises(IncompleteExport, match='holds 2 dashboards, but 4 could be read'):
        _graph(path)


@pytest.mark.parametrize('manifest', [
    {'type': 'ALL'},                       # says nothing about dashboards
    {'type': 'ALL', 'dashboards': 0},      # an instance with none
])
def test_an_export_that_declares_none_is_not_a_failure(tmp_path, manifest):
    path = _write(tmp_path, dashboards=[], manifest=manifest, owner_member=False)
    assert read_export(path).items == []
    assert _graph(path).nodes == {}


def test_per_owner_counts_alone_are_enough_to_know(tmp_path):
    path = _write(tmp_path, dashboards=[], owner_member=False,
                  manifest={'type': 'ALL', 'dashboardsByOwner': [{'owner': OWNER, 'count': 2}]})
    with pytest.raises(IncompleteExport):
        read_export(path)


def test_the_page_refuses_it_and_keeps_the_open_export(export, tmp_path):
    state = PageState(str(export))
    broken = _write(tmp_path, dashboards=[], manifest={'type': 'ALL', 'dashboards': 7}, owner_member=False)
    state.open_export(str(broken))
    assert 'looks incomplete' in state.error and 'Take the export again' in state.error
    assert state.zip_path == str(export) and state.graph is not None
    first = PageState()
    first.open_export(str(broken))
    assert first.graph is None and 'looks incomplete' in first.error


def test_a_failed_export_is_logged(tmp_path, capsys):
    path = _write(tmp_path, dashboards=[], manifest={'type': 'ALL', 'dashboards': 7}, owner_member=False)
    log = tmp_path / 'run.log'
    assert main(['inspect', str(path), '--log', str(log), '--log-level', 'info']) == 1
    assert 'input.incomplete_export' in log.read_text()
