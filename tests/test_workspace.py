"""Workflow checks for the inventory and review surface."""
import pytest

from vcfcf_migrator.ui import PageState, dispatch
from vcfcf_migrator import selection


@pytest.fixture
def page(config_dir, export_zip):
    return PageState(str(export_zip))


def test_select_shown_keeps_existing_picks_and_closes_dependencies(page):
    first = next(n for n in page.graph.ordered() if n.kind == 'symptom')
    page.toggle(first.key, True)
    dispatch(page, '/category', {'kind': 'dashboard'})
    dispatch(page, '/filter', {'filter': 'VM Overview'})
    shown = page.visible_nodes()
    assert shown and len(shown) < len(page.graph.nodes)
    dispatch(page, '/select-shown', {})
    assert page.selected_keys() == {first.key} | {n.key for n in shown}
    assert page.selection_keys() == set(selection.close(page.graph, page.picked).keys)


def test_empty_search_selects_nothing_extra(page):
    page.select_all()
    before = page.selected_keys()
    dispatch(page, '/filter', {'filter': 'nothing matches this'})
    dispatch(page, '/select-shown', {})
    assert page.selected_keys() == before


def test_missing_dependency_build_cannot_be_bypassed_by_post(page, tmp_path):
    broken = next(n for n in page.graph.ordered() if page.graph.missing_for(n.key))
    page.toggle(broken.key, True)
    out = tmp_path / 'blocked.zip'
    dispatch(page, '/build', {'out': str(out)})
    assert 'blocked' in page.error
    assert not out.exists()
    assert 'Build is blocked' in page.render()
    assert "class='primary' disabled>Build the bundle" in page.render()


def test_review_recovers_after_removing_affected_pick(page, tmp_path):
    broken = next(n for n in page.graph.ordered() if page.graph.missing_for(n.key))
    good = next(n for n in page.graph.ordered()
                if n.kind == 'supermetric' and not selection.close(page.graph, [n.key]).missing)
    page.toggle(broken.key, True)
    page.toggle(good.key, True)
    dispatch(page, '/remove-pick', {'key': broken.key})
    assert page.tab == 'review'
    assert page.selected_keys() == {good.key}
    assert not page.selection.missing
    out = tmp_path / 'ready.zip'
    dispatch(page, '/build', {'out': str(out)})
    assert not page.error
    assert out.is_file()
    assert 'Last build' in page.render()


def test_removing_direct_pick_retains_it_if_another_pick_needs_it(page):
    parent = next(n for n in page.graph.ordered() if page.graph.edges.get(n.key))
    child = page.graph.edges[parent.key][0]
    page.toggle(parent.key, True)
    page.toggle(child, True)
    dispatch(page, '/remove-pick', {'key': child})
    assert child not in page.selected_keys()
    assert child in page.selection_keys()
    assert 'still included' in page.message


def test_selected_category_includes_automatic_dependencies(page):
    parent = next(n for n in page.graph.ordered() if page.graph.edges.get(n.key))
    page.toggle(parent.key, True)
    dispatch(page, '/category', {'kind': 'selected'})
    assert {n.key for n in page.visible_nodes()} == page.selection_keys()


def test_category_validation_does_not_erase_selection(page):
    page.select_all()
    before = page.selected_keys()
    dispatch(page, '/category', {'kind': '<script>'})
    assert page.error
    assert page.inventory_kind == 'all'
    assert page.selected_keys() == before


def test_duplicate_names_keep_their_identity_in_review(page):
    from make_export_fixture import DASHBOARD_ID, OWNER, OWNER_2
    for owner in (OWNER, OWNER_2):
        page.toggle(f'dashboard:{DASHBOARD_ID}@{owner}', True)
    dispatch(page, '/tab', {'tab': 'review'})
    markup = page.render().split('<h3>Your picks</h3>', 1)[1]
    assert OWNER in markup and OWNER_2 in markup
    assert markup.count(DASHBOARD_ID) >= 2


def test_inspector_tabs_and_expansion_preserve_selection(page):
    node = next(n for n in page.graph.ordered() if n.kind == 'dashboard')
    page.toggle(node.key, True)
    before = page.selection_keys()
    dispatch(page, '/preview', {'key': node.key})
    dispatch(page, '/inspector-tab', {'panel': 'dependencies'})
    assert 'Depends on</h3>' in page.render()
    dispatch(page, '/inspector-tab', {'panel': 'details'})
    assert node.uuid in page.render().split("id='inspector'", 1)[1]
    dispatch(page, '/inspector-tab', {'panel': 'preview'})
    dispatch(page, '/expand-preview', {'expanded': '1'})
    assert "class='inspector expanded'" in page.render()
    dispatch(page, '/expand-preview', {'expanded': '0'})
    assert page.selection_keys() == before
