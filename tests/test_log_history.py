"""A long session must remain explainable without its source export."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from vcfcf_migrator import runlog
from vcfcf_migrator.ui import PageState, dispatch


def test_settings_keep_clock_counters_and_active_operation(config_dir, tmp_path):
    state = PageState()
    log = state.log
    origin = log._t0
    for i in range(20):
        log.detail('closure.picked', index=i)
    before = log.written()
    dispatch(state, '/settings', {'log_level': 'debug'})
    assert state.log is log
    assert log._t0 == origin and log.written() > before
    ends = [e for e in log.events if e['event'] == 'phase.end']
    assert ends[-1]['action'] == '/settings'
    assert ends[-1]['operation']


def test_report_preserves_operations_and_warnings_after_detail_eviction(config_dir, tmp_path):
    state = PageState()
    log = state.log
    log.event_cap = 8
    with log.phase('page', action='/select-all'):
        with log.phase('select'):
            log.count('picked', 100)
            log.warn('ref.missing', name='Private Person')
    for i in range(100):
        log.detail('closure.picked', index=i)
    out = tmp_path / 'diagnostics.jsonl'
    state.save_diagnostics(str(out))
    head, *events = map(json.loads, out.read_text().splitlines())
    ends = [e for e in events if e['event'] == 'phase.end']
    assert len(ends) == 2
    child, parent = ends
    assert child['parent_operation'] == parent['operation']
    assert child['picked'] == 100 and child['start_t'] <= child['t']
    assert parent['action'] == '/select-all'
    assert any(e['event'] == 'ref.missing' for e in events)
    assert 'Private Person' not in out.read_text()
    assert head['retention']['dropped'] == log.dropped > 0
    assert head['events'] == len(events)


def test_overlapping_phases_have_independent_parents_and_counts():
    log = runlog.Log()
    log.events = []
    barrier = Barrier(2)

    def work(count):
        with log.phase('page'):
            barrier.wait(timeout=5)
            with log.phase('select'):
                log.count('picked', count)
                barrier.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(work, [3, 7]))
    ends = [e for e in log.events if e['event'] == 'phase.end']
    parents = {e['operation'] for e in ends if e['phase'] == 'page'}
    children = [e for e in ends if e['phase'] == 'select']
    assert {e['parent_operation'] for e in children} == parents
    assert {e['picked'] for e in children} == {3, 7}
    assert all(e['parent_operation'] == 0 for e in ends if e['phase'] == 'page')


def test_render_is_not_session_end(config_dir):
    state = PageState()
    state.render()
    assert not any(e['event'] == 'run.end' for e in state.log.events)
    assert any(e['event'] == 'phase.end' and e['phase'] == 'render'
               for e in state.log.events)


def test_summary_retention_is_bounded_and_counts_evictions():
    log = runlog.Log()
    log.events = []
    log.event_cap = 8
    log.history_cap = 4
    log.problem_cap = 3
    for i in range(20):
        with log.phase('read'):
            pass
        log.warn('ref.missing', index=i)
    assert len(log.history) <= 4
    assert log.history_dropped == 16
    assert log.problems_dropped == 18  # 20 warnings plus log.truncated
    assert len(log.problems) <= 3
    assert sum(log.retention()['dropped_by_level'].values()) == log.dropped
    assert len(log.snapshot()) <= log.event_cap + log.history_cap + log.problem_cap


def test_error_level_keeps_diagnostic_operations_but_honors_file_level(config_dir, tmp_path):
    target = tmp_path / 'errors.jsonl'
    state = PageState(log_cli=str(target), log_level_cli='error')
    with state.log.phase('read'):
        state.log.warn('shape.unhandled')
    assert not target.read_text()
    assert any(e['event'] == 'phase.end' for e in state.log.snapshot())
    assert any(e['event'] == 'shape.unhandled' for e in state.log.snapshot())
    state.log.close()


def test_successful_operations_do_not_evict_failure_history():
    import pytest
    log = runlog.Log()
    log.events = []
    log.event_cap, log.history_cap = 8, 2
    with pytest.raises(ValueError):
        with log.phase('read'):
            raise ValueError('private error text')
    for _ in range(10):
        with log.phase('render'):
            pass
    failures = [e for e in log.snapshot() if e['event'] == 'phase.end' and e.get('failed')]
    assert len(failures) == 1 and failures[0]['phase'] == 'read'
    assert not log.problems_dropped


def test_native_session_ends_only_after_window_closes(config_dir, monkeypatch):
    from vcfcf_migrator import desktop, ui
    seen = []

    def window(state):
        state.render()
        assert not any(e['event'] == 'run.end' for e in state.log.events)
        seen.append(state)

    monkeypatch.setattr(desktop, 'run', window)
    ui.run_desktop()
    assert len([e for e in seen[0].log.events if e['event'] == 'run.end']) == 1


def test_new_file_destinations_receive_original_session_context(config_dir, tmp_path):
    state = PageState()
    original = next(e.copy() for e in state.log.events if e['event'] == 'run.start')
    for basename in ('first.jsonl', 'second.jsonl'):
        path = tmp_path / basename
        state.save_setting({'log_file': str(path)})
        rows = list(map(json.loads, path.read_text().splitlines()))
        assert next(e for e in rows if e['event'] == 'run.start') == original
    assert next(e for e in state.log.events if e['event'] == 'run.start') == original
    state.log.close()
