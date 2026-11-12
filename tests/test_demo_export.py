"""The invented exports the walk tool drives hold every situation the screens
have to explain. If one of them stops doing so, the walk stops covering it."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import demo_export  # noqa: E402
from vcfcf_migrator.ui import PageState  # noqa: E402


@pytest.fixture
def files(tmp_path):
    return demo_export.write_all(tmp_path)


def test_the_rich_export_holds_one_of_each_situation(files, config_dir):
    state = PageState(str(files['ops-prod-content-export.zip']))
    graph, insight = state.graph, state.insight

    def key(name, owner=None):
        return next(n.key for n in graph.nodes.values() if n.name == name
                    and (owner is None or graph.owner_names.get(n.owner) == owner))

    assert len(graph.nodes) == 30 and graph.counts()['dashboard'] == 9
    # Two copies of one dashboard, under two owners.
    assert insight.twins[key('Capacity Overview', 'admin')] == [key('Capacity Overview', 'dana.reyes')]
    # Never finished loading: one says what it waited for, one does not.
    waiting = {n.name: n.waiting_on for n in graph.hollow_dashboards()}
    assert waiting == {'Kubernetes Clusters': [], 'SQL Server Estate': ['the Microsoft SQL Server adapter']}
    # Comes with VCF Operations, and is brought along by a report.
    assert [n.name for n in graph.nodes.values() if n.stock_or_derived] == ['vSAN Operations Overview']
    assert key('Monthly Capacity Report') in insight.stock
    # Needs something the export does not hold, two ways.
    assert {key('Datastore Health'), key('Page the capacity team')} <= insight.blocked
    assert key('Capacity Overview', 'admin') not in insight.blocked
    # A widget whose view was deleted: said, and not a reason to refuse.
    state.toggle(key('Datastore Health'), True)
    issue, = state.open_issues()
    assert [catch.badge for _node, catch in issue.catches] == ['One widget will be empty']
    state.toggle(key('Datastore Health'), False)
    # Shared with a company-directory group, and a group with a policy of its own.
    state.toggle(key('Capacity Overview', 'admin'), True)
    titles = [i.title for i in state.issues()]
    assert 'VM Rightsizing: sharing with Platform Engineering is not carried' in titles
    assert any('Production Clusters used the policy Production on the source.' in step
               for task in state.tasks() for step in task.steps)
    assert 'vCenter (the system that manages your virtual machines)' in state.tasks()[-1].why
    state.log.close()


@pytest.mark.parametrize('name, kind', [
    ('export-missing-its-dashboards.zip', 'incomplete'),
    ('empty-export.zip', 'empty'),
    ('holiday-photos.zip', 'not_export'),
    ('damaged-download.zip', 'not_zip'),
])
def test_each_bad_file_goes_wrong_in_its_own_way(files, config_dir, name, kind):
    state = PageState(str(files[name]))
    assert state.graph is None and state.open_error['kind'] == kind
    state.log.close()
