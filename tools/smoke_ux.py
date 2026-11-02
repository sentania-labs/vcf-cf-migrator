"""Exercise UX controls in a real Qt window, with synthetic Operations responses.

Run with xvfb-run -a python tools/smoke_ux.py [proof-directory]. File dialogs,
confirmation dialogs, keyboard focus and computed contrast are tested in Qt.
No live server or customer content is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests' / 'fixtures'))
from make_export_fixture import build_export_zip, DASHBOARD_ID, OWNER, EMPTY_SM_ID
from vcfcf_migrator import desktop, source
from vcfcf_migrator.ui import PageState
from vcfcf_migrator.export_reader import read_export
import webview
from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PySide6.QtTest import QTest
from smoke_native import wait_for


class MainThread(QObject):
    execute = Signal(object)

    def __init__(self):
        super().__init__()
        self.execute.connect(lambda callback: callback())


# Uses computed colors, not a scan of the stylesheet or token names.
CONTRAST = """(function() {
 function rgb(v) { return v.match(/[\\d.]+/g).map(Number); }
 function lum(c) { return c.slice(0,3).map(v => {v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0); }
 function background(el) {
   var value=rgb(getComputedStyle(el).backgroundColor);
   if(value.length<4 || value[3]===1) return value;
   var parent=el.parentElement ? background(el.parentElement) : [255,255,255];
   return value.slice(0,3).map((v,i)=>v*value[3]+parent[i]*(1-value[3]));
 }
 var bad=[];
 document.querySelectorAll('button,label,p,h1,h2,h3,small,summary,.kindtag,.status,.pv-key,.pv-sub').forEach(el=>{
   if(!el.getClientRects().length || !el.textContent.trim() || el.disabled || getComputedStyle(el).visibility==='hidden') return;
   var css=getComputedStyle(el), a=lum(rgb(css.color)), b=lum(background(el));
   var ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
   if(ratio<4.5)bad.push([el.tagName,el.textContent.slice(0,60),ratio]);
 });
 return bad;
})()"""


def main():
    proof = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if proof:
        proof.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    main_thread = MainThread()
    errors, pending_dialogs, timers = [], [], []
    with tempfile.TemporaryDirectory(prefix='migrator-ux-') as temporary:
        scratch = Path(temporary)
        os.environ['VCFCF_MIGRATOR_CONFIG_DIR'] = str(scratch / 'settings')
        export = scratch / 'fixture.zip'; export.write_bytes(build_export_zip())
        ca = scratch / 'company-ca.pem'; ca.write_text('synthetic file picker input')
        page = PageState()
        desktop.releases.newer_release = lambda version: None
        calls = []
        slow_started, release_slow = threading.Event(), threading.Event()

        class Client:
            def __init__(self, address, **kwargs):
                self.base = source.endpoint(address)
                calls.append(('policy', address, kwargs))

            def login_sources(self):
                if 'slow.example' in self.base:
                    slow_started.set()
                    release_slow.wait(15)
                    return ['Stale directory']
                if 'failed.example' in self.base:
                    raise source.SourceError('Certificate verification failed. Choose a trusted CA bundle.')
                return ['Directory A', 'SSO / B']

            def test_connection(self, username, password, auth_source):
                calls.append(('test', username, password, auth_source))

        source.Client = Client
        original_start = webview.start

        def arm_dialog(kind, answer, name):
            """Interact with the actual Qt modal dialog on the GUI thread."""
            done = threading.Event()
            pending_dialogs.append(done)
            def arm():
                timer = QTimer(); timers.append(timer)
                def inspect():
                    dialog = app.activeModalWidget()
                    expected = QFileDialog if kind == 'file' else QMessageBox
                    if not isinstance(dialog, expected):
                        return
                    timer.stop()
                    if proof:
                        dialog.grab().save(str(proof / (name + '.png')))
                    if kind == 'file':
                        if answer is None:
                            dialog.reject()
                        else:
                            dialog.selectFile(str(answer)); dialog.accept()
                    else:
                        dialog.done(QMessageBox.Ok if answer else QMessageBox.Cancel)
                    done.set()
                timer.timeout.connect(inspect); timer.start(50)
            main_thread.execute.emit(arm)
            return done

        def exercise():
            try:
                window = webview.windows[0]
                js = window.evaluate_js
                def ready():
                    return js("Boolean(window.pywebview?.api?.act) && !document.body.hasAttribute('aria-busy')")
                wait_for(ready)
                def submit(action, field=None, value=None):
                    selector = 'f.getAttribute("action") === ' + json.dumps(action)
                    if field:
                        selector += ' && f.querySelector("[name=' + field + ']")?.value === ' + json.dumps(value)
                    js('(function(){var f=Array.from(document.forms).find(f=>' + selector + '); if(!f)throw Error("Missing form");f.requestSubmit();})()')
                    wait_for(ready)
                def fill(ident, value, event='input'):
                    js('(function(){var e=document.getElementById(' + json.dumps(ident) + ');e.value=' + json.dumps(value) + ';e.dispatchEvent(new Event(' + json.dumps(event) + ',{bubbles:true}));})()')
                def text(ident):
                    return js('document.getElementById(' + json.dumps(ident) + ')?.textContent || ""')
                def capture(name):
                    if not proof:
                        return
                    time.sleep(.25)
                    done = threading.Event()
                    def save():
                        from webview.platforms.qt import BrowserView
                        BrowserView.instances[window.uid].grab().save(str(proof / (name + '.png')))
                        done.set()
                    main_thread.execute.emit(save); wait_for(done.is_set)
                assert js("document.querySelectorAll('.source-choices button').length") == 2
                submit('/source-mode', 'mode', 'connect')
                wait_for(lambda: js("Boolean(document.getElementById('source-ignore'))"))
                assert not js("document.getElementById('source-ignore').checked")
                fill('source-auth_source', 'Manually entered')
                fill('source-address', 'https://slow.example', 'change')
                wait_for(slow_started.is_set)
                assert 'Loading login sources' in text('source-status')
                capture('login-loading')
                fill('source-address', 'https://fresh.example', 'change')
                wait_for(lambda: 'loaded' in text('source-status'))
                release_slow.set(); time.sleep(.2)
                assert js("Array.from(document.querySelectorAll('#login-sources option')).map(e=>e.value)") == ['Local', 'Directory A', 'SSO / B']
                assert js("document.getElementById('source-auth_source').value") == 'Manually entered'
                fill('source-address', 'https://failed.example', 'change')
                wait_for(lambda: 'Enter a login source manually' in text('source-status'))
                assert js("document.getElementById('source-auth_source').value") == 'Manually entered'
                capture('login-failure')
                fill('source-address', 'not a URL', 'change')
                count = len(calls); time.sleep(.5)
                assert len(calls) == count
                done = arm_dialog('file', ca, 'native-ca-browse')
                js("document.querySelector('[data-pick=ca]').click()")
                wait_for(done.is_set)
                wait_for(lambda: js("document.getElementById('source-ca_file').value") == str(ca))
                js("document.getElementById('source-ignore').click()")
                assert js("document.getElementById('source-ca_file').disabled && document.querySelector('[data-pick=ca]').disabled && !document.getElementById('ssl-warning').hidden")
                capture('ignore-ssl')
                js("document.getElementById('source-ignore').click()")
                assert js("!document.getElementById('source-ca_file').disabled")
                assert js("document.getElementById('source-ca_file').value") == str(ca)
                fill('source-address', 'https://fresh.example', 'change')
                wait_for(lambda: 'loaded' in text('source-status'))
                fill('source-username', 'synthetic-user'); fill('source-password', 'synthetic-password')
                js("document.getElementById('test-connection').click()")
                wait_for(lambda: 'Connection succeeded' in text('test-status'))
                assert any(c[0] == 'test' for c in calls)
                assert not page.graph
                assert 'synthetic-password' not in json.dumps(page.log.snapshot())
                assert not (scratch / 'settings' / 'settings.json').exists()
                assert not js(CONTRAST), js(CONTRAST)
                capture('connection-tested')
                submit('/source-mode', 'mode', 'zip')
                wait_for(lambda: js("Boolean(document.getElementById('zip'))"))
                fill('zip', str(export)); submit('/open')
                wait_for(lambda: page.graph is not None and ready())
                dash = 'dashboard:' + DASHBOARD_ID + '@' + OWNER
                submit('/select', 'key', dash)
                wait_for(lambda: dash in page.picked and ready())
                assert js("document.querySelectorAll('.tick:disabled').length") > 0
                submit('/tab', 'tab', 'review')
                wait_for(lambda: js("document.body.innerText.includes('Remove affected selection')"))
                capture('missing-reference-actions')
                submit('/remove-pick', 'key', dash)
                wait_for(lambda: not page.picked and ready())
                submit('/tab', 'tab', 'preview')
                wait_for(lambda: js("Boolean(document.querySelector('.tick'))"))
                safe = 'supermetric:' + EMPTY_SM_ID
                submit('/select', 'key', safe)
                wait_for(lambda: safe in page.picked and ready())
                submit('/tab', 'tab', 'source')
                wait_for(lambda: js("Boolean(document.getElementById('zip'))"))
                fill('zip', str(export))
                done = arm_dialog('confirm', False, 'native-change-source')
                submit('/open'); wait_for(done.is_set); wait_for(ready)
                assert page.picked == [safe]
                submit('/tab', 'tab', 'preview')
                wait_for(lambda: page.tab == 'preview' and ready())
                # Keyboard focus survives selection redraws; next Tab remains usable.
                tick_id = 'tick-' + __import__('vcfcf_migrator.uipage', fromlist=['anchor']).anchor(safe)
                js('document.getElementById(' + json.dumps(tick_id) + ').focus()')
                submit('/select', 'key', safe)
                wait_for(lambda: not page.picked and ready())
                assert js('document.activeElement.id') == tick_id
                def tab_key():
                    from webview.platforms.qt import BrowserView
                    view = BrowserView.instances[window.uid].webview
                    view.setFocus()
                    QTest.keyClick(view.focusProxy() or view, Qt.Key_Tab)
                main_thread.execute.emit(tab_key)
                wait_for(lambda: js("document.activeElement.matches('button,input,select,a')") and js('document.activeElement.id') != tick_id)
                assert js("getComputedStyle(document.activeElement).outlineStyle") != 'none', js('document.activeElement.outerHTML')
                submit('/select', 'key', safe)
                wait_for(lambda: safe in page.picked and ready())
                submit('/tab', 'tab', 'review')
                wait_for(lambda: js("Boolean(document.getElementById('out'))"))
                done = arm_dialog('file', scratch / 'selected bundle', 'native-bundle-save')
                js("document.querySelector('[data-pick=bundle]').click()")
                wait_for(done.is_set)
                wait_for(lambda: js("document.getElementById('out').value").endswith('selected bundle.zip'))
                submit('/build')
                wait_for(lambda: bool(page.built_path) and ready())
                assert read_export(page.built_path).counts() == {'supermetric': 1}
                capture('saved-bundle')
                submit('/tab', 'tab', 'settings')
                wait_for(lambda: js("Boolean(document.getElementById('theme'))"))
                before = js("document.getElementById('diag_out').value")
                done = arm_dialog('file', None, 'native-diagnostics-cancel')
                submit('/diagnostics'); wait_for(done.is_set); wait_for(ready)
                assert js("document.getElementById('diag_out').value") == before
                assert not page.diagnostics_out
                done = arm_dialog('file', scratch / 'shared diagnostics', 'native-diagnostics-save')
                submit('/diagnostics'); wait_for(done.is_set)
                wait_for(lambda: bool(page.diagnostics_out) and ready())
                document = Path(page.diagnostics_out).read_text()
                assert '[Fixture]' not in document and 'synthetic-password' not in document
                # Switching appearance must update the live DOM, not just saved settings.
                for theme in ('dark', 'light'):
                    fill('theme', theme, 'change')
                    submit('/settings', 'theme', theme)
                    wait_for(lambda: js('document.body.dataset.theme') == theme and ready())
                    assert not js(CONTRAST), js(CONTRAST)
                    second = PageState(); assert second.theme == theme; second.log.close()
                    capture(theme + '-settings')
                    submit('/tab', 'tab', 'preview')
                    wait_for(lambda: page.tab == 'preview' and ready())
                    submit('/preview', 'key', dash)
                    wait_for(lambda: page.preview_key == dash and ready())
                    assert not js(CONTRAST), js(CONTRAST)
                    capture(theme + '-preview')
                    submit('/tab', 'tab', 'settings')
                    wait_for(lambda: js("Boolean(document.getElementById('theme'))") and ready())
                print('Native source discovery/stale results/manual fallback, TLS controls, test connection, CA/save dialogs, source confirmation, blocker removal, keyboard focus, bundle readback, diagnostics, theme persistence and contrast passed.', flush=True)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                errors.append(exc)
            finally:
                release_slow.set()
                webview.windows[0].destroy()

        def start(**kwargs):
            original_start(exercise, **kwargs)
        webview.start = start
        watchdog = threading.Timer(150, lambda: os._exit(2)); watchdog.daemon = True; watchdog.start()
        try:
            desktop.run(page, width=1440, height=1050)
        finally:
            watchdog.cancel(); page.log.close(); page.close_source()
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
