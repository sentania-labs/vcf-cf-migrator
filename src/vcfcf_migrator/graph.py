"""The dependency graph over an export's own documents.

Edges, from the spec's scope section: dashboard to view, view to super
metric, super metric to super metric, alert to symptom and to
recommendation, notification rule to alert, report to view and to dashboard.
Three more are extracted because the corpus carries them and leaving them out
would let a closed selection still produce a bundle referencing something it
does not carry, which is the one failure mode subsetting can introduce on its
own:

* dashboard to super metric. Widgets address a super metric directly as
  ``sm_<uuid>`` without going through a view; 53 such references on one
  corpus export.
* notification rule to outbound setting, by ``PluginID``
  (``@pluginType``/``@pluginName``), which is the spec's "rule to endpoint".
* notification rule to notification template, via the container's
  ``ruleNameToTemplateNameMap``.
* symptom to super metric. A symptom's threshold can sit on a super metric
  attribute (``<Condition key="Super Metric|sm_<uuid>" type="metric"/>``); on
  three of the five corpus exports a symptom does exactly that, to a super
  metric the same export carries.

The list is not guesswork. Every document of every kind in all five corpus
exports was scanned for any identifier that resolves to another object in the
same export; the only hits not already an edge were the symptom one above and
super metric uuids quoted inside a description's prose, which are not
references and are deliberately not followed.

Reference extraction reads the raw document, not a re-parse into a factory
model: the export's own spellings are what the edges are made of, and the
library's walker (``vcfcf_core.common.dep_walker``) resolves factory YAML
models by *name*, which is a different graph over different inputs. The one
piece that carries straight across is the wire spelling of a super metric
reference, ``Super Metric|sm_<uuid>``, which
``vcfcf_core.supermetrics.crossref`` documents and which is the same token
here, in a view column, a dashboard widget and another super metric's
formula.

A node is identified by ``kind:ident``. ``ident`` is the uuid where the
export carries one; custom groups and outbound settings carry none, so their
identity is the name (and ``<pluginType>/<pluginName>``) the export gives
them. Dashboards get ``@<owner>`` appended, because the same dashboard uuid
appears under two owners on a real export and each copy is its own document.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from vcfcf_migrator import containers as _containers
from vcfcf_migrator.containers import Container

# ``Super Metric|sm_<uuid>``: the wire spelling of every super metric
# reference, in a view column, a widget config and an SM formula alike.
SM_REF_RE = re.compile(rb"sm_([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                       rb"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
VIEW_REF_RE = re.compile(rb'"viewDefinitionId"\s*:\s*"([^"]+)"')

KIND_ORDER = [
    "dashboard", "view", "supermetric", "customgroup", "symptom", "alert",
    "recommendation", "report", "notificationrule", "notificationtemplate",
    "outboundsetting",
]


@dataclass(frozen=True)
class Ref:
    """One edge out of a node: what it points at, and where it was found."""
    kind: str
    ident: str
    via: str


@dataclass
class Node:
    kind: str
    ident: str
    name: str
    uuid: str
    member: str
    index: int
    owner: str = ""
    refs: List[Ref] = field(default_factory=list)

    @property
    def key(self) -> str:
        if self.kind == "dashboard" and self.owner:
            return f"{self.kind}:{self.ident}@{self.owner}"
        return f"{self.kind}:{self.ident}"

    def label(self) -> str:
        """Kind, name and uuid. Custom groups and outbound settings carry no
        uuid on any corpus export, so for those the name is the identity and
        there is no bracket to print."""
        return f"{self.kind} {self.name}" + (f" [{self.uuid}]" if self.uuid else "")

    def as_dict(self) -> dict:
        return {
            "key": self.key, "kind": self.kind, "name": self.name,
            "uuid": self.uuid, "ident": self.ident, "member": self.member,
            "owner": self.owner or None,
            "refs": [{"kind": r.kind, "ident": r.ident, "via": r.via} for r in self.refs],
        }


@dataclass
class MissingEdge:
    """An edge whose target is not in the export. Information, not an error:
    the target instance may well already have the object."""
    source_key: str
    kind: str
    ident: str
    via: str


@dataclass
class Graph:
    nodes: Dict[str, Node] = field(default_factory=dict)
    containers: List[Container] = field(default_factory=list)
    unknown_members: List[str] = field(default_factory=list)
    missing: List[MissingEdge] = field(default_factory=list)
    # node key -> resolved target keys, in the order the references appear
    edges: Dict[str, List[str]] = field(default_factory=dict)

    def by_kind(self, kind: str) -> List[Node]:
        return [n for n in self.ordered() if n.kind == kind]

    def ordered(self) -> List[Node]:
        def key(node: Node):
            rank = KIND_ORDER.index(node.kind) if node.kind in KIND_ORDER else len(KIND_ORDER)
            return (rank, node.name.lower(), node.ident, node.owner)
        return sorted(self.nodes.values(), key=key)

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for node in self.nodes.values():
            out[node.kind] = out.get(node.kind, 0) + 1
        return out

    def roots(self) -> List[Node]:
        """Nodes nothing in the export points at: the top of the printed tree."""
        pointed_at = {t for targets in self.edges.values() for t in targets}
        return [n for n in self.ordered() if n.key not in pointed_at]

    def missing_for(self, key: str) -> List[MissingEdge]:
        return [m for m in self.missing if m.source_key == key]


def _resolve_index(nodes: Dict[str, Node]) -> Dict[Tuple[str, str], List[str]]:
    """(kind, ident) -> node keys. A dashboard uuid under two owners resolves
    to both: a reference to it means both documents, and the admin who wants
    only one says so with ``kind:uuid@owner``."""
    index: Dict[Tuple[str, str], List[str]] = {}
    for node in nodes.values():
        index.setdefault((node.kind, node.ident), []).append(node.key)
        if node.uuid and node.uuid != node.ident:
            index.setdefault((node.kind, node.uuid), []).append(node.key)
    return index


# ---------------------------------------------------------------------------
# Reference extraction, per kind, from the raw document
# ---------------------------------------------------------------------------

def _sm_refs(raw: bytes, via: str, skip: str = "") -> List[Ref]:
    out, seen = [], set()
    for match in SM_REF_RE.finditer(raw):
        ident = match.group(1).decode("ascii")
        if ident == skip or ident in seen:
            continue
        seen.add(ident)
        out.append(Ref("supermetric", ident, via))
    return out


def _dashboard_refs(raw: bytes) -> List[Ref]:
    out, seen = [], set()
    for match in VIEW_REF_RE.finditer(raw):
        ident = match.group(1).decode("utf-8", "replace")
        if ident and ident not in seen:
            seen.add(ident)
            out.append(Ref("view", ident, "widget viewDefinitionId"))
    return out + _sm_refs(raw, "widget metric Super Metric|sm_")


def _view_refs(raw: bytes) -> List[Ref]:
    return _sm_refs(raw, "column attributeKey Super Metric|sm_")


def _supermetric_refs(raw: bytes, self_ident: str) -> List[Ref]:
    return _sm_refs(raw, "formula Super Metric|sm_", skip=self_ident)


def _alert_refs(raw: bytes) -> List[Ref]:
    out: List[Ref] = []
    try:
        el = ET.fromstring(raw)
    except ET.ParseError:
        return out
    seen = set()
    for child in el.iter():
        ref = child.get("ref")
        if not ref:
            continue
        if child.tag in ("SymptomSet", "Symptom"):
            kind, via = "symptom", f"<{child.tag} ref>"
        elif child.tag == "Recommendation":
            kind, via = "recommendation", "<Recommendation ref>"
        else:
            continue
        if (kind, ref) in seen:
            continue
        seen.add((kind, ref))
        out.append(Ref(kind, ref, via))
    return out


def _report_refs(raw: bytes) -> List[Ref]:
    out: List[Ref] = []
    try:
        el = ET.fromstring(raw)
    except ET.ParseError:
        return out
    for section in el.iter("Section"):
        content_type = (section.findtext("ContentType") or "").strip()
        key = (section.findtext("ContentKey") or "").strip()
        if not key:
            continue
        if content_type == "View":
            out.append(Ref("view", key, "section ContentKey"))
        elif content_type == "Dashboard":
            out.append(Ref("dashboard", key, "section ContentKey"))
    return out


def _walk_alert_ids(node, out: List[str]) -> None:
    """Every ``AlertDefinitionID`` anywhere under a notification rule's
    condition entries; the nesting differs between 8.x and 9.1.1."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "AlertDefinitionID":
                if isinstance(value, list):
                    out.extend(str(v) for v in value if isinstance(v, str))
                elif isinstance(value, str):
                    out.append(value)
            else:
                _walk_alert_ids(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_alert_ids(item, out)


def _rule_refs(raw: bytes) -> List[Ref]:
    try:
        doc = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(doc, dict):
        return []
    out: List[Ref] = []
    ids: List[str] = []
    _walk_alert_ids(doc.get("entry"), ids)
    for ident in dict.fromkeys(ids):
        out.append(Ref("alert", ident, "condition AlertDefinitionID"))
    plugin = doc.get("PluginID")
    if isinstance(plugin, dict):
        ptype = plugin.get("@pluginType") or doc.get("PluginType")
        pname = plugin.get("@pluginName")
        if ptype and pname:
            out.append(Ref("outboundsetting", f"{ptype}/{pname}", "rule PluginID"))
    return out


_REF_EXTRACTORS = {
    "dashboard": lambda entry: _dashboard_refs(entry.raw),
    "view": lambda entry: _view_refs(entry.raw),
    "supermetric": lambda entry: _supermetric_refs(entry.raw, entry.ident),
    # A symptom's threshold can be on a super metric attribute:
    # <Condition key="Super Metric|sm_<uuid>" type="metric" .../>.
    "symptom": lambda entry: _sm_refs(entry.raw, "symptom Condition key Super Metric|sm_"),
    "alert": lambda entry: _alert_refs(entry.raw),
    "report": lambda entry: _report_refs(entry.raw),
    "notificationrule": lambda entry: _rule_refs(entry.raw),
}


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def build_graph(members: Dict[str, bytes]) -> Graph:
    """Every content object in *members*, with its edges resolved."""
    found, unknown = _containers.discover(members)
    graph = Graph(containers=found, unknown_members=unknown)

    for container in found:
        for entry in container.entries():
            node = Node(kind=entry.kind, ident=entry.ident, name=entry.name,
                        uuid=entry.uuid, member=container.member, index=entry.index,
                        owner=entry.owner)
            node.refs = list(_REF_EXTRACTORS.get(entry.kind, lambda _e: [])(entry))
            if node.key in graph.nodes:
                # The same object in two members (a full export writes the
                # notification templates into both notificationrules.json and
                # payloadtemplates.json). The first member wins the node; the
                # bundle writer still carries both copies.
                continue
            graph.nodes[node.key] = node

    _add_rule_template_refs(found, graph)

    index = _resolve_index(graph.nodes)
    for node in graph.nodes.values():
        targets: List[str] = []
        for ref in node.refs:
            hits = index.get((ref.kind, ref.ident))
            if not hits:
                graph.missing.append(MissingEdge(node.key, ref.kind, ref.ident, ref.via))
                continue
            for hit in hits:
                if hit != node.key and hit not in targets:
                    targets.append(hit)
        graph.edges[node.key] = targets
    return graph


def _add_rule_template_refs(found: Sequence[Container], graph: Graph) -> None:
    """``ruleNameToTemplateNameMap`` is a container-level table, so the edge
    it describes is added once the rules are nodes."""
    for container in found:
        if not isinstance(container, _containers.NotificationRulesContainer):
            continue
        raw = _containers.rawdoc.member(container.block_members, "ruleNameToTemplateNameMap")
        if raw is None:
            continue
        try:
            doc = json.loads(raw.raw(container.text))
        except ValueError:
            continue
        pairs: List[Tuple[str, str]] = []
        for block in doc if isinstance(doc, list) else []:
            entry = block.get("entry") if isinstance(block, dict) else None
            items = entry if isinstance(entry, list) else ([entry] if isinstance(entry, dict) else [])
            for item in items:
                strings = item.get("string") if isinstance(item, dict) else None
                if isinstance(strings, list) and len(strings) == 2:
                    pairs.append((str(strings[0]), str(strings[1])))
        by_name = {(n.kind, n.name): n for n in graph.nodes.values()}
        for rule_name, template_name in pairs:
            rule = by_name.get(("notificationrule", rule_name))
            if rule is None:
                continue
            template = by_name.get(("notificationtemplate", template_name))
            ident = template.ident if template is not None else template_name
            ref = Ref("notificationtemplate", ident, "ruleNameToTemplateNameMap")
            if ref not in rule.refs:
                rule.refs.append(ref)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_tree(graph: Graph, roots: Optional[Sequence[Node]] = None,
                max_depth: int = 40) -> str:
    """The readable tree: one line per node, children indented under it."""
    lines: List[str] = []
    counts = graph.counts()
    lines.append("items: " + (", ".join(f"{k}={counts[k]}" for k in KIND_ORDER if k in counts)
                              or "none"))
    seen_global = set()
    starts = list(roots) if roots is not None else graph.roots()

    def walk(node: Node, depth: int, seen: set) -> None:
        pad = "  " * depth
        lines.append(pad + node.label() + (f" owner {node.owner}" if node.owner else ""))
        if node.key in seen or depth >= max_depth:
            if graph.edges.get(node.key):
                lines.append(f"{pad}  (already shown above)")
            return
        seen = seen | {node.key}
        for target in graph.edges.get(node.key, []):
            child = graph.nodes.get(target)
            if child is not None:
                walk(child, depth + 1, seen)
        for gap in graph.missing_for(node.key):
            lines.append(f"{pad}  MISSING {gap.kind} [{gap.ident}] (via {gap.via})")

    for node in starts:
        walk(node, 0, set())
        seen_global.add(node.key)

    shown = _reachable(graph, starts)
    orphans = [n for n in graph.ordered() if n.key not in shown]
    if orphans:
        lines.append("")
        lines.append("also carried, reachable only as a dependency above:")
        for node in orphans:
            lines.append("  " + node.label())
    if graph.missing:
        lines.append("")
        lines.append(f"edges to objects this export does not carry: {len(graph.missing)}")
        for gap in graph.missing:
            source = graph.nodes.get(gap.source_key)
            lines.append(f"  {gap.kind} [{gap.ident}] wanted by "
                         f"{source.label() if source else gap.source_key} (via {gap.via})")
    if graph.unknown_members:
        lines.append("")
        lines.append("members this tool does not understand (never carried unless selected, "
                     "and they cannot be selected): " + ", ".join(graph.unknown_members))
    return "\n".join(lines) + "\n"


def _reachable(graph: Graph, starts: Sequence[Node]) -> set:
    """Every node key reachable from *starts*, computed once."""
    stack = [n.key for n in starts]
    seen: set = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.edges.get(current, []))
    return seen


def as_dict(graph: Graph) -> dict:
    return {
        "counts": graph.counts(),
        "nodes": [n.as_dict() for n in graph.ordered()],
        "edges": {k: v for k, v in sorted(graph.edges.items()) if v},
        "roots": [n.key for n in graph.roots()],
        "missing": [{"source": m.source_key, "kind": m.kind, "ident": m.ident, "via": m.via}
                    for m in graph.missing],
        "unknown_members": list(graph.unknown_members),
    }
