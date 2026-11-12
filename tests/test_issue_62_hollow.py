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
from pages import save_to, visible
from vcfcf_migrator.ui import PageState, dispatch

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


def _linking_export(tmp_path):
    dashboards = _dashboards()
    dashboards[0]['dashboardNavigations'] = {'w1': [{'id': HOLLOW, 'widgets': []}]}
    path = tmp_path / 'linked.zip'
    path.write_bytes(export_bytes(dashboards))
    return path


def test_a_finished_dashboard_linking_to_one_carries_it_and_says_so(tmp_path):
    g = _graph(_linking_export(tmp_path))
    hollow_key = f'dashboard:{HOLLOW}@{OWNER}'
    picked = selection.select_all(g)
    assert hollow_key in picked.keys
    assert hollow_key not in picked.left_out
    assert len(picked.left_out) == 1
    text = selection.render(g, picked)
    assert 'Left out: 1 dashboard not finished loading on the source' in text
    assert 'Warning: 1 dashboard not finished loading on the source' in text
    assert 'Loading dashboard' not in text.split('Left out:')[1].split('\n')[0]


def test_the_page_does_not_call_a_pulled_in_dashboard_left_out(tmp_path):
    state = PageState(str(_linking_export(tmp_path)))
    state.add_shown()
    # The finished dashboard that links to an unfinished one brings it along,
    # so it needs a decision too and is not added in bulk. One other
    # unfinished dashboard is left out as well.
    assert len(state.picked) == 1
    assert 'Not added, because each needs a decision from you: 3 things that carry a warning' in state.note.text
    state.toggle(f'dashboard:{FINISHED}@{OWNER}', True)
    assert f'dashboard:{HOLLOW}@{OWNER}' in state.selection_keys()
    assert 'Loading dashboard, which it uses, never finished loading on the source' in state.note.text
    state.log.close()


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


def test_the_list_marks_them_and_the_panel_explains(state):
    page = visible(state.render())
    assert page.count('Never finished loading') >= 3   # the filter and two rows
    assert page.count('left out unless ticked') == 2
    assert ('Opened synthetic.zip. It holds 4 things you can move. 2 dashboards never finished loading '
            'on the source; they are marked and left out unless you tick them.') in state.note.text
    dispatch(state, '/focus', {'key': f'dashboard:{HOLLOW}@{OWNER}'})
    page = visible(state.render())
    assert ('Never finished loading on the source. VCF Operations could not finish setting this dashboard '
            'up there: it was waiting for the ExampleSqlAdapter adapter, the resource kind '
            'ExampleSqlAdapter / Database and the object Example World (Example / world).') in page
    # What it needs is unknown, which is not the same as needing nothing.
    assert 'What it needs is unknown' in page and 'Needs nothing else' not in page
    dispatch(state, '/focus', {'key': f'dashboard:{HOLLOW_BARE}@{OWNER}'})
    page = visible(state.render())
    assert 'and the export does not say why' in page
    for word in INTERNAL_WORDS:
        assert word not in page


def test_the_filter_shows_only_them(state):
    dispatch(state, '/filter', {'f': 'unf'})
    assert sorted(n.name for n in state.visible_nodes()) == ['Loading dashboard', 'Loading with no detail']


def test_adding_everything_shown_skips_them(state):
    state.add_shown()
    assert len(state.picked) == 2
    assert not [k for k in state.picked if state.graph.nodes[k].hollow]
    assert ('Added 2 things. Not added, because each needs a decision from you: '
            '2 things that carry a warning.') in state.note.text


def test_ticking_one_works_and_review_waits_for_an_answer(state, tmp_path):
    key = f'dashboard:{HOLLOW}@{OWNER}'
    state.toggle(key, True)
    assert key in state.picked
    assert 'It never finished loading on the source' in state.note.text
    assert 'You will be asked at the Review step whether to keep it.' in state.note.text
    issue, = state.open_issues()
    assert issue.title == 'Loading dashboard: never finished loading on the source'
    dispatch(state, '/go', {'to': 'review'})
    page = visible(state.render())
    assert '1 thing to decide before you can save' in page
    assert 'It was waiting for the ExampleSqlAdapter adapter' in page and 'The source tried 3 times.' in page
    assert 'Leave out Loading dashboard (recommended)' in page and 'Keep it in anyway' in page
    for word in INTERNAL_WORDS:
        assert word not in page
    # Until it is answered there is nothing to save.
    out = tmp_path / 'kept.zip'
    save_to(state, out)
    assert not out.exists() and state.screen == 'review'
    state.keep_in(issue.id)
    assert not state.open_issues()
    page = visible(state.render())
    assert ('The exception is Loading dashboard: it never finished loading, so the export does not record '
            'what it needs.') in page
    assert any(task.title == 'Repair Loading dashboard' for task in state.tasks())
    save_to(state, out)
    assert out.exists() and state.save_state == 'saved'
    report = out.with_name('kept-report.txt').read_text()
    assert 'Kept in against the usual advice' in report
    assert 'Loading dashboard: Loading dashboard never finished loading on the source and arrives empty.' in report
    assert 'Warning: 1 dashboard not finished loading on the source' in report
    assert 'Repair Loading dashboard' in out.with_name('kept-checklist.txt').read_text()


def test_review_is_quiet_without_them(state):
    state.toggle(f'dashboard:{FINISHED}@{OWNER}', True)
    dispatch(state, '/go', {'to': 'review'})
    page = visible(state.render())
    assert 'Ready to save' in page and 'never finished loading' not in page


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
    state.toggle(f'dashboard:{FINISHED}@{OWNER}', True)
    state.open_export(str(broken))
    assert state.open_error == {'kind': 'incomplete', 'name': 'e.zip', 'declared': 7, 'found': 0}
    assert state.zip_path == str(export) and state.graph is not None and len(state.picked) == 1
    page = visible(state.render())
    assert 'This export is missing its dashboards' in page
    assert 'It says it holds 7 dashboards, but none of them are inside.' in page
    assert 'synthetic.zip is still open and your 1 choice is untouched.' in page
    assert 'What to do:' in page
    first = PageState()
    first.open_export(str(broken))
    assert first.graph is None and first.open_error['kind'] == 'incomplete'
    assert 'still open' not in visible(first.render())


def test_a_failed_export_is_logged(tmp_path, capsys):
    path = _write(tmp_path, dashboards=[], manifest={'type': 'ALL', 'dashboards': 7}, owner_member=False)
    log = tmp_path / 'run.log'
    assert main(['inspect', str(path), '--log', str(log), '--log-level', 'info']) == 1
    assert 'input.incomplete_export' in log.read_text()
