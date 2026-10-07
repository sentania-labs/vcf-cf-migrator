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
from vcfcf_migrator import desktop
from vcfcf_migrator.export_reader import read_export
from vcfcf_migrator.ui import PageState
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
        os.environ['VCFCF_MIGRATOR_CONFIG_DIR'] = str(scratch / 'settings')
        source = scratch / 'source.zip'
        source.write_bytes(build_export_zip())
        state = PageState()
        original_start = webview.start
        load_started = threading.Event()
        continue_load = threading.Event()
        original_open = state.open_export

        def delayed_open(path):
            load_started.set()
            if not continue_load.wait(15):
                raise AssertionError('Loading feedback was not acknowledged')
            return original_open(path)

        state.open_export = delayed_open

        def reject_server(*args, **kwargs):
            raise AssertionError('The native UI must not start an HTTP server')

        webview.http.start_server = reject_server
        webview.http.start_global_server = reject_server

        def exercise():
            try:
                window = webview.windows[0]
                wait_for(load_started.is_set)
                assert state.graph is None
                wait_for(lambda: window.evaluate_js("document.body.getAttribute('aria-busy') === 'true' && document.getElementById('operation-progress').innerText.includes('Opening export')"))
                wait_for(lambda: window.evaluate_js("document.getElementById('operation-progress').innerText.includes('(1 seconds)')"), timeout=3)
                continue_load.set()
                wait_for(lambda: state.graph is not None)
                wait_for(lambda: window.evaluate_js("!document.body.hasAttribute('aria-busy') && Boolean(document.querySelector('form[action=\"/select\"]'))"))

                def submit(action, field=None, value=None):
                    selector = "f.getAttribute('action') === " + json.dumps(action)
                    if field:
                        selector += " && f.querySelector('[name=" + field + "]')?.value === " + json.dumps(value)
                    window.evaluate_js("(function(){ var f=Array.from(document.querySelectorAll('form')).find(f=>" + selector + "); if(!f)throw Error('Form missing'); f.requestSubmit(); })()")

                submit('/select', 'key', 'supermetric:' + EMPTY_SM_ID)
                wait_for(lambda: len(state.picked) == 1)
                wait_for(lambda: window.evaluate_js("!document.querySelector('form[action=\"/tab\"] button').disabled"))
                submit('/tab', 'tab', 'review')
                wait_for(lambda: window.evaluate_js("Boolean(document.getElementById('out'))"))
                output = scratch / 'bundle.zip'
                window.evaluate_js("document.getElementById('out').value=" + json.dumps(str(output)))
                submit('/build')
                wait_for(output.exists)
                wait_for(lambda: 'bundle written' in window.evaluate_js('document.body.innerText'))
                assert read_export(output).counts() == {'supermetric': 1}
                submit('/tab', 'tab', 'settings')
                wait_for(lambda: window.evaluate_js("Boolean(document.getElementById('diag_out'))"))
                report = scratch / 'diagnostics.jsonl'
                window.evaluate_js("document.getElementById('diag_out').value=" + json.dumps(str(report)))
                submit('/diagnostics')
                wait_for(report.exists)
                text = report.read_text()
                assert str(scratch) not in text
                assert '[Fixture]' not in text
                assert EMPTY_SM_ID not in text
                assert json.loads(text.splitlines()[0])['kind'] == 'vcfcf-migrator-diagnostics'
                print('Native delayed-load feedback, initial load, selection, build/readback, and anonymized diagnostics passed.', flush=True)
            except Exception as exc:
                continue_load.set()
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
