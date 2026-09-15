"""``corpus-check``: the local regression tier the spec asks for.

Two tiers of test material (spec, "Repo"): committed fixtures that CI runs
on, and a corpus of the admin's own export zips that never enters the repo.
This is the second tier. It walks every zip in a directory, runs inspect,
tree, a preview of every object and a select-all build on each, then reads
the bundle back and checks both halves of the pass-through contract: every
document byte-identical to the source's, and every rebuilt container
structurally identical, since a select-all drops nothing. One line per zip:
ok with counts, refused with the reason, or error. Its output goes in a PR
body; CI cannot run it and does not try.

The preview pass renders every object rather than sampling: a renderer that
throws does so on one document shape, and a sample is exactly how that shape
gets missed. It is the command this tool's admin looks at most, so leaving it
out of the regression run meant the only proof it survives a real export was
a script somebody wrote once and threw away.

One thing it never does: it never writes into the corpus directory. Bundles
go to a scratch directory that is removed afterwards, because the corpus is
the admin's own data and a tool that writes there is a tool that can corrupt
it. Every zip in the directory is checked the same way; nothing has to be
declared about any of them.

Exit status: non-zero only on an error. A refusal is an answer, not a
failure: it is the tool declining to guess.
"""
from __future__ import annotations

import shutil
import zipfile
import tempfile
from pathlib import Path
from typing import Dict, List, Sequence, TextIO, Tuple

from vcfcf_migrator import bundle as _bundle
from vcfcf_migrator import containers as _containers
from vcfcf_migrator import graph as _graph
from vcfcf_migrator import preview as _preview
from vcfcf_migrator import runlog
from vcfcf_migrator import selection as _selection
from vcfcf_migrator.export_reader import (
    NotAnExport,
    read_export,
    read_members,
)
from vcfcf_migrator.rawdoc import RawDocError
from vcfcf_migrator.wording import plural

def check_one(path: Path, scratch: Path) -> str:
    """One line for one zip. Never writes next to *path*."""
    with runlog.phase("corpus-zip", zip=str(path)):
        line = _check_one(path, scratch)
        runlog.info("corpus.zip_checked", zip=str(path), verdict=line.split()[0],
                    line=line)
        return line


