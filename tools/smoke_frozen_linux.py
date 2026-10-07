"""Drive the packaged Linux window with X11 input and verify its output ZIP.

Run under xvfb-run with a 1280x1024 display and xdotool installed.
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    with tempfile.TemporaryDirectory(prefix='migrator-frozen-') as directory:
        scratch = Path(directory)
        source = scratch / 'source.zip'
        make_export(source, 1, 0)
        env = dict(os.environ, VCFCF_MIGRATOR_CONFIG_DIR=str(scratch / 'settings'))
        with (scratch / 'application.log').open('w+') as log:
            process = subprocess.Popen([str(executable), 'ui', str(source)], env=env,
                                       stdout=log, stderr=log)
            try:
                window = subprocess.check_output(
                    ['xdotool', 'search', '--sync', '--onlyvisible', '--name',
                     '^VCF content migrator$'], timeout=45, text=True).splitlines()[0]
                # The test display and native window size are fixed. These are
                # real pointer events, so the packaged JavaScript bridge runs.
                time.sleep(2)
                for x, y in [(435, 266), (1170, 827), (190, 458)]:
                    subprocess.run(['xdotool', 'mousemove', '--window', window,
                                    str(x), str(y), 'click', '1'], check=True)
                    time.sleep(1)
                output = scratch / 'source-bundle.zip'
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
                print('Packaged Linux native initial load, selection, and bundle build passed.')
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
