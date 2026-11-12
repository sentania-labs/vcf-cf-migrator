"""The six steps, driven through the same actions the window sends.

Open an export, choose content, dashboard owner, review, save, import on the
target. Each test does part of a real job the way a person would and reads
what the screen then says. Fixture only.
"""
from __future__ import annotations

import re
import zipfile

import pytest

from make_export_fixture import (
    DASHBOARD_ID,
    EMPTY_DASHBOARD_ID,
    EMPTY_SM_ID,
    GROUP_NAME,
    GROUP_NAME_2,
    OWNER,
    OWNER_2,
    RULE_ID_2,
    SM_IDS,
    TEMPLATE_ID_2,
    VIEW_IDS,
    build_export_zip,
)
from pages import page_over, save_to, visible
from vcfcf_migrator import preview, uipage
from vcfcf_migrator.cli import main
from vcfcf_migrator.desktop import Bridge
from vcfcf_migrator.graph import Graph, Node
from vcfcf_migrator.ui import ACTIONS, LIST_LIMIT, POST_PATHS, STEP_NAMES, STEPS, PageState, can_go, dispatch

SM_EMPTY = f'supermetric:{EMPTY_SM_ID}'
HOST_RULE = f'notificationrule:{RULE_ID_2}'
HOST_TEMPLATE = f'notificationtemplate:{TEMPLATE_ID_2}'
MAIL = 'outboundsetting:StandardEmailPlugin/[Fixture] Mail relay'
GROUP = f'customgroup:{GROUP_NAME}'
WEB_TIER = f'customgroup:{GROUP_NAME_2}'
DASH = f'dashboard:{DASHBOARD_ID}@{OWNER}'
DASH_CLEAN = f'dashboard:{EMPTY_DASHBOARD_ID}@{OWNER_2}'


@pytest.fixture
def state(config_dir, export_zip):
    page = PageState(str(export_zip))
    yield page
    page.log.close()


def act(state, path, **form):
    dispatch(state, path, {k: str(v) for k, v in form.items()})
    return state.render()


def main_of(page: str) -> str:
    return page.split('<main>')[1].split('</main>')[0]


def foot_of(page: str) -> str:
    return page.split('</main>')[1].split('<script>')[0]


# -- the frame ----------------------------------------------------------------

def test_first_run_asks_one_question_and_shows_the_six_steps(config_dir):
    state = PageState()
    page = state.render()
    text = visible(page)
    assert 'What do you want to move?' in text
    assert 'Choose the export file…' in text and 'Sign in and download…' in text
    assert 'No export yet? Make one like this:' in text
    # The steps, in order, and the first one is where you are.
    positions = [text.index(STEP_NAMES[step]) for step in STEPS]
    assert positions == sorted(positions)
    assert '<span class="step now" aria-current="step"><span class="n">1</span>Open an export</span>' in page
    # Nothing to go to yet, so no step is a button.
    assert 'class="step" data-act' not in page
    assert 'Working from' not in text
    assert 'Everything happens on this computer. The export file is only read, never changed.' in text
    # One filled button: the way forward.
    assert (main_of(page) + foot_of(page)).count('lat-btn--primary') == 1
    state.log.close()


def test_the_header_names_the_export_and_has_no_version_or_id(state):
    page = state.render()
    header = page.split('<header')[1].split('</header>')[0]
    assert 'VCF Content Migrator' in header and 'Working from <b>fixture-export.zip</b>' in header
    assert 'Help and settings' in header
    assert not re.search(r'\d+\.\d+\.\d+', header)
    assert '<title>VCF Content Migrator</title>' in page


def test_one_screen_at_a_time_each_with_one_next_step(state):
    act(state, '/select', key=SM_EMPTY, on='1')
    headings = {'open': 'Open a different export', 'choose': 'Find by name', 'review': 'Ready to save',
                'save': 'Save the bundle', 'help': 'How this works'}
    for screen, heading in headings.items():
        state.screen = screen
        page = state.render()
        text = visible(main_of(page) + foot_of(page))
        assert heading in page, screen
        for other, words in headings.items():
            if other != screen:
                assert words not in text or (screen == 'save' and other == 'save'), (screen, other)
        # Never more than one filled button in the footer, and it is the last thing in it.
        assert foot_of(page).count('lat-btn--primary') <= 1, screen


