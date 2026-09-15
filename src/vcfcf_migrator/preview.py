"""The preview: one HTML page per object, so an admin can recognise it.

The spec's reason for this milestone is that an admin picking content out of
an export has nothing to go on but a name and a uuid, and a name is not enough
to decide whether to carry a dashboard. So each kind gets the smallest thing
that makes it recognisable:

* a **dashboard** is laid out the way it lays itself out, widget by widget, on
  the same twelve-column grid the export declares, each frame showing its
  title, its type, and content appropriate to that type;
* a **view** shows the column headers it defines, with a few mock rows;
* a **super metric** shows its formula, with every reference resolved to the
  name of the object it points at;
* an **alert** states its symptom sets in words, with its recommendations;
* every other kind gets the facts its document carries, stated plainly.

Three rules hold across all of them.

**Names and labels come from the export.** Every title, column heading, metric
key and symptom name on the page is a string the document carried. Only the
values are made up, and they come from ``mockdata``, which derives them by
hash from those same strings: the same export previews identically on every
run, and two admins comparing notes see the same page.

**Every box says one of three things, and never nothing.** Each widget, and
each object, resolves to exactly one of:

1. here is the thing, drawn;
2. the export carries this, but this page does not lay out that type, named;
3. the export carries nothing here, with what is missing said plainly (no
   configuration at all, no metric, no column, no rule).

State 3 wins over state 2. An empty widget of a type this page does not draw
is *empty first*: "a Skittles widget, which this preview does not lay out" is
a fact about the tool, while "this widget carries no configuration at all" is
a fact about the admin's own content, shows nothing on the real dashboard
either, and is usually an unfinished leftover. The three states look
different at a glance, and the notes count the empties so an admin scanning a
fourteen-widget dashboard does not have to read every box.

Drawing a Geo widget as a bar chart would be worse than drawing nothing,
because the admin would believe it, which is why state 2 names the type
rather than guessing at it.

**No network.** The page is one file: inline CSS, inline SVG, no font, no
script, no image. It opens on a workstation with no route to anything.
"""
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import unquote
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from vcfcf_migrator import mockdata
from vcfcf_migrator import runlog
from vcfcf_migrator.wording import plural
from vcfcf_migrator.graph import Graph, Node

MOCK_ROWS = 5

# Every state-3 case this module can produce, by code. These are the gate:
# ``tests/test_preview.py`` asserts the fixture produces every one of them, so
# a new "carries nothing" branch fails the suite until something exercises it.
# The first version of the never-shows rule called 27 selectors broken, and
# nothing in the suite touched the classification at all.
WIDGET_EMPTY_CODES = (
    "widget-no-config",          # no config and no state blob either
    "widget-title-only",         # a title and nothing else
    "widget-no-view",            # a View widget naming no view
    "widget-no-metric",          # a scoreboard-family widget naming no metric
    "widget-no-pareto-metric",   # a pareto chart naming no metric
    "widget-no-health-metric",   # a health chart naming no metric
    "widget-no-heatmap-metric",  # a heatmap declaring no colour or size metric
    "widget-no-text",            # a text widget with no text and no file
    "widget-no-heading",         # a section divider with no heading
    "widget-never-shows",        # waits on a selection nothing provides
    "widget-view-no-columns",    # shows a view that declares no columns
)

# Not emptiness. Two things a widget can be that this page must not call
# empty, because in both cases the widget works and the gap is elsewhere.
WIDGET_ELSEWHERE_CODES = (
    # VCF Operations stores a ResourceList's or an AlertList's column layout
    # in ``states[].value``, a nested percent-encoded blob this page does not
    # decode. A widget with no ``config`` and a 4kB state blob is configured;
    # saying it carries nothing was simply false.
    "widget-state-not-read",
    # A view the export does not carry. An export declares ``type=CUSTOM`` and
    # carries custom content only, so a widget pointing at a view shipped in a
    # management pack points outside every export by construction: 99 of the
    # view uuids named this way across the corpus are pak content. Nothing in
    # an export distinguishes a built-in from one that is genuinely gone.
    "widget-view-not-carried",
    # A widget that drives others and has no content of its own to draw. It
    # is the dashboard's control, not an empty widget.
    "widget-drives-only",
)

OBJECT_EMPTY_CODES = (
    "dashboard-no-widgets",
    "view-no-columns",
    "view-no-attributes",
    "supermetric-no-formula",
    "alert-no-state",
    "alert-no-symptoms",
    "symptom-no-state",
    "symptom-no-condition",
    "recommendation-no-text",
    "report-no-sections",
    "group-no-rules",
    "rule-no-conditions",
)

# How a widget comes by the object it shows. Not emptiness: a selector and a
# context-driven widget both work, and the earlier rule called the first
# broken because it never asked whether the widget drives anything.
SUBJECT_KINDS = ("self", "fed", "selector", "from-outside", "never-shows")
DEFAULT_GRID_COLUMNS = 12
# A grid can be widened to fit a widget the dashboard places outside its own
# columns; past this it is a number no layout can mean, and the widget is
# clamped and named instead.
MAX_GRID_COLUMNS = 48

