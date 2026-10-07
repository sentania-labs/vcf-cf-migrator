"""Reading a selection, and closing it over the dependency graph.

The spec: "selecting a dashboard pulls in its views and their super metrics".
A bundle whose documents reference objects it does not carry is the one
failure mode subsetting can introduce on its own, so closure is not optional
and not a warning: every selected node drags in everything the export knows
it depends on, and the result says which object was added and why.

A selection file is one line per object, ``#`` comments and blank lines
allowed. Three spellings, all case-insensitive on the identifier:

    6e8310ed-1753-45a4-aacc-7f1025c03d11        any object with that uuid
    view:6e8310ed-1753-45a4-aacc-7f1025c03d11   that kind only
    dashboard:<uuid>@<owner>                    one owner's copy

Custom groups and outbound settings carry no uuid on any corpus export, so
they are named the way the export names them
(``customgroup:Prod Clusters``, ``outboundsetting:StandardEmailPlugin/relay``).

A line that matches nothing is refused: continuing would write a bundle
quietly missing what the admin asked for.

A closed selection that still depends on content the export does not carry is
refused too, by ``refuse_missing_dependencies``, which both ``build`` and the
page's Build button call before the writer. There is no flag and no checkbox
past it. Scott, 2026-10-05 (issue #31): "there should be no override - a
missing dependency breaks the bundle - it's something we need to guard
against." What counts as a missing dependency, and what is merely referenced
but not carried, is ``graph.is_missing_dependency``.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from vcfcf_migrator import runlog
from vcfcf_migrator.graph import (
    Graph,
    MissingEdge,
    Node,
    is_missing_dependency,
    missing_line,
    missing_reason,
)


class BadSelection(Exception):
    """A selection file that names something the export does not carry."""


class MissingDependency(Exception):
    """A closed selection that depends on content the export does not carry.

    Carries every missing dependency, so the caller can name all of them
    rather than the first: an admin fixing an export wants the whole list.
    ``lines`` is one readable line per dependency, the same line ``tree``
    prints for it.
    """

    def __init__(self, gaps: Sequence[MissingEdge], lines: Sequence[str]):
        self.gaps = list(gaps)
        self.lines = list(lines)
        super().__init__(
            f"the selection depends on {len(self.gaps)} "
            + ("object" if len(self.gaps) == 1 else "objects")
            + " this export does not carry, and a bundle without them would break on "
              "the target; export them as well, or leave out what depends on them")


@dataclass
class Addition:
    """One node pulled in by closure, and the node that needed it."""
    key: str
    reason: str


@dataclass
class Selection:
    keys: List[str] = field(default_factory=list)          # closed, in add order
    picked: List[str] = field(default_factory=list)        # what the file named
    added: List[Addition] = field(default_factory=list)    # what closure added
    missing: List[MissingEdge] = field(default_factory=list)
    # By-name references in the closure that several objects answer to, and
    # field values in a shape the tool does not read. The command that writes
    # the bundle has to say both, not only ``tree``.
    ambiguous: List[str] = field(default_factory=list)
    unhandled: List[str] = field(default_factory=list)
    lines: int = 0

    def counts(self, graph: Graph) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for key in self.keys:
            node = graph.nodes.get(key)
            if node is not None:
                out[node.kind] = out.get(node.kind, 0) + 1
        return out

    def missing_dependencies(self) -> List[MissingEdge]:
        """The edges out of this selection a bundle would break without.
        Non-empty means a build is refused."""
        return [g for g in self.missing if is_missing_dependency(g)]

    def not_carried(self) -> List[MissingEdge]:
        """The edges out of this selection to content the tool never carries
        and every instance has anyway: said, and not in the way."""
        return [g for g in self.missing if not is_missing_dependency(g)]


def parse_selection_file(path) -> List[str]:
    """The non-comment, non-blank lines of a selection file."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        runlog.error("selection.unreadable", path=str(path), reason=str(e))
        raise BadSelection(f"cannot read selection file {path}: {e}") from e
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    runlog.detail("selection.file_read", path=str(path), lines=len(out))
    return out


def match_line(graph: Graph, line: str) -> List[str]:
    """Every node key a selection line names; empty when it names nothing."""
    kind: Optional[str] = None
    ident = line
    if ":" in line:
        head, tail = line.split(":", 1)
        if head in {n.kind for n in graph.nodes.values()} or head in (
                "dashboard", "view", "supermetric", "customgroup", "symptom",
                "alert", "recommendation", "report", "notificationrule",
                "notificationtemplate", "outboundsetting"):
            kind, ident = head, tail
    owner = ""
    if "@" in ident and (kind == "dashboard" or kind is None):
        ident, owner = ident.rsplit("@", 1)
    lowered = ident.lower()
    hits = []
    for node in graph.ordered():
        if kind is not None and node.kind != kind:
            continue
        if owner and node.owner != owner:
            continue
        if lowered in (node.ident.lower(), node.uuid.lower(), node.name.lower()):
            hits.append(node.key)
    return hits