def test_steps_already_done_can_be_gone_back_to_and_steps_ahead_cannot_be_skipped(state):
    page = state.render()
    assert 'id="step-open"' in page and 'id="step-owner"' not in page and 'id="step-save"' not in page
    # Nothing chosen: the next step is off, and posting past it does nothing.
    assert re.search(r'id="next"[^>]*disabled', page)
    for step in ('owner', 'review', 'save', 'target'):
        act(state, '/go', to=step)
        assert state.screen == 'choose', step
    act(state, '/select', key=SM_EMPTY, on='1')
    assert not can_go(state, 'owner')              # no dashboards: that step is not needed
    assert 'Next: review →' in visible(state.render())
    act(state, '/go', to='save')
    assert state.screen == 'save' and state.max_step == STEPS.index('save')
    act(state, '/go', to='target')
    assert state.screen == 'save'                  # nothing saved yet
    page = state.render()
    assert 'id="step-choose"' in page and 'id="step-review"' in page and 'id="step-target"' not in page
    # Changing the bundle means the later steps are walked again.
    act(state, '/go', to='choose')
    act(state, '/select', key=GROUP, on='1')
    assert state.max_step == STEPS.index('choose')
    assert 'id="step-review"' not in state.render()


def test_the_page_carries_no_external_resource_and_one_script(state):
    act(state, '/select', key=HOST_RULE, on='1')
    for screen in ('open', 'connect', 'choose', 'review', 'save', 'help'):
        state.screen = screen
        page = state.render()
        assert not re.search(r'''(?:src|href|action)=["']?(?:https?:)?//''', page), screen
        assert '<link' not in page and '@import' not in page and 'url(' not in page, screen
        assert page.count('<script>') == 1 and uipage.CLIENT_JS in page
        # Nothing is folded away, and nothing is a wall of fixed-width text.
        assert '<details' not in page and '<pre' not in page, screen
        assert '<form' not in page                   # buttons name actions; nothing posts anywhere


def test_no_text_on_any_screen_is_set_smaller_than_twelve_pixels():
    sizes = [float(size) for size in re.findall(r'font-size:\s*([\d.]+)px', uipage.STYLE + uipage.PREVIEW_OVERRIDES)]
    assert sizes and min(sizes) >= 12


def test_actions_are_registered_once_and_nothing_else_is_reachable(state):
    assert set(POST_PATHS) == set(ACTIONS)
    for retired in ('/tab', '/build', '/select-all', '/select-shown', '/remove-pick', '/disclose',
                    '/inspector-tab', '/expand-preview', '/category', '/source-mode', '/diagnostics'):
        assert retired not in ACTIONS


# -- choose content -------------------------------------------------------------

def test_every_object_is_listed_once_with_a_tick_box_and_a_plain_kind(state):
    page = state.render()
    keys = re.findall(r'<div class="it[^"]*" data-act="/focus" data-key="([^"]+)"', page)
    assert sorted(keys) == sorted(k.replace('&', '&amp;') for k in state.graph.nodes) or len(keys) == len(state.graph.nodes)
    assert len(keys) == len(set(keys)) == len(state.graph.nodes)
    assert page.count('type="checkbox"') == len(state.graph.nodes)
    text = visible(page)
    assert f'All {len(state.graph.nodes)}' in text and f'Everything {len(state.graph.nodes)}' in text
    for plain in ('Dashboards 4', 'Views 4', 'Super metrics 4', 'Groups 3', 'Notification rules 3',
                  'Outbound settings 1'):
        assert plain in text
    for internal in ('supermetric', 'customgroup', 'notificationrule', 'outboundsetting'):
        assert internal not in text
    # Each kind explains itself on hover.
    assert 'title="A calculation built from other measurements, such as an average across a cluster."' in page


def test_no_identifier_is_shown_anywhere_on_the_choose_screen(state):
    act(state, '/focus', key=DASH)
    text = visible(state.render())
    for node in state.graph.nodes.values():
        if node.uuid:
            assert node.uuid not in text, node.name
    assert OWNER not in text and OWNER_2 not in text
    assert 'Dashboard, owned by admin on the source.' in text