PREVIEW_CSS = """
.pv { --pv-bg:#1b1f24; --pv-panel:#23282f; --pv-panel2:#2a3038; --pv-line:#363d47;
  --pv-ink:#f2f4f7; --pv-ink2:#b7bfca; --pv-ink3:#7f8896; --pv-accent:#3987e5;
  --pv-warn:#e0a400; --pv-ok:#199e70; --pv-bad:#d95926;
  background:var(--pv-bg); color:var(--pv-ink); border-radius:6px; padding:14px 16px 18px;
  font:13px/1.45 system-ui,"Segoe UI",sans-serif; }
.pv * { box-sizing:border-box }
.pv h2.pv-title { font-size:17px; margin:0 0 2px; font-weight:600; color:var(--pv-ink);
  text-transform:none; letter-spacing:normal }
.pv .pv-sub { color:var(--pv-ink2); font-size:12px; margin:0 0 10px }
.pv .pv-sub code, .pv code { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:11px; color:var(--pv-ink3) }
.pv .pv-banner { background:var(--pv-panel2); border-left:3px solid var(--pv-warn); color:var(--pv-ink2);
  font-size:11px; padding:6px 10px; border-radius:3px; margin:0 0 12px }
.pv .pv-grid { display:grid; grid-template-columns:repeat(12,1fr);
  grid-auto-rows:minmax(26px,auto); gap:8px; align-items:start }
.pv .pv-w { background:var(--pv-panel); border:1px solid var(--pv-line); border-radius:4px;
  padding:8px 10px; overflow:hidden; min-height:64px }
.pv .pv-w > h3 { font-size:12px; font-weight:600; margin:0 0 6px; display:flex; gap:8px;
  align-items:baseline; flex-wrap:wrap }
.pv .pv-w > h3 .pv-type { margin-left:auto; font-weight:400; font-size:10px; color:var(--pv-ink3);
  text-transform:uppercase; letter-spacing:.04em; white-space:nowrap }
.pv .pv-flow { font-size:10px; font-weight:500; padding:1px 6px; border-radius:9px;
  white-space:nowrap }
.pv .pv-flow.driven { background:rgba(57,135,229,0.16); color:#8fbcf5 }
.pv .pv-flow.drives { background:rgba(25,158,112,0.16); color:#67c9a2 }
.pv .pv-w.is-receiver { border-left:3px solid var(--pv-accent) }
.pv .pv-w.is-provider { border-left:3px solid var(--pv-ok) }
.pv .pv-drivenby { font-size:10.5px; color:var(--pv-ink3); margin:0 0 6px }
.pv .pv-wiring { background:var(--pv-panel2); border-radius:4px; padding:8px 11px; margin:0 0 12px;
  font-size:11.5px; color:var(--pv-ink2) }
.pv .pv-wiring b { color:var(--pv-ink); font-weight:600 }
.pv .pv-wiring ul { margin:5px 0 0; padding-left:18px }
.pv .pv-wiring li { margin:1px 0 }
.pv .pv-wiring .arrow { color:var(--pv-accent); font-weight:600 }
.pv .pv-lbl { font-size:11px; color:var(--pv-ink2); margin-bottom:2px; }
.pv .pv-key { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:10px; color:var(--pv-ink3);
  word-break:break-all }
.pv table.pv-tbl { width:100%; table-layout:fixed; border-collapse:collapse; font-size:11.5px }
.pv table.pv-tbl th { text-align:left; color:var(--pv-ink3); font-weight:500; font-size:10.5px;
  padding:3px 6px; border-bottom:1px solid var(--pv-line); white-space:normal;
  overflow-wrap:anywhere }
.pv table.pv-tbl td { padding:3px 6px; border-bottom:1px solid var(--pv-line); color:var(--pv-ink2);
  overflow-wrap:anywhere }
.pv table.pv-tbl td.num { text-align:right; font-variant-numeric:tabular-nums }
.pv .pv-scroll { overflow-x:auto }
.pv .pv-tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); gap:6px }
.pv .pv-tile { background:var(--pv-panel2); border-radius:4px; padding:6px 8px }
.pv .pv-tile .v { font-size:19px; font-weight:600; font-variant-numeric:tabular-nums }
.pv .pv-tile .l { font-size:10.5px; color:var(--pv-ink2) }
.pv .pv-chart { width:100%; height:72px; display:block }
.pv .pv-heat { display:grid; grid-template-columns:repeat(8,1fr); gap:3px }
.pv .pv-heat i { display:block; padding-top:100%; border-radius:2px }
.pv .pv-placeholder { border:1px dashed var(--pv-line); border-radius:3px; color:var(--pv-ink3);
  font-size:11px; padding:10px; text-align:center }
.pv .pv-nothing { border:1px solid var(--pv-warn); border-left-width:3px; border-radius:3px;
  background:rgba(224,164,0,0.09); color:var(--pv-ink2); font-size:11.5px; padding:9px 11px }
.pv .pv-nothing b { color:var(--pv-warn); display:block; font-size:10px; text-transform:uppercase;
  letter-spacing:.06em; margin-bottom:3px }
.pv .pv-elsewhere { border:1px dashed var(--pv-accent); border-radius:3px;
  background:rgba(57,135,229,0.07); color:var(--pv-ink2); font-size:11.5px; padding:9px 11px }
.pv .pv-elsewhere b { color:var(--pv-accent); display:block; font-size:10px;
  text-transform:uppercase; letter-spacing:.06em; margin-bottom:3px }
.pv .pv-section { grid-column:1 / -1; border-bottom:1px solid var(--pv-line); color:var(--pv-ink2);
  font-size:12px; font-weight:600; padding:4px 2px; min-height:0 }
.pv .pv-facts { margin:0; display:grid; grid-template-columns:max-content 1fr; gap:3px 14px; font-size:12px }
.pv .pv-facts dt { color:var(--pv-ink3) }
.pv .pv-facts dd { margin:0; color:var(--pv-ink) }
.pv pre.pv-code { background:var(--pv-panel2); border:1px solid var(--pv-line); border-radius:4px;
  padding:9px 11px; overflow-x:auto; font-family:ui-monospace,Menlo,Consolas,monospace;
  font-size:11.5px; color:var(--pv-ink); white-space:pre-wrap; word-break:break-word; margin:0 0 10px }
.pv pre.pv-code .ref { color:var(--pv-accent) }
.pv pre.pv-code .unres { color:var(--pv-warn) }
.pv h4.pv-h { font-size:12px; margin:14px 0 6px; color:var(--pv-ink2); text-transform:uppercase;
  letter-spacing:.05em; font-weight:600 }
.pv ul.pv-logic { list-style:none; margin:0; padding-left:0 }
.pv ul.pv-logic ul { list-style:none; margin:2px 0 2px 0; padding-left:16px;
  border-left:2px solid var(--pv-line) }
.pv ul.pv-logic li { padding:2px 0; color:var(--pv-ink) }
.pv ul.pv-logic .op { color:var(--pv-ink3); font-size:11px; text-transform:uppercase; letter-spacing:.05em }
.pv .pv-miss { color:var(--pv-warn) }
.pv .pv-notes { margin:14px 0 0; padding:8px 10px; background:var(--pv-panel2); border-radius:4px;
  color:var(--pv-ink2); font-size:11px }
.pv .pv-notes ul { margin:4px 0 0; padding-left:18px }
.pv .pv-text { color:var(--pv-ink2); font-size:12px; white-space:pre-wrap;
  max-height:100%; overflow:auto }
@media (max-width: 720px) {
  .pv .pv-grid { display:block }
  .pv .pv-grid > .pv-w { margin-bottom:8px }
}
"""

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ margin:0; padding:18px; background:#11151a; font:13px/1.45 system-ui,"Segoe UI",sans-serif; }}
{css}
</style>
</head>
<body>
{body}
</body>
</html>
"""


class PreviewError(Exception):
    """The object's document cannot be previewed (it is not in the export, or
    its document does not parse)."""


@dataclass
class Preview:  # noqa: D101
    """One rendered preview, with what the renderer met on the way."""
    node: Node
    title: str
    subtitle: str
    body: str
    widget_types: Dict[str, int] = field(default_factory=dict)
    unhandled_types: Dict[str, int] = field(default_factory=dict)
    # State 3: (widget title, widget type, what is missing) per empty widget,
    # and the same sentence for an object whose whole document shows nothing.
    empty_widgets: List[Tuple[str, str, str]] = field(default_factory=list)
    empty_codes: List[str] = field(default_factory=list)
    empty_reason: str = ""
    empty_code: str = ""
    subjects: Dict[str, int] = field(default_factory=dict)
    # Interaction wiring: how many widgets are driven by another widget's
    # selection, and how many of those have nothing feeding them.
    receivers: int = 0
    providers: int = 0
    # Widgets whose content is real but lives somewhere this page cannot
    # follow: (title, type, code, sentence).
    elsewhere: List[Tuple[str, str, str, str]] = field(default_factory=list)
    # widget id -> (state, code, subject), where state is "empty",
    # "elsewhere" or "". The page renders from this; the census counts from
    # it, so neither can drift from the other.
    widget_verdicts: Dict[str, Tuple[str, str, str]] = field(default_factory=dict)
    orphan_receivers: int = 0
    context_driven: int = 0
    selectors: int = 0
    notes: List[str] = field(default_factory=list)


def _mark_object_empty(preview: Preview, code: str, reason: str) -> str:
    """Record that this whole object carries nothing to show, and say it."""
    assert code in OBJECT_EMPTY_CODES, code
    if not preview.empty_reason:
        preview.empty_reason, preview.empty_code = reason, code
    return nothing_here(reason)


def _mark_widget_empty(preview: Optional[Preview], title: str, widget_type: str,
                       code: str, reason: str) -> None:
    assert code in WIDGET_EMPTY_CODES, code
    if preview is None:
        return
    preview.empty_widgets.append((title or "(untitled widget)",
                                  widget_type or "no type", reason))
    preview.empty_codes.append(code)


def _mark_widget_elsewhere(preview: Optional[Preview], title: str, widget_type: str,
                           code: str, reason: str) -> str:
    """The widget is configured, or points at something real; this page just
    cannot follow it. Counted apart from the empties, and worded as what it
    is rather than as a fault in the content."""
    assert code in WIDGET_ELSEWHERE_CODES, code
    if preview is not None:
        preview.elsewhere.append((title or "(untitled widget)",
                                  widget_type or "no type", code, reason))
    return (f"<div class='pv-elsewhere'><b>not shown here</b>{_e(reason)}</div>")


def widget_state_blob(widget: dict) -> str:
    """The widget's stored state, where it actually carries something.

    VCF Operations keeps a ResourceList's and an AlertList's column layout
    here rather than in ``config``. The value is a nested percent-encoded
    ExtJS state object whose grammar the factory captured in
    ``knowledge/context/api-surface/resourcelist_column_state_wire_format.md``
    (RULE-001): one unquoting gives ``o:columns=a:...``, where ``o:`` opens an
    object, ``a:`` an array. This page does not decode past that, but it does
    have to tell a layout from an empty one: three corpus widgets carry a
    state whose whole value is ``o%3A``, an object with nothing in it, and
    telling their admin they were configured was as wrong as telling the
    others they were not.

    Returns the concatenated values that carry something past the marker, so
    an empty state reads as no state at all.
    """
    states = widget.get("states")
    if not isinstance(states, list):
        return ""
    kept = []
    for entry in states:
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("value") or "")
        decoded = unquote(raw).strip()
        # "o:" alone is an object with no fields. Anything past the marker is
        # a field, which is a layout.
        while decoded[:2] in ("o:", "a:"):
            decoded = decoded[2:].strip()
        if decoded:
            kept.append(raw)
    return "".join(kept)


# ---------------------------------------------------------------------------
# Getting at the document
# ---------------------------------------------------------------------------

def raw_document(graph: Graph, node: Node) -> bytes:
    """The object's document, byte for byte as the export holds it.

    The graph keeps a node's member and its index inside that member rather
    than its bytes, so this walks back to the container. Matching on member,
    index *and* kind matters: one member can hold three kinds at once (an
    ``alertContent`` document carries symptoms, alerts and recommendations),
    and their indexes are per kind.
    """
    for container in graph.containers:
        if container.member != node.member:
            continue
        for entry in container.entries():
            if (entry.kind == node.kind and entry.index == node.index
                    and entry.ident == node.ident and entry.owner == node.owner):
                return entry.raw
    runlog.warn("document.absent", kind=node.kind, uuid=node.uuid or "", name=node.name,
                member=node.member, index=node.index, owner=node.owner or None,
                reason=runlog.prose("the graph places this object in a member that holds no document "
                       "matching its kind, index and identifier"))
    raise PreviewError(f"{node.label()} has no document in {node.member}")


def _json_doc(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError as e:
        runlog.warn("document.unreadable", format="json", detail=str(e),
                    document_bytes=len(raw))
        raise PreviewError(f"the document is not readable JSON: {e}") from e


def _xml_doc(raw: bytes) -> ET.Element:
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        runlog.warn("document.unreadable", format="xml", detail=str(e),
                    document_bytes=len(raw))
        raise PreviewError(f"the document is not readable XML: {e}") from e


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def nothing_here(detail: str) -> str:
    """State 3: the document is here and carries nothing to show.

    The wording is about the content, never about the tool. "This preview does
    not lay out X" is a limitation of this page; "carries no configuration at
    all" is something the admin can act on, because a widget with no
    configuration shows nothing on the real dashboard either.
    """
    return (f"<div class='pv-nothing'><b>nothing to show</b>{_e(detail)}</div>")


def _text(el: Optional[ET.Element], path: str, default: str = "") -> str:
    if el is None:
        return default
    found = el.findtext(path)
    return (found or default).strip() or default


# ---------------------------------------------------------------------------
# Resolving a reference to a name
# ---------------------------------------------------------------------------

def _find_node(graph: Graph, kind: str, ident: str) -> Optional[Node]:
    """The node an identifier names, by uuid, export ident or display name.

    Returns one node. Where an identifier answers to several (a dashboard uuid
    under two owners, two super metrics sharing a display name) the first in
    the graph's stable order wins, and the caller says so where it matters;
    the *tree* is where ambiguity is reported in full, and repeating all of it
    inside a formula would bury the formula.
    """
    if not ident:
        return None
    lowered = str(ident).lower()
    for node in graph.ordered():
        if node.kind != kind:
            continue
        if lowered in (node.ident.lower(), node.uuid.lower(), node.name.lower()):
            return node
    return None


def _named(graph: Graph, kind: str, ident: str, missing_text: str = "") -> str:
    """HTML for a reference: the object's name when the export carries it,
    otherwise the identifier and why there is no name for it."""
    node = _find_node(graph, kind, ident)
    if node is not None:
        return f"{_e(node.name)} <code>{_e(ident)}</code>"
    tail = missing_text or "not in this export; the target instance must already have it"
    return f"<code>{_e(ident)}</code> <span class='pv-miss'>({_e(tail)})</span>"


# ---------------------------------------------------------------------------
# Views: the columns, and the mock rows under them
# ---------------------------------------------------------------------------

@dataclass
class Column:
    label: str
    key: str
    is_string: bool
    is_property: bool
    unit: str


def view_columns(root: ET.Element) -> List[Column]:
    """The columns a ViewDef declares, in the order it declares them.

    They live in an ``attributes-selector`` control, as a list of Value blocks
    whose Property children carry the key, the display name and the unit. A
    view can carry more than one such control, so all of them are read.
    """
    out: List[Column] = []
    for control in root.findall("./Controls/Control"):
        if control.get("type") != "attributes-selector":
            continue
        for prop in control.findall("./Property"):
            if prop.get("name") != "attributeInfos":
                continue
            for item in prop.findall("./List/Item"):
                value = item.find("Value")
                if value is None:
                    continue
                fields = {p.get("name"): (p.get("value") or "")
                          for p in value.findall("Property")}
                key = fields.get("attributeKey", "")
                label = fields.get("displayName") or key or "(unnamed column)"
                out.append(Column(
                    label=label, key=key,
                    is_string=declared_flag(fields.get("isStringAttribute")) is True,
                    is_property=declared_flag(fields.get("isProperty")) is True,
                    unit=fields.get("preferredUnitId", "")))
    return out


# ---------------------------------------------------------------------------
# What a person reads, and what hides behind it
# ---------------------------------------------------------------------------

# ``Super Metric|sm_6dfea6a6-e633-4b32-b438-8975e3dc06fa`` is how a view names
# a super metric column. Across two corpus exports, 54 columns carry a key of
# this shape under a perfectly good display name. Printing it tells a reader
# nothing they can act on and buries the name it sits under.
# The trailing group is an instanced metric: ``sm_<uuid>:vmhba1``. Anchoring
# the pattern at the uuid let those through untouched, printing the uuid.
_SM_KEY = re.compile(
    r"(?i)^\s*super\s*metric\s*\|\s*sm[_-]?([0-9a-f][0-9a-f-]{7,})(?::(.+))?\s*$")


def metric_text(graph: Optional[Graph], label: str, key: str) -> Tuple[str, str]:
    """``(text, title)``: what to show, and the raw key to put in a tooltip.

    VCF Operations shows an admin the display name, so a preview that claims
    to look like the dashboard has to do the same. The key is not thrown away,
    it moves to a tooltip, because the one reader who wants it wants it badly
    and everyone else was reading around it.

    When there is no usable label, a super metric key resolves to the metric's
    name from this export rather than being printed as a uuid. Only when
    nothing can be resolved does the key itself become the text, which is
    still better than showing nothing at all.
    """
    label = str(label or "").strip()
    key = str(key or "").strip()
    if label and label != key:
        return label, key
    match = _SM_KEY.match(key)
    instance = f" ({match.group(2)})" if match and match.group(2) else ""
    if match and graph is not None:
        node = _find_node(graph, "supermetric", match.group(1))
        if node is not None and node.name:
            return node.name + instance, key
    if match:
        return "super metric (not in this export)" + instance, key
    return label or key, key


def titled(text: str, title: str = "", cls: str = "pv-lbl") -> str:
    """A label, with its raw key reachable but not in the way."""
    attr = f" title='{_e(title)}'" if title and title != text else ""
    return f"<div class='{cls}'{attr}>{_e(text)}</div>"


def _tile_label(label: str, key: str, ctx: dict) -> str:
    """A scoreboard tile's caption.

    The caption used to fall back to the metric key, so a super metric tile
    read as "Super Metric|sm_<uuid>" where the dashboard shows its name.
    """
    text, title = metric_text(ctx.get("graph"), label, key)
    attr = f" title='{_e(title)}'" if title and title != text else ""
    return f"<div class='l'{attr}>{_e(text)}</div>"


def _metric_code(key: str, graph: Optional[Graph]) -> str:
    """A metric named inside a condition, as a person would recognise it."""
    text, title = metric_text(graph, "", key)
    attr = f" title='{_e(title)}'" if title and title != text else ""
    return f"<code{attr}>{_e(text)}</code>"


def _cell(column: Column, row: int) -> Tuple[str, bool]:
    """One mock cell: its text, and whether it is numeric (right aligned)."""
    if column.is_string:
        return mockdata.string_value(column.key, row, column.label), False
    return mockdata.metric_value(column.key, column.unit, row, column.label), True


def _column_th(column: Column, graph: Optional[Graph], collides: bool) -> str:
    """One header cell: the display name, with the raw key on hover.

    The key used to be printed on a second line under every heading. For a
    super metric that line is "Super Metric|sm_<uuid>", which is noise sitting
    on top of the name a person recognises, and it is what the dashboard this
    preview claims to imitate does not show.

    Except when two columns in the same table carry the same display name, and
    12 views across the corpus do. VCF Operations renders those identically
    too, but this is a tool for deciding what to carry across, and a reviewer
    looking at two columns called "Service Name" needs to see that one is
    Credential Manager and the other is SQL Server. Where the name alone
    cannot tell them apart, the key comes back for those columns only.
    """
    text, key = metric_text(graph, column.label, column.key)
    attr = f" title='{_e(key)}'" if key and key != text else ""
    tail = (f"<br><span class='pv-key'>{_e(key)}</span>"
            if collides and key and key != text else "")
    return f"<th{attr}>{_e(text)}{tail}</th>"


def _columns_table(columns: Sequence[Column], rows: int = MOCK_ROWS,
                   graph: Optional[Graph] = None) -> str:
    """The view's columns as a table with mock rows under them."""
    if not columns:
        return nothing_here("this view declares no columns, so it shows an empty table "
                            "wherever it is used")
    texts = [metric_text(graph, c.label, c.key)[0] for c in columns]
    seen = {}
    for one in texts:
        seen[one] = seen.get(one, 0) + 1
    head = "".join(
        _column_th(c, graph, seen.get(text, 0) > 1)
        for c, text in zip(columns, texts))
    body = []
    for row in range(rows):
        cells = []
        for column in columns:
            text, numeric = _cell(column, row)
            cells.append(f"<td class='num'>{_e(text)}</td>" if numeric
                         else f"<td>{_e(text)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    # Fixed layout and wrapping headers rather than one nowrap row: a column
    # header wider than the widget used to be cut mid-word ("vCPU:pCPU Rat"),
    # which reads as a broken tool rather than as a narrow widget. The columns
    # now share the width and wrap; pv-scroll stays as the backstop.
    return ("<div class='pv-scroll'><table class='pv-tbl'><thead><tr>" + head
            + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>")


# ---------------------------------------------------------------------------
# Dashboard widgets
# ---------------------------------------------------------------------------

def _cfg_metrics(cfg: dict) -> List[Tuple[str, str, str]]:
    """(label, key, unit) for every metric a widget's ``metric`` block names.

    Two shapes, both in the corpus: the scoreboard family nests
    ``resourceKindMetrics`` / ``resourceMetrics`` lists under ``metric``, and
    the pareto family puts one ``{name, metricKey}`` object there.
    """
    out: List[Tuple[str, str, str]] = []
    metric = cfg.get("metric")
    blocks: List[dict] = []
    if isinstance(metric, dict):
        for key in ("resourceKindMetrics", "resourceMetrics"):
            value = metric.get(key)
            if isinstance(value, list):
                blocks.extend(b for b in value if isinstance(b, dict))
        if not blocks and ("metricKey" in metric or "name" in metric):
            blocks.append(metric)
    elif isinstance(metric, list):
        blocks.extend(b for b in metric if isinstance(b, dict))
    for block in blocks:
        key = str(block.get("metricKey") or block.get("key") or "")
        label = str(block.get("metricName") or block.get("name") or key or "(unnamed metric)")
        unit = block.get("metricUnitId") or block.get("defUnit") or ""
        out.append((label, key, "" if unit in (-1, "-1", None) else str(unit)))
    if not out:
        metrics = cfg.get("metrics")
        if isinstance(metrics, list):
            for block in metrics:
                if isinstance(block, dict) and block.get("metricKey"):
                    key = str(block["metricKey"])
                    out.append((str(block.get("metricName") or key), key, ""))
    return out


def _cfg_unit(cfg: dict) -> str:
    unit = cfg.get("metricUnit")
    if isinstance(unit, dict):
        value = unit.get("metricUnitId")
        if value not in (None, -1, "-1"):
            return str(value)
        return str(unit.get("metricUnitName") or "")
    return str(unit or "")


def _sparkline(key: str, unit: str = "", color: str = "var(--pv-accent)") -> str:
    """An inline SVG polyline over a mock series. No library, no remote asset."""
    points = mockdata.series(key or "series", points=24, unit=unit)
    low, high = min(points), max(points)
    span = (high - low) or 1.0
    coords = " ".join(
        f"{i * (300 / (len(points) - 1)):.1f},{60 - ((v - low) / span) * 52:.1f}"
        for i, v in enumerate(points))
    return (f"<svg class='pv-chart' viewBox='0 0 300 64' preserveAspectRatio='none' "
            f"role='img' aria-label='mock trend'>"
            f"<line x1='0' y1='62' x2='300' y2='62' stroke='var(--pv-line)'/>"
            f"<polyline points='{coords}' fill='none' stroke='{color}' stroke-width='2'/></svg>")


def _bars(key: str, count: int, unit: str = "") -> str:
    values = mockdata.ranked(key or "bars", count, unit)
    high = max(values) or 1.0
    width = 300 / max(count, 1)
    rects = []
    for i, value in enumerate(values):
        height = max(2.0, (value / high) * 52)
        rects.append(f"<rect x='{i * width + 1.5:.1f}' y='{60 - height:.1f}' "
                     f"width='{width - 3:.1f}' height='{height:.1f}' fill='var(--pv-accent)'/>")
    return ("<svg class='pv-chart' viewBox='0 0 300 64' preserveAspectRatio='none' "
            "role='img' aria-label='mock ranking'>"
            "<line x1='0' y1='62' x2='300' y2='62' stroke='var(--pv-line)'/>"
            + "".join(rects) + "</svg>")


def _tiles(metrics: Sequence[Tuple[str, str, str]], ctx: dict) -> str:
    if not metrics:
        return _record_empty(ctx, "widget-no-metric",
                             "this widget names no metric, so it shows nothing until "
                             "someone picks one")
    cells = []
    for label, key, unit in metrics[:8]:
        value = mockdata.metric_value(key or label, unit, 0, label)
        # One caption, as the dashboard shows it. This used to carry a
        # second line with the raw key; routing that line through the same
        # resolver made it print the metric's name a second time, larger than
        # the caption above it, on 61 tiles across the corpus.
        cells.append(f"<div class='pv-tile'><div class='v'>{_e(value)}</div>"
                     + _tile_label(label, key, ctx)
                     + "</div>")
    return "<div class='pv-tiles'>" + "".join(cells) + "</div>"


def _widget_view(cfg: dict, ctx: dict) -> str:
    graph: Graph = ctx["graph"]
    view_id = str(cfg.get("viewDefinitionId") or "")
    node = _find_node(graph, "view", view_id) if view_id else None
    if node is None:
        # Not emptiness. An export declares type=CUSTOM and carries custom
        # content only, so a widget pointing at a view that ships inside a
        # management pack points outside every export by construction; 99 of
        # the view uuids named this way across the corpus are pak content.
        # Nothing in an export tells a built-in apart from one that is
        # genuinely gone, and this page must not pretend otherwise.
        return _mark_widget_elsewhere(
            ctx.get("preview"), ctx.get("title") or "",
            str((ctx.get("widget") or {}).get("type") or ""),
            "widget-view-not-carried",
            f"this widget shows view {view_id or '(none named)'}, which this export does "
            "not carry: an export holds custom content only, so the view may ship with a "
            "management pack or with the product. It will show whatever the target already "
            "has, and an export cannot tell the two apart")
    try:
        root = _xml_doc(raw_document(graph, node))
    except PreviewError as e:
        runlog.warn("swallowed.widget_view_unreadable", kind=node.kind,
                    uuid=node.uuid or "", name=node.name, member=node.member,
                    detail=str(e),
                    reason=runlog.prose("the view this widget shows has a document the page cannot "
                           "read; the widget draws a placeholder rather than failing the "
                           "whole preview"))
        return f"<div class='pv-placeholder'>{_e(str(e))}</div>"
    columns = view_columns(root)
    presentation = (root.find("Presentation").get("type")
                    if root.find("Presentation") is not None else "")
    head = f"<div class='pv-key'>{_e(node.name)} ({_e(presentation or 'view')})</div>"
    if not columns:
        return head + _record_empty(
            ctx, "widget-view-no-columns",
            f"the view this widget shows, {node.name}, declares no columns, so the widget "
            "shows an empty table")
    if presentation and presentation != "list":
        return head + _non_list_view(presentation, columns, ctx.get("graph"))
    return head + _columns_table(columns[:6], rows=3, graph=ctx.get("graph"))


def _non_list_view(presentation: str, columns: Sequence[Column],
                   graph: Optional[Graph] = None) -> str:  # noqa: D401
    """A view whose presentation is not a list says what it is.

    Drawing a donut for a distribution view would be drawing buckets the
    export defines and this tool does not read, which is exactly the kind of
    plausible-looking wrong the preview is supposed to avoid.
    """
    if not columns:
        return nothing_here(f"this {presentation} view declares no attributes, so it has "
                            "nothing to chart wherever it is used")
    # A column with no display name falls back to its key here too, so this
    # states attribute names as uuids unless it resolves them like everywhere
    # else. The fixture had no unlabelled column, so nothing caught it.
    attrs = ", ".join(_e(metric_text(graph, c.label, c.key)[0]) for c in columns[:6])
    return (f"<div class='pv-placeholder'>a <b>{_e(presentation)}</b> view. This preview "
            f"lays out list views only, so its shape is stated rather than drawn.<br>"
            f"attributes: {attrs}</div>")


def _widget_scoreboard(cfg: dict, ctx: dict) -> str:
    return _tiles(_cfg_metrics(cfg), ctx)


def _widget_metricchart(cfg: dict, ctx: dict) -> str:
    metrics = _cfg_metrics(cfg)
    if not metrics:
        return _sparkline(ctx["seed"])
    parts = []
    for label, key, unit in metrics[:3]:
        parts.append(titled(*metric_text(ctx.get("graph"), label, key))
                     + _sparkline(key or label, unit))
    return "".join(parts)


def _widget_pareto(cfg: dict, ctx: dict) -> str:
    metrics = _cfg_metrics(cfg)
    label, key, unit = metrics[0] if metrics else (
        str(cfg.get("metricName") or "(no metric named)"), "", "")
    unit = unit or _cfg_unit(cfg)
    bars = cfg.get("barsCount")
    count = bars if isinstance(bars, int) and 0 < bars <= 40 else 10
    return (titled(*metric_text(ctx.get("graph"), label, key))
            + _bars(key or label, count, unit))


def metric_ref(value) -> Tuple[str, str]:
    """``(label, key)`` for a metric reference, however the export writes it.

    A heatmap names what it colours and sizes by in two shapes: the flat string
    ``"cpu|usage_average"``, and the pair ``{"metricKey": "...", "value":
    "CPU Contention %"}``, which is what all 41 heatmaps in the corpus carry.
    Reading the second with ``str()`` printed the dict itself onto the page,
    braces and quotes and all, under every corpus heatmap.
    """
    if isinstance(value, dict):
        key = str(value.get("metricKey") or value.get("key") or "")
        label = str(value.get("value") or value.get("metricName")
                    or value.get("name") or "")
        return label, key
    return "", str(value or "")


def heatmap_metrics(cfg: dict) -> List[Tuple[str, str]]:
    """What a heatmap colours and sizes by: ``(label, key)`` for each, skipping
    the ones the export leaves empty. The renderer and the emptiness rule both
    read it, so neither can call a heatmap configured that the other draws
    blank."""
    configs = cfg.get("configs")
    out: List[Tuple[str, str]] = []
    if isinstance(configs, list) and configs and isinstance(configs[0], dict):
        first = configs[0]
        for field in ("colorBy", "sizeBy"):
            label, key = metric_ref(first.get(field))
            if label or key:
                out.append((label, key))
    return out


# VCF Operations' heat colours, green at the good end and red at the bad end.
# Not invented here and not the page's own chrome: these are the hex values the
# corpus's own heatmaps carry in ``config.configs[].color.thresholds.colors``
# (the shape documented in the factory's
# knowledge/context/api-surface/widget_types_survey.md, "Heatmap"), and they are
# the product's badge scale. The page's blue accent is chart ink and says
# nothing about a value, so it never appears in a heat scale: a VCF Operations
# heatmap runs red to green and never shows blue.
HEAT_GOOD = "#74B43B"    # green, the good end
HEAT_FAIR = "#ECC33E"    # yellow
HEAT_POOR = "#E07720"    # orange
HEAT_BAD = "#DE3F30"     # red, the bad end
HEAT_RAMP = (HEAT_GOOD, HEAT_GOOD, HEAT_FAIR, HEAT_POOR, HEAT_BAD)
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def heatmap_palette(cfg: dict) -> Tuple[str, ...]:
    """The colours to draw a heatmap in: the widget's own, where the export
    carries them.

    A heatmap declares its scale, thresholds and colours both, and the scale
    runs either way round depending on the metric (high CPU contention is bad,
    high free capacity is good). Twenty of the corpus's heatmap layers carry a
    green-to-red list and several carry the reverse, so taking the widget's own
    list keeps each one pointing the way its author meant. Anything not a plain
    hex colour is ignored, and a widget that declares none gets the product
    ramp, good to bad.
    """
    configs = cfg.get("configs")
    if isinstance(configs, list):
        for block in configs:
            if not isinstance(block, dict):
                continue
            colour = block.get("color")
            thresholds = colour.get("thresholds") if isinstance(colour, dict) else None
            colours = thresholds.get("colors") if isinstance(thresholds, dict) else None
            kept = tuple(c for c in colours or [] if isinstance(c, str) and _HEX.match(c))
            if kept:
                return kept
    return HEAT_RAMP


def _widget_heatmap(cfg: dict, ctx: dict) -> str:
    metrics = heatmap_metrics(cfg)
    label, key = metrics[0] if metrics else ("", "")
    cells = []
    palette = heatmap_palette(cfg)
    for i in range(24):
        colour = mockdata.pick(palette, "heat", ctx["seed"], i)
        cells.append(f"<i style='background:{colour}'></i>")
    text, title = metric_text(ctx.get("graph"), label, key)
    head = titled(text, title) if text else ""
    return head + "<div class='pv-heat'>" + "".join(cells) + "</div>"


def _widget_propertylist(cfg: dict, ctx: dict) -> str:
    metrics = _cfg_metrics(cfg)
    if not metrics:
        return _record_empty(ctx, "widget-no-metric",
                             "this property list names no property, so it shows nothing "
                             "until someone picks one")
    rows = []
    for label, key, unit in metrics[:8]:
        value = (mockdata.string_value(key or label, 0, label) if not unit
                 else mockdata.metric_value(key or label, unit, 0, label))
        _text, _key = metric_text(ctx.get("graph"), label, key)
        rows.append(f"<tr><td title='{_e(_key)}'>{_e(_text)}</td>"
                    f"<td>{_e(value)}</td></tr>")
    return "<table class='pv-tbl'><tbody>" + "".join(rows) + "</tbody></table>"


def _widget_alertlist(cfg: dict, ctx: dict) -> str:
    """An alert list with the columns VCF Operations gives one, and mock rows.

    The column headings here are the widget's own, not something the export
    carries: an alert list has no per-instance column definition the way a
    view does. They are labelled as the widget's standard columns so nobody
    reads them as a definition out of the document.
    """
    graph: Graph = ctx["graph"]
    named = cfg.get("alertDefinitions")
    names: List[str] = []
    if isinstance(named, list):
        for ident in named[:MOCK_ROWS]:
            node = _find_node(graph, "alert", str(ident))
            names.append(node.name if node is not None else str(ident))
    rows = []
    severities = ("critical", "immediate", "warning", "info")
    for row in range(3):
        name = names[row] if row < len(names) else mockdata.sample_name("alert", row)
        rows.append(
            "<tr><td>" + _e(mockdata.pick(severities, ctx["seed"], "sev", row)) + "</td>"
            "<td>" + _e(name) + "</td>"
            "<td>" + _e(mockdata.object_name(ctx["seed"], row)) + "</td></tr>")
    return ("<table class='pv-tbl'><thead><tr><th>criticality</th><th>alert</th>"
            "<th>object</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            "<div class='pv-key'>standard alert list columns; the export defines none</div>")


def _widget_resourcelist(cfg: dict, ctx: dict) -> str:
    extra = cfg.get("additionalColumns")
    labels = []
    if isinstance(extra, list):
        for block in extra[:4]:
            if isinstance(block, dict):
                labels.append(str(block.get("name") or block.get("metricKey") or ""))
            elif isinstance(block, str):
                labels.append(block)
    labels = [label for label in labels if label]
    head = "<th>object</th>" + "".join(f"<th>{_e(label)}</th>" for label in labels)
    rows = []
    for row in range(3):
        cells = [f"<td>{_e(mockdata.object_name(ctx['seed'], row))}</td>"]
        for label in labels:
            cells.append(f"<td class='num'>{_e(mockdata.metric_value(label, '', row))}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return ("<table class='pv-tbl'><thead><tr>" + head + "</tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def text_widget_content(cfg: dict) -> Tuple[str, Optional[bool]]:
    """A text widget's words, and whether the export says they are markup.

    **``viewModeHTML`` is a flag, not the text.** Every one of the 25
    TextDisplay widgets in the corpus carries it as the boolean ``True``, with
    the words themselves in ``editorData`` (682, 671, 808, 382, 139, 262, 364,
    631 and 715 characters of them, and seven widgets where it is empty). The
    first version of this read ``viewModeHTML or editorData``, so the flag won
    the ``or`` on all 25 and every text widget on every dashboard rendered as
    the single word ``True``, including the one in the README's own screenshot.
    That is the same mistake ``instanced="false"`` was: a field's truthiness
    taken for its content, which is what ``declared_flag`` exists to stop.

    So the content comes from ``editorData``, and the flag says how to read it.
    A string in ``viewModeHTML`` is still accepted as content, because a flag
    and a body sharing one key is exactly the kind of shape an export turns out
    to write two ways, and nothing here may assume the corpus has shown all of
    them.

    Returns the raw content and the flag (``None`` where the export does not
    say). One function, because the renderer and the emptiness rule have to
    agree about what "carries no text" means.
    """
    raw = cfg.get("editorData")
    if isinstance(raw, dict):
        raw = json.dumps(raw)
    if not isinstance(raw, str) or not raw.strip():
        other = cfg.get("viewModeHTML")
        raw = other if isinstance(other, str) else ""
    return str(raw or ""), declared_flag(cfg.get("viewModeHTML"), key="viewModeHTML")


def text_widget_words(cfg: dict) -> str:
    """The readable words of a text widget, however the export wrote them.

    The page never injects an export's markup: the preview is a local file an
    admin opens, and pasting a document's markup into it would let an export
    decide what the admin's browser runs. So markup has its tags stripped and
    the result is escaped by the caller. Text the export says is *not* markup
    keeps its own characters, since stripping there would eat any ``<`` the
    admin wrote on purpose.
    """
    raw, is_html = text_widget_content(cfg)
    if is_html is False and "<" not in raw:
        return " ".join(raw.split())
    return _strip_tags(raw)


def _widget_text(cfg: dict, ctx: dict) -> str:
    """The widget's own text, shown as text."""
    text = text_widget_words(cfg)
    if not text:
        location = cfg.get("locationUrl") or cfg.get("locationFile") or ""
        if location:
            return (f"<div class='pv-placeholder'>text widget sourced from "
                    f"<span class='pv-key'>{_e(location)}</span>, which this preview does "
                    "not fetch</div>")
        return _record_empty(ctx, "widget-no-text",
                             "this text widget carries no text and points at no file")
    # Long text is cut at a length the widget can show, and says so: a
    # paragraph ending mid-word with nothing to explain it reads as a fault in
    # the tool rather than as a widget with more text in it than fits.
    shown = text[:600]
    if len(text) > len(shown):
        # Back to the last word break, unless there is not one: text with no
        # spaces in its first 600 characters (one long token, or a language
        # that does not space its words) would otherwise lose the lot.
        head = shown.rsplit(" ", 1)[0] if " " in shown else shown
        shown = head + f" ... ({len(text)} characters in all)"
    return f"<div class='pv-text'>{_e(shown)}</div>"


class _TextOnly(HTMLParser):
    """The text of a markup fragment, entities decoded, tags dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):  # a tag break is a word break
        self.parts.append(" ")

    def handle_endtag(self, tag):
        self.parts.append(" ")


def _strip_tags(markup: str) -> str:
    """The readable text of a widget's markup.

    A parser rather than a depth counter over ``<`` and ``>``: a ``>`` inside
    an attribute value ends a tag early for a counter, which leaks attribute
    text into what the admin reads as the widget's words, and a counter also
    leaves ``&amp;`` on screen as five characters. Escaping is unaffected
    either way; this is about what the text says.
    """
    parser = _TextOnly()
    try:
        parser.feed(markup)
        parser.close()
    except Exception as e:  # noqa: BLE001 - a broken fragment still has to render
        # The page stays quiet about this on purpose: a text widget whose
        # markup does not parse still has to render, and an admin looking at a
        # dashboard preview cannot act on a parser error. Quiet in the output
        # is not quiet in the log, which is the whole point of the log.
        runlog.warn("swallowed.markup_unparsed", failure=type(e).__name__,
                    detail=str(e), markup_bytes=len(markup),
                    reason=runlog.prose("the text widget's markup did not parse, so its words are shown "
                           "with the tags stripped by hand; nothing is said on the page "
                           "because the page cannot be acted on"))
        return _tidy(" ".join(markup.split()))
    return _tidy(" ".join("".join(parser.parts).split()))


def _tidy(text: str) -> str:
    """A tag break is a word break, which puts a space before the full stop of
    ``<strong>after HA</strong>.`` Closing punctuation keeps its place."""
    return re.sub(r"\s+([,.;:!?%)\]])", r"\1", text)


def _widget_healthchart(cfg: dict, ctx: dict) -> str:
    label = str(cfg.get("metricName") or cfg.get("metricLabel") or "")
    key = str(cfg.get("metricKey") or "")
    unit = _cfg_unit(cfg)
    head = (titled(*metric_text(ctx.get("graph"), label, key))
            if (label or key) else "")
    # Chart ink, not a verdict. This used to draw in the page's green, which
    # is the product's "healthy" colour, over a value this page invented: a
    # health chart cannot claim health it has not read.
    return head + _sparkline(key or label or ctx["seed"], unit)


def _widget_section(cfg: dict, ctx: dict) -> str:
    return ""


WIDGET_RENDERERS = {
    "View": _widget_view,
    "Scoreboard": _widget_scoreboard,
    "MetricChart": _widget_metricchart,
    "SparklineChart": _widget_metricchart,
    "ParetoAnalysis": _widget_pareto,
    "Heatmap": _widget_heatmap,
    "PropertyList": _widget_propertylist,
    "AlertList": _widget_alertlist,
    "ProblemAlertsList": _widget_alertlist,
    "ResourceList": _widget_resourcelist,
    "TextDisplay": _widget_text,
    "HealthChart": _widget_healthchart,
    "Section": _widget_section,
}


# The widget types this preview lays out on purpose, derived from the table
# above so the two cannot disagree. Anything else is named rather than drawn.
HANDLED_WIDGETS = tuple(sorted(WIDGET_RENDERERS))


# What each widget type needs before it has anything to show. The check runs
# before the renderer, and before the unhandled-type branch, because state 3
# beats state 2: an empty widget of a type this page does not draw is empty
# first.
# What a document can write for "yes" and for "no". XML hands every attribute
# back as a string, so ``instanced="false"`` is truthy and reading it as a
# boolean says the opposite of what the export says; JSON writes real booleans
# today, and nothing stops a future export writing the string. One reader for
# all of them, and it answers None where the export says nothing, because
# "not declared" and "declared false" are different facts and several of these
# fields mean something different when absent.
_TRUE_WORDS = frozenset({"true", "yes", "y", "1", "on"})
_FALSE_WORDS = frozenset({"false", "no", "n", "0", "off"})


def declared_flag(value, key: str = "", depth: int = 4) -> Optional[bool]:
    """``True``, ``False``, or ``None`` when the document does not say.

    *key* lets a value that nests under its own name (``{"selfProvider":
    {"selfProvider": false}}``, which is how every corpus widget writes it)
    be unwrapped.
    """
    for _ in range(depth):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            word = value.strip().lower()
            if word in _TRUE_WORDS:
                return True
            if word in _FALSE_WORDS:
                return False
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return bool(value)
        if isinstance(value, dict) and key and key in value:
            value = value[key]
            continue
        return None
    return None


def _self_provider(cfg: dict) -> Optional[bool]:
    """Whether the widget chooses its own subject, per the export.

    The value is written two ways. Every corpus widget that carries it nests
    it, ``"selfProvider": {"selfProvider": false}``, and the flat boolean is
    what the key's name implies and what a hand-written document would carry.
    Reading only the flat form silently treats 674 real widgets as "not
    stated", which is how the never-shows-data case would have been missed on
    exactly the content it was written for. ``None`` means the export does not
    say, and nothing is claimed about it.
    """
    return declared_flag(cfg.get("selfProvider"), key="selfProvider")


def _widget_nothing(widget_type: str, cfg: dict, widget: dict) -> Tuple[str, str]:
    """``(code, sentence)`` for what is missing, or ``("", "")`` when the
    widget has something to show.

    Read ``_widget_verdict`` for the two things that come before this: a
    widget that drives others is a selector and is never empty, and a widget
    whose configuration lives in its state blob is configured.
    """
    if not isinstance(cfg, dict) or not cfg:
        return ("widget-no-config",
                "this widget carries no configuration at all, and no stored state either, "
                "so it shows nothing on the dashboard; it is usually an unfinished "
                "leftover")
    # A config that is nothing but a title is the same emptiness wearing a hat.
    if set(cfg) <= {"title", "titleLocalized", "refreshContent", "refreshInterval"}:
        if widget_type != "Section":
            return ("widget-title-only",
                    "this widget carries a title and nothing else: no subject, no metric "
                    "and no content, so it shows nothing on the dashboard either")
    if widget_type == "View":
        if not str(cfg.get("viewDefinitionId") or "").strip():
            return ("widget-no-view",
                    "this widget names no view, so there is nothing for it to show")
    elif widget_type in ("Scoreboard", "MetricChart", "SparklineChart", "PropertyList"):
        if not _cfg_metrics(cfg):
            return ("widget-no-metric",
                    f"this {widget_type.lower()} names no metric, so it shows nothing "
                    "until someone picks one")
    elif widget_type == "ParetoAnalysis":
        if not _cfg_metrics(cfg) and not str(cfg.get("metricName") or "").strip():
            return ("widget-no-pareto-metric",
                    "this chart names no metric, so it shows nothing until someone picks one")
    elif widget_type == "HealthChart":
        if not (str(cfg.get("metricKey") or "").strip()
                or str(cfg.get("metricName") or "").strip()):
            return ("widget-no-health-metric",
                    "this chart names no metric, so it shows nothing until someone picks one")
    elif widget_type == "Heatmap":
        # Through the same reader as the renderer: a configs list holding a
        # block that names neither a colour nor a size metric is a heatmap with
        # nothing to draw, however full the block looks.
        if not heatmap_metrics(cfg):
            return ("widget-no-heatmap-metric",
                    "this heatmap declares no colour or size metric, so it shows nothing "
                    "until someone picks them")
    elif widget_type == "TextDisplay":
        # Through the same reader as the renderer: a widget whose editorData is
        # empty carries no text, whatever its viewModeHTML flag says, and the
        # two surfaces may not disagree about that.
        if (not text_widget_words(cfg)
                and not (cfg.get("locationUrl") or cfg.get("locationFile"))):
            return ("widget-no-text",
                    "this text widget carries no text and points at no file")
    elif widget_type == "Section":
        title = str(widget.get("title") or cfg.get("title") or "").strip()
        if not title:
            return ("widget-no-heading", "this section divider carries no heading")
    return ("", "")


def subject_of(cfg: dict, feeds: bool, driven: bool, dashboard_has_wiring: bool) -> str:
    """How a widget comes by the object it shows: one rule, one place.

    The census used to carry its own copy of this and had already drifted
    from it. Returns one of ``SUBJECT_KINDS``.
    """
    if driven:
        return "fed"
    if feeds:
        return "selector"
    if _self_provider(cfg) is False:
        return "never-shows" if dashboard_has_wiring else "from-outside"
    return "self"


def _widget_verdict(widget_type: str, cfg: dict, widget: dict,
                    feeds: bool) -> Tuple[str, str, str]:
    """``(kind, code, sentence)`` for one widget, where kind is "empty",
    "elsewhere" or "" for a widget with something to draw.

    The order is the finding of this round. A widget that drives other widgets
    is the dashboard's selector and cannot be empty whatever its config looks
    like: 48 widgets in the corpus drive others and were called empty anyway,
    24 of them under a badge reading how many widgets they drive. A widget
    whose column layout sits in its state blob is configured, in a place this
    page does not read. Only what is left can be empty.
    """
    if feeds:
        return ("", "", "")
    blob = widget_state_blob(widget)
    if blob and (not isinstance(cfg, dict) or not cfg):
        return ("elsewhere", "widget-state-not-read",
                f"VCF Operations stores this widget's layout in its saved state rather than "
                f"in its configuration ({len(blob)} characters of it), in a form this page "
                "does not decode, so the widget is configured and its shape is not shown "
                "here")
    code, sentence = _widget_nothing(widget_type, cfg, widget)
    return ("empty" if code else "", code, sentence)


def _record_empty(ctx: dict, code: str, detail: str) -> str:
    """A state-3 box from inside a renderer, counted like the rest.

    Two rules live here rather than in the caller, because a rule the caller
    can forget is not a rule. Renderers used to print a box with nothing in it
    without the roll-up hearing about it, so everything goes through here. And
    a widget that drives other widgets is the dashboard's selector: it may
    well have no metric and no columns of its own, and saying it shows nothing
    directly under a badge counting what it drives is the contradiction this
    module has now printed twice. A driving widget gets the selector's own
    sentence instead, whatever the renderer found.
    """
    if ctx.get("feeds"):
        return _mark_widget_elsewhere(
            ctx.get("preview"), ctx.get("title") or "",
            str((ctx.get("widget") or {}).get("type") or ""),
            "widget-drives-only",
            "this widget drives the widgets wired to it and carries no content of its "
            "own to draw here; on the dashboard it is the control the others follow")
    _mark_widget_empty(ctx.get("preview"), ctx.get("title") or "",
                       str((ctx.get("widget") or {}).get("type") or ""), code, detail)
    return nothing_here(detail)


def _widget_unhandled(widget_type: str) -> str:
    return (f"<div class='pv-placeholder'>a <b>{_e(widget_type or 'untyped')}</b> widget. "
            "This preview does not lay out this type, so its shape is named rather than "
            "drawn.</div>")


# ---------------------------------------------------------------------------
# Per-kind previews
# ---------------------------------------------------------------------------

def _grid_columns(doc: dict, widgets: Sequence[dict]) -> int:
    """How many columns to draw: the dashboard's own, widened to fit any
    widget that runs past them.

    An export can place a widget outside the grid it declares (one corpus
    dashboard puts an alert list at x=13 on a 12 column grid). Clamping that
    into whatever columns are left draws a full width widget as an unreadable
    sliver, and the admin deciding from the preview cannot tell the preview
    moved it. Widening the grid keeps every widget its declared width and
    keeps their order; it only makes the neighbours proportionally narrower.
    """
    declared = doc.get("gridsterMaxColumns")
    columns = declared
    if not isinstance(columns, int) or not 1 <= columns <= MAX_GRID_COLUMNS:
        runlog.detail("grid.columns_defaulted", declared=str(declared),
                      columns=DEFAULT_GRID_COLUMNS,
                      reason=runlog.prose("the dashboard declares no usable column count"))
        columns = DEFAULT_GRID_COLUMNS
    for widget in widgets:
        coords = widget.get("gridsterCoords")
        if not isinstance(coords, dict):
            continue
        try:
            needed = int(coords.get("x", 1)) + int(coords.get("w", 1)) - 1
        except (TypeError, ValueError):
            runlog.debug("grid.coords_unreadable", widget=str(widget.get("id") or ""),
                         widget_type=str(widget.get("type") or ""),
                         reason=runlog.prose("this widget's coordinates are not numbers, so it does not "
                                "widen the grid and flows after the placed widgets"))
            continue
        if needed > columns:
            runlog.detail("grid.widened", declared=columns,
                          columns=min(needed, MAX_GRID_COLUMNS),
                          widget=str(widget.get("id") or ""),
                          widget_type=str(widget.get("type") or ""),
                          reason=runlog.prose("a widget runs past the grid the dashboard declares; "
                                 "widening keeps every widget its declared width rather "
                                 "than drawing this one as a sliver"))
        columns = max(columns, min(needed, MAX_GRID_COLUMNS))
    return columns


@dataclass
class Wiring:
    """Which widget drives which, as the dashboard's own document says.

    ``widgetInteractions`` is a flat list of
    ``{widgetIdProvider, type, widgetIdReceiver}``; every one of the 666
    entries in the corpus names a widget in the same document, so this is a
    within-document relationship and not a dependency edge (see
    ``knowledge``: the census is in the M4b PR body).

    It matters for the preview because ``selfProvider`` is false on most
    widgets in a real dashboard: those widgets show nothing until an object is
    picked in the widget that feeds them. A preview that draws every widget as
    though it stands alone hides the one thing an interaction-driven dashboard
    is about.
    """
    providers: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    receivers: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    titles: Dict[str, str] = field(default_factory=dict)
    # Navigation targets the export does not carry: a link that will not land.
    foreign_navigations: int = 0

    def is_receiver(self, widget_id: str) -> bool:
        return bool(self.providers.get(widget_id))

    def is_provider(self, widget_id: str) -> bool:
        return bool(self.receivers.get(widget_id))

    def title(self, widget_id: str) -> str:
        return self.titles.get(widget_id) or "(untitled widget)"


def _widget_title(widget: dict) -> str:
    cfg = widget.get("config") if isinstance(widget.get("config"), dict) else {}
    return str(widget.get("title") or cfg.get("title") or "").strip()


def read_wiring(doc: dict, widgets: Sequence[dict]) -> Wiring:
    """The dashboard's own interaction wiring, provider to receiver."""
    wiring = Wiring()
    for widget in widgets:
        ident = str(widget.get("id") or "")
        if ident:
            wiring.titles[ident] = _widget_title(widget)
    known = set(wiring.titles)
    entries = doc.get("widgetInteractions")
    entries = entries if isinstance(entries, list) else (
        [entries] if isinstance(entries, dict) and entries else [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("widgetIdProvider") or "")
        receiver = str(entry.get("widgetIdReceiver") or "")
        kind = str(entry.get("type") or "selection")
        if not provider or not receiver or provider not in known or receiver not in known:
            # No corpus entry does this; if one ever does, the wiring simply
            # does not claim a relationship it cannot name on both ends.
            continue
        if (provider, kind) not in wiring.providers.setdefault(receiver, []):
            wiring.providers[receiver].append((provider, kind))
        if (receiver, kind) not in wiring.receivers.setdefault(provider, []):
            wiring.receivers[provider].append((receiver, kind))

    navigations = doc.get("dashboardNavigations")
    if isinstance(navigations, dict):
        for targets in navigations.values():
            for target in targets if isinstance(targets, list) else []:
                if isinstance(target, dict) and str(target.get("id") or "") not in known:
                    wiring.foreign_navigations += 1
    return wiring


def _wiring_summary(wiring: Wiring) -> str:
    """The dashboard's flow, above the layout, so a reader sees it without
    opening every widget."""
    if not wiring.receivers:
        return ""
    lines = []
    for provider, fed in wiring.receivers.items():
        # One widget fed by two interaction types is one widget, so the line
        # dedupes on the receiver's id. Two different widgets sharing a title
        # are two widgets, so deduping on the title lost one: they are
        # numbered instead, and the count in the badge is the count here.
        seen_ids: List[str] = []
        for receiver, _kind in fed:
            if receiver not in seen_ids:
                seen_ids.append(receiver)
        used: Dict[str, int] = {}
        labels = []
        titles = [wiring.title(r) for r in seen_ids]
        for title in titles:
            if titles.count(title) > 1:
                used[title] = used.get(title, 0) + 1
                labels.append(f"{_e(title)} ({used[title]})")
            else:
                labels.append(_e(title))
        names = ", ".join(labels)
        lines.append(f"<li><b>{_e(wiring.title(provider))}</b> "
                     f"<span class='arrow'>drives</span> {names}</li>")
    return ("<div class='pv-wiring'>This dashboard is interaction driven: what most of it "
            "shows depends on the object picked in another widget."
            f"<ul>{''.join(lines)}</ul></div>")


def _tab_names(doc: dict) -> Dict[object, str]:
    """``tabId`` to the tab's name, where the document carries one.

    No dashboard in any corpus export has more than one tab, so the shape of a
    tab table is unproven; these are the keys a tab list would be written
    under. Where nothing answers, the heading says the document gives only an
    id rather than printing the id as if it were a name.
    """
    out: Dict[object, str] = {}
    for key in ("tabs", "dashboardTabs", "tabList"):
        value = doc.get(key)
        for entry in value if isinstance(value, list) else []:
            if not isinstance(entry, dict):
                continue
            ident = entry.get("id", entry.get("tabId"))
            name = entry.get("name") or entry.get("title")
            if ident is not None and name:
                out[ident] = str(name)
    return out


def _dashboard_preview(graph: Graph, node: Node, preview: Preview) -> str:
    doc = _json_doc(raw_document(graph, node))
    widgets = doc.get("widgets")
    widgets = [w for w in widgets if isinstance(w, dict)] if isinstance(widgets, list) else []
    columns = _grid_columns(doc, widgets)
    declared = doc.get("gridsterMaxColumns")
    if isinstance(declared, int) and 1 <= declared < columns:
        preview.notes.append(
            f"the dashboard declares {declared} columns and at least one widget runs past "
            f"them, so the grid is drawn {columns} columns wide rather than squashing it")

    wiring = read_wiring(doc, widgets)
    keys = widget_keys(widgets)
    preview.receivers = sum(1 for w in widgets
                            if wiring.is_receiver(str(w.get("id") or "")))
    preview.providers = len(wiring.receivers)

    tabs: List[object] = []
    for widget in widgets:
        tab = widget.get("tabId")
        if tab not in tabs:
            tabs.append(tab)
    named = _tab_names(doc)

    parts: List[str] = [_wiring_summary(wiring)]
    for tab in tabs:
        members = [w for w in widgets if w.get("tabId") == tab]
        if len(tabs) > 1:
            if tab in named:
                label = _e(named[tab])
            elif tab is None:
                label = "no tab"
            else:
                label = f"id {_e(tab)}, which is all the document gives"
            parts.append(f"<h4 class='pv-h'>tab {label} ({len(members)} widgets)</h4>")
        parts.append(_widget_grid(graph, members, columns, preview, wiring, keys))
    if not widgets:
        parts.append(_mark_object_empty(
            preview, "dashboard-no-widgets",
            "this dashboard carries no widgets at all, so it opens empty on the target too"))

    if preview.receivers:
        preview.notes.append(
            f"{preview.receivers} of {len(widgets)} widgets are driven by the object picked "
            f"in another widget ({_plural(preview.providers, 'widget')} "
            + ("does" if preview.providers == 1 else "do")
            + " the driving); on the real "
            "dashboard those show nothing until a selection is made")
    if wiring.foreign_navigations:
        preview.notes.append(
            f"{_plural(wiring.foreign_navigations, 'dashboard navigation target')} are "
            "not in this export, so those links will not land unless the target instance "
            "already has what they point at")

    description = str(doc.get("description") or "").strip()
    head = f"<p class='pv-sub'>{_e(description)}</p>" if description else ""
    return head + "".join(parts)


def widget_keys(widgets: Sequence[dict]) -> Dict[int, str]:
    """``id(widget) -> the key this widget is counted under``.

    One expression, called once per document, because two that happen to
    agree do not: the page renders widgets tab by tab and the census walks
    them in document order, so an id-less widget keyed by "its position"
    meant two different positions and 14 fixture widgets were counted under
    another widget's verdict. The key is the widget's own id where it has
    one, and its index in the document where it does not.
    """
    return {id(widget): str(widget.get("id") or f"index-{index}")
            for index, widget in enumerate(widgets)}


def _ordered_widgets(widgets: Sequence[dict]) -> List[dict]:
    """The widgets in the order the dashboard reads on screen: down the rows,
    then left to right inside a row.

    An export stores widgets in whatever order they were added, and their
    gridster coordinates are what say where they sit. Sorting by (y, x) and
    placing each one in its own columns lets the grid pick the rows, which is
    what keeps a widget from being clipped: a row sized by its content cannot
    cut off the table inside it, and a row whose height came from the export
    can and did.
    """
    def position(pair):
        index, widget = pair
        coords = widget.get("gridsterCoords")
        if not isinstance(coords, dict):
            return (1 << 30, 1 << 30, index)
        try:
            return (int(coords.get("y", 1)), int(coords.get("x", 1)), index)
        except (TypeError, ValueError):
            runlog.debug("widget.order_unreadable",
                         widget=str(widget.get("id") or ""),
                         widget_type=str(widget.get("type") or ""),
                         reason=runlog.prose("this widget's coordinates are not numbers, so it is "
                                "drawn after the placed widgets in document order"))
            return (1 << 30, 1 << 30, index)

    return [w for _pos, w in sorted(enumerate(widgets), key=position)]


def _widget_grid(graph: Graph, widgets: Sequence[dict], columns: int,
                 preview: Preview, wiring: Optional[Wiring] = None,
                 keys: Optional[Dict[int, str]] = None) -> str:
    wiring = wiring or Wiring()
    # The keys are computed once for the whole document, above the tab split,
    # so a widget's key does not depend on which tab it is rendered under.
    keys = widget_keys(widgets) if keys is None else keys
    cells: List[str] = []
    for position, widget in enumerate(_ordered_widgets(widgets)):
        widget_type = str(widget.get("type") or "")
        preview.widget_types[widget_type] = preview.widget_types.get(widget_type, 0) + 1
        cfg = widget.get("config") if isinstance(widget.get("config"), dict) else {}
        title = str(widget.get("title") or cfg.get("title") or "").strip()
        renderer = WIDGET_RENDERERS.get(widget_type)
        seed = f"{widget.get('id') or position}:{widget_type}"
        # State 3 first: what the widget does not carry is a fact about the
        # admin's content, and it outranks what this page cannot draw.
        ident = str(widget.get("id") or "")
        driven_by = wiring.providers.get(ident, [])
        feeds = wiring.receivers.get(ident, [])
        verdict, missing_code, missing = _widget_verdict(
            widget_type, cfg, widget, feeds=bool(feeds))
        elsewhere = missing if verdict == "elsewhere" else ""
        missing = missing if verdict == "empty" else ""
        subject_kind = subject_of(cfg, bool(feeds), bool(driven_by),
                                  bool(wiring.receivers))
        subject = ""      # how this widget gets the object it shows, in words
        never_shows = ""   # state 3, kept as a caption over the drawn widget
        if subject_kind == "selector":
            # A widget that drives others is the dashboard's selector, and
            # that is true whether or not it declares selfProvider: the corpus
            # has selectors with no config at all, whose column layout lives
            # in their saved state. Asking selfProvider first is what let 48
            # of them be called empty.
            subject_kind = "selector"
            # Two clauses, and only one of them is always true. 72 of the 92
            # selectors in the corpus declare selfProvider true: they drive
            # other widgets *and* choose their own subject, and the sentence
            # said the opposite of every one of them. Driving is what the
            # wiring says; choosing is what selfProvider says, and it is only
            # claimed where the export says it.
            subject = f"this widget drives {_plural(len(feeds), 'widget')} on this dashboard"
            chooses = _self_provider(cfg)
            if chooses is False:
                # Not "shows whatever is picked in them": the export names
                # this widget the provider on every edge it sits on and names
                # no provider for it, so nothing feeds it and the earlier tail
                # had the wiring pointing backwards. 17 of the corpus's 92
                # selectors are shaped this way.
                subject += ("; the export says it does not choose its own subject and "
                            "names nothing that feeds it")
            elif chooses is True:
                subject += ", and picks its own subject"
            preview.selectors += 1
        elif not missing and subject_kind in ("never-shows", "from-outside"):
            # The export says this widget does not choose its own subject and
            # names nothing that feeds it. Two things wear that shape:
            #
            # * the dashboard wires other widgets and not this one: it is
            #   wired wrong and stays blank, which is the admin's fact;
            # * the dashboard wires nothing at all (whole summary-style
            #   dashboards where seven of eight widgets are like this): the
            #   subject arrives from outside, as it does for a dashboard
            #   opened in an object's context. Calling those broken would be
            #   inventing a fault.
            if subject_kind == "never-shows":
                never_shows = ("this widget takes its subject from another widget's "
                               "selection, and nothing on this dashboard feeds it while "
                               "other widgets here are wired, so it will never show data")
                preview.orphan_receivers += 1
                _mark_widget_empty(preview, title, widget_type, "widget-never-shows",
                                   never_shows)
            else:
                subject = ("this widget takes its subject from a selection made outside "
                           "this dashboard: nothing here is wired to feed it, and the "
                           "values below stand for whatever object arrives")
                preview.context_driven += 1
        preview.subjects[subject_kind] = preview.subjects.get(subject_kind, 0) + 1
        before_elsewhere, before_empty = len(preview.elsewhere), len(preview.empty_codes)
        if elsewhere:
            inner = _mark_widget_elsewhere(preview, title, widget_type,
                                           missing_code, elsewhere)
        elif missing:
            _mark_widget_empty(preview, title, widget_type, missing_code, missing)
            inner = nothing_here(missing)
        elif renderer is None:
            preview.unhandled_types[widget_type] = (
                preview.unhandled_types.get(widget_type, 0) + 1)
            inner = _widget_unhandled(widget_type)
        else:
            before_elsewhere, before_empty = len(preview.elsewhere), len(preview.empty_codes)
            inner = renderer(cfg, {"graph": graph, "seed": seed, "widget": widget,
                                   "preview": preview, "title": title,
                                   # The renderers refuse to say "nothing to
                                   # show" about a widget that drives others,
                                   # which is what makes the rule structural
                                   # rather than a habit of one caller.
                                   "feeds": bool(feeds)})
        if never_shows:
            # The sentence is the state-3 one and it is counted as such, but
            # the widget is still drawn under it: a blank box shows neither
            # its columns nor its metrics, which is the same argument this
            # code already makes for a widget that is fed.
            inner = nothing_here(never_shows) + inner
        if subject:
            inner = f"<p class='pv-drivenby'>{_e(subject)}</p>" + inner
        if not missing and driven_by:
            # Mock values are shown rather than an empty frame, and labelled
            # with whose selection they stand for: the preview exists so an
            # admin can recognise the widget, and a blank box shows neither
            # its columns nor its metrics. The label is what keeps that
            # honest.
            names = ", ".join(_e(wiring.title(pid)) for pid, _kind in driven_by)
            inner = (f"<p class='pv-drivenby'>values below stand for one object picked in "
                     f"{names}; this widget shows nothing until that selection is made</p>"
                     + inner)
        # never_shows is recorded by the subject block above, so it belongs in
        # the verdict the census reads as much as the renderer verdicts do.
        state = ("elsewhere" if elsewhere else
                 "empty" if (missing or never_shows) else "")
        code = (missing_code if missing else
                "widget-never-shows" if never_shows else
                missing_code if elsewhere else "")
        if not state:
            # A renderer can reach a verdict of its own, because the view a
            # widget names is a second document. Whatever it recorded while
            # this widget was rendering is this widget's verdict: counted by
            # position in the two lists rather than matched by title, since
            # two widgets can share a title.
            if len(preview.elsewhere) > before_elsewhere:
                state, code = "elsewhere", preview.elsewhere[before_elsewhere][2]
            elif len(preview.empty_codes) > before_empty:
                state, code = "empty", preview.empty_codes[before_empty]
        preview.widget_verdicts[keys[id(widget)]] = (state, code, subject_kind)
        # The widget classification, with the code and the evidence, at the one
        # place the verdict is settled. Every surface reads this same verdict,
        # so the log cannot drift from the page or from the census.
        runlog.detail("widget.classified", widget=ident, widget_type=widget_type,
                      title=title, state=state or "drawn", code=code or "",
                      subject=subject_kind, drives=len(feeds), driven_by=len(driven_by),
                      renderer=("none" if renderer is None else widget_type),
                      state_blob_chars=len(widget_state_blob(widget)),
                      config_keys=sorted(cfg) if isinstance(cfg, dict) else [],
                      reason=(elsewhere or missing or never_shows
                              or ("this preview does not lay out this type, so it is named "
                                  "rather than drawn" if renderer is None else "")) or None)
        cells.append(_widget_cell(widget, widget_type, title, inner, columns, preview,
                                  driven_by=[wiring.title(pid) for pid, _k in driven_by],
                                  feeds=[wiring.title(rid) for rid, _k in feeds]))
    if not cells:
        return ""
    return (f"<div class='pv-grid' style='grid-template-columns:repeat({columns},1fr)'>"
            + "".join(cells) + "</div>")


def _widget_cell(widget: dict, widget_type: str, title: str, inner: str,
                 columns: int, preview: Preview, driven_by: Sequence[str] = (),
                 feeds: Sequence[str] = ()) -> str:
    """One widget frame, placed where the dashboard places it.

    The columns are the export's own: gridster ``x`` is one-based and ``w`` is
    a span, so a half-width widget stays half width and a full-width one still
    runs the width of the dashboard. The *height* is the content's rather than
    the export's, which is a deliberate trade: honouring ``h`` clipped the
    table inside a widget whose mock rows are taller than the source's row
    band, and a clipped widget is exactly the thing an admin cannot recognise.

    The grid is already widened to fit any widget that runs past the declared
    columns (``_grid_columns``), so a clamp here means a widget beyond even
    that, which is a document this tool has not seen: it is drawn at the edge
    and named in the notes rather than quietly resized. A widget with no
    coordinates at all (none in any corpus export, but the fallback costs
    nothing) flows after the placed ones rather than landing on top of one.
    """
    coords = widget.get("gridsterCoords")
    style = ""
    if isinstance(coords, dict):
        try:
            raw_x, raw_w = int(coords.get("x", 1)), int(coords.get("w", columns))
        except (TypeError, ValueError):
            runlog.debug("widget.placement_unreadable",
                         widget=str(widget.get("id") or ""), widget_type=widget_type,
                         reason=runlog.prose("this widget's coordinates are not numbers, so it flows "
                                "after the placed widgets"))
            style = ""
        else:
            x = max(1, min(raw_x, columns))
            w = max(1, min(raw_w, columns - x + 1))
            if (x, w) != (max(1, raw_x), max(1, raw_w)):
                runlog.detail("widget.clamped", widget=str(widget.get("id") or ""),
                              widget_type=widget_type, declared_x=raw_x, declared_w=raw_w,
                              drawn_x=x, drawn_w=w, columns=columns,
                              reason=runlog.prose("the widget does not fit the grid even after widening, "
                                     "so the preview moves it and says so"))
                preview.notes.append(
                    f"{title or '(untitled widget)'} ({widget_type or 'no type'}) sits at "
                    f"column {raw_x} spanning {raw_w} of a {columns} column grid, so it is "
                    f"drawn at column {x} spanning {w}")
            style = f"grid-column:{x} / span {w}"
    # A Section is a divider, so it normally has no body; an empty one still
    # shows its state-3 box, or the rule would have a silent exception.
    empty = "pv-nothing" in inner
    klass = "pv-section" if (widget_type == "Section" and not empty) else "pv-w"
    if driven_by:
        klass += " is-receiver"
    elif feeds:
        klass += " is-provider"
    badge = ""
    if driven_by:
        badge = (f"<span class='pv-flow driven'>driven by {_e(', '.join(driven_by))}</span>")
    elif feeds:
        badge = (f"<span class='pv-flow drives'>drives {len(feeds)} widget"
                 f"{'s' if len(feeds) != 1 else ''}</span>")
    head = (f"<h3>{_e(title or '(untitled widget)')}{badge}"
            f"<span class='pv-type'>{_e(widget_type or 'no type')}</span></h3>")
    body = inner if (widget_type != "Section" or empty) else ""
    return (f"<div class='{klass}'" + (f" style='{style}'" if style else "") + ">"
            + head + body + "</div>")


def _view_preview(graph: Graph, node: Node, preview: Preview) -> str:
    root = _xml_doc(raw_document(graph, node))
    presentation = (root.find("Presentation").get("type")
                    if root.find("Presentation") is not None else "")
    providers = [d.get("dataType") or "" for d in root.findall("./DataProviders/DataProvider")]
    subjects = ", ".join(
        f"{s.get('adapterKind') or ''} {s.get('resourceKind') or ''} ({s.get('type') or ''})".strip()
        for s in root.findall("SubjectType")) or "none declared"
    usages = ", ".join(sorted({(u.text or "").strip() for u in root.findall("Usage")}
                              - {""}))
    columns = view_columns(root)
    description = _text(root, "Description")

    facts = _facts([
        ("presentation", presentation or "not declared"),
        ("data provider", ", ".join(p for p in providers if p) or "not declared"),
        ("subject", subjects),
        ("used in", usages or "not declared"),
        ("columns", str(len(columns))),
    ])
    head = (f"<p class='pv-sub'>{_e(description)}</p>" if description else "") + facts
    if not columns:
        code = "view-no-columns" if (not presentation or presentation == "list") \
            else "view-no-attributes"
        _mark_object_empty(preview, code,
                           "this view declares no columns, so it shows an empty table "
                           "wherever it is used" if code == "view-no-columns" else
                           f"this {presentation} view declares no attributes, so it has "
                           "nothing to chart wherever it is used")
    if presentation and presentation != "list":
        preview.notes.append(
            f"this view's presentation is {presentation}; only list views are laid out")
        return head + "<h4 class='pv-h'>attributes</h4>" + _non_list_view(presentation, columns, graph)
    return head + "<h4 class='pv-h'>columns, with mock rows</h4>" + _columns_table(columns, graph=graph)


def _supermetric_preview(graph: Graph, node: Node, preview: Preview) -> str:
    doc = _json_doc(raw_document(graph, node))
    formula = str(doc.get("formula") or "")
    description = str(doc.get("description") or "").strip()
    kinds = doc.get("resourceKinds")
    kind_text = "none declared"
    if isinstance(kinds, list) and kinds:
        kind_text = ", ".join(
            f"{k.get('adapterKindKey') or ''} {k.get('resourceKindKey') or ''}".strip()
            for k in kinds if isinstance(k, dict)) or kind_text

    if not formula.strip():
        return ((f"<p class='pv-sub'>{_e(description)}</p>" if description else "")
                + _mark_object_empty(
                    preview, "supermetric-no-formula",
                    "this super metric carries an empty formula, so it computes nothing on "
                    "the target either"))

    resolved, references = _resolve_formula(graph, node, formula)
    facts = _facts([
        ("unit", str(doc.get("unitId") or "none declared")),
        ("resource kinds", kind_text),
    ])
    parts = [f"<p class='pv-sub'>{_e(description)}</p>" if description else "", facts,
             "<h4 class='pv-h'>formula, as exported</h4>",
             f"<pre class='pv-code'>{_e(formula) or '(empty)'}</pre>"]
    if references:
        parts += ["<h4 class='pv-h'>formula, with references resolved to names</h4>",
                  f"<pre class='pv-code'>{resolved}</pre>",
                  "<h4 class='pv-h'>references</h4>",
                  "<ul class='pv-logic'>"
                  + "".join(f"<li>{item}</li>" for item in references) + "</ul>"]
    else:
        parts.append("<p class='pv-sub'>this formula names no other super metric</p>")
    return "".join(parts)


def _resolve_formula(graph: Graph, node: Node, formula: str) -> Tuple[str, List[str]]:
    """The formula with each super metric reference replaced by its name, and a
    line per reference.

    The references themselves come from the graph, which read them out of the
    parsed document; this only has to find where each one sits in the text, so
    a spelling the graph handles and this does not cannot invent an edge.
    """
    import re

    references: List[str] = []
    seen: List[str] = []
    for ref in node.refs:
        if ref.kind != "supermetric" or ref.ident in seen:
            continue
        seen.append(ref.ident)
        references.append(_named(graph, "supermetric", ref.ident))
    if not seen:
        return _e(formula), []

    # Escape the formula first and match the escaped spelling of each
    # identifier in it, so the only markup on the finished line is the markup
    # this function put there. Substituting first and escaping after would
    # escape the spans; escaping the identifier is what keeps the two in step
    # for a name carrying a quote or an ampersand.
    escaped = {ident: _e(ident) for ident in seen}
    by_escaped = {escaped[ident]: ident for ident in seen}
    pattern = re.compile("|".join(
        re.escape(escaped[ident]) for ident in sorted(seen, key=len, reverse=True)))

    def replace(match) -> str:
        ident = by_escaped.get(match.group(0), match.group(0))
        target = _find_node(graph, "supermetric", ident)
        if target is None:
            return f"<span class='unres'>{_e(ident)}</span>"
        return f"<span class='ref'>{_e(target.name)}</span>"

    return pattern.sub(replace, _e(formula)), references


def _alert_preview(graph: Graph, node: Node, preview: Preview) -> str:
    root = _xml_doc(raw_document(graph, node))
    facts = _facts([
        ("object kind", f"{root.get('adapterKind') or ''} "
                        f"{root.get('resourceKind') or ''}".strip() or "not declared"),
        ("wait cycles", root.get("waitCycle") or "not declared"),
        ("cancel cycles", root.get("cancelCycle") or "not declared"),
        ("type / sub type", f"{root.get('type') or '?'} / {root.get('subType') or '?'}"),
    ])
    description = (root.get("description") or "").strip()
    parts = [f"<p class='pv-sub'>{_e(description)}</p>" if description else "", facts]

    for state in root.findall("State"):
        severity = state.get("severity") or "not declared"
        parts.append(f"<h4 class='pv-h'>alert state, severity {_e(severity)}</h4>")
        impacts = ", ".join(f"{i.get('type') or ''} {i.get('key') or ''}".strip()
                            for i in state.findall("Impact"))
        if impacts:
            parts.append(f"<p class='pv-sub'>impact: {_e(impacts)}</p>")
        parts.append(_symptom_logic(graph, state, preview))
        # Both spellings: an AlertDefinition usually wraps its references in a
        # <Recommendations> element, and some exports hang them straight off
        # the state. Reading one spelling only loses the recommendations
        # entirely on the other, silently.
        recommendations = (state.findall("./Recommendations/Recommendation")
                           + state.findall("Recommendation"))
        if recommendations:
            parts.append("<h4 class='pv-h'>recommendations, in priority order</h4>")
            ordered = sorted(recommendations,
                             key=lambda r: _int_or(r.get("priority"), 99))
            items = []
            for rec in ordered:
                ref = rec.get("ref") or ""
                items.append(f"<li><span class='op'>{_e(rec.get('priority') or '?')}.</span> "
                             + _recommendation_text(graph, ref) + "</li>")
            parts.append("<ul class='pv-logic'>" + "".join(items) + "</ul>")
        else:
            parts.append("<p class='pv-sub'>no recommendations are attached</p>")
    if not root.findall("State"):
        parts.append(_mark_object_empty(
            preview, "alert-no-state",
            "this alert declares no state, so it carries no symptoms and can never fire"))
    return "".join(parts)


def _int_or(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _recommendation_text(graph: Graph, ref: str) -> str:
    """A recommendation reference as words: its own text where the export
    carries it, its key and a reason where it does not."""
    node = _find_node(graph, "recommendation", ref)
    if node is None:
        return (f"<code>{_e(ref)}</code> <span class='pv-miss'>(not in this export; it is "
                "out-of-the-box content the target instance is expected to have)</span>")
    return _e(node.name)


def _symptom_logic(graph: Graph, state: ET.Element, preview: Optional[Preview] = None) -> str:
    """The symptom sets stated in words, nesting as the document nests them."""
    blocks: List[str] = []
    for sets_el in state.findall("SymptomSets"):
        operator = (sets_el.get("operator") or "or").lower()
        inner = "".join(_symptom_set(graph, s) for s in sets_el.findall("SymptomSet"))
        blocks.append(f"<li><span class='op'>{_e(operator)} of these symptom sets</span>"
                      f"<ul>{inner}</ul></li>")
    for set_el in state.findall("SymptomSet"):
        blocks.append(_symptom_set(graph, set_el))
    if not blocks:
        reason = "this alert state names no symptoms, so nothing can make it fire"
        if preview is None:
            return nothing_here(reason)
        # Half of this was implemented: an alert with no state said so and an
        # alert with a state naming no symptoms did not.
        return _mark_object_empty(preview, "alert-no-symptoms", reason)
    return ("<p class='pv-sub'>the alert triggers when:</p><ul class='pv-logic'>"
            + "".join(blocks) + "</ul>")


def _symptom_set(graph: Graph, set_el: ET.Element) -> str:
    operator = (set_el.get("operator") or "all").lower()
    apply_on = set_el.get("applyOn") or ""
    refs = [s.get("ref") or "" for s in set_el.findall("Symptom")]
    if set_el.get("ref"):
        refs.insert(0, set_el.get("ref"))
    scope = f" on the {_e(apply_on)} object" if apply_on else ""
    if not refs:
        return "<li><span class='op'>an empty symptom set</span></li>"
    if len(refs) == 1:
        return (f"<li>{_symptom_name(graph, refs[0])}"
                + (f"<span class='op'>{scope}</span>" if scope else "") + "</li>")
    items = "".join(f"<li>{_symptom_name(graph, ref)}</li>" for ref in refs)
    word = "all" if operator in ("and", "all") else "any"
    return (f"<li><span class='op'>{_e(word)} of{scope}</span><ul>{items}</ul></li>")


def _symptom_name(graph: Graph, ref: str) -> str:
    return _named(graph, "symptom", ref)


def _symptom_preview(graph: Graph, node: Node, preview: Preview) -> str:
    root = _xml_doc(raw_document(graph, node))
    facts = _facts([
        ("object kind", f"{root.get('adapterKind') or ''} "
                        f"{root.get('resourceKind') or ''}".strip() or "not declared"),
        ("symptom type", root.get("symptomDefType") or "not declared"),
    ])
    parts = [facts]
    if not root.findall("State"):
        parts.append(_mark_object_empty(
            preview, "symptom-no-state",
            "this symptom declares no state, so it carries no condition and can never "
            "trigger"))
    for state in root.findall("State"):
        parts.append(f"<h4 class='pv-h'>condition, severity "
                     f"{_e(state.get('severity') or 'not declared')}</h4>")
        items = [f"<li>{_condition_words(condition, graph)}</li>"
                 for condition in state.findall("Condition")]
        if items:
            parts.append("<ul class='pv-logic'>" + "".join(items) + "</ul>")
        else:
            parts.append(_mark_object_empty(
                preview, "symptom-no-condition",
                "this symptom declares no condition, so nothing can trigger it"))
    return "".join(parts)


def _condition_words(condition: ET.Element, graph: Optional[Graph] = None) -> str:
    """One symptom condition in words, per the shapes the corpus carries:
    a metric or property threshold, a message event, and a log query."""
    kind = (condition.get("type") or "").lower()
    operator = condition.get("operator") or "?"
    if kind in ("metric", "property"):
        key = condition.get("key") or "(no key)"
        value = condition.get("value")
        target = condition.get("targetKey")
        threshold = condition.get("thresholdType") or ""
        tail = (f" {_e(target)}" if target else f" {_e(value)}" if value is not None else "")
        # ElementTree hands back the string "false", which is truthy: read
        # as a boolean it labelled the condition instanced when the export
        # said the opposite.
        instanced = " (instanced)" if declared_flag(condition.get("instanced")) else ""
        return (f"{_metric_code(key, graph)} <span class='op'>{_e(operator)}</span>{tail}"
                + (f" <span class='op'>({_e(threshold)} threshold)</span>" if threshold else "")
                + instanced)
    if kind == "message_event" or condition.get("eventType"):
        return (f"a {_e(condition.get('eventType') or 'message')} event "
                f"<span class='op'>{_e(operator)}</span> "
                f"<code>{_e(condition.get('eventMsg') or '')}</code>")
    if kind == "log":
        trigger = condition.find("triggerCondition")
        detail = ""
        if trigger is not None:
            detail = (f" <span class='op'>{_e(trigger.get('function') or '')} over "
                      f"{_e(trigger.get('interval') or '?')} minutes "
                      f"{_e(trigger.get('operator') or '')} "
                      f"{_e(trigger.get('value') or '')}</span>")
        return f"a log query <code>{_e(condition.get('queryText') or '')}</code>{detail}"
    attrs = ", ".join(f"{k}={v}" for k, v in sorted(condition.attrib.items()))
    return (f"<span class='pv-miss'>a {_e(kind or 'untyped')} condition this preview does "
            f"not state in words</span>: <code>{_e(attrs)}</code>")


def _recommendation_preview(graph: Graph, node: Node, preview: Preview) -> str:
    root = _xml_doc(raw_document(graph, node))
    text = _text(root, "Description") or (root.get("description") or "")
    body = (f"<div class='pv-text'>{_e(text)}</div>" if text else _mark_object_empty(
        preview, "recommendation-no-text",
        "this recommendation carries no description, so an operator acting on the alert is "
        "told nothing"))
    action = root.find("ActionDefinition")
    if action is not None:
        body += _facts([("action", action.get("id") or action.get("key") or "declared")])
    return body


def _report_preview(graph: Graph, node: Node, preview: Preview) -> str:
    root = _xml_doc(raw_document(graph, node))
    description = _text(root, "Description")
    subjects = ", ".join(
        f"{s.get('adapterKind') or ''} {s.get('resourceKind') or ''} ({s.get('type') or ''})".strip()
        for s in root.findall("SubjectType")) or "none declared"
    rows = []
    for index, section in enumerate(root.findall("./Sections/Section"), start=1):
        content_type = (section.findtext("ContentType") or "").strip()
        key = (section.findtext("ContentKey") or "").strip()
        if content_type == "View":
            what = _named(graph, "view", key)
        elif content_type == "Dashboard":
            what = _named(graph, "dashboard", key)
        else:
            what = f"<code>{_e(key)}</code>"
        rows.append(f"<tr><td class='num'>{index}</td><td>{_e(content_type or 'unnamed')}</td>"
                    f"<td>{what}</td></tr>")
    body = (f"<p class='pv-sub'>{_e(description)}</p>" if description else "")
    body += _facts([("subject", subjects), ("sections", str(len(rows)))])
    body += "<h4 class='pv-h'>pages, in order</h4>"
    if rows:
        body += ("<table class='pv-tbl'><thead><tr><th>#</th><th>section</th><th>content</th>"
                 "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
    else:
        body += _mark_object_empty(
            preview, "report-no-sections",
            "this report declares no sections, so it renders as a cover page and nothing "
            "else")
    return body


def _customgroup_preview(graph: Graph, node: Node, preview: Preview) -> str:
    doc = _json_doc(raw_document(graph, node))
    facts = _facts([
        ("group kind", f"{doc.get('adapterKind') or ''} "
                       f"{doc.get('resourceKind') or ''}".strip() or "not declared"),
        ("membership", _flag_words(doc.get("autoResolveMembership"),
                                   "kept up to date automatically", "fixed at import")),
        ("policy", str(doc.get("policy") or "none attached")),
    ])
    definition = doc.get("membershipDefinition")
    groups = definition.get("ruleGroups") if isinstance(definition, dict) else None
    blocks = []
    for index, group in enumerate(groups or [], start=1):
        if not isinstance(group, dict):
            continue
        rules = group.get("rules")
        items = []
        for rule in rules if isinstance(rules, list) else []:
            if isinstance(rule, dict):
                items.append(f"<li>{_rule_words(rule)}</li>")
        blocks.append(
            f"<h4 class='pv-h'>rule set {index}: "
            f"{_e((group.get('adapterKind') or '') + ' ' + (group.get('resourceKind') or ''))}"
            "</h4><ul class='pv-logic'>" + ("".join(items) or
                                            "<li><span class='op'>no rules</span></li>")
            + "</ul>")
    if not blocks:
        blocks.append(_mark_object_empty(
            preview, "group-no-rules",
            "this group declares no membership rules, so it takes no members of its own on "
            "the target: whatever it held on the source was picked by hand and does not "
            "travel"))
    return facts + "".join(blocks)


def _rule_words(rule: dict) -> str:
    kind = str(rule.get("ruleType") or "rule")
    operator = str(rule.get("ruleStringOperator") or rule.get("ruleOperator") or "")
    value = rule.get("ruleStringValue")
    if value is None:
        value = rule.get("ruleValue")
    key = rule.get("ruleKey") or rule.get("attributeKey") or ""
    relation = rule.get("ruleRelationshipType") or ""
    bits = [f"<b>{_e(kind)}</b>"]
    if relation:
        bits.append(f"<span class='op'>{_e(relation)}</span>")
    if key:
        bits.append(f"<code>{_e(key)}</code>")
    if operator:
        bits.append(f"<span class='op'>{_e(operator)}</span>")
    if value is not None:
        bits.append(_e(value))
    return " ".join(bits)


def _rule_preview(graph: Graph, node: Node, preview: Preview) -> str:
    doc = _json_doc(raw_document(graph, node))
    plugin = doc.get("PluginID")
    plugin_name = ""
    if isinstance(plugin, dict):
        plugin_name = str(plugin.get("@pluginName") or "")
    facts = _facts([
        ("delivered by", f"{doc.get('PluginType') or 'not declared'}"
                         + (f" ({plugin_name})" if plugin_name else "")),
        ("rule type", str(doc.get("RuleType") or "not declared")),
        # Absence means enabled: a rule with no Disabled field is live.
        ("enabled", "no" if declared_flag(doc.get("Disabled")) is True else "yes"),
    ])
    entries = doc.get("entry")
    entries = entries if isinstance(entries, list) else ([entries] if entries else [])
    items = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        condition = str(entry.get("ConditionType") or "condition")
        detail = ", ".join(sorted(k for k in entry if k != "ConditionType"))
        items.append(f"<li><b>{_e(condition)}</b>"
                     + (f" <span class='op'>{_e(detail)}</span>" if detail else "") + "</li>")
    if items:
        body = "<h4 class='pv-h'>conditions</h4><ul class='pv-logic'>" + "".join(items) + "</ul>"
    else:
        body = _mark_object_empty(
            preview, "rule-no-conditions",
            "this rule declares no conditions, so nothing will ever match it and it "
            "notifies no one")
    return facts + body


def _template_preview(graph: Graph, node: Node, preview: Preview) -> str:
    doc = _json_doc(raw_document(graph, node))
    return _facts([(key, json.dumps(value) if not isinstance(value, str) else value)
                   for key, value in sorted(doc.items())][:12])


def _outbound_preview(graph: Graph, node: Node, preview: Preview) -> str:
    doc = _json_doc(raw_document(graph, node))
    config = doc.get("pluginConfig") if isinstance(doc.get("pluginConfig"), dict) else {}
    rows = [("plugin type", str(doc.get("pluginType") or "not declared")),
            ("name", str(config.get("pluginName") or node.name)),
            ("enabled", _flag_words(config.get("enabled"), "yes", "no"))]
    for key, value in sorted(config.items()):
        if key in ("pluginName", "enabled") or isinstance(value, (dict, list)):
            continue
        rows.append((key, str(value)))
    return _facts(rows[:14]) + (
        "<p class='pv-sub'>values the export encrypted are carried through untouched and are "
        "not shown here; the target's import asks for the password used at export time</p>")


def _flag_words(value, when_true: str, when_false: str,
                when_unsaid: str = "not declared") -> str:
    """A declared flag in words, with a third answer for a document that does
    not declare it at all."""
    flag = declared_flag(value)
    return when_true if flag is True else when_false if flag is False else when_unsaid


def _facts(rows: Sequence[Tuple[str, str]]) -> str:
    cells = "".join(f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in rows)
    return f"<dl class='pv-facts'>{cells}</dl>"


PREVIEWERS = {
    "dashboard": _dashboard_preview,
    "view": _view_preview,
    "supermetric": _supermetric_preview,
    "alert": _alert_preview,
    "symptom": _symptom_preview,
    "recommendation": _recommendation_preview,
    "report": _report_preview,
    "customgroup": _customgroup_preview,
    "notificationrule": _rule_preview,
    "notificationtemplate": _template_preview,
    "outboundsetting": _outbound_preview,
}


# ---------------------------------------------------------------------------
# Assembling
# ---------------------------------------------------------------------------

def build(graph: Graph, node: Node) -> Preview:
    """The preview for one node: its body markup and what the renderer met."""
    preview = Preview(node=node, title=node.name,
                      subtitle=_subtitle(node), body="")
    renderer = PREVIEWERS.get(node.kind)
    if renderer is None:
        preview.body = (f"<div class='pv-placeholder'>a {_e(node.kind)} object. This preview "
                        "does not lay out this kind, so it is named rather than drawn.</div>")
        preview.notes.append(f"no preview is written for {node.kind} objects")
        runlog.detail("preview.kind_not_drawn", kind=node.kind, uuid=node.uuid or "",
                      name=node.name,
                      reason=runlog.prose("this preview does not lay out this kind, so the object is "
                             "named rather than drawn"))
        return preview
    preview.body = renderer(graph, node, preview)
    if preview.selectors:
        preview.notes.append(
            f"{_plural(preview.selectors, 'widget')} on this dashboard "
            + ("is a selector: it drives" if preview.selectors == 1
               else "are selectors: they drive")
            + " the widgets wired to them")
    if preview.context_driven:
        preview.notes.append(
            f"{_plural(preview.context_driven, 'widget')} take their subject from "
            "outside this dashboard: nothing here is wired to feed them, which is how a "
            "dashboard opened in an object's context works")
    if preview.orphan_receivers:
        preview.notes.append(
            f"{_plural(preview.orphan_receivers, 'widget')} wait on a selection that "
            "nothing on this dashboard provides, so they will never show data")
    if preview.elsewhere:
        by_code: Dict[str, int] = {}
        for _title, _type, code, _reason in preview.elsewhere:
            by_code[code] = by_code.get(code, 0) + 1
        if by_code.get("widget-view-not-carried"):
            number = by_code["widget-view-not-carried"]
            preview.notes.append(
                f"{_plural(number, 'widget')} show a view this export does not carry, so "
                "they will show whatever the target already has; an export carries custom "
                "content only, so a view that ships with a management pack or with the "
                "product is never in one, and nothing here tells that apart from a view "
                "that is genuinely gone")
        if by_code.get("widget-state-not-read"):
            number = by_code["widget-state-not-read"]
            preview.notes.append(
                f"{_plural(number, 'widget')} keep their layout in the saved state VCF "
                "Operations writes rather than in their configuration, which this page does "
                "not decode: they are configured, and their shape is not drawn here")
    if preview.empty_widgets:
        # Grouped on the short reason, not the sentence: a sentence carrying a
        # uuid is unique per widget, which printed one clause per widget.
        reasons: Dict[str, int] = {}
        for _title, _type, reason in preview.empty_widgets:
            short = _short_reason(reason)
            reasons[short] = reasons.get(short, 0) + 1
        count = len(preview.empty_widgets)
        preview.notes.append(
            f"{_plural(count, 'widget')} with nothing to show: "
            + "; ".join(f"{number} where {short}"
                        for short, number in sorted(reasons.items(),
                                                    key=lambda pair: (-pair[1], pair[0])))
            + " (" + ", ".join(f"{title} [{kind}]"
                               for title, kind, _r in preview.empty_widgets[:6])
            + (f" and {count - 6} more" if count > 6 else "") + ")")
    if preview.unhandled_types:
        preview.notes.append(
            "widget types named rather than drawn: "
            + ", ".join(f"{name} x{count}" for name, count
                        in sorted(preview.unhandled_types.items())))
    if preview.empty_reason:
        runlog.detail("object.carries_nothing", kind=node.kind, uuid=node.uuid or "",
                      name=node.name, code=preview.empty_code,
                      reason=preview.empty_reason)
    runlog.detail("preview.built", kind=node.kind, uuid=node.uuid or "", name=node.name,
                  owner=node.owner or None, member=node.member,
                  widgets=sum(preview.widget_types.values()),
                  widget_types=dict(preview.widget_types),
                  empty_widgets=len(preview.empty_widgets),
                  elsewhere_widgets=len(preview.elsewhere),
                  unhandled_types=dict(preview.unhandled_types),
                  selectors=preview.selectors, receivers=preview.receivers,
                  providers=preview.providers,
                  orphan_receivers=preview.orphan_receivers,
                  context_driven=preview.context_driven,
                  subjects=dict(preview.subjects),
                  notes=len(preview.notes))
    runlog.count("previews")
    return preview


# The roll-up prints a short form of each reason; the full sentence is in the
# widget's own box, where the admin is looking when they need it.
_SHORT_REASONS = (
    ("no configuration at all", "the widget carries no configuration at all"),
    ("a title and nothing else", "the widget carries a title and nothing else"),
    ("names no view", "the widget names no view"),
    ("names no metric", "the widget names no metric"),
    ("no colour or size metric", "the heatmap names no metric"),
    ("carries no text", "the text widget carries no text"),
    ("no heading", "the section divider carries no heading"),
    ("nothing on this dashboard feeds it",
     "the widget waits on a selection nothing provides"),
    ("declares no columns, so the widget", "the view the widget shows declares no columns"),
)


# One wording helper for every surface: see vcfcf_migrator.wording.
_plural = plural


def _short_reason(reason: str) -> str:
    for needle, short in _SHORT_REASONS:
        if needle in reason:
            return short
    return reason


def _subtitle(node: Node) -> str:
    bits = [node.kind]
    if node.uuid:
        bits.append(node.uuid)
    if node.owner:
        bits.append(f"owner {node.owner}")
    bits.append(f"from {node.member}")
    return " | ".join(bits)


def fragment(graph: Graph, node: Node) -> str:
    """The preview as markup to drop inside another page (the selection page).

    The page that embeds this carries ``PREVIEW_CSS`` once; the fragment
    carries no style of its own so two previews on one page cannot disagree.
    """
    preview = build(graph, node)
    parts = [
        "<div class='pv'>",
        f"<h2 class='pv-title'>{_e(preview.title)}</h2>",
        f"<p class='pv-sub'>{_e(preview.subtitle)}</p>",
        "<p class='pv-banner'>Every value below is made up, derived by hash from the keys "
        "and names the export carries, so this page looks the same on every run. Names, "
        "titles, columns and keys are the export's own. Widget widths and order are the "
        "dashboard's own; widget heights are this page's, taken from the content so "
        "nothing is clipped.</p>",
        preview.body,
    ]
    if preview.notes:
        parts.append("<div class='pv-notes'><b>notes</b><ul>"
                     + "".join(f"<li>{_e(note)}</li>" for note in preview.notes)
                     + "</ul></div>")
    parts.append("</div>")
    return "".join(parts)


def render_page(graph: Graph, node: Node) -> str:
    """The preview as one standalone HTML file: inline CSS, inline SVG, no
    network of any kind."""
    return _PAGE.format(title=_e(f"{node.name} ({node.kind}) preview"),
                        css=PREVIEW_CSS, body=fragment(graph, node))
