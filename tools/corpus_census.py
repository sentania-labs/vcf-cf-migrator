"""Count what the corpus actually holds, by distinct content.

Why this is a script in the repo rather than a number in a report: three
rounds of review caught a census that could not be reproduced, that changed
when the zips were read in a different order, and that stated one identity
rule while implementing another. A figure nobody else can recompute is not a
finding, so this is the only thing that may produce one.

    python3 tools/corpus_census.py corpus
    python3 tools/corpus_census.py corpus --json

It never writes into the corpus directory, and nothing it prints belongs in
the repo: the corpus is the admin's own content (see the spec's two tiers of
test material), so its names and uuids stay on the workstation.

**Where the numbers come from.** Every per-widget verdict is the one the
preview reached while rendering that widget, read out of
``Preview.widget_verdicts``; this script counts, it does not classify. An
earlier version carried its own copy of the rules and had drifted from them
twice. Widgets are keyed with ``preview.widget_keys``, the page's own key
expression, for the same reason.

**Identity.** An object is ``(kind, uuid)``, falling back to
``(kind, ident)`` for the kinds an export gives no uuid (custom groups,
outbound settings). A dashboard is its uuid alone. A widget is
``(dashboard uuid, widget id)``, and a widget with no id of its own is
``(dashboard uuid, "index-<n>")``, which is stable within a document.

**Order independence.** The same content appears in more than one export (the
two devel zips are one instance twice) and one dashboard uuid can appear
under two owners with different widgets. Every count here is built by
accumulating into a dict keyed by identity and then reducing, so the result
does not depend on the order the zips are read. The suite proves it: the
figures are recomputed with the zip list reversed and compared.

**When two copies of one identity differ.** They are reported rather than
silently merged:

* a dashboard uuid whose copies carry different widget ids contributes the
  union of those ids, and the dashboard is counted once in ``divergent
  dashboards``;
* a widget or an object whose copies classify differently is **excluded from
  every per-reason total** and counted in ``divergent widgets`` or
  ``divergent objects``. An earlier version broke the tie by sorting, which
  quietly picked "clean" for widgets and "empty" for objects and hid 14
  classified widgets from the headline. A tie-break in either direction is a
  claim about content the exports disagree about, and there is nothing here
  to base it on;
* an object identity carrying two different names is counted once and
  reported in ``objects with two names``.

Every per-reason total is therefore "where the copies agree", and the
divergent counts say how much is not in them.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vcfcf_migrator import graph as _graph  # noqa: E402
from vcfcf_migrator import preview as _preview  # noqa: E402
from vcfcf_migrator import runlog as _runlog  # noqa: E402
from vcfcf_migrator.corpus_check import read_versions  # noqa: E402
from vcfcf_migrator.export_reader import read_members  # noqa: E402

# Widget states that are about the admin's own content, as against the two
# that are about where the content lives.
UNFINISHED_CODES = tuple(c for c in _preview.WIDGET_EMPTY_CODES)


class Census:
    """Every count, keyed by identity so the order of the walk cannot move it."""

    def __init__(self) -> None:
        self.object_names: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        self.object_empty: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        self.dashboard_widgets: Dict[str, Set[str]] = defaultdict(set)
        self.dashboard_widget_sets: Dict[str, Set[Tuple[str, ...]]] = defaultdict(set)
        self.dashboard_driven: Dict[str, Set[bool]] = defaultdict(set)
        self.widget_state: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        self.widget_subject: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        self.occurrences: Counter = Counter()

    # -- walking -----------------------------------------------------------

    def add_export(self, path: Path, declared: Optional[str]) -> None:
        members = read_members(path, source_version=declared)
        graph = _graph.build_graph(members.data)
        for node in graph.ordered():
            preview = _preview.build(graph, node)
            oid = (node.kind, node.uuid or node.ident)
            self.object_names[oid].add(node.name)
            self.object_empty[oid].add(preview.empty_code or "")
            self.occurrences["objects"] += 1
            if preview.empty_code:
                self.occurrences["empty objects"] += 1
            if node.kind != "dashboard":
                continue
            self.add_dashboard(graph, node, preview)

    def add_dashboard(self, graph, node, preview) -> None:
        doc = json.loads(_preview.raw_document(graph, node))
        raw_widgets = doc.get("widgets")
        widgets = ([w for w in raw_widgets if isinstance(w, dict)]
                   if isinstance(raw_widgets, list) else [])
        wiring = _preview.read_wiring(doc, widgets)
        dash = node.uuid or node.ident
        # One key expression, the page's own: two that happen to agree do not
        # (the page renders tab by tab, so "position" meant two different
        # positions and id-less widgets were counted under another widget's
        # verdict).
        keys = _preview.widget_keys(widgets)
        ids = [keys[id(w)] for w in widgets]
        self.dashboard_widgets[dash].update(ids)
        self.dashboard_widget_sets[dash].add(tuple(sorted(ids)))
        self.dashboard_driven[dash].add(bool(wiring.receivers))
        self.occurrences["widgets"] += len(widgets)
        self.occurrences["dashboards"] += 1
        self.occurrences["empty widgets"] += len(preview.empty_widgets)
        self.occurrences["elsewhere widgets"] += len(preview.elsewhere)
        for kind, count in preview.subjects.items():
            self.occurrences[f"subject {kind}"] += count

        # Every verdict comes from the page that renders it. The census used
        # to re-derive them and had already drifted twice: once on the subject
        # rule, once on whether a selector's view is resolved at all.
        # Set equality, not membership: the per-tab keys that caused this are
        # a *subset* of the document-index keys, so no lookup misses and a
        # membership check sees nothing. Duplicate keys are checked too, since
        # two widgets sharing a key would collapse into one verdict.
        if set(ids) != set(preview.widget_verdicts) or len(set(ids)) != len(ids):
            raise KeyError(
                f"dashboard {dash}: the census and the page are keying widgets "
                f"differently ({len(set(ids))} census keys, "
                f"{len(preview.widget_verdicts)} page keys)")
        for wid in ids:
            state, code, subject = preview.widget_verdicts[wid]
            self.widget_subject[(dash, wid)].add(subject)
            self.widget_state[(dash, wid)].add(code if state else "")
        for (_title, _kind, code, _reason) in preview.elsewhere:
            if code == "widget-view-not-carried":
                self.occurrences["widgets showing a view the export does not carry"] += 1

    # -- reducing ----------------------------------------------------------

    def report(self) -> dict:
        widgets = {k: sorted(v) for k, v in self.widget_state.items()}
        divergent_widgets = sum(1 for v in widgets.values() if len(v) > 1)
        # Agreed copies only: see the module docstring on divergence.
        agreed = {k: v[0] for k, v in widgets.items() if len(v) == 1}
        empty = {k: c for k, c in agreed.items() if c}
        by_code = Counter(empty.values())
        subjects = Counter(next(iter(v)) for v in self.widget_subject.values()
                           if len(v) == 1)
        divergent_subjects = sum(1 for v in self.widget_subject.values() if len(v) > 1)
        object_empty = {k: next(iter(v)) for k, v in self.object_empty.items()
                        if len(v) == 1}
        divergent_objects = sum(1 for v in self.object_empty.values() if len(v) > 1)
        unfinished = {k: c for k, c in empty.items() if c in UNFINISHED_CODES}
        # A divergent identity is in no by-reason block, so the count of the
        # ones whose copies disagree *about carrying nothing* is the figure
        # that would otherwise go missing between the block and the total.
        disputed_widgets = sum(1 for v in widgets.values() if len(v) > 1
                               and any(c in UNFINISHED_CODES for c in v))
        disputed_objects = sum(1 for v in self.object_empty.values() if len(v) > 1
                               and any(c for c in v))
        return {
            "objects": len(self.object_names),
            "objects with two names": sum(1 for v in self.object_names.values() if len(v) > 1),
            "objects carrying nothing": sum(1 for v in object_empty.values() if v),
            "objects disputed as carrying nothing": disputed_objects,
            "divergent objects": divergent_objects,
            "objects carrying nothing by reason": dict(
                Counter(v for v in object_empty.values() if v)),
            "dashboards": len(self.dashboard_widgets),
            "dashboards interaction driven": sum(1 for v in self.dashboard_driven.values()
                                                 if True in v),
            "divergent dashboards": sum(1 for v in self.dashboard_widget_sets.values()
                                        if len(v) > 1),
            # Both sides of the union rule: the widget ids seen for each
            # dashboard, and the per-widget classifications. They are built
            # from the same walk and a mutation to either shows up here.
            "widgets": sum(len(v) for v in self.dashboard_widgets.values()),
            "widgets classified": len(self.widget_state),
            "divergent widgets": divergent_widgets,
            "widgets carrying nothing": len(unfinished),
            "widgets disputed as carrying nothing": disputed_widgets,
            "widgets carrying nothing by reason": dict(
                Counter(c for c in unfinished.values())),
            "widgets shown elsewhere by reason": dict(
                Counter(c for c in empty.values() if c not in UNFINISHED_CODES)),
            "widget subjects": dict(subjects),
            "widgets with a subject": sum(subjects.values()),
            "divergent widget subjects": divergent_subjects,
            "occurrences": dict(self.occurrences),
            "_codes": dict(by_code),
        }


def walk(directory: Path, declared: Optional[str] = None,
         paths: Optional[List[Path]] = None) -> dict:
    versions = read_versions(directory)
    zips = paths if paths is not None else sorted(
        p for p in directory.iterdir() if p.suffix.lower() == ".zip")
    census = Census()
    for path in zips:
        census.add_export(path, versions.get(path.name, declared))
    return census.report()


def render(report: dict) -> str:
    """The report as text, with a note against each block saying what that
    block counts.

    One note covering four blocks was wrong about two of them: the subject
    block filters on subject agreement, which nothing in the corpus breaks, so
    it leaves nothing out; and the two carrying-nothing totals are built from
    agreed copies, so the header's claim that the totals count everything was
    false for them. Each note now belongs to one block, and
    ``tests/test_census.py`` asserts every block's own sum against the figure
    its note claims, so a future edit cannot put the two back out of step.
    """
    lines = ["distinct content across the corpus",
             "  (the totals below count every identity, divergent copies included, except "
             "the two carrying-nothing counts, which count only identities whose copies "
             "agree)"]
    for key in ("objects", "objects carrying nothing", "dashboards",
                "dashboards interaction driven", "widgets", "widgets classified",
                "widgets carrying nothing"):
        lines.append(f"  {key:34s} {report[key]:6d}")
    lines.append("  widgets carrying nothing, by reason")
    for code, count in sorted(report["widgets carrying nothing by reason"].items()):
        lines.append(f"    {code:34s} {count:6d}")
    lines.append("  widgets whose content is elsewhere, by reason")
    for code, count in sorted(report["widgets shown elsewhere by reason"].items()):
        lines.append(f"    {code:34s} {count:6d}")
    lines.append(f"    the two blocks above cover the {report['widgets classified'] - report['divergent widgets']} "
                 f"widgets whose copies agree on what they carry; the other "
                 f"{report['divergent widgets']} are in neither, and "
                 f"{report['widgets disputed as carrying nothing']} of those carry nothing "
                 "in at least one copy")
    lines.append("  objects carrying nothing, by reason")
    for code, count in sorted(report["objects carrying nothing by reason"].items()):
        lines.append(f"    {code:34s} {count:6d}")
    lines.append(f"    this block covers the {report['objects'] - report['divergent objects']} "
                 f"objects whose copies agree; the other {report['divergent objects']} are "
                 f"not in it, and {report['objects disputed as carrying nothing']} of those "
                 "carry nothing in at least one copy")
    lines.append("  how widgets come by their subject")
    for kind, count in sorted(report["widget subjects"].items()):
        lines.append(f"    {kind:34s} {count:6d}")
    lines.append(f"    this block covers the {report['widgets with a subject']} widgets whose "
                 f"copies agree on the subject; {report['divergent widget subjects']} "
                 "disagree and are not in it")
    lines.append("  where copies of one identity disagree")
    for key in ("objects with two names", "divergent objects", "divergent dashboards",
                "divergent widgets", "divergent widget subjects"):
        lines.append(f"    {key:34s} {report[key]:6d}")
    lines.append("occurrences (the same content counted once per export it appears in)")
    for key, count in sorted(report["occurrences"].items()):
        lines.append(f"  {key:36s} {count:6d}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", nargs="?", default=os.environ.get("VCFCF_MIGRATOR_CORPUS",
                                                                "corpus"),
                        help="corpus directory (default: the corpus setting)")
    parser.add_argument("--source-version", default=None,
                        help="declared source version for zips versions.json does not name")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--log", metavar="FILE", default=None,
                        help="write a run log to FILE (- for stderr)")
    parser.add_argument("--log-level", metavar="LEVEL", default=None,
                        choices=_runlog.LEVEL_NAMES,
                        help="how much of the run to log: " + ", ".join(_runlog.LEVEL_NAMES))
    args = parser.parse_args(argv)
    directory = Path(args.dir)
    if not directory.is_dir():
        print(f"corpus directory {directory} does not exist", file=sys.stderr)
        return 1
    log = _runlog.NULL
    if args.log:
        level, _from = _runlog.resolve_level(args.log_level)
        log = _runlog.open_log(args.log, level=level)
        _runlog.set_current(log)
        import vcfcf_core

        from vcfcf_migrator import __version__ as _tool_version

        log.header(["corpus_census.py"] + list(argv or sys.argv[1:]),
                   tool_version=f"{_tool_version} (tools/corpus_census.py)",
                   core_version=vcfcf_core.__version__, corpus_dir=directory,
                   corpus_from="command line")
    try:
        with log.phase("census", dir=str(directory)):
            report = walk(directory, args.source_version)
            _runlog.info("census.counted",
                         objects=report["objects"], dashboards=report["dashboards"],
                         widgets=report["widgets"])
    finally:
        log.finish(0, what="census")
        log.close()
        _runlog.set_current(None)
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report),
          end="" if not args.json else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
