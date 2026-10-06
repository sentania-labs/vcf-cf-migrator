"""Two page actions must never expose or overwrite a partial selection."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from vcfcf_migrator import selection, workspace
from vcfcf_migrator.desktop import Bridge
from vcfcf_migrator.graph import Graph, Node
from vcfcf_migrator.ui import PageState, dispatch


def state():
    page = PageState()
    page.graph = Graph()
    for i in range(78):
        node = Node('dashboard', str(i), 'Dashboard ' + str(i), str(i), 'synthetic', i)
        page.graph.nodes[node.key] = node
    report = Node('report', 'report', 'Report', 'report', 'synthetic', 0)
    page.graph.nodes[report.key] = report
    page.graph.edges[report.key] = ['dashboard:' + str(i) for i in range(4)]
    page._reclose()
    return page


@pytest.mark.parametrize('entry', ['direct', 'dispatch', 'bridge'])
def test_clear_waits_for_pending_selection_and_wins(monkeypatch, entry):
    page = state()
    entered, release, clearing = threading.Event(), threading.Event(), threading.Event()
    close = selection.close

    def delayed(graph, keys):
        if keys:
            entered.set()
            assert release.wait(5)
        return close(graph, keys)

    monkeypatch.setattr(selection, 'close', delayed)

    def act(action):
        if entry == 'direct':
            return page.select_all() if action == '/select-all' else page.clear()
        if entry == 'dispatch':
            return dispatch(page, action, {})
        return Bridge(page).act(action, {})

    def clear():
        clearing.set()
        return act('/clear')

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(act, '/select-all')
        assert entered.wait(2)
        last = pool.submit(clear)
        assert clearing.wait(2)
        try:
            # Once selection is pending it must not publish new picks alone.
            assert page.picked == []
        finally:
            release.set()
        first.result(timeout=5)
        last.result(timeout=5)
    assert page.picked == [] and page.selection.keys == []


def test_selection_failure_preserves_previous_committed_state(monkeypatch):
    page = state()
    page.toggle('dashboard:5', True)
    previous = page.picked[:], page.selection.keys[:]

    def fail(*args):
        raise ValueError('injected closure failure')

    monkeypatch.setattr(selection, 'close', fail)
    with pytest.raises(ValueError):
        page.select_all()
    assert (page.picked, page.selection.keys) == previous


def test_counts_include_dependencies_and_explicit_zero():
    page = state()
    page.toggle('report:report', True)
    assert '1 picked + 4 required = 5 total objects' in workspace.footer(page)
    page.clear()
    assert '0 picked + 0 required = 0 total objects' in workspace.footer(page)
    page.select_all()
    assert '79 picked + 0 required = 79 total objects' in workspace.footer(page)