def test_ticking_adds_what_it_needs_and_says_so(state):
    page = visible(act(state, '/select', key=HOST_RULE, on='1'))
    assert state.picked == [HOST_RULE]
    assert {HOST_TEMPLATE, MAIL, GROUP} <= state.selection_keys()
    assert 'Added [Fixture] Host rule. 3 things it needs were added too.' in page
    assert ('In your bundle: 4 things. 1 you chose, 3 added automatically because your choice needs '
            'them.') in page
    assert page.count('Added automatically') == 3
    assert 'needed by [Fixture] Host rule' in page
    # What was added for you cannot be unticked on its own, and says why.
    html = state.render()
    box = html.split(f'id="tick-{uipage.anchor(MAIL)}"')[1].split('>')[0]
    assert 'checked' in box and 'disabled' in box and 'was added automatically' in box
    act(state, '/select', key=MAIL, on='0')
    assert MAIL in state.selection_keys()
    # Ticking something already there by need makes it your own choice.
    page = visible(act(state, '/select', key=MAIL, on='1'))
    assert MAIL in state.picked
    assert 'It was already in the bundle because something else you chose needs it.' in page


def test_unticking_a_choice_another_choice_needs_keeps_it_in_and_says_why(state):
    act(state, '/select', key=HOST_RULE, on='1')
    act(state, '/select', key=MAIL, on='1')
    page = visible(act(state, '/select', key=MAIL, on='0'))
    assert state.picked == [HOST_RULE] and MAIL in state.selection_keys()
    assert ('Left out [Fixture] Mail relay (StandardEmailPlugin). It stays in the bundle because '
            '[Fixture] Host rule needs it.') in page
    page = visible(act(state, '/select', key=HOST_RULE, on='0'))
    assert state.picked == [] and not state.selection_keys()
    assert 'Left out [Fixture] Host rule. 3 things only it needed were left out too.' in page
    assert 'Tick at least one thing to continue.' in page


def test_the_panel_shows_what_it_is_what_it_needs_and_what_uses_it(state):
    page = visible(act(state, '/focus', key=HOST_RULE))
    assert '[Fixture] Host rule Notification rule. Decides who is told when an alert is raised.' in page
    assert 'Needs 3 other things' in page
    assert 'These are in this export and come along automatically when you add it.' in page
    for needed in ('[Fixture] Host template', '[Fixture] Mail relay (StandardEmailPlugin)', GROUP_NAME):
        assert needed in page.split('Needs 3 other things')[1]
    page = visible(act(state, '/focus', key=MAIL))
    assert 'Needs nothing else' in page and 'It stands on its own.' in page
    assert 'Used by' in page and '[Fixture] Host rule' in page.split('Used by')[1]
    # Something missing is in the list in red, counted in the heading, never an id.
    html = act(state, '/focus', key=f'supermetric:{SM_IDS[2]}')
    assert 'Needs 1 other thing, 1 of them missing' in visible(html)
    assert '<li class="gone">' in html and 'Not in this export' in html
    # Clicking a name in the tree goes to it, and the list follows.
    act(state, '/filter', f='dashboard')
    page = act(state, '/focus', key=MAIL, reveal='1')
    assert state.focus_key == MAIL and state.filter_kind == 'outboundsetting'
    # Exactly one thing is brought into view: the row, never a link in the panel.
    assert page.count(' data-into-view') == 1
    assert 'data-into-view' in page.split(f'id="{uipage.anchor(MAIL)}"')[1].split('>')[0]
    assert "querySelector('[data-into-view]')" in uipage.CLIENT_JS
    assert "querySelector('[data-reveal]')" not in uipage.CLIENT_JS
    assert ' data-into-view' not in state.render()          # once only, not on every redraw


