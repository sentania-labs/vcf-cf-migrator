"""Launch a packaged Mac app, use its native controls, and read the built ZIP.

Requires a logged-in Mac graphical session and pyobjc-framework-Quartz / pyobjc-framework-Vision.
Only invented content is used. A missing GUI or input permission fails the check.
"""
from __future__ import annotations

import argparse
import atexit
import json
from importlib.metadata import version
from pathlib import Path
import plistlib
import socket
import subprocess
import tempfile
import time
import zipfile

from profile_large_export import make_export


def wait_for(predicate, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError('The packaged Mac app did not complete the native action.')


def main():
    import Quartz
    from AppKit import NSRunningApplication
    from Foundation import NSURL
    import Vision

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--objects', type=int, default=1)
    parser.add_argument('--identities', type=int, default=0)
    parser.add_argument('--screenshot', type=Path, default=Path('macos-app-smoke.png'))
    args = parser.parse_args()
    if args.objects < 1 or args.identities < 0:
        parser.error('objects must be positive and identities nonnegative')
    # Hosted Mac runners start at 1024x768, smaller than this app's default.
    # Select a real supported mode and restore it when this check exits.
    display = Quartz.CGMainDisplayID()
    original_mode = Quartz.CGDisplayCopyDisplayMode(display)
    if Quartz.CGDisplayModeGetWidth(original_mode) < 1280 or Quartz.CGDisplayModeGetHeight(original_mode) < 1000:
        modes = [mode for mode in Quartz.CGDisplayCopyAllDisplayModes(display, None)
                 if Quartz.CGDisplayModeGetWidth(mode) >= 1280 and Quartz.CGDisplayModeGetHeight(mode) >= 1000]
        if not modes:
            raise AssertionError('The Mac test display has no supported mode of at least 1280x1000.')
        mode = min(modes, key=lambda m: Quartz.CGDisplayModeGetWidth(m) * Quartz.CGDisplayModeGetHeight(m))
        if Quartz.CGDisplaySetDisplayMode(display, mode, None) != Quartz.kCGErrorSuccess:
            raise AssertionError('Could not select the native smoke display size.')
        atexit.register(Quartz.CGDisplaySetDisplayMode, display, original_mode, None)
    app = args.app.resolve()
    with (app / 'Contents' / 'Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    assert info['CFBundleIdentifier'] == 'net.sentania.vcfcf-migrator'
    assert info['CFBundlePackageType'] == 'APPL'
    expected_version = version('vcf-cf-migrator').split('+')[0].split('.dev')[0]
    assert info['CFBundleVersion'] == info['CFBundleShortVersionString'] == expected_version
    executable = app / 'Contents' / 'MacOS' / info['CFBundleExecutable']
    assert executable.is_file()
    with tempfile.TemporaryDirectory(prefix='migrator-mac-') as directory:
        scratch = Path(directory)
        source = scratch / 'source.zip'
        make_export(source, args.objects, args.identities)
        events_path = scratch / 'events.jsonl'
        before = {a.processIdentifier() for a in NSRunningApplication.runningApplicationsWithBundleIdentifier_(info['CFBundleIdentifier'])}
        running = None
        with (scratch / 'application.log').open('w+') as log:
            process = subprocess.Popen(['open', '-n', '-W', str(app), '--args',
                                        '--log', str(events_path), '--log-format', 'jsonl',
                                        'ui', str(source)], stdout=log, stderr=log)
            try:
                running = wait_for(lambda: next((a for a in NSRunningApplication.runningApplicationsWithBundleIdentifier_(
                    info['CFBundleIdentifier']) if a.processIdentifier() not in before), None))
                def events():
                    if not events_path.exists():
                        return []
                    parsed = []
                    for line in events_path.read_text().splitlines():
                        try:
                            parsed.append(json.loads(line))
                        except ValueError:
                            pass  # The current line may still be being written.
                    return parsed

                def rendered_after(action, since=0):
                    records = events()[since:]
                    completed = [i for i, e in enumerate(records) if e.get('event') == 'phase.end'
                                 and e.get('phase') == 'page' and e.get('action') == action
                                 and not e.get('failed')]
                    return completed and any(e.get('event') == 'phase.end' and e.get('phase') == 'render'
                                             for e in records[completed[-1] + 1:])

                def window():
                    if process.poll() is not None:
                        raise AssertionError('The application exited before opening a native window.')
                    return next((w for w in Quartz.CGWindowListCopyWindowInfo(
                        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
                        if w.get('kCGWindowOwnerPID') == running.processIdentifier()
                        and w.get('kCGWindowLayer') == 0
                        and w.get('kCGWindowBounds', {}).get('Width', 0) > 600), None)

                native = wait_for(window)
                def button_point(label):
                    # Find the visible label, rather than assuming Cocoa and Qt
                    # render fonts and control spacing at the same coordinates.
                    capture = scratch / 'screen.png'
                    subprocess.run(['screencapture', '-x', '-D', '1', str(capture)], check=True)
                    request = Vision.VNRecognizeTextRequest.alloc().init()
                    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
                    request.setRecognitionLanguages_(['en-US'])
                    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
                        NSURL.fileURLWithPath_(str(capture)), {})
                    ok, error = handler.performRequests_error_([request], None)
                    if not ok:
                        raise AssertionError('Could not read native control labels: ' + str(error))
                    matches = [item for item in request.results() or []
                               if str(item.topCandidates_(1)[0].string()).lower().startswith(label.lower())]
                    if len(matches) != 1:
                        return None
                    rect = matches[0].boundingBox()
                    screen = Quartz.CGDisplayBounds(display)
                    return (screen.origin.x + (rect.origin.x + rect.size.width / 2) * screen.size.width,
                            screen.origin.y + (1 - rect.origin.y - rect.size.height / 2) * screen.size.height)

                def click(label, action, scroll=False):
                    offset = len(events())
                    def find():
                        point = button_point(label)
                        if point is None and scroll:
                            # End scrolls the web document, even when the last
                            # clicked control is in the fixed review footer.
                            for down in (True, False):
                                event = Quartz.CGEventCreateKeyboardEvent(None, 119, down)
                                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
                        return point
                    point = wait_for(find)
                    print('Clicking visible native control:', label, flush=True)
                    for event_type in (Quartz.kCGEventMouseMoved, Quartz.kCGEventLeftMouseDown,
                                       Quartz.kCGEventLeftMouseUp):
                        event = Quartz.CGEventCreateMouseEvent(None, event_type, point, Quartz.kCGMouseButtonLeft)
                        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
                        time.sleep(.1)
                    wait_for(lambda: rendered_after(action, offset))

                wait_for(lambda: rendered_after('/open'))
                click('Select all', '/select-all')
                click('Review bundle', '/tab')
                click('Build the bundle', '/build', scroll=True)
                output = scratch / 'source-bundle.zip'
                wait_for(output.exists, timeout=20)
                with zipfile.ZipFile(output) as bundle:
                    metrics = json.loads(bundle.read('supermetrics.json'))
                    assert len(metrics) == args.objects
                    with zipfile.ZipFile(source) as original:
                        assert metrics == json.loads(original.read('supermetrics.json'))
                subprocess.run(['screencapture', '-x', str(args.screenshot)], check=True)
                click('Help & diagnostics', '/tab')
                click('Save anonymized diagnostics', '/diagnostics')
                report = (scratch / 'vcfcf-migrator-diagnostics.jsonl').read_text()
                assert json.loads(report.splitlines()[0])['kind'] == 'vcfcf-migrator-diagnostics'
                for private in (str(scratch), str(Path.home()), Path.home().name, socket.gethostname(),
                                'Synthetic capacity metric', 'synthetic-login-', 'Invented Person', '@example.invalid'):
                    assert private not in report, 'Private identity remained in diagnostics'
                assert not any(ident in report for ident in metrics)
                subprocess.run(['screencapture', '-x', str(args.screenshot.with_name(args.screenshot.stem + '-diagnostics.png'))], check=True)
                timings = {e['action']: e['ms'] for e in events() if e.get('event') == 'phase.end'
                           and e.get('phase') == 'page' and e.get('action') in ('/open', '/select-all')}
                print(json.dumps(dict(objects=args.objects, identities=args.identities, page_ms=timings,
                                      bundle_readback='passed', diagnostics_privacy='passed')))
                print('Packaged Mac native load, selection, bundle readback, and saved diagnostics passed.')
            except Exception:
                subprocess.run(['screencapture', '-x', str(args.screenshot)], check=False)
                log.seek(0)
                print(log.read())
                if events_path.exists():
                    print('\n'.join(json.dumps(e) for e in events() if e.get('phase') in ('page', 'render')))
                raise
            finally:
                if running is not None:
                    running.terminate()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if running is not None:
                        running.forceTerminate()
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