def _check_one(path: Path, scratch: Path) -> str:
    try:
        export = read_export(path)
    except (NotAnExport, ValueError) as e:
        return f"error    {path.name}: {e}"

    try:
        members = read_members(path)
        graph = _graph.build_graph(members.data)
    except (NotAnExport, RawDocError) as e:
        return f"error    {path.name}: tree failed: {e}"

    inspect_counts = export.counts()
    tree_counts = graph.counts()
    if inspect_counts != tree_counts:
        return (f"error    {path.name}: inspect and tree disagree: "
                f"{inspect_counts} against {tree_counts}")

    rendered, preview_errors = _preview_all(graph)
    if preview_errors:
        first = preview_errors[0]
        return (f"error    {path.name}: preview failed on {len(preview_errors)} of "
                f"{plural(len(graph.nodes), 'object')}, first: {first}")

    try:
        picked = _selection.select_all(graph)
        out = scratch / (path.stem + "-bundle.zip")
        result = _bundle.build_bundle(members.data, members.order, graph, picked,
                                      out, marker=members.marker,
                                      directories=members.directories,
                                      directory_order=members.directory_order)
        rebuilt = read_export(out)
    except (RawDocError, NotAnExport, ValueError, OSError) as e:
        return f"error    {path.name}: build failed: {e}"

    synthesized = [n for n in result.members if n not in members.data
                   and n not in ("configuration.json",) and not n.endswith("/")]
    entry_problems = namelist_problems(path, out, graph.unknown_members, synthesized)
    if entry_problems:
        return (f"error    {path.name}: the bundle's zip entries do not match the export: "
                + "; ".join(entry_problems[:4]))

    bundle_members = read_members(out).data
    source_docs = _containers.documents(members.data)
    bundle_docs = _containers.documents(bundle_members)
    changed = [k for k, v in source_docs.items() if bundle_docs.get(k) != v]
    if changed or len(bundle_docs) != len(source_docs):
        return (f"error    {path.name}: {plural(len(changed), 'document')} did not survive the copy "
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
        return (f"error    {path.name}: select-all reshaped {plural(len(reshaped), 'container')}: "
                + ", ".join(reshaped))
    unexplained = sorted(k for k in source_shapes
                         if k not in bundle_shapes and k not in graph.unknown_members)
    if unexplained:
        return (f"error    {path.name}: select-all dropped {plural(len(unexplained), 'member')} that "
                "are not in the unreadable list: " + ", ".join(unexplained))

    if rebuilt.counts() != inspect_counts:
        return (f"error    {path.name}: the bundle does not carry what the export did: "
                f"{_fmt(rebuilt.counts())} against {_fmt(inspect_counts)}")
    missing = len(graph.missing)
    tail = (f", {plural(missing, 'edge')} to objects a bundle cannot carry"
            if missing else "")
    return (f"ok       {path.name}: {_fmt(inspect_counts)}; "
            f"{plural(rendered, 'object')} previewed; "
            f"select-all bundle round trips, {len(result.members)} members "
            f"({len(result.directories)} directory entries), "
            f"{len(source_docs)} documents byte-identical, "
            f"{len(bundle_shapes)} containers unchanged{tail}")


def namelist_problems(source: Path, bundle: Path, unknown_members: Sequence[str],
                      synthesized: Sequence[str] = ()) -> List[str]:
    """What a select-all bundle's zip entries say against the source export's.

    **Read with zipfile, not with the reader.** Every comparison this tool made
    before went through ``read_members``, which dropped zip directory entries
    on both sides, so the two agreed about a thing neither could see. VCF
    Operations refused every bundle this tool had ever built, with
    ``INVALID_FILE_FORMAT`` and an empty operation list, because the
    ``dashboards/`` and ``dashboardsharings/`` entries were missing, and no
    check here could have noticed. So this one opens both zips itself.

    On a select-all the bundle carries every member the tool understands, so
    the two lists must agree except for: members this tool never carries, a
    directory holding nothing but those, and a scaffolding member the target
    requires that the source did not have.
    """
    with zipfile.ZipFile(source) as z:
        src = z.namelist()
    with zipfile.ZipFile(bundle) as z:
        got = z.namelist()
    unknown = set(unknown_members)
    problems: List[str] = []
    for name in src:
        if name in got or name in unknown:
            continue
        if name.endswith("/"):
            # A directory entry is required only where the bundle still has
            # something under it.
            if any(other.startswith(name) for other in got):
                problems.append(f"the bundle is missing the directory entry {name}")
            continue
        problems.append(f"the bundle is missing {name}")
    for name in got:
        if name in src or name in synthesized:
            continue
        problems.append(f"the bundle carries {name}, which the source did not")
    for name in got:
        if name.endswith("/"):
            if not any(other != name and other.startswith(name) for other in got):
                problems.append(f"the bundle declares the empty directory {name}")
            continue
        if name.startswith("dashboards/"):
            # Every carried owner has a sharing member beside its dashboards
            # member, whatever the source held: a bundle without one is refused
            # by the target, and the source can be empty, unparseable, or about
            # dashboards this selection left behind.
            sharing = "dashboardsharings/" + name.split("/", 1)[1]
            if sharing not in got:
                problems.append(f"the bundle carries {name} with no {sharing}")
        parent = name.rsplit("/", 1)[0] + "/" if "/" in name else ""
        if parent and parent not in got:
            problems.append(f"the bundle writes {name} with no {parent} entry")
    return problems


def _preview_all(graph: _graph.Graph) -> Tuple[int, List[str]]:
    """Render every object's preview. Returns how many rendered, and a line
    per failure naming the object and what went wrong."""
    rendered = 0
    failures: List[str] = []
    with runlog.phase("preview-all", objects=len(graph.nodes)):
        return _preview_each(graph, rendered, failures)


def _preview_each(graph: _graph.Graph, rendered: int, failures: List[str]):
    for node in graph.ordered():
        try:
            page = _preview.render_page(graph, node)
        except (_preview.PreviewError, ValueError, KeyError, TypeError,
                AttributeError, IndexError) as e:
            runlog.error("preview.failed", kind=node.kind, uuid=node.uuid or "",
                         name=node.name, member=node.member, owner=node.owner or None,
                         failure=type(e).__name__, detail=str(e))
            failures.append(f"{node.label()}: {type(e).__name__}: {e}")
            continue
        if not page.startswith("<!doctype html>"):
            runlog.error("preview.not_a_page", kind=node.kind, uuid=node.uuid or "",
                         name=node.name,
                         reason=runlog.prose("the rendered preview is not an HTML document"))
            failures.append(f"{node.label()}: the page is not an HTML document")
            continue
        rendered += 1
    return rendered, failures


def _fmt(counts: Dict[str, int]) -> str:
    return ", ".join(f"{k}={counts[k]}" for k in _graph.KIND_ORDER if k in counts) or "no content"


def run(directory, source: str, stream: TextIO) -> int:
    directory = Path(directory)
    if not directory.is_dir():
        runlog.error("corpus.absent", dir=str(directory), dir_from=source,
                     reason=runlog.prose("the corpus directory does not exist"))
        stream.write(f"corpus directory {directory} does not exist (from {source})\n")
        return 1
    zips = sorted(p for p in directory.iterdir() if p.suffix.lower() == ".zip")
    runlog.info("corpus.walk", dir=str(directory), dir_from=source, zips=len(zips))
    stream.write(f"corpus: {directory} (from {source}), {plural(len(zips), 'zip')}\n")
    if not zips:
        return 0
    scratch = Path(tempfile.mkdtemp(prefix="vcfcf-migrator-corpus-"))
    errors = 0
    try:
        for path in zips:
            line = check_one(path, scratch)
            stream.write(line + "\n")
            if line.startswith("error"):
                errors += 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    runlog.info("corpus.checked", zips=len(zips), errors=errors)
    return 1 if errors else 0


def lines(directory) -> List[str]:
    """The same walk, as a list, for callers that are not a terminal."""
    import io

    buf = io.StringIO()
    run(directory, "caller", buf)
    return buf.getvalue().splitlines()