def test_search_is_by_name_and_survives_ticking_and_looking(state):
    bridge = Bridge(state)
    assert 'cluster' in bridge.find('CLUSTER')['list'].lower()
    names = [n.name for n in state.visible_nodes()]
    assert names and all('cluster' in name.lower() for name in names)
    # An id or an internal kind name is not something a person searches by.
    assert bridge.find(EMPTY_SM_ID)['cnt'] == f'0 of {len(state.graph.nodes)}'
    assert bridge.find('supermetric')['cnt'] == f'0 of {len(state.graph.nodes)}'
    act(state, '/filter', f='customgroup', q='tier')
    assert [n.name for n in state.visible_nodes()] == [GROUP_NAME_2]
    act(state, '/select', key=WEB_TIER, on='1', q='tier')
    act(state, '/focus', key=WEB_TIER, q='tier')
    assert state.filter_kind == 'customgroup' and state.filter_text == 'tier'
    page = state.render()
    assert 'value="tier"' in page and visible(page).count('[Fixture] Web Tier') >= 2
    # Nothing matches: a designed empty state with a way out.
    page = visible(act(state, '/filter', f='customgroup', q='oracle'))
    assert 'Nothing matches' in page and 'No names contain "oracle" in this part of the list.' in page
    act(state, '/show-all')
    assert (state.filter_kind, state.filter_text) == ('all', '')
    # A kind the export does not have is ignored rather than emptying the list.
    act(state, '/filter', f='spreadsheet')
    assert state.filter_kind == 'all'


def test_show_the_bundle_lists_choices_and_what_came_with_them(state):
    page = visible(act(state, '/filter', f='in'))
    assert 'Your bundle is empty' in page and 'Tick things in the list to add them.' in page
    act(state, '/select', key=HOST_RULE, on='1')
    act(state, '/filter', f='in')
    assert {n.key for n in state.visible_nodes()} == {HOST_RULE, HOST_TEMPLATE, MAIL, GROUP}
    assert 'In your bundle 4' in visible(state.render())


def test_add_all_of_these_adds_what_is_shown_skips_what_needs_a_decision_and_can_be_undone(state):
    act(state, '/select', key=SM_EMPTY, on='1')
    act(state, '/filter', f='customgroup')
    page = visible(act(state, '/add-shown'))
    assert set(state.picked) == {SM_EMPTY, GROUP, WEB_TIER, 'customgroup:[Fixture] Edge Nodes'}
    assert 'Added 3 things. Undo' in page
    assert state.undo
    page = visible(act(state, '/undo'))
    assert state.picked == [SM_EMPTY] and 'Undone. Your bundle is as it was.' in page
    # With no filter it takes everything that needs no decision, and says what it left.
    act(state, '/show-all')
    page = visible(act(state, '/add-shown'))
    assert not any(state.insight.needs_decision(key) for key in state.picked)
    assert not state.open_issues()
    left = [n for n in state.graph.ordered() if n.key not in state.selection_keys()]
    assert left and all(state.insight.needs_decision(n.key) for n in left)
    assert 'Not added, because each needs a decision from you' in page
    assert 'that need something this export does not have. Tick those yourself if you want them.' in page
    # Nothing left to add in bulk: the button goes rather than doing nothing.
    assert 'Add all of these' not in page


def test_emptying_the_bundle_asks_first(state):
    act(state, '/select', key=HOST_RULE, on='1')
    page = state.render()
    # Away from the add controls, and not a filled button.
    assert 'Empty the bundle' in foot_of(page) and 'Empty the bundle' not in main_of(page)
    page = act(state, '/clear')
    assert state.picked == [HOST_RULE]
    text = visible(page)
    assert 'Empty the bundle? This unticks the one thing you chose. Nothing is deleted from the export.' in text
    assert re.search(r'lat-btn--primary[^>]*data-act="/ask-no"', page)        # the safe answer is the filled one
    act(state, '/escape')
    assert state.picked == [HOST_RULE] and state.ask is None
    act(state, '/clear')
    page = visible(act(state, '/ask-yes'))
    assert state.picked == [] and 'Your bundle is empty again.' in page
    # With nothing chosen there is nothing to ask.
    act(state, '/clear')
    assert state.ask is None


