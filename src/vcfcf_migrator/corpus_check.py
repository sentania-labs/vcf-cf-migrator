"""``corpus-check``: the local regression tier the spec asks for.

Two tiers of test material (spec, "Repo"): committed fixtures that CI runs
on, and a corpus of the admin's own export zips that never enters the repo.
This is the second tier. It walks every zip in a directory, runs inspect,
tree and a select-all build on each, then reads the bundle back and checks
both halves of the pass-through contract: every document byte-identical to
the source's, and every rebuilt container structurally identical, since a
select-all drops nothing. One line per zip: ok with counts, refused with the
reason, or error. Its output goes in a PR body; CI
cannot run it and does not try.

Two things it never does. It never writes into the corpus directory: bundles
go to a scratch directory that is removed afterwards, because the corpus is
the admin's own data and a tool that writes there is a tool that can corrupt
it. And it never guesses a source version: an export carries none, so the
version comes from a ``versions.json`` beside the zips (``{"<file>":
"8.18.7"}``, read only, never written), or from ``--source-version`` for the
whole run. A zip with neither gets its build line refused, not a made-up
version.

Exit status: non-zero only on an error. A refusal is an answer, not a
failure: it is the tool declining to guess.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, TextIO

from vcfcf_migrator import bundle as _bundle
from vcfcf_migrator import containers as _containers
from vcfcf_migrator import graph as _graph
from vcfcf_migrator import selection as _selection
from vcfcf_migrator.export_reader import (
    NotAnExport,
    UnsupportedExport,
    read_export,
    read_members,
)
from vcfcf_migrator.rawdoc import RawDocError

VERSIONS_FILE = "versions.json"


def read_versions(directory: Path) -> Dict[str, str]:
    """``versions.json`` beside the zips, if the admin wrote one. Read only."""
    path = directory / VERSIONS_FILE
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in doc.items()} if isinstance(doc, dict) else {}


def check_one(path: Path, declared: Optional[str], scratch: Path) -> str:
    """One line for one zip. Never writes next to *path*."""
    try:
        export = read_export(path, source_version=declared)
    except UnsupportedExport as e:
        return f"refused  {path.name}: {e}"
    except (NotAnExport, ValueError) as e:
        return f"error    {path.name}: {e}"

    try:
        members = read_members(path, source_version=declared)
        graph = _graph.build_graph(members.data)
    except (NotAnExport, UnsupportedExport, RawDocError) as e:
        return f"error    {path.name}: tree failed: {e}"

    inspect_counts = export.counts()
    tree_counts = graph.counts()
    if inspect_counts != tree_counts:
        return (f"error    {path.name}: inspect and tree disagree: "
                f"{inspect_counts} against {tree_counts}")

    if declared is None:
        return (f"refused  {path.name}: inspect and tree ok ({_fmt(tree_counts)}), "
                "build needs a declared source version (versions.json or --source-version)")

    try:
        picked = _selection.select_all(graph)
        out = scratch / (path.stem + "-bundle.zip")
        result = _bundle.build_bundle(members.data, members.order, graph, picked,
                                      out, marker=members.marker)
        rebuilt = read_export(out, source_version=declared)
    except (RawDocError, NotAnExport, UnsupportedExport, ValueError, OSError) as e:
        return f"error    {path.name}: build failed: {e}"

    bundle_members = read_members(out, source_version=declared).data
    source_docs = _containers.documents(members.data)
    bundle_docs = _containers.documents(bundle_members)
    changed = [k for k, v in source_docs.items() if bundle_docs.get(k) != v]
    if changed or len(bundle_docs) != len(source_docs):
        return (f"error    {path.name}: {len(changed)} document(s) did not survive the copy "
                f"byte for byte, {len(bundle_docs)} carried against {len(source_docs)}")

    # Documents are copied; containers are rebuilt, so the container is the
    # half that can drift. On a select-all nothing is dropped, so every member
    # the bundle carries must have the same structure as the source's, and
    # every member it does not carry must be one this tool cannot read.
    source_shapes = _containers.container_shapes(members.data)
    bundle_shapes = _containers.container_shapes(bundle_members)
    reshaped = sorted(k for k, v in source_shapes.items()
                      if k in bundle_shapes and bundle_shapes[k] != v)
    if reshaped:
        return (f"error    {path.name}: select-all reshaped {len(reshaped)} container(s): "
                + ", ".join(reshaped))
    unexplained = sorted(k for k in source_shapes
                         if k not in bundle_shapes and k not in graph.unknown_members)
    if unexplained:
        return (f"error    {path.name}: select-all dropped {len(unexplained)} member(s) that "
                "are not in the unreadable list: " + ", ".join(unexplained))

    if rebuilt.counts() != inspect_counts:
        return (f"error    {path.name}: the bundle does not carry what the export did: "
                f"{_fmt(rebuilt.counts())} against {_fmt(inspect_counts)}")
    missing = len(graph.missing)
    tail = f", {missing} edge(s) to objects a bundle cannot carry" if missing else ""
    return (f"ok       {path.name}: {_fmt(inspect_counts)}; select-all bundle round trips, "
            f"{len(result.members)} members, {len(source_docs)} documents byte-identical, "
            f"{len(bundle_shapes)} containers unchanged{tail}")


def _fmt(counts: Dict[str, int]) -> str:
    return ", ".join(f"{k}={counts[k]}" for k in _graph.KIND_ORDER if k in counts) or "no content"


def run(directory, source: str, declared: Optional[str], stream: TextIO) -> int:
    directory = Path(directory)
    if not directory.is_dir():
        stream.write(f"corpus directory {directory} does not exist (from {source})\n")
        return 1
    zips = sorted(p for p in directory.iterdir() if p.suffix.lower() == ".zip")
    stream.write(f"corpus: {directory} (from {source}), {len(zips)} zip(s)\n")
    if not zips:
        return 0
    versions = read_versions(directory)
    scratch = Path(tempfile.mkdtemp(prefix="vcfcf-migrator-corpus-"))
    errors = 0
    try:
        for path in zips:
            line = check_one(path, versions.get(path.name, declared), scratch)
            stream.write(line + "\n")
            if line.startswith("error"):
                errors += 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 1 if errors else 0


def lines(directory, declared: Optional[str] = None) -> List[str]:
    """The same walk, as a list, for callers that are not a terminal."""
    import io

    buf = io.StringIO()
    run(directory, "caller", declared, buf)
    return buf.getvalue().splitlines()
