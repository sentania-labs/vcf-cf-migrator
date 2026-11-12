"""Walk every job in the real Qt window, with stand-in services.

Run with ``xvfb-run -a python tools/smoke_ux.py [proof-directory] [WIDTHxHEIGHT]``.

The nine jobs the app exists for are done start to finish the way a person
does them: by clicking what is on screen. Along the way every state in the
screen inventory is reached, and at each one the walk checks that nothing but
a list scrolls, that text has enough contrast, and that nothing is folded
away. File and Save windows are the computer's own; the VCF Operations it
signs in to is a small HTTPS server started here with an invented export.

Only invented content is used. No real server is contacted. With a proof
directory, a screenshot of every state is saved there.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo_export
from vcfcf_migrator import desktop, releases, source, ui
from vcfcf_migrator.export_reader import read_export
from vcfcf_migrator.ui import PageState
from vcfcf_migrator.uipage import anchor
import webview
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit
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
 document.querySelectorAll('body *').forEach(el=>{
   if(!Array.from(el.childNodes).some(n=>n.nodeType===Node.TEXT_NODE && n.textContent.trim()) ||
      el.closest('script,style,option,button:disabled,.pv') || !el.getClientRects().length ||
      getComputedStyle(el).visibility==='hidden') return;
   var css=getComputedStyle(el), a=lum(rgb(css.color)), b=lum(background(el));
   var ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
   if(ratio<4.5)bad.push([el.tagName,el.textContent.slice(0,60),Math.round(ratio*100)/100]);
 });
 return bad;
})()"""

# The page itself must never scroll: only a list, inside its own panel.
OVERFLOW = """(function(){
  var bad=[], de=document.documentElement, b=document.body;
  if (de.scrollHeight>de.clientHeight+1 || b.scrollHeight>b.clientHeight+1 || de.scrollWidth>de.clientWidth+1)
    bad.push('page '+de.scrollHeight+'/'+de.clientHeight+' wide '+de.scrollWidth+'/'+de.clientWidth);
  ['main','.steps','.foot','.lat-header'].forEach(function(s){
    var e=document.querySelector(s);
    if(e && (e.scrollHeight>e.clientHeight+1 || e.scrollWidth>e.clientWidth+1))
      bad.push(s+' '+e.scrollHeight+'/'+e.clientHeight+' wide '+e.scrollWidth+'/'+e.clientWidth);
  });
  var small=Array.from(document.querySelectorAll('body *')).filter(function(e){
    return !e.closest('script,style,.pv') && e.getClientRects().length &&
      Array.from(e.childNodes).some(n=>n.nodeType===3 && n.textContent.trim()) &&
      parseFloat(getComputedStyle(e).fontSize) < 12; });
  if (small.length) bad.push('text under 12px: '+small[0].textContent.slice(0,40));
  if (document.querySelector('details')) bad.push('a folded section');
  return bad;})()"""


