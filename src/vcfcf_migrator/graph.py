"""The dependency graph over an export's own documents.

An export writes a reference in one of two spellings, and both have to be
followed. **By uuid** is the 9.x norm: ``sm_<uuid>``, ``viewDefinitionId``,
``ref="SymptomDefinition-<uuid>"``. **By name** is not a fallback, it is the
only spelling available in places: an 8.x super metric formula names another
by ``Super Metric|@supermetric:"<Name>"``, and a custom group has no uuid in
an export at all, so everything that reaches one reaches it by name. An audit
that resolves uuid-shaped identifiers is blind to the second class by
construction, which is how the by-name super metric edge was missed once
already.

The edges, with how the export writes each:

===========================  =================  ==================================
From and to                  Spelling           Where it is read
===========================  =================  ==================================
dashboard to view            uuid               widget ``viewDefinitionId``
dashboard to supermetric     uuid and name      widget metric key
dashboard to customgroup     name               widget resource binding
view to supermetric          uuid and name      column ``attributeKey`` Property
supermetric to supermetric   uuid and name      the ``formula`` field
symptom to supermetric       uuid and name      ``<Condition key=...>``
alert to symptom             uuid               ``<SymptomSet ref>``, ``<Symptom ref>``
alert to recommendation      uuid               ``<Recommendation ref>``
customgroup to customgroup   name               membership ``RelationshipRule``
notificationrule to alert    uuid               condition ``AlertDefinitionID``
notificationrule to outbound name               rule ``PluginID``
notificationrule to template name               ``ruleNameToTemplateNameMap``
report to view, dashboard    uuid               section ``ContentKey``
===========================  =================  ==================================

Seven of those are named in the spec's scope section. The other six are here
because the corpus carries them and leaving one out lets a closed selection
still produce a bundle referencing something it does not carry, which is the
one failure mode subsetting can introduce on its own.

Two things are deliberately *not* followed. A super metric's description is
prose and these exports quote another super metric's uuid in it, so super
metric references are read from the ``formula`` field alone. And a by-name
value that names no object in this export is not a missing dependency: most
membership ``ruleStringValue``s name ordinary resources, and reporting each
one would drown the report an admin reads to find out what will break.

The wire spellings come from the format, and the by-name token from the
factory's own ``vcfcf_core.supermetrics.crossref``, which defines it. The
library's walker (``vcfcf_core.common.dep_walker``) resolves factory YAML
models by name within a project scope; an export has no projects, so the
scope-based tie-break does not apply here and a name more than one object
answers to carries every match instead (see ``_resolve_index``).

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

from vcfcf_core.supermetrics.crossref import crossref_names

from vcfcf_migrator import containers as _containers
from vcfcf_migrator.containers import Container

# ``Super Metric|sm_<uuid>``: the wire spelling of every super metric
# reference, in a view column, a widget config and an SM formula alike.
SM_REF_RE = re.compile(rb"sm_([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                       rb"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
VIEW_REF_RE = re.compile(rb'"viewDefinitionId"\s*:\s*"([^"]+)"')
# A dashboard widget scoped to a custom group binds to it as a resource, by
# name, with a Container resource kind: {"resource": {"resourceName": "<group
# name>", "resourceKindId": "002009ContainerFunction"}}. The Container marker
# is required, so a widget bound to an ordinary resource that happens to share
# a group's name is not mistaken for a reference to the group.
RESOURCE_BINDING_RE = re.compile(
    rb'"resourceName"\s*:\s*"([^"]*)"[^{}]*?"resourceKindId"\s*:\s*"[^"]*Container[^"]*"'
    rb'|"resourceKindId"\s*:\s*"[^"]*Container[^"]*"[^{}]*?"resourceName"\s*:\s*"([^"]*)"')
# A custom group whose membership is defined relative to another group names
# it in a RelationshipRule's ruleStringValue.
GROUP_RULE_RE = re.compile(rb'"ruleStringValue"\s*:\s*"([^"]*)"')

KIND_ORDER = [
    "dashboard", "view", "supermetric", "customgroup", "symptom", "alert",
    "recommendation", "report", "notificationrule", "notificationtemplate",
    "outboundsetting",
]


@dataclass(frozen=True)
class Ref:
    """One edge out of a node: what it points at, and where it was found.

    ``optional`` marks a reference whose target is only *sometimes* an object
    in the export. A custom group's membership rule names resources, most of
    which are ordinary VMs; a widget binds to a resource that may or may not
    be a group. Failing to resolve one of those is normal and must not be
    reported as a missing dependency, or the report that tells an admin what
    will break on import fills up with things that will not.
    """
    kind: str
    ident: str
    via: str
    optional: bool = False


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
    # By-name references that more than one object answers to; every match is
    # carried, and the admin is told rather than left to find out.
    ambiguous: List[str] = field(default_factory=list)
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
    """(kind, identifier) -> node keys, for every identifier an object can be
    named by: its uuid, its export ident, and its display name.

    Two nodes can answer to one identifier, and both cases are deliberate. A
    dashboard uuid under two owners resolves to both, because a reference to
    it means both documents and the admin who wants one says so with
    ``kind:uuid@owner``. Two super metrics sharing a display name also resolve
    to both: the factory picks one by project scope
    (``dep_walker._pick_sm_by_name``), but an export has no projects to pick
    by, and guessing wrong here means a bundle missing the super metric its
    formula actually meant. Carrying both is the error that can be undone.
    """
    index: Dict[Tuple[str, str], List[str]] = {}
    for node in nodes.values():
        for identifier in (node.ident, node.uuid, node.name):
            if not identifier:
                continue
            keys = index.setdefault((node.kind, identifier), [])
            if node.key not in keys:
                keys.append(node.key)
    return index


# ---------------------------------------------------------------------------
# Reference extraction, per kind, from the raw document
# ---------------------------------------------------------------------------

def _sm_refs(raw: bytes, where: str, skip: str = "") -> List[Ref]:
    """Super metric references in *raw*, in both wire spellings.

    ``Super Metric|sm_<uuid>`` is the resolved form. ``Super
    Metric|@supermetric:"<Name>"`` is the by-name form, which an 8.x export
    carries in super metric formulas, and which is invisible to any audit that
    only resolves uuid-shaped identifiers. The token is found with the
    factory's own ``vcfcf_core.supermetrics.crossref``, which is where that
    spelling is defined, rather than with a second regex that could drift from
    it: the token is matched case-insensitively, the quoted name exactly.
    """
    out, seen = [], set()
    for match in SM_REF_RE.finditer(raw):
        ident = match.group(1).decode("ascii")
        if ident == skip or ident in seen:
            continue
        seen.add(ident)
        out.append(Ref("supermetric", ident, f"{where} Super Metric|sm_<uuid>"))
    for name in crossref_names(raw.decode("utf-8", "replace")):
        if name and name not in seen:
            seen.add(name)
            out.append(Ref("supermetric", name,
                           f'{where} Super Metric|@supermetric:"<name>"'))
    return out


def _dashboard_refs(raw: bytes) -> List[Ref]:
    out, seen = [], set()
    for match in VIEW_REF_RE.finditer(raw):
        ident = match.group(1).decode("utf-8", "replace")
        if ident and ident not in seen:
            seen.add(ident)
            out.append(Ref("view", ident, "widget viewDefinitionId"))
    groups = set()
    for match in RESOURCE_BINDING_RE.finditer(raw):
        name = (match.group(1) or match.group(2) or b"").decode("utf-8", "replace")
        if name and name not in groups:
            groups.add(name)
            out.append(Ref("customgroup", name, "widget resource binding, by name",
                           optional=True))
    return out + _sm_refs(raw, "widget metric")


def _customgroup_refs(raw: bytes) -> List[Ref]:
    """A membership RelationshipRule naming another custom group.

    Custom groups carry no uuid in an export, so this is a by-name reference
    and stays one; the node's identity is its name for the same reason. A
    value naming no group in this export resolves to nothing and is reported
    as missing, which is right: most ruleStringValues name ordinary resources.
    """
    out, seen = [], set()
    for match in GROUP_RULE_RE.finditer(raw):
        name = match.group(1).decode("utf-8", "replace")
        if name and name not in seen:
            seen.add(name)
            out.append(Ref("customgroup", name, "membership RelationshipRule, by name",
                           optional=True))
    return out


def _view_refs(raw: bytes) -> List[Ref]:
    return _sm_refs(raw, "column attributeKey")


def _supermetric_refs(raw: bytes, self_ident: str, self_name: str = "") -> List[Ref]:
    """Read the formula, and only the formula.

    A super metric's description is prose, and these exports quote another
    super metric's uuid in it ("Companion to X (UUID ...)"). Scanning the
    whole document turns that into a reference, which at best adds noise to
    the one report telling an admin what will be missing on import, and at
    worst over-carries. The formula is the only field a reference can live in,
    so it is the only field read.
    """
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = None
    formula = doc.get("formula") if isinstance(doc, dict) else None
    if not isinstance(formula, str):
        # Not the shape this tool knows; fall back to the whole document
        # rather than silently finding no dependencies at all.
        return [r for r in _sm_refs(raw, "formula", skip=self_ident)
                if r.ident != self_name]
    refs = _sm_refs(formula.encode("utf-8"), "formula", skip=self_ident)
    return [r for r in refs if r.ident != self_name]


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
    "supermetric": lambda entry: _supermetric_refs(entry.raw, entry.ident, entry.name),
    "customgroup": lambda entry: _customgroup_refs(entry.raw),
    # A symptom's threshold can be on a super metric attribute:
    # <Condition key="Super Metric|sm_<uuid>" type="metric" .../>.
    "symptom": lambda entry: _sm_refs(entry.raw, "symptom Condition key"),
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
                if not ref.optional:
                    graph.missing.append(MissingEdge(node.key, ref.kind, ref.ident, ref.via))
                continue
            if len(hits) > 1 and ref.ident not in (h.split(":", 1)[1] for h in hits):
                note = (f"{node.label()} names {ref.kind} {ref.ident!r} and "
                        f"{len(hits)} objects answer to that name; all are carried")
                if note not in graph.ambiguous:
                    graph.ambiguous.append(note)
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
    if graph.ambiguous:
        lines.append("")
        lines.append(f"references by name that more than one object answers to: {len(graph.ambiguous)}")
        for note in graph.ambiguous:
            lines.append(f"  {note}")
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
        "ambiguous": list(graph.ambiguous),
        "unknown_members": list(graph.unknown_members),
    }
