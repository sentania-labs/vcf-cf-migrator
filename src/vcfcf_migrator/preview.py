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

**A widget type nobody laid out says what it is.** Drawing a Geo widget as a
bar chart would be worse than drawing nothing, because the admin would believe
it. An unhandled type renders as a labelled placeholder naming the type, and
the page's footer counts how many it met.

**No network.** The page is one file: inline CSS, inline SVG, no font, no
script, no image. It opens on a workstation with no route to anything.
"""
from __future__ import annotations

import html
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from vcfcf_migrator import mockdata
from vcfcf_migrator.graph import Graph, Node

MOCK_ROWS = 5

# Widget types this preview lays out on purpose. Anything else is named rather
# than drawn: see ``_widget_unhandled``.
HANDLED_WIDGETS = (
    "View", "Scoreboard", "MetricChart", "SparklineChart", "ParetoAnalysis",
    "Heatmap", "PropertyList", "AlertList", "ProblemAlertsList", "ResourceList",
    "TextDisplay", "HealthChart", "Section",
)

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
.pv .pv-w > h3 { font-size:12px; font-weight:600; margin:0 0 6px; display:flex; gap:8px; align-items:baseline }
.pv .pv-w > h3 .pv-type { margin-left:auto; font-weight:400; font-size:10px; color:var(--pv-ink3);
  text-transform:uppercase; letter-spacing:.04em; white-space:nowrap }
.pv .pv-key { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:10px; color:var(--pv-ink3);
  word-break:break-all }
.pv table.pv-tbl { width:100%; border-collapse:collapse; font-size:11.5px }
.pv table.pv-tbl th { text-align:left; color:var(--pv-ink3); font-weight:500; font-size:10.5px;
  padding:3px 6px; border-bottom:1px solid var(--pv-line); white-space:nowrap }
.pv table.pv-tbl td { padding:3px 6px; border-bottom:1px solid var(--pv-line); color:var(--pv-ink2) }
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
.pv .pv-text { color:var(--pv-ink2); font-size:12px; white-space:pre-wrap }
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
class Preview:
    """One rendered preview, with what the renderer met on the way."""
    node: Node
    title: str
    subtitle: str
    body: str
    widget_types: Dict[str, int] = field(default_factory=dict)
    unhandled_types: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


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
    raise PreviewError(f"{node.label()} has no document in {node.member}")


def _json_doc(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError as e:
        raise PreviewError(f"the document is not readable JSON: {e}") from e


def _xml_doc(raw: bytes) -> ET.Element:
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        raise PreviewError(f"the document is not readable XML: {e}") from e


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


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
                    is_string=fields.get("isStringAttribute", "").lower() == "true",
                    is_property=fields.get("isProperty", "").lower() == "true",
                    unit=fields.get("preferredUnitId", "")))
    return out


def _cell(column: Column, row: int) -> Tuple[str, bool]:
    """One mock cell: its text, and whether it is numeric (right aligned)."""
    if column.is_string:
        return mockdata.string_value(column.key, row, column.label), False
    return mockdata.metric_value(column.key, column.unit, row, column.label), True


def _columns_table(columns: Sequence[Column], rows: int = MOCK_ROWS,
                   show_keys: bool = True) -> str:
    """The view's columns as a table with mock rows under them."""
    if not columns:
        return ("<div class='pv-placeholder'>this view declares no columns in its "
                "attributes selector</div>")
    head = "".join(
        f"<th>{_e(c.label)}"
        + (f"<br><span class='pv-key'>{_e(c.key)}</span>" if show_keys and c.key else "")
        + "</th>" for c in columns)
    body = []
    for row in range(rows):
        cells = []
        for column in columns:
            text, numeric = _cell(column, row)
            cells.append(f"<td class='num'>{_e(text)}</td>" if numeric
                         else f"<td>{_e(text)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
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
    return (f"<svg class='pv-chart' viewBox='0 0 300 64' preserveAspectRatio='none' "
            f"role='img' aria-label='mock ranking'>"
            f"<line x1='0' y1='62' x2='300' y2='62' stroke='var(--pv-line)'/>"
            + "".join(rects) + "</svg>")


def _tiles(metrics: Sequence[Tuple[str, str, str]], seed: str) -> str:
    if not metrics:
        return ("<div class='pv-placeholder'>this widget names no metric in the "
                "export; it is bound at view time</div>")
    cells = []
    for label, key, unit in metrics[:8]:
        value = mockdata.metric_value(key or label, unit, 0, label)
        cells.append(f"<div class='pv-tile'><div class='v'>{_e(value)}</div>"
                     f"<div class='l'>{_e(label)}</div>"
                     + (f"<div class='pv-key'>{_e(key)}</div>" if key else "")
                     + "</div>")
    return "<div class='pv-tiles'>" + "".join(cells) + "</div>"


def _widget_view(cfg: dict, ctx: dict) -> str:
    graph: Graph = ctx["graph"]
    view_id = str(cfg.get("viewDefinitionId") or "")
    node = _find_node(graph, "view", view_id) if view_id else None
    if node is None:
        return ("<div class='pv-placeholder'>view <span class='pv-key'>"
                + _e(view_id or "(none named)")
                + "</span> <span class='pv-miss'>is not in this export</span>, so its "
                  "columns cannot be shown</div>")
    try:
        root = _xml_doc(raw_document(graph, node))
    except PreviewError as e:
        return f"<div class='pv-placeholder'>{_e(str(e))}</div>"
    columns = view_columns(root)
    presentation = (root.find("Presentation").get("type")
                    if root.find("Presentation") is not None else "")
    head = f"<div class='pv-key'>{_e(node.name)} ({_e(presentation or 'view')})</div>"
    if presentation and presentation != "list":
        return head + _non_list_view(presentation, columns)
    return head + _columns_table(columns[:6], rows=3)


def _non_list_view(presentation: str, columns: Sequence[Column]) -> str:
    """A view whose presentation is not a list says what it is.

    Drawing a donut for a distribution view would be drawing buckets the
    export defines and this tool does not read, which is exactly the kind of
    plausible-looking wrong the preview is supposed to avoid.
    """
    attrs = ", ".join(_e(c.label) for c in columns[:6]) or "no attributes declared"
    return (f"<div class='pv-placeholder'>a <b>{_e(presentation)}</b> view. This preview "
            f"lays out list views only, so its shape is stated rather than drawn.<br>"
            f"attributes: {attrs}</div>")


def _widget_scoreboard(cfg: dict, ctx: dict) -> str:
    return _tiles(_cfg_metrics(cfg), ctx["seed"])


def _widget_metricchart(cfg: dict, ctx: dict) -> str:
    metrics = _cfg_metrics(cfg)
    if not metrics:
        return _sparkline(ctx["seed"])
    parts = []
    for label, key, unit in metrics[:3]:
        parts.append(f"<div class='pv-key'>{_e(label)}{(' | ' + _e(key)) if key else ''}</div>"
                     + _sparkline(key or label, unit))
    return "".join(parts)


def _widget_pareto(cfg: dict, ctx: dict) -> str:
    metrics = _cfg_metrics(cfg)
    label, key, unit = metrics[0] if metrics else (
        str(cfg.get("metricName") or "(no metric named)"), "", "")
    unit = unit or _cfg_unit(cfg)
    bars = cfg.get("barsCount")
    count = bars if isinstance(bars, int) and 0 < bars <= 40 else 10
    return (f"<div class='pv-key'>{_e(label)}{(' | ' + _e(key)) if key else ''}</div>"
            + _bars(key or label, count, unit))


def _widget_heatmap(cfg: dict, ctx: dict) -> str:
    configs = cfg.get("configs")
    label = ""
    if isinstance(configs, list) and configs and isinstance(configs[0], dict):
        first = configs[0]
        label = str(first.get("colorBy") or first.get("sizeBy") or "")
    cells = []
    palette = ("var(--pv-ok)", "var(--pv-ok)", "var(--pv-warn)", "var(--pv-bad)",
               "var(--pv-accent)")
    for i in range(24):
        colour = mockdata.pick(palette, "heat", ctx["seed"], i)
        cells.append(f"<i style='background:{colour}'></i>")
    head = f"<div class='pv-key'>{_e(label)}</div>" if label else ""
    return head + "<div class='pv-heat'>" + "".join(cells) + "</div>"


def _widget_propertylist(cfg: dict, ctx: dict) -> str:
    metrics = _cfg_metrics(cfg)
    if not metrics:
        return ("<div class='pv-placeholder'>this property list names no property in "
                "the export; it is bound at view time</div>")
    rows = []
    for label, key, unit in metrics[:8]:
        value = (mockdata.string_value(key or label, 0, label) if not unit
                 else mockdata.metric_value(key or label, unit, 0, label))
        rows.append(f"<tr><td>{_e(label)}<br><span class='pv-key'>{_e(key)}</span></td>"
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


def _widget_text(cfg: dict, ctx: dict) -> str:
    """The widget's own text, shown as text.

    ``viewModeHTML`` is markup the export carries, and this page never injects
    it: the preview is a local file an admin opens, and pasting a document's
    markup into it would let an export decide what the admin's browser runs.
    The tags are stripped for readability and the result is escaped.
    """
    raw = cfg.get("viewModeHTML") or cfg.get("editorData") or ""
    if isinstance(raw, dict):
        raw = json.dumps(raw)
    text = _strip_tags(str(raw))
    if not text:
        location = cfg.get("locationUrl") or cfg.get("locationFile") or ""
        if location:
            return (f"<div class='pv-placeholder'>text widget sourced from "
                    f"<span class='pv-key'>{_e(location)}</span>, which this preview does "
                    "not fetch</div>")
        return "<div class='pv-placeholder'>text widget with no content in the export</div>"
    return f"<div class='pv-text'>{_e(text[:600])}</div>"


def _strip_tags(markup: str) -> str:
    out: List[str] = []
    depth = 0
    for char in markup:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
            out.append(" ")
        elif depth == 0:
            out.append(char)
    return " ".join("".join(out).split())


def _widget_healthchart(cfg: dict, ctx: dict) -> str:
    label = str(cfg.get("metricName") or cfg.get("metricLabel") or "")
    key = str(cfg.get("metricKey") or "")
    unit = _cfg_unit(cfg)
    head = (f"<div class='pv-key'>{_e(label)}{(' | ' + _e(key)) if key else ''}</div>"
            if (label or key) else "")
    return head + _sparkline(key or label or ctx["seed"], unit, "var(--pv-ok)")


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


def _widget_unhandled(widget_type: str) -> str:
    return (f"<div class='pv-placeholder'>a <b>{_e(widget_type or 'untyped')}</b> widget. "
            "This preview does not lay out this type, so its shape is named rather than "
            "drawn.</div>")


# ---------------------------------------------------------------------------
# Per-kind previews
# ---------------------------------------------------------------------------

def _dashboard_preview(graph: Graph, node: Node, preview: Preview) -> str:
    doc = _json_doc(raw_document(graph, node))
    widgets = doc.get("widgets")
    widgets = [w for w in widgets if isinstance(w, dict)] if isinstance(widgets, list) else []
    columns = doc.get("gridsterMaxColumns")
    if not isinstance(columns, int) or not 1 <= columns <= 24:
        columns = 12

    tabs: List[object] = []
    for widget in widgets:
        tab = widget.get("tabId")
        if tab not in tabs:
            tabs.append(tab)

    parts: List[str] = []
    for tab in tabs:
        members = [w for w in widgets if w.get("tabId") == tab]
        if len(tabs) > 1:
            parts.append(f"<h4 class='pv-h'>tab {_e(tab if tab is not None else 'default')}"
                         f" ({len(members)} widgets)</h4>")
        parts.append(_widget_grid(graph, members, columns, preview))
    if not widgets:
        parts.append("<div class='pv-placeholder'>this dashboard carries no widgets</div>")

    description = str(doc.get("description") or "").strip()
    head = f"<p class='pv-sub'>{_e(description)}</p>" if description else ""
    return head + "".join(parts)


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
            return (1 << 30, 1 << 30, index)

    return [w for _pos, w in sorted(enumerate(widgets), key=position)]


def _widget_grid(graph: Graph, widgets: Sequence[dict], columns: int,
                 preview: Preview) -> str:
    cells: List[str] = []
    for position, widget in enumerate(_ordered_widgets(widgets)):
        widget_type = str(widget.get("type") or "")
        preview.widget_types[widget_type] = preview.widget_types.get(widget_type, 0) + 1
        cfg = widget.get("config") if isinstance(widget.get("config"), dict) else {}
        title = str(widget.get("title") or cfg.get("title") or "").strip()
        renderer = WIDGET_RENDERERS.get(widget_type)
        seed = f"{widget.get('id') or position}:{widget_type}"
        if renderer is None:
            preview.unhandled_types[widget_type] = (
                preview.unhandled_types.get(widget_type, 0) + 1)
            inner = _widget_unhandled(widget_type)
        else:
            inner = renderer(cfg, {"graph": graph, "seed": seed, "widget": widget})
        cells.append(_widget_cell(widget, widget_type, title, inner, columns))
    if not cells:
        return ""
    return (f"<div class='pv-grid' style='grid-template-columns:repeat({columns},1fr)'>"
            + "".join(cells) + "</div>")


def _widget_cell(widget: dict, widget_type: str, title: str, inner: str,
                 columns: int) -> str:
    """One widget frame, placed where the dashboard places it.

    The columns are the export's own: gridster ``x`` is one-based and ``w`` is
    a span, so a half-width widget stays half width and a full-width one still
    runs the width of the dashboard. The *height* is the content's rather than
    the export's, which is a deliberate trade: honouring ``h`` clipped the
    table inside a widget whose mock rows are taller than the source's row
    band, and a clipped widget is exactly the thing an admin cannot recognise.
    A widget with no coordinates (rare, but the 8.x exports carry some) flows
    after the placed ones rather than landing on top of one.
    """
    coords = widget.get("gridsterCoords")
    style = ""
    if isinstance(coords, dict):
        try:
            x = max(1, min(int(coords.get("x", 1)), columns))
            w = max(1, min(int(coords.get("w", columns)), columns - x + 1))
        except (TypeError, ValueError):
            style = ""
        else:
            style = f"grid-column:{x} / span {w}"
    klass = "pv-section" if widget_type == "Section" else "pv-w"
    head = (f"<h3>{_e(title or '(untitled widget)')}"
            f"<span class='pv-type'>{_e(widget_type or 'no type')}</span></h3>")
    body = inner if widget_type != "Section" else ""
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
    if presentation and presentation != "list":
        preview.notes.append(
            f"this view's presentation is {presentation}; only list views are laid out")
        return head + "<h4 class='pv-h'>attributes</h4>" + _non_list_view(presentation, columns)
    return head + "<h4 class='pv-h'>columns, with mock rows</h4>" + _columns_table(columns)


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
        parts.append(_symptom_logic(graph, state))
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
        parts.append("<div class='pv-placeholder'>this alert declares no state</div>")
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


def _symptom_logic(graph: Graph, state: ET.Element) -> str:
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
        return "<div class='pv-placeholder'>this state names no symptoms</div>"
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
    for state in root.findall("State"):
        parts.append(f"<h4 class='pv-h'>condition, severity "
                     f"{_e(state.get('severity') or 'not declared')}</h4>")
        items = [f"<li>{_condition_words(condition)}</li>"
                 for condition in state.findall("Condition")]
        parts.append("<ul class='pv-logic'>" + "".join(items) + "</ul>" if items
                     else "<div class='pv-placeholder'>this state declares no condition</div>")
    return "".join(parts)


def _condition_words(condition: ET.Element) -> str:
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
        instanced = " (instanced)" if condition.get("instanced") else ""
        return (f"<code>{_e(key)}</code> <span class='op'>{_e(operator)}</span>{tail}"
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
    body = f"<div class='pv-text'>{_e(text)}</div>" if text else (
        "<div class='pv-placeholder'>this recommendation carries no description</div>")
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
    body += ("<table class='pv-tbl'><thead><tr><th>#</th><th>section</th><th>content</th>"
             "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>") if rows else (
        "<div class='pv-placeholder'>this report declares no sections</div>")
    return body


def _customgroup_preview(graph: Graph, node: Node, preview: Preview) -> str:
    doc = _json_doc(raw_document(graph, node))
    facts = _facts([
        ("group kind", f"{doc.get('adapterKind') or ''} "
                       f"{doc.get('resourceKind') or ''}".strip() or "not declared"),
        ("membership", "kept up to date automatically"
                       if doc.get("autoResolveMembership") else "fixed at import"),
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
        blocks.append("<div class='pv-placeholder'>this group declares no membership "
                      "rules, so its members were picked by hand on the source</div>")
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
        ("enabled", "no" if str(doc.get("Disabled", "")).lower() == "true" else "yes"),
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
    body = ("<h4 class='pv-h'>conditions</h4><ul class='pv-logic'>" + "".join(items) + "</ul>"
            ) if items else "<div class='pv-placeholder'>this rule declares no conditions</div>"
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
            ("enabled", "yes" if config.get("enabled") else "no")]
    for key, value in sorted(config.items()):
        if key in ("pluginName", "enabled") or isinstance(value, (dict, list)):
            continue
        rows.append((key, str(value)))
    return _facts(rows[:14]) + (
        "<p class='pv-sub'>values the export encrypted are carried through untouched and are "
        "not shown here; the target's import asks for the password used at export time</p>")


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
        return preview
    preview.body = renderer(graph, node, preview)
    if preview.unhandled_types:
        preview.notes.append(
            "widget types named rather than drawn: "
            + ", ".join(f"{name} x{count}" for name, count
                        in sorted(preview.unhandled_types.items())))
    return preview


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
        "titles, columns and keys are the export's own.</p>",
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