def resolve(graph: Graph, lines: Sequence[str]) -> List[str]:
    """Turn selection lines into node keys, refusing the first line that
    names nothing. Refusing is the point: a bundle that quietly omits what
    the admin asked for is worse than no bundle."""
    keys: List[str] = []
    unknown: List[str] = []
    # A selection line is the admin naming content, the same as an identifier
    # typed on the command line, so the identifiers in it are allowed in the
    # log. Without this a refusal read "the selection names [excluded:id]",
    # which is the one line where the identifier is the whole point.
    for line in lines:
        runlog.content_id(line)
    for line in lines:
        hits = match_line(graph, line)
        if not hits:
            unknown.append(line)
            continue
        for key in hits:
            if key not in keys:
                keys.append(key)
    for line in lines:
        runlog.debug("selection.line", line=str(line),
                     matched=len(match_line(graph, line)))
    if unknown:
        runlog.warn("selection.refused", lines=list(unknown),
                    reason=runlog.prose("the selection names objects this export does not carry; "
                           "continuing would write a bundle quietly missing what was asked "
                           "for, so no bundle is written"))
        raise BadSelection(
            "the selection names " + ("objects" if len(unknown) > 1 else "an object")
            + " this export does not carry: " + ", ".join(unknown))
    runlog.detail("selection.resolved", lines=len(lines), keys=len(keys))
    return keys


def close(graph: Graph, keys: Sequence[str]) -> Selection:
    """Pull in everything the selected nodes depend on, recording why.

    Dependencies the export does not carry are collected here and said, in the
    log and in the report; the closure itself does not refuse, because the
    page shows a selection long before anything is built. Refusing is the
    build's job, through ``refuse_missing_dependencies``.
    """
    with runlog.phase("select", picked=len(keys)):
        return _close(graph, keys)


def _close(graph: Graph, keys: Sequence[str]) -> Selection:
    selection = Selection(picked=list(keys), lines=len(keys))
    for key in keys:
        node = graph.nodes.get(key)
        if node is not None:
            runlog.detail("closure.picked", kind=node.kind, uuid=node.uuid or "",
                          name=node.name, owner=node.owner or None,
                          member=node.member, reason=runlog.prose("named by the selection"))
    queue = deque(keys)
    queued = set(keys)
    seen = set()
    ordered = []
    gaps_by_source = {}
    for gap in graph.missing:
        gaps_by_source.setdefault(gap.source_key, []).append(gap)
    selected_gaps = set()
    while queue:
        key = queue.popleft()
        queued.discard(key)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
        node = graph.nodes.get(key)
        if node is None:
            continue
        for target in graph.edges.get(key, []):
            if target in seen or target in queued:
                continue
            child = graph.nodes.get(target)
            if child is None:
                continue
            selection.added.append(Addition(
                target, f"{child.label()} added: required by {node.label()}"))
            runlog.detail("closure.added", kind=child.kind, uuid=child.uuid or "",
                          name=child.name, owner=child.owner or None,
                          member=child.member,
                          required_by_kind=node.kind, required_by_uuid=node.uuid or "",
                          required_by_name=node.name,
                          reason=runlog.prose("the selected object depends on it, so a bundle without "
                                 "it would point at an object it does not carry"))
            runlog.count("added_by_closure")
            queue.append(target)
            queued.add(target)
        for gap in gaps_by_source.get(key, ()):
            if gap not in selected_gaps:
                selection.missing.append(gap)
                selected_gaps.add(gap)
        # Matched on the node, not on the wording of a string built elsewhere.
        for note in graph.ambiguous:
            if note.source_key == key and note.text not in selection.ambiguous:
                selection.ambiguous.append(note.text)
        for note in graph.unhandled:
            if note.source_key == key and note.text not in selection.unhandled:
                selection.unhandled.append(note.text)
    selection.keys = ordered
    for gap in selection.missing:
        source = graph.nodes.get(gap.source_key)
        fields = dict(wants=gap.kind, ident=gap.ident, via=gap.via,
                      kind=source.kind if source else "", name=source.name if source else "",
                      uuid=(source.uuid or "") if source else "")
        if is_missing_dependency(gap):
            runlog.warn("closure.missing_dependency",
                        reason=runlog.prose(f"{missing_reason(gap)}; a build of this "
                                            "selection is refused while it depends on it"),
                        **fields)
        else:
            runlog.detail("closure.not_carried",
                          reason=runlog.prose(f"{missing_reason(gap)}, so it will be missing "
                                              "on import unless the target already has it"),
                          **fields)
    runlog.info("selection.closed", picked=len(selection.picked),
                carried=len(selection.keys), added=len(selection.added),
                counts=selection.counts(graph),
                missing_dependencies=len(selection.missing_dependencies()),
                not_carried=len(selection.not_carried()),
                ambiguous=len(selection.ambiguous), unhandled=len(selection.unhandled))
    return selection


