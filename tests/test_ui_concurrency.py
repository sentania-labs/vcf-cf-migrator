"""Two page actions must never expose or overwrite a partial selection."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from pages import page_over, visible
from vcfcf_migrator import selection
from vcfcf_migrator.desktop import Bridge
from vcfcf_migrator.graph import Graph, Node
from vcfcf_migrator.ui import dispatch


def state():
    graph = Graph()
    for i in range(78):
        node = Node('dashboard', str(i), 'Dashboard ' + str(i), str(i), 'synthetic', i)
        graph.nodes[node.key] = node
    report = Node('report', 'report', 'Report', 'report', 'synthetic', 0)
    graph.nodes[report.key] = report
    graph.edges[report.key] = ['dashboard:' + str(i) for i in range(4)]
    return page_over(graph)


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
            return page.add_shown() if action == '/add-shown' else page.clear()
        if entry == 'dispatch':
            return dispatch(page, action, {})
        return Bridge(page).act(action, {})

    def clear():
        clearing.set()
        # Emptying the bundle asks first; this is the answer to that question.
        page.ask = {'kind': 'clear'}
        return act('/clear') if entry == 'direct' else act('/ask-yes')

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(act, '/add-shown')
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
        page.add_shown()
    assert (page.picked, page.selection.keys) == previous


def test_the_footer_counts_what_was_chosen_and_what_came_with_it():
    page = state()
    assert 'Tick at least one thing to continue.' in visible(page.render())
    page.toggle('report:report', True)
    assert ('In your bundle: 5 things. 1 you chose, 4 added automatically because your choice needs '
            'them.') in visible(page.render())
    page.clear()
    assert 'Tick at least one thing to continue.' in visible(page.render())
    page.add_shown()
    assert 'In your bundle: 79 things. 79 you chose.' in visible(page.render())
    page.log.close()
