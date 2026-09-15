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
        widgets = [w for w in doc.get("widgets", []) if isinstance(w, dict)]
        wiring = _preview.read_wiring(doc, widgets)
        dash = node.uuid or node.ident
        ids = [str(w.get("id") or f"index-{i}") for i, w in enumerate(widgets)]
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
        for wid in ids:
            state, code, subject = preview.widget_verdicts.get(wid, ("", "", "self"))
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
        return {
            "objects": len(self.object_names),
            "objects with two names": sum(1 for v in self.object_names.values() if len(v) > 1),
            "objects carrying nothing": sum(1 for v in object_empty.values() if v),
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
            "widgets carrying nothing by reason": dict(
                Counter(c for c in unfinished.values())),
            "widgets shown elsewhere by reason": dict(
                Counter(c for c in empty.values() if c not in UNFINISHED_CODES)),
            "widget subjects": dict(subjects),
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
    lines = ["distinct content across the corpus"]
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
    lines.append("  objects carrying nothing, by reason")
    for code, count in sorted(report["objects carrying nothing by reason"].items()):
        lines.append(f"    {code:34s} {count:6d}")
    lines.append("  how widgets come by their subject")
    for kind, count in sorted(report["widget subjects"].items()):
        lines.append(f"    {kind:34s} {count:6d}")
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
    args = parser.parse_args(argv)
    directory = Path(args.dir)
    if not directory.is_dir():
        print(f"corpus directory {directory} does not exist", file=sys.stderr)
        return 1
    report = walk(directory, args.source_version)
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report),
          end="" if not args.json else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