def refuse_missing_dependencies(graph: Graph, selection: Selection) -> None:
    """Raise ``MissingDependency`` if a bundle of *selection* would break.

    The one gate between a closed selection and the writer, called by the
    command line and by the page alike. It has no parameter that turns it
    off, on purpose: a bundle that points at a view or a super metric it does
    not carry imports and then shows an empty widget or a metric that never
    computes, which is worse than no bundle, and the admin who wants it
    anyway has a better move, which is to export the content it names.

    Every refusal is logged with every dependency it names.
    """
    gaps = selection.missing_dependencies()
    if not gaps:
        return
    lines = [missing_line(graph, gap) for gap in gaps]
    for gap in gaps:
        source = graph.nodes.get(gap.source_key)
        runlog.error("build.missing_dependency", wants=gap.kind, ident=gap.ident,
                     via=gap.via, kind=source.kind if source else "",
                     name=source.name if source else "",
                     uuid=(source.uuid or "") if source else "",
                     owner=(source.owner or None) if source else None,
                     reason=missing_reason(gap))
    runlog.error("build.refused", missing_dependencies=len(gaps),
                 wants=[f"{gap.kind}:{gap.ident}" for gap in gaps],
                 carried=len(selection.keys),
                 reason=runlog.prose("the selection depends on content this export does not "
                                     "carry; a bundle without it breaks on the target, so no "
                                     "bundle is written and nothing overrides this"))
    raise MissingDependency(gaps, lines)


def select_all(graph: Graph) -> Selection:
    """Every node in the export. Closure is a no-op but still runs, so the
    select-all path and the subset path write bundles the same way."""
    return close(graph, [n.key for n in graph.ordered()])


def render(graph: Graph, selection: Selection) -> str:
    lines = [f"selected: {len(selection.picked)} named, "
             f"{len(selection.keys)} after closure"]
    counts = selection.counts(graph)
    if counts:
        lines.append("carrying: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if selection.added:
        lines.append(f"pulled in by dependency: {len(selection.added)}")
        for add in selection.added:
            lines.append(f"  {add.reason}")
    if selection.ambiguous:
        lines.append(f"references by name that several objects answer to: "
                     f"{len(selection.ambiguous)}")
        for note in selection.ambiguous:
            lines.append(f"  {note}")
    if selection.unhandled:
        lines.append(f"field values in a shape this tool does not read: "
                     f"{len(selection.unhandled)}")
        for note in selection.unhandled:
            lines.append(f"  {note}")
    blocking = selection.missing_dependencies()
    if blocking:
        lines.append(f"missing dependencies: {len(blocking)}; a build of this selection "
                     "is refused until the export carries them")
        for gap in blocking:
            lines.append("  " + missing_line(graph, gap))
    not_carried = selection.not_carried()
    if not_carried:
        lines.append(f"referenced but not carried: {len(not_carried)}")
        for gap in not_carried:
            lines.append(f"  {missing_line(graph, gap)}, so it will be missing on import "
                         "unless the target already has it")
    return "\n".join(lines) + "\n"


def _gap_dict(gap: MissingEdge) -> dict:
    return {"source": gap.source_key, "kind": gap.kind, "ident": gap.ident, "via": gap.via,
            "missing_dependency": is_missing_dependency(gap),
            "reason": str(missing_reason(gap))}


def as_dict(graph: Graph, selection: Selection) -> dict:
    return {
        "picked": list(selection.picked),
        "keys": list(selection.keys),
        "counts": selection.counts(graph),
        "added": [{"key": a.key, "reason": a.reason} for a in selection.added],
        "ambiguous": list(selection.ambiguous),
        "unhandled_shapes": list(selection.unhandled),
        "missing": [_gap_dict(m) for m in selection.missing],
        "missing_dependencies": [_gap_dict(m) for m in selection.missing_dependencies()],
        "not_carried": [_gap_dict(m) for m in selection.not_carried()],
    }


def selected_nodes(graph: Graph, selection: Selection) -> List[Node]:
    return [graph.nodes[k] for k in selection.keys if k in graph.nodes]