def test_the_preview_is_a_picture_over_the_whole_window_with_no_ids(state):
    act(state, '/focus', key=DASH)
    assert 'See what it looks like' in visible(state.render())
    page = act(state, '/preview', key=DASH)
    text = visible(page)
    assert state.preview_key == DASH and 'Real layout, made-up numbers' in text
    assert ('This is a picture to help you recognise it. The widget titles, columns and layout come from '
            'the export. The names and numbers are invented, because the app has no live data.') in text
    assert 'Close preview' in text and '[Fixture] Cluster Overview' in text
    # The technical lines the command line's preview prints are not drawn here:
    # no id line, no "derived by hash" banner, no notes for an admin, no id.
    overlay = page.split('class="pv-over"')[1].split('<script>')[0]
    assert 'pv-sub' not in overlay and 'pv-banner' not in overlay and 'pv-notes' not in overlay
    shown = visible(overlay)
    assert 'derived by hash' not in shown and DASHBOARD_ID not in shown
    assert not re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', shown)
    assert 'This widget will be empty. The view it shows is not in the export.' in shown
    act(state, '/escape')
    assert state.preview_key == '' and state.screen == 'choose' and state.focus_key == DASH
    act(state, '/preview', key='dashboard:no-such-thing')
    assert state.preview_key == ''
    # Only things that have a look are offered one.
    act(state, '/focus', key=SM_EMPTY)
    assert 'See what it looks like' not in visible(state.render())
    # The command line keeps its own, technical preview.
    standalone = preview.fragment(state.graph, state.graph.nodes[DASH])
    assert DASHBOARD_ID in standalone and 'derived by hash' in standalone


def test_a_large_export_draws_a_window_of_the_list_and_still_adds_all_of_it(config_dir):
    graph = Graph()
    for i in range(LIST_LIMIT + 400):
        node = Node('supermetric', f'sm-{i:04d}', f'Metric {i:04d}', f'sm-{i:04d}', 'supermetrics.json', i)
        graph.nodes[node.key] = node
    state = page_over(graph)
    page = state.render()
    assert page.count('class="it') == LIST_LIMIT
    text = visible(page)
    assert f'Showing the first {LIST_LIMIT} of {LIST_LIMIT + 400}.' in text
    assert f'All {LIST_LIMIT + 400}' in text
    parts = Bridge(state).find('metric 069')
    assert parts['cnt'] == f'10 of {LIST_LIMIT + 400}' and parts['list'].count('class="it') == 10
    Bridge(state).find('')
    state.add_shown()
    assert len(state.picked) == LIST_LIMIT + 400
    assert f'In your bundle: {LIST_LIMIT + 400} things.' in visible(state.render())
    # The long lists on Review are windowed the same way.
    state.screen, state.issue = 'review', 'contents'
    assert visible(state.render()).count('Leave out') == LIST_LIMIT
    state.log.close()


def test_a_name_in_another_script_still_ticks_and_keeps_its_place(config_dir):
    graph = Graph()
    for ident, name in (('a', 'Übersicht Kapazität'), ('b', '容量の概要')):
        node = Node('view', ident, name, '', 'views.zip', 0)
        node.ident = name
        graph.nodes[node.key] = node
    state = page_over(graph)
    first, second = sorted(graph.nodes)
    assert uipage.anchor(first) != uipage.anchor(second)
    assert re.fullmatch(r'node-[A-Za-z0-9-]+', uipage.anchor(first))
    focus = dispatch(state, '/select', {'key': second, 'on': '1'})
    assert state.picked == [second] and f'id="{focus}"' in state.render()
    assert '容量の概要' in visible(state.render())
    state.log.close()


def test_opening_another_export_resets_what_the_list_was_showing(state, export_zip, tmp_path):
    act(state, '/filter', f='view', q='list')
    act(state, '/focus', key=f'view:{VIEW_IDS[0]}')
    other = tmp_path / 'second.zip'
    other.write_bytes(build_export_zip())
    state.open_export(str(other))
    assert (state.filter_kind, state.filter_text, state.focus_key) == ('all', '', '')
    assert state.screen == 'choose' and state.max_step == 1


