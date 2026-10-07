"""Launch a packaged Mac app, use its native controls, and read the built ZIP.

Requires a logged-in Mac graphical session and pyobjc-framework-Quartz.
Only invented content is used. A missing GUI or input permission fails the check.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
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

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--screenshot', type=Path, default=Path('macos-app-smoke.png'))
    args = parser.parse_args()
    app = args.app.resolve()
    with (app / 'Contents' / 'Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    assert info['CFBundleIdentifier'] == 'net.sentania.vcfcf-migrator'
    assert info['CFBundlePackageType'] == 'APPL'
    executable = app / 'Contents' / 'MacOS' / info['CFBundleExecutable']
    assert executable.is_file()
    with tempfile.TemporaryDirectory(prefix='migrator-mac-') as directory:
        scratch = Path(directory)
        source = scratch / 'source.zip'
        make_export(source, 1, 0)
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

                def rendered_after(action):
                    records = events()
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
                bounds = native['kCGWindowBounds']
                # Cocoa's title bar is outside the 1280x860 web content area.
                # Coordinates match the synthetic one-object inventory/review.
                title_height = bounds['Height'] - 860
                assert bounds['Width'] >= 1280 and 0 <= title_height <= 60, bounds
                wait_for(lambda: rendered_after('/open'))
                for x, y, action in [(435, 266, '/select-all'), (1170, 827, '/tab'), (190, 458, '/build')]:
                    # The completed render is observable; allow its native paint event to follow.
                    time.sleep(.5)
                    point = (bounds['X'] + x, bounds['Y'] + title_height + y)
                    for event_type in (Quartz.kCGEventMouseMoved, Quartz.kCGEventLeftMouseDown,
                                       Quartz.kCGEventLeftMouseUp):
                        event = Quartz.CGEventCreateMouseEvent(None, event_type, point, Quartz.kCGMouseButtonLeft)
                        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
                        time.sleep(.1)
                    wait_for(lambda: rendered_after(action))
                output = scratch / 'source-bundle.zip'
                wait_for(output.exists, timeout=20)
                with zipfile.ZipFile(output) as bundle:
                    metrics = json.loads(bundle.read('supermetrics.json'))
                    assert len(metrics) == 1
                    assert next(iter(metrics.values()))['name'] == 'Synthetic capacity metric 00000'
                subprocess.run(['screencapture', '-x', str(args.screenshot)], check=True)
                print('Packaged Mac native initial load, selection, and bundle readback passed.')
            except Exception:
                subprocess.run(['screencapture', '-x', str(args.screenshot)], check=False)
                log.seek(0)
                print(log.read())
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