def stand_in_operations(scratch: Path):
    """A VCF Operations that is not one: an HTTPS server with a certificate
    this computer has never seen, which hands out the invented export."""
    cert, key = scratch / 'standin-ca.pem', scratch / 'standin-key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                    '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
                    '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
    seen, slow = [], {'seconds': 0.0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            payload = json.loads(body) if body else None
            code, response = 200, {'sources': [{'name': 'corp.example'}]}
            if self.path.endswith('/acquire'):
                if (payload or {}).get('password') == 'wrong':
                    code, response = 401, {}
                else:
                    response = {'token': 'synthetic-session'}
            if self.path.endswith('/content/operations/export'):
                if self.command == 'POST':
                    seen.append('export')
                    response = {'scope': 'CUSTOM', 'contentTypes': list(source.CONTENT_TYPES)}
                elif seen:
                    time.sleep(slow['seconds'])
                    response = {'state': 'FINISHED', 'id': 'synthetic-export-%d' % len(seen),
                                'startTime': len(seen)}
                else:
                    response = {'state': 'NOT_INITIALIZED'}
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(demo_export.build_export() if self.path.endswith('/export/zip')
                             else json.dumps(response).encode())

        do_GET = do_POST = answer

    server = ThreadingHTTPServer(('localhost', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f'https://localhost:{server.server_port}', cert, slow


def main():
    proof = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    width, height = (int(v) for v in (sys.argv[2] if len(sys.argv) > 2 else '1280x860').split('x'))
    if proof:
        proof.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    main_thread = MainThread()
    errors, timers = [], []
    with tempfile.TemporaryDirectory(prefix='migrator-ux-') as temporary:
        scratch = Path(temporary)
        home = scratch / 'home'
        downloads = home / 'Downloads'
        downloads.mkdir(parents=True)
        os.environ['HOME'] = str(home)
        os.environ['VCFCF_MIGRATOR_CONFIG_DIR'] = str(scratch / 'settings')
        files = demo_export.write_all(downloads)
        good = files['ops-prod-content-export.zip']
        address, ca, slow = stand_in_operations(scratch)
        page = PageState()
        opened = []
        releases.release_status = lambda version: ('v99.0.0', True)
        desktop.webbrowser.open = lambda url, new=0: opened.append(url) or True
        hold_read, reading = threading.Event(), threading.Event()
        hold_read.set()
        real_read = page._read

        def slow_read(job, *args, **kwargs):
            reading.set()
            assert hold_read.wait(20)
            return real_read(job, *args, **kwargs)
        page._read = slow_read
        original_start = webview.start

        def arm_dialog(answer, name=None, expect_directory=None):
            """Answer the computer's own file window, on the GUI thread."""
            done = threading.Event()

            def arm():
                timer = QTimer()
                timers.append(timer)

                def inspect():
                    dialog = app.activeModalWidget()
                    if not isinstance(dialog, QFileDialog):
                        return
                    timer.stop()
                    if expect_directory is not None:
                        assert Path(dialog.directory().absolutePath()) == expect_directory, (
                            'the file window opened in ' + dialog.directory().absolutePath())
                    if answer is not None:
                        # Fill the visible filename field after the async directory
                        # model settles; selectFile+accept in the same tick can lose
                        # the selection on Qt's non-platform Xvfb dialog.
                        dialog.findChild(QLineEdit, 'fileNameEdit').setText(str(answer))
                    if proof and name:
                        dialog.grab().save(str(proof / (name + '.png')))
                    dialog.finished.connect(lambda result: done.set())
                    if answer is None:
                        dialog.reject()
                    else:
                        QTimer.singleShot(150, dialog.accept)
                timer.timeout.connect(inspect)
                timer.start(50)
            main_thread.execute.emit(arm)
            return done

        def exercise():
            try:
                window = webview.windows[0]
                js = window.evaluate_js

                def ready():
                    return js("Boolean(window.pywebview && window.pywebview.api && window.migrator) && "
                              "!document.body.hasAttribute('aria-busy') && document.body.dataset.busy !== '1'")

                def settle():
                    time.sleep(.15)
                    wait_for(ready)

                def click(ident, wait=True):
                    found = js("(function(){var el=document.getElementById(%s);if(!el)return 'missing';"
                               "if(el.disabled)return 'disabled';el.click();return 'ok';})()" % json.dumps(ident))
                    assert found == 'ok', f'{ident}: {found}'
                    if wait:
                        settle()

                def press(text, tag='button', wait=True):
                    """Click the visible control whose words start with *text*."""
                    found = js("(function(){var t=%s;var el=Array.from(document.querySelectorAll(%s)).find("
                               "function(e){return e.textContent.trim().indexOf(t)===0&&e.getClientRects().length;});"
                               "if(!el)return 'missing';if(el.disabled)return 'disabled';el.click();return 'ok';})()"
                               % (json.dumps(text), json.dumps(tag)))
                    assert found == 'ok', f'{text!r}: {found}'
                    if wait:
                        settle()

                def fill(ident, value):
                    js("(function(){var e=document.getElementById(%s);e.focus();e.value=%s;"
                       "e.dispatchEvent(new Event('input',{bubbles:true}));})()"
                       % (json.dumps(ident), json.dumps(value)))

                def text():
                    return js('document.body.innerText') or ''

                def see(*words, timeout=20):
                    for word in words:
                        wait_for(lambda: word in text(), timeout)

                def gone(word):
                    assert word not in text(), f'{word!r} should not be on screen'

                def on_main(callback):
                    done = threading.Event()
                    main_thread.execute.emit(lambda: (callback(), done.set()))
                    wait_for(done.is_set)

                shots = []

                def look(name, contrast=True):
                    """A state has been reached: check it, and keep a picture."""
                    time.sleep(.3)
                    overflow = js(OVERFLOW)
                    assert not overflow, f'{name}: {overflow}'
                    if contrast:
                        low = js(CONTRAST)
                        assert not low, f'{name}: low contrast {low}'
                    shots.append(name)
                    if proof:
                        def save():
                            from webview.platforms.qt import BrowserView
                            BrowserView.instances[window.uid].grab().save(str(proof / (name + '.png')))
                        on_main(save)

                def key(name, owner=None):
                    graph = page.graph
                    return next(n.key for n in graph.nodes.values() if n.name == name
                                and (owner is None or graph.owner_names.get(n.owner) == owner))

                def tick(name, owner=None):
                    click('tick-' + anchor(key(name, owner)))

                def choose_file(path):
                    page.file_picker = lambda: str(path)
                    click('pick-export')

                wait_for(ready)
                on_main(lambda: window.move(0, 0))
                real_picker = page.file_picker

                # ---- Job 1: open an export -------------------------------------------
                see('What do you want to move?', 'Choose the export file…', 'or drop the ZIP file here')
                wait_for(lambda: 'Update available: version 99.0.0' in text())
                look('01-first-run')
                click('help-button'); see('Help and settings'); click('help-back')
                see('What do you want to move?')
                # The computer's own file window, opened in Downloads; cancelling is not an error.
                done = arm_dialog(None, expect_directory=downloads)
                click('pick-export', wait=False); wait_for(done.is_set); settle()
                gone('could not be opened')
                hold_read.clear(); reading.clear()
                done = arm_dialog(good, 'native-open-export', expect_directory=downloads)
                click('pick-export', wait=False); wait_for(done.is_set)
                wait_for(reading.is_set)
                see('Reading ops-prod-content-export.zip', 'Cancelling changes nothing.')
                wait_for(lambda: js("document.getElementById('secs').textContent") not in ('0', None), 5)
                look('02-reading')
                click('cancel-job', wait=False)
                see('Cancelled.', 'Nothing was changed.')
                hold_read.set(); settle()
                assert page.graph is None
                done = arm_dialog(good)
                click('pick-export', wait=False); wait_for(done.is_set)
                wait_for(lambda: page.graph is not None and ready())
                see('Opened ops-prod-content-export.zip.', 'It holds 30 things you can move.',
                    'Working from ops-prod-content-export.zip')
                look('03-choose-just-opened')
                page.file_picker = real_picker

                # ---- Job 2: find a dashboard and see what it depends on --------------
                click('rail-dashboard')
                fill('q', 'data')
                wait_for(lambda: js("document.getElementById('cnt').textContent") == '1 of 30')
                press('Datastore Health', '.nmb')
                see('Needs 4 other things, 2 of them missing', 'Datastore Daily Delta', 'Not in this export',
                    'One widget will be empty', 'An indented item is needed by the one above it.')
                assert js("document.getElementById('q').value") == 'data'
                look('04-choose-needs-something-missing')
                press('See what it looks like'); see('Real layout, made-up numbers', 'Close preview')
                look('05-preview', contrast=False)
                click('close-preview')
                fill('q', 'oracle')
                wait_for(lambda: 'Nothing matches' in text())
                look('06-choose-nothing-matches')
                press('Show everything')
                wait_for(lambda: js("document.getElementById('cnt').textContent") == 'All 30')

                # ---- Job 3: choose content, see what was added automatically ----------
                tick('Capacity Overview', 'admin')
                see('Added Capacity Overview', '7 things it needs were added too.',
                    'In your bundle: 8 things.', '1 you chose, 7 added automatically')
                assert js("document.querySelectorAll('.it input:checked:disabled').length") == 7
                press('Capacity Overview', '.nmb')
                look('07-choose-added-automatically')
                # The keyboard keeps its place across the redraw.
                first = 'tick-' + anchor(key('NOC Wallboard'))
                js('document.getElementById(%s).focus()' % json.dumps(first))
                click(first)
                assert js('document.activeElement.id') == first
                press('Show the bundle', '.linkbtn'); see('In your bundle')
                assert js("document.querySelectorAll('.it').length") == len(page.selection.keys)
                click('rail-all')
                press('Add all of these'); see('Not added, because each needs a decision from you', 'Undo')
                look('08-choose-added-all')
                press('Undo', '.linkbtn'); see('Undone.')
                assert len(page.picked) == 2
                press('Empty the bundle', '.linkbtn'); see('Empty the bundle?', 'Keep my choices')
                look('09-empty-the-bundle-question')
                click('ask-no')
                assert len(page.picked) == 2

                # ---- Job 4: who will own the dashboards --------------------------------
                tick('Capacity Overview', 'dana.reyes')
                see('You now have both copies of this dashboard.')
                click('next')
                see('First: which copy of Capacity Overview do you want?', 'The copy owned by admin',
                    'The copy owned by dana.reyes', 'Both, under their original owners')
                look('10-owner-two-copies')
                click('keep-both')
                see('Both copies are kept', 'Who should own these 4 dashboards on the target?')
                look('11-owner-same-user-after-keeping-both')
                click('own-admin')
                see('First: which copy of Capacity Overview do you want?')
                press('Keep this copy')
                see('was left out of the bundle', 'Who should own these 3 dashboards on the target?',
                    'Safest', 'May fail', 'Warning: The import, or the owner mapping, may fail')
                assert js("document.querySelectorAll('input[name=own]').length") == 2
                assert js("document.querySelectorAll('main select, main input.lat-input').length") == 0
                look('12-owner-the-normal-question')
                click('own-source')
                see('Skipped if dana.reyes is not on the target',
                    'Whole dashboard skipped if the target has no such group')
                look('13-owner-same-user-as-on-the-source')
                click('next')
                see('Ready to save', 'The import may fail if a dashboard owner is not on the target')
                click('issue-' + anchor('k-users')); see('Change the owner choice')
                look('14-review-same-user-reminder')
                press('Change the owner choice'); click('own-admin')

                # ---- Job 5: review and understand every warning --------------------------
                click('step-choose')
                for name in ('SQL Server Estate', 'Monthly Capacity Report', 'Page the capacity team',
                             'Datastore Health'):
                    tick(name)
                see('You will be asked at the Review step whether to keep it.')
                look('15-choose-with-warnings')
                click('next'); click('next')
                see('4 things to decide before you can save', 'Decide before saving',
                    'Good to know, nothing to decide', 'For reference')
                assert js("document.getElementById('next').disabled")
                look('16-review-decisions-needed')
                see('Leave out SQL Server Estate (recommended)', 'Keep it in anyway')
                click('leave-out'); see('Left out SQL Server Estate.', 'Undo', '3 things to decide')
                press('Undo', '.linkbtn'); see('4 things to decide')
                click('keep-in'); see('3 things to decide', 'Importing yours overwrites the target’s copy for good.')
                look('17-review-replaces-a-built-in-dashboard')
                click('keep-in')
                see('Page the capacity team needs the outbound setting "On-call webhook"',
                    'I have checked this already exists on the target. Keep it in anyway',
                    'Open an export that has what is missing…')
                look('18-review-missing-dependency')
                press('Open an export that has what is missing…')
                see('You are looking for an export that also contains the outbound setting "On-call webhook".')
                look('19-open-looking-for')
                press('← Back to ops-prod-content-export.zip'); click('next'); click('next')
                click('keep-in'); see('1 thing to decide')
                click('leave-out')
                see('Ready to save', 'except 1 thing you confirmed is already on the target',
                    'Kept in by your decision')
                assert not js("document.getElementById('next').disabled")
                look('20-review-ready-to-save')
                click('issue-' + anchor('contents')); see('You chose', 'Added automatically')
                look('21-review-what-is-in-the-bundle')
                count = len(page.tasks())
                click('issue-' + anchor('todo')); see(f'{count} things to do on the target', 'Nothing to do now.')
                look('22-review-after-import-preview')

                # ---- Job 6: save the bundle and find the report ---------------------------
                click('next')
                see('Save the bundle', 'ops-prod-content-export-bundle.zip', 'In folder', 'Downloads')
                look('23-save-ready')
                done = arm_dialog(downloads / 'capacity move', 'native-save-as', expect_directory=downloads)
                click('pick-save', wait=False); wait_for(done.is_set); settle()
                see('capacity move.zip')
                click('save')
                wait_for(lambda: page.save_state == 'saved' and ready())
                see('Bundle saved', 'Three files were saved in Downloads', 'capacity move-report.txt',
                    'capacity move-checklist.txt')
                bundle = downloads / 'capacity move.zip'
                assert read_export(bundle).counts()['dashboard'] == 5
                look('24-save-saved')
                click('read-report'); see('Bundle report', 'Kept in against the usual advice', 'What was checked')
                look('25-saved-report')
                click('close-doc')
                click('read-checklist'); see('Checklist for the target', 'Confirm that Page the capacity team')
                look('26-saved-checklist')
                js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))"); settle()
                gone('Checklist for the target')
                # The same name again: asked in the page, safe answer first.
                page.save_state = 'ready'; js('window.migrator.refresh()'); settle()
                click('save'); see('Downloads already has a file called capacity move.zip.',
                                   'Save with a different name…', 'Replace the old file')
                assert js("document.getElementById('save').disabled")
                look('27-save-file-exists')
                # A folder that takes no new files.
                locked = scratch / 'Reports'
                locked.mkdir(); locked.chmod(0o500)
                page.save_folder, page.save_state = locked, 'ready'
                js('window.migrator.refresh()'); settle()
                click('save'); see('Could not save in Reports.', 'That folder does not allow new files.',
                                   'Choose a different folder…')
                look('28-save-could-not-save')
                locked.chmod(0o700)
                page.save_folder, page.save_state = downloads, 'ready'
                js('window.migrator.refresh()'); settle()
                click('save'); click('replace')
                wait_for(lambda: page.save_state == 'saved' and ready())

                # ---- Job 7: what to do on the target ---------------------------------------
                click('next')
                see('Now, on the target', f'Step 1 of {count}', 'Import capacity move.zip', 'I have done this',
                    f'0 of {count} done.')
                look('29-target-checklist')
                click('done'); see(f'1 of {count} done.', f'Step 2 of {count}', 'Switch on ')
                click('finish'); see(f'Stopped for now, {count - 1} tasks still to do', 'You can close the app now.')
                look('30-target-stopped-for-now')
                press('Back to the checklist')
                for _ in range(count - 1):
                    click('done')
                see(f'{count} of {count} done.', 'Nothing left to do.')
                click('finish'); see('You have ticked everything')
                look('31-target-finished')

                # ---- Job 8: recover from a bad or incomplete export -------------------------
                press('Back to the checklist'); click('step-choose'); click('step-open')
                see('Open a different export',
                    'Your current export stays open until another one has been read successfully.')
                picked = list(page.picked)
                for file, title, shot in (
                        ('damaged-download.zip', 'That file could not be opened', '32-open-damaged-file'),
                        ('holiday-photos.zip', 'That ZIP is not a content export', '33-open-not-an-export'),
                        ('export-missing-its-dashboards.zip', 'This export is missing its dashboards',
                         '34-open-missing-its-dashboards'),
                        ('empty-export.zip', 'That export has nothing in it to move', '35-open-empty-export')):
                    choose_file(files[file])
                    see(title, 'What to do:', 'ops-prod-content-export.zip is still open and your '
                        f'{len(picked)} choices are untouched.')
                    assert page.picked == picked and page.zip_path == str(good)
                    look(shot)
                choose_file(good)
                see('Read ops-prod-content-export.zip again?', 'No, keep working')
                look('36-open-read-again-question')
                click('ask-no'); assert page.picked == picked
                click('step-open'); choose_file(good); click('ask-yes')
                wait_for(lambda: page.graph is not None and ready())
                see(f'Your {len(picked)} choices were ticked again.',
                    'What you decided at Review is asked again, because this is a different export.')
                assert page.picked == picked and page.decided == {}
                look('37-choose-choices-ticked-again')

                # ---- Job 9: help and diagnostics --------------------------------------------
                click('help-button')
                see('Something not working?', 'Save a support report (diagnostics)', 'How this works',
                    'What the words mean', 'You have version')
                look('38-help')
                click('support-report'); see('to your Downloads folder.', 'Save another')
                report = next(downloads.glob('support-report-*.txt'))
                body = report.read_text()
                assert json.loads(body.splitlines()[0])['kind'] == 'vcfcf-migrator-diagnostics'
                for private in ('Capacity Overview', 'dana.reyes', str(scratch), 'On-call webhook'):
                    assert private not in body, 'a real name reached the support report'
                look('39-help-support-report-saved')
                press('Report a problem'); wait_for(lambda: opened == [ui.ISSUES_PAGE])
                click('log-on'); see('Writing to Downloads / vcf-content-migrator.log'); gone('How much to record'); gone('Change…')
                assert (downloads / 'vcf-content-migrator.log').exists()
                look('40-help-log-file-on')
                click('log-on'); gone('Writing to')
                click('gloss'); see('What the words mean', 'Never finished loading', 'Management pack')
                look('41-what-the-words-mean')
                click('close-gloss')
                press('Get it'); wait_for(lambda: opened[-1] == releases.RELEASE_PAGE)
                click('theme-dark')
                wait_for(lambda: js('document.documentElement.dataset.theme') == 'dark' and ready())
                second = PageState(); assert second.theme == 'dark'; second.log.close()
                look('42-help-dark')
                click('help-back'); see('In your bundle')
                press('Datastore Health', '.nmb')
                look('43-choose-dark')
                click('next'); click('next'); see('Decide before saving', 'Good to know, nothing to decide')
                look('44-review-dark')
                click('help-button'); click('theme-light')
                wait_for(lambda: js('document.documentElement.dataset.theme') == 'light' and ready())
                click('help-back')

                # ---- Getting the export from VCF Operations ----------------------------------
                click('step-choose'); click('step-open'); click('to-connect')
                see('Sign in to the source', 'Make up a password for the export', 'Type it again')
                assert js("document.getElementById('conn-go').disabled")
                look('45-sign-in-empty-form')
                fill('c-addr', address); fill('c-user', 'migration'); fill('c-pw', 'synthetic-password')
                click('conn-test')
                see('The server could not be verified',
                    'This server uses a certificate your computer does not recognise.')
                assert js("document.getElementById('conn-go').disabled")
                look('46-sign-in-certificate-problem')
                done = arm_dialog(ca, 'native-certificate-file')
                press('Use my company certificate file…', wait=False); wait_for(done.is_set); settle()
                host = address.split('//')[1]
                see(f'Using the certificate file standin-ca.pem for {host}.')
                # The choice is about this one server: typing another address drops it,
                # and coming back means being asked again.
                fill('c-addr', 'https://elsewhere.invalid')
                wait_for(lambda: 'Using the certificate file' not in text())
                assert page.conn['cert'] is None
                fill('c-addr', address); click('conn-test')
                see('This server uses a certificate your computer does not recognise.')
                done = arm_dialog(ca, None)
                press('Use my company certificate file…', wait=False); wait_for(done.is_set); settle()
                see(f'Using the certificate file standin-ca.pem for {host}.')
                wait_for(lambda: 'Found it. Sign-in choices loaded below.' in text())
                assert 'An account from the company directory corp.example' in js(
                    "document.getElementById('c-src').innerText")
                click('conn-test'); see('Signed in fine. Nothing was exported.')
                assert js("document.getElementById('c-user').value") == 'migration'
                fill('c-epw', 'export-pass'); fill('c-epw2', 'export-pas')
                see('The two do not match yet.')
                assert js("document.getElementById('conn-go').disabled")
                fill('c-epw2', 'export-pass')
                look('47-sign-in-ready')
                fill('c-pw', 'wrong'); click('conn-go')
                see('Could not sign in.', 'Type the password again.')
                assert js("document.getElementById('c-pw').value") == ''
                assert js("document.getElementById('c-epw').value") == 'export-pass'
                look('48-sign-in-wrong-password')
                fill('c-pw', 'synthetic-password')
                slow['seconds'] = 1.5
                click('conn-go', wait=False)
                see('Getting your content', 'Signing in', 'Asking VCF Operations to prepare the export',
                    'Downloading the export', 'Reading what is inside', 'Cancelling leaves the source untouched.')
                look('49-sign-in-downloading')
                click('cancel-job', wait=False)
                see('Cancelled.', 'Nothing was downloaded.'); settle()
                assert page.source_snapshot is None and page.zip_path == str(good)
                slow['seconds'] = 0
                time.sleep(2)
                fill('c-pw', 'synthetic-password'); fill('c-epw', 'export-pass'); fill('c-epw2', 'export-pass')
                click('conn-go', wait=False)
                see('Switch to localhost', 'It was read successfully.')
                look('50-sign-in-switch-question')
                click('ask-yes')
                wait_for(lambda: page.source_snapshot is not None and ready())
                see('The downloaded export is kept only while the app is open',
                    f'Your {len(picked)} choices were ticked again.')
                assert 'synthetic-password' not in json.dumps(page.log.snapshot())
                assert 'export-pass' not in json.dumps(page.log.snapshot())
                assert 'theme' in json.loads((scratch / 'settings' / 'settings.json').read_text())
                assert 'password' not in (scratch / 'settings' / 'settings.json').read_text()
                look('51-choose-after-download')
                print(f'UX walk passed at {width}x{height}: {len(shots)} states checked for overflow, '
                      'contrast and folded sections; nine jobs done through the real window, file and Save '
                      'windows, and a stand-in VCF Operations.', flush=True)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                errors.append(exc)
                if proof:
                    try:
                        from webview.platforms.qt import BrowserView
                        done = threading.Event()
                        main_thread.execute.emit(lambda: (BrowserView.instances[webview.windows[0].uid].grab().save(
                            str(proof / 'FAILED.png')), done.set()))
                        done.wait(5)
                        print(webview.windows[0].evaluate_js('document.body.innerText')[:1500], flush=True)
                    except Exception:
                        pass
            finally:
                hold_read.set()
                webview.windows[0].destroy()

        def start(**kwargs):
            original_start(exercise, **kwargs)
        webview.start = start
        watchdog = threading.Timer(300, lambda: os._exit(2))
        watchdog.daemon = True
        watchdog.start()
        try:
            desktop.run(page, width=width, height=height)
        finally:
            watchdog.cancel()
            page.log.close()
            page.close_source()
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