def test_the_page_still_works_with_no_export_open(config_dir):
    state = PageState()
    for path in ACTIONS:
        if path not in ('/pick-export', '/connect', '/support-report'):
            dispatch(state, path, {})
            assert '<html' in state.render(), path
            assert state.graph is None and state.screen in ('open', 'connect', 'help'), path
    state.log.close()


# -- review, save -------------------------------------------------------------------

def test_review_says_where_things_stand_and_lists_what_is_inside(state):
    act(state, '/select', key=HOST_RULE, on='1')
    page = visible(act(state, '/go', to='review'))
    assert 'Ready to save Everything the export says this bundle needs is inside it.' in page
    assert 'Good to know, nothing to decide' in page
    assert 'The group arrives without a policy of its own' in page
    assert 'For reference' in page and 'What is in the bundle (4)' in page
    assert 'This was checked against the export only. The app cannot see the target.' in page
    assert ('In the bundle: 1 group, 1 notification rule, 1 notification template, 1 outbound setting.') in page
    page = visible(act(state, '/issue', id='contents'))
    assert 'You chose (1)' in page and 'Added automatically (3)' in page
    assert 'needed by [Fixture] Host rule' in page
    page = visible(act(state, '/issue', id='todo'))
    assert 'Nothing to do now. After you save, the last step walks you through these one at a time.' in page
    assert 'Import fixture-export-bundle.zip' in page


def test_leaving_something_out_at_review_says_what_went_with_it_and_can_be_undone(state):
    act(state, '/select', key=HOST_RULE, on='1')
    act(state, '/select', key=SM_EMPTY, on='1')
    act(state, '/go', to='review')
    page = visible(act(state, '/leave', key=HOST_RULE))
    assert state.picked == [SM_EMPTY] and state.screen == 'review'
    assert 'Left out [Fixture] Host rule. 3 things only it needed went with it:' in page and 'Undo' in page
    act(state, '/undo')
    assert state.picked == [HOST_RULE, SM_EMPTY]
    # Leaving the last thing out goes back to choosing rather than to an empty review.
    act(state, '/leave', key=HOST_RULE)
    page = visible(act(state, '/leave', key=SM_EMPTY))
    assert state.screen == 'choose' and 'Your bundle is empty. Tick something to carry on.' in page


def test_a_post_cannot_get_past_review(state, tmp_path):
    act(state, '/select', key=DASH, on='1')
    state.save_name, state.save_folder = 'sneaky.zip', tmp_path
    for form in ({}, {'force': '1'}):
        dispatch(state, '/save', dict(form))
        assert not (tmp_path / 'sneaky.zip').exists() and state.save_state != 'saved'
    act(state, '/go', to='save')
    assert state.screen != 'save'


def test_a_bundle_saved_through_the_window_is_the_bundle_the_command_line_writes(
        state, export_zip, tmp_path):
    for key in (SM_EMPTY, HOST_RULE, DASH_CLEAN):
        act(state, '/select', key=key, on='1')
    out = tmp_path / 'window.zip'
    save_to(state, out)
    assert state.save_state == 'saved'
    picks = tmp_path / 'picks.txt'
    picks.write_text('\n'.join((SM_EMPTY, HOST_RULE, DASH_CLEAN)) + '\n', encoding='utf-8')
    cli = tmp_path / 'cli.zip'
    assert main(['build', str(export_zip), '--select', str(picks), '--out', str(cli)]) == 0
    assert out.read_bytes() == cli.read_bytes()
    # The checklist the command line writes is the same list, named for its own file.
    assert (out.with_name('window-checklist.txt').read_text().replace('window.zip', 'cli.zip')
            == cli.with_name('cli-checklist.txt').read_text())
    again = tmp_path / 'again.zip'
    save_to(state, again)
    assert again.read_bytes() == out.read_bytes()
    assert zipfile.ZipFile(out).testzip() is None


