"""Exercise the native window with invented content. Requires a display.

Run on Linux with xvfb-run -a python tools/smoke_native.py.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests' / 'fixtures'))
from make_export_fixture import EMPTY_SM_ID, build_export_zip
from vcfcf_migrator import desktop, ui
from vcfcf_migrator.export_reader import read_export
from vcfcf_migrator.ui import PageState
from vcfcf_migrator.uipage import anchor
import webview
import webview.http


def wait_for(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError('Native window action timed out')


def main():
    if sys.platform.startswith('linux'):
        import PySide6
        qt = Path(PySide6.__file__).parent / 'Qt'
        for relative in ('plugins/platforms/libqxcb.so', 'libexec/QtWebEngineProcess'):
            output = subprocess.check_output(['ldd', str(qt / relative)], text=True)
            missing = [line.strip() for line in output.splitlines() if 'not found' in line]
            if missing:
                raise RuntimeError('Missing native runtime libraries: ' + '; '.join(missing))
    failures = []
    with tempfile.TemporaryDirectory(prefix='migrator-native-') as directory:
        scratch = Path(directory)
        # Bundles and support reports go to Downloads, so the smoke gets a home
        # of its own rather than writing into the real one.
        (scratch / 'home' / 'Downloads').mkdir(parents=True)
        os.environ['HOME'] = os.environ['USERPROFILE'] = str(scratch / 'home')
        os.environ['VCFCF_MIGRATOR_CONFIG_DIR'] = str(scratch / 'settings')
        source = scratch / 'source.zip'
        source.write_bytes(build_export_zip())
        state = PageState()
        original_start = webview.start
        load_started = threading.Event()
        continue_load = threading.Event()
        original_read = state._read
        check_started = threading.Event()
        continue_check = threading.Event()
        opened_releases = []

        def delayed_release_check(version):
            check_started.set()
            if not continue_check.wait(30):
                return None, False
            return 'v99.0.0', True

        desktop.releases.release_status = delayed_release_check
        desktop.webbrowser.open = lambda url, new: opened_releases.append(url) or True

        def delayed_read(job, *args, **kwargs):
            load_started.set()
            if not continue_load.wait(15):
                raise AssertionError('Loading feedback was not acknowledged')
            return original_read(job, *args, **kwargs)

        state._read = delayed_read

        def reject_server(*args, **kwargs):
            raise AssertionError('The native UI must not start an HTTP server')

        webview.http.start_server = reject_server
        webview.http.start_global_server = reject_server

        def exercise():
            try:
                window = webview.windows[0]
                js = window.evaluate_js

                def ready():
                    return js("Boolean(window.migrator) && !document.body.hasAttribute('aria-busy') "
                              "&& document.body.dataset.busy !== '1'")

                def click(ident):
                    found = js("(function(){var el=document.getElementById(%s);if(!el)return 'missing';"
                               "if(el.disabled)return 'disabled';el.click();return 'ok';})()" % json.dumps(ident))
                    assert found == 'ok', f'{ident}: {found}'
                    time.sleep(0.1)
                    wait_for(ready)

                wait_for(load_started.is_set)
                wait_for(check_started.is_set)
                assert js("document.getElementById('update-slot').innerText") == ''
                assert state.graph is None
                # The export named on the command line is being read: the window
                # says so, counts the seconds and offers Cancel.
                wait_for(lambda: js("document.body.dataset.busy === '1' && "
                                    "document.body.innerText.includes('Reading source.zip')"))
                assert js("Boolean(document.getElementById('cancel-job'))")
                wait_for(lambda: js("document.getElementById('secs').textContent") not in ('0', None), timeout=4)
                continue_load.set()
                wait_for(lambda: state.graph is not None)
                wait_for(lambda: ready() and js("Boolean(document.getElementById('q'))"))
                # Content loading finishes while the update request is still
                # deliberately stalled. Publishing the notice redraws nothing.
                js("document.getElementById('q').value='unfinished search'")
                continue_check.set()
                wait_for(lambda: js("document.getElementById('update-slot').innerText")
                         == 'Update available: version 99.0.0')
                assert js("document.getElementById('q').value") == 'unfinished search'
                js("document.querySelector('#update-slot button').click()")
                wait_for(lambda: len(opened_releases) == 1)
                assert opened_releases == [desktop.releases.RELEASE_PAGE]
                js("document.getElementById('q').value=''")

                # Three boxes ticked while the first is still being acted on:
                # every one has to reach the program, in the order ticked. A
                # tick that only the page had would be undone by the next
                # redraw and left out of the bundle without a word.
                keys = json.loads(js(
                    "JSON.stringify(Array.from(document.querySelectorAll("
                    "'input[type=checkbox][data-act=\"/select\"]:not(:checked):not(:disabled)'))"
                    ".slice(0, 3).map(function (el) { return el.dataset.key; }))"))
                assert len(keys) == 3, keys
                held, release = threading.Event(), threading.Event()
                real_dispatch = ui.dispatch

                def held_dispatch(page, path, fields):
                    if path == '/select' and not release.is_set():
                        held.set()
                        release.wait(15)
                    return real_dispatch(page, path, fields)

                def tick_now(key):
                    assert js("(function(){var el=document.getElementById(%s);el.click();return el.checked;})()"
                              % json.dumps('tick-' + anchor(key)))

                ui.dispatch = held_dispatch
                try:
                    tick_now(keys[0])
                    wait_for(held.is_set)
                    tick_now(keys[1])
                    tick_now(keys[2])
                    assert state.picked == []          # nothing has got through yet
                    release.set()
                    time.sleep(0.1)
                    wait_for(ready)
                finally:
                    release.set()
                    ui.dispatch = real_dispatch
                assert state.picked == keys, f'ticked {keys}, but the program has {state.picked}'
                for key in keys:
                    assert js("document.getElementById(%s).checked" % json.dumps('tick-' + anchor(key))), key
                for key in keys:
                    click('tick-' + anchor(key))
                wait_for(lambda: state.picked == [])

                click('tick-' + anchor('supermetric:' + EMPTY_SM_ID))
                wait_for(lambda: len(state.picked) == 1)
                assert 'Added [Fixture] SM Empty.' in js('document.body.innerText')
                click('next')                      # no dashboards chosen, so straight to Review
                wait_for(lambda: 'Ready to save' in js('document.body.innerText'))
                click('next')
                wait_for(lambda: 'Save the bundle' in js('document.body.innerText'))
                output = scratch / 'home' / 'Downloads' / 'source-bundle.zip'
                assert 'source-bundle.zip' in js('document.body.innerText')
                click('save')
                wait_for(output.exists)
                wait_for(lambda: 'Bundle saved' in js('document.body.innerText'))
                assert read_export(output).counts() == {'supermetric': 1}
                assert output.with_name('source-bundle-report.txt').exists()
                assert output.with_name('source-bundle-checklist.txt').exists()
                click('help-button')
                wait_for(lambda: js("Boolean(document.getElementById('support-report'))"))
                # One click, no file window: straight into Downloads.
                dialogs = []
                window.create_file_dialog = lambda *args, **kwargs: dialogs.append(args) or None
                click('support-report')
                wait_for(lambda: list(output.parent.glob('support-report-*.txt')))
                assert not dialogs
                text = next(output.parent.glob('support-report-*.txt')).read_text()
                assert str(scratch) not in text
                assert '[Fixture]' not in text
                assert EMPTY_SM_ID not in text
                assert json.loads(text.splitlines()[0])['kind'] == 'vcfcf-migrator-diagnostics'
                print('Native reading feedback, update notice/browser link, initial load, quick ticks, selection, '
                      'save/readback, and anonymized support report passed.', flush=True)
            except Exception as exc:
                continue_load.set()
                continue_check.set()
                failures.append(exc)
                print(f'Native smoke failed: {exc}', file=sys.stderr, flush=True)
            finally:
                webview.windows[0].destroy()

        def start(**kwargs):
            original_start(exercise, **kwargs)

        webview.start = start
        watchdog = threading.Timer(90, lambda: os._exit(2))
        watchdog.daemon = True
        watchdog.start()
        try:
            desktop.run(state, initial_zip=str(source))
        finally:
            watchdog.cancel()
            state.log.close()
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
