#!/usr/bin/env python3
"""Profile a synthetic export with many objects and account values.

All content is invented. This measures identity-table scaling and dependency
logging, not every content shape in a customer export. Run outside the repo:
  python tools/profile_large_export.py /tmp/migrator-profile
The directory must be new. It holds the source, rebuilt bundle and profile.
"""
from __future__ import annotations

import argparse
import cProfile
import json
import os
from pathlib import Path
import time
import uuid
import zipfile


def make_export(path: Path, objects: int, identities: int) -> None:
    ids = [str(uuid.UUID(int=i + 1)) for i in range(objects)]
    metrics = {}
    for i, ident in enumerate(ids):
        formula = '${this, metric=Super Metric|sm_' + ids[i + 1] + '}' if i < min(6716, objects - 1) else '1'
        metrics[ident] = dict(name=f'Synthetic capacity metric {i:05d}', formula=formula,
                             description='Invented content for scale testing.', unitId='', resourceKinds=[])
    users = [dict(userName=f'synthetic-login-{i:04d}', displayName=f'Invented Person {i:04d}',
                  emailAddress=f'synthetic-{i:04d}@example.invalid') for i in range(identities)]
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('1L.v1', '')
        archive.writestr('configuration.json', json.dumps(dict(superMetrics=objects, users=identities)))
        archive.writestr('usermappings.json', json.dumps(dict(sources=[], users=users)))
        archive.writestr('supermetrics.json', json.dumps(metrics))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--objects', type=int, default=9000)
    parser.add_argument('--identities', type=int, default=113)
    args = parser.parse_args()
    if args.objects < 1 or args.identities < 0:
        parser.error('objects must be positive and identities nonnegative')
    args.directory.mkdir(parents=True, exist_ok=False)
    os.environ['VCFCF_MIGRATOR_CONFIG_DIR'] = str(args.directory / 'settings')
    source = args.directory / 'synthetic.zip'
    make_export(source, args.objects, args.identities)
    from vcfcf_migrator.ui import PageState
    from vcfcf_migrator import graph, runlog
    from vcfcf_migrator.export_reader import read_members

    profiler = cProfile.Profile()
    profiler.enable()
    started = time.perf_counter()
    state = PageState(str(source), log_level_cli='detail')
    opened = time.perf_counter()
    state.select_all()
    selected = time.perf_counter()
    state.render()
    rendered = time.perf_counter()
    profiler.disable()
    profiler.dump_stats(str(args.directory / 'operations.prof'))
    assert state.graph and len(state.graph.nodes) == args.objects, state.error
    assert state.selection and len(state.selection.keys) == args.objects
    state.build(str(args.directory / 'bundle.zip'))
    assert not state.error, state.error
    previous = runlog.set_current(runlog.NULL)
    try:
        rebuilt = graph.build_graph(read_members(args.directory / 'bundle.zip').data)
        assert rebuilt.counts() == state.graph.counts()
        assert rebuilt.edges == state.graph.edges
    finally:
        runlog.set_current(previous)
    state.save_diagnostics(str(args.directory / 'diagnostics.jsonl'))
    print(json.dumps(dict(objects=args.objects, identities=args.identities,
                          edges=sum(map(len, state.graph.edges.values())),
                          open_seconds=round(opened-started, 3),
                          select_seconds=round(selected-opened, 3),
                          render_seconds=round(rendered-selected, 3),
                          bundle_readback='passed')))


if __name__ == '__main__':
    main()