def test_the_saved_screen_lets_the_report_and_checklist_be_read_in_place(state, tmp_path):
    act(state, '/select', key=HOST_RULE, on='1')
    save_to(state, tmp_path / 'read-me.zip')
    page = visible(state.render())
    assert 'Bundle saved 4 things saved to your' in page
    assert 'Nothing has been imported yet: that is the next step, on the target.' in page
    for name in ('read-me.zip', 'read-me-report.txt', 'read-me-checklist.txt'):
        assert name in page and (tmp_path / name).is_file()
    assert 'Next: import on the target →' in page
    page = visible(act(state, '/doc', k='report'))
    assert 'read-me-report.txt' in page and 'Bundle report' in page and 'Chosen by you (1)' in page
    assert 'Added because they are needed (3)' in page and '(needed by [Fixture] Host rule)' in page
    assert 'What was checked' in page and 'The target was not looked at.' in page
    act(state, '/escape')
    page = visible(act(state, '/doc', k='checklist'))
    assert 'Checklist for the target' in page and 'Import read-me.zip' in page
    act(state, '/close-doc')
    assert state.doc == ''
    # What is on screen is what is in the files.
    saved = (tmp_path / 'read-me-report.txt').read_text()
    assert saved.startswith('Bundle report\n=============\nread-me.zip, made from fixture-export.zip')
    assert 'Chosen by you (1)' in saved and '  - [Fixture] Host rule' in saved
    checklist = (tmp_path / 'read-me-checklist.txt').read_text()
    for task in state.tasks():
        assert task.title in checklist
        for step in task.steps:
            assert step in checklist


# -- import on the target -------------------------------------------------------------

def test_the_last_step_walks_the_checklist_one_task_at_a_time(state, tmp_path):
    act(state, '/select', key=HOST_RULE, on='1')
    save_to(state, tmp_path / 'done.zip')
    page = visible(act(state, '/go', to='target'))
    tasks = state.tasks()
    assert [task.id for task in tasks] == ['import', 'grp']
    assert 'Now, on the target' in page and 'The bundle is saved but not imported.' in page
    assert 'Step 1 of 2' in page and 'Import done.zip' in page and 'I have done this' in page
    assert 'open Administration, then Content, then Import' in page
    assert '0 of 2 done. You can stop here and do the rest later from the saved checklist.' in page
    assert 'Stop for now' in page
    page = visible(act(state, '/tick', id='import', on='1'))
    assert state.task == 'grp' and '1 of 2 done.' in page and 'Step 2 of 2' in page
    assert 'You have ticked the import. Work through the rest in order.' in page
    page = visible(act(state, '/finish', on='1'))
    assert 'Stopped for now, 1 task still to do' in page and 'You can close the app now.' in page
    act(state, '/finish', on='0')
    page = visible(act(state, '/tick', id='grp', on='1'))
    assert '2 of 2 done. Nothing left to do.' in page and 'Finish' in page
    page = act(state, '/finish', on='1')
    assert 'You have ticked everything' in visible(page)
    assert 'This app cannot see the target, so it is going by your ticks.' in visible(page)
    assert '<div class="foot">' not in page
    # A tick can be taken back, and a task that is not on the list is ignored.
    act(state, '/finish', on='0')
    act(state, '/tick', id='grp', on='0')
    act(state, '/tick', id='made-up', on='1')
    assert state.ticks == {'import': True, 'grp': False}
    # Another bundle from the same export starts clean.
    act(state, '/again')
    assert (state.screen, state.picked, state.ticks, state.save_state) == ('choose', [], {}, 'ready')


def test_the_import_task_says_which_password_the_target_will_ask_for(state, tmp_path):
    act(state, '/select', key=SM_EMPTY, on='1')
    typed = 'it is the one that was typed when the export was made on the source'
    made_up = 'type the export password you made up when you signed in to the source'
    assert typed in ' '.join(state.tasks()[0].steps)
    save_to(state, tmp_path / 'file.zip')
    assert typed in (tmp_path / 'file-checklist.txt').read_text()
    assert 'Keep the export password' not in visible(state.render())
    # An export this app fetched was locked with a password the person made up.
    state.source_snapshot = type('Snapshot', (), {'close': lambda self: None, 'path': tmp_path / 'x'})()
    assert made_up in ' '.join(state.tasks()[0].steps)
    save_to(state, tmp_path / 'fetched.zip')
    assert made_up in (tmp_path / 'fetched-checklist.txt').read_text()
    assert 'Keep the export password you made up when you signed in' in visible(state.render())
    state.source_snapshot = None
