"""Drive the packaged Linux window with X11 input and verify its output ZIP.

Run under xvfb-run with a 1280x1024 display and xdotool installed.
The packaged window ticks content and saves through its normal bridge. A
bundle goes to the Downloads folder, so the smoke gives the app a home of its
own and reads the bundle from there.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import zipfile

from profile_large_export import make_export


# Where the two controls are in the default 1280 x 860 window, measured in the
# real window with document.elementFromPoint over the same one-object export.
# Re-measure them if the frame's layout changes.
TICK_BOX = (251, 192)
NEXT_STEP = (1215, 830)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    with tempfile.TemporaryDirectory(prefix='migrator-frozen-') as directory:
        scratch = Path(directory)
        source = scratch / 'source.zip'
        make_export(source, 1, 0)
        home = scratch / 'home'
        (home / 'Downloads').mkdir(parents=True)
        output = home / 'Downloads' / 'source-bundle.zip'
        env = dict(os.environ, VCFCF_MIGRATOR_CONFIG_DIR=str(scratch / 'settings'), HOME=str(home))
        with (scratch / 'application.log').open('w+') as log:
            process = subprocess.Popen([str(executable), 'ui', str(source)], env=env,
                                       stdout=log, stderr=log)
            try:
                window = subprocess.check_output(
                    ['xdotool', 'search', '--sync', '--onlyvisible', '--name',
                     '^VCF Content Migrator$'], timeout=45, text=True).splitlines()[0]
                # The test display and native window size are fixed. These are
                # real pointer events, so the packaged JavaScript bridge runs.
                # The export named on the command line opens on "Choose content".
                # In a 1280 x 860 window: the first row's tick box, then the
                # next-step button, which is bottom right on every screen
                # ("Next: review", "Next: save", "Save the bundle").
                time.sleep(3)
                for x, y in [TICK_BOX, NEXT_STEP, NEXT_STEP, NEXT_STEP]:
                    subprocess.run(['xdotool', 'mousemove', '--window', window,
                                    str(x), str(y), 'click', '1'], check=True)
                    time.sleep(1.5)
                deadline = time.monotonic() + 15
                while not output.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise AssertionError('Packaged window exited before building')
                    time.sleep(0.1)
                assert output.exists(), 'Packaged native controls did not write the bundle'
                with zipfile.ZipFile(output) as bundle:
                    metrics = json.loads(bundle.read('supermetrics.json'))
                    assert len(metrics) == 1
                    assert next(iter(metrics.values()))['name'] == 'Synthetic capacity metric 00000'
                # The report is written after the bundle, so give it a moment.
                companions = [output.with_name('source-bundle-report.txt'),
                              output.with_name('source-bundle-checklist.txt')]
                deadline = time.monotonic() + 15
                while not all(path.exists() for path in companions) and time.monotonic() < deadline:
                    time.sleep(0.1)
                assert all(path.exists() for path in companions), 'The report and checklist were not written'
                print('Packaged Linux native initial load, selection, and bundle save passed.')
            except Exception:
                log.seek(0)
                print(log.read())
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
