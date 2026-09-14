"""The dependency graph over an export's own documents.

**References are read from the parsed document, never with a regex over its
text.** That is a correctness rule, not a style one. The same reference can be
written in several shapes: a widget's scope is an object in one widget and a
list of objects in the next, a notification rule writes that same scope under
another key, an 8.x formula names a super metric where a 9.x one gives its
uuid. A regex matches one shape and says nothing about the others, which is
how three sibling shapes were missed one review round after another. Walking
the parsed structure over a field whose shapes have been enumerated cannot
miss a sibling shape: it either handles it or shows up as one you have not
handled. Two regexes remain, both matching inside a single *value*: the
metric-key spelling ``Super Metric|sm_<uuid>`` and, through
``vcfcf_core.supermetrics.crossref``, the by-name ``@supermetric:"<Name>"``.

An export writes a reference by uuid or by name, and by name is not a
fallback: an export gives custom groups no uuid at all, so everything that
reaches one reaches it by name.

The edges, with how the export writes each:

============================  =============  =====================================
From and to                   Spelling       Where it is read
============================  =============  =====================================
dashboard to view             uuid           widget ``config.viewDefinitionId``
dashboard to supermetric      uuid and name  any widget string value
dashboard to customgroup      name           widget ``config.resource`` scope
view to supermetric           uuid and name  any attribute or text value
supermetric to supermetric    uuid and name  the ``formula`` field
symptom to supermetric        uuid and name  any attribute or text value
alert to symptom              uuid           ``<SymptomSet ref>``, ``<Symptom ref>``
alert to recommendation       uuid           ``<Recommendation ref>``
customgroup to customgroup    name           membership ``RelationshipRule``
customgroup to policy         uuid           ``policy``, always reported missing
notificationrule to alert     uuid           condition ``AlertDefinitionID``
notificationrule to group     name           condition ``ResourceID.resourceName``
notificationrule to outbound  name           rule ``PluginID``
notificationrule to template  name           ``ruleNameToTemplateNameMap``
report to view, dashboard     uuid           section ``ContentKey``
============================  =============  =====================================

Seven are named in the spec's scope section. The rest are here because the
corpus carries them, and leaving one out lets a closed selection still produce
a bundle referencing something it does not carry, which is the one failure
mode subsetting can introduce on its own.

Three deliberate exclusions, each a decision rather than an oversight:

* **Prose.** ``PROSE_KEYS`` is skipped for every kind, not only for super
  metrics: these exports quote another object's uuid in a description
  ("Companion to X (UUID ...)"), and reading that as a reference puts
  something in the report that tells an admin what will break which will not.
  Super metrics are stricter still and are read from ``formula`` alone,
  because the wire format gives them exactly one field a reference can live
  in.
* **Optional by-name values.** A membership rule value or a resource scope
  that names no object here is not a missing dependency: most of them name
  ordinary resources. A miss is silent; only ``policy`` is always reported,
  because ``policies.xml`` is a member this tool never carries.
* **A resource kind that is present and is not a Container** is positive
  evidence the binding is an ordinary resource, so it is skipped. Absent is
  not evidence either way, because two of the three corpus shapes never carry
  one.

The library's walker (``vcfcf_core.common.dep_walker``) resolves factory YAML
models by name within a project scope; an export has no projects, so the
scope-based tie-break does not apply and a name several objects answer to
carries every match instead (see ``_resolve_index``).

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

# The only regexes left, and both match inside a *value*, never across a
# document. ``Super Metric|sm_<uuid>`` is a metric key, and a metric key is a
# string; scanning a whole document for it is what let a reference written in a
# sibling shape slip past three times running.
SM_REF_RE = re.compile(r"sm_([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                       r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")

# Fields that hold prose, not references. These exports quote another object's
# uuid in a description ("Companion to X (UUID ...)"), and reading that as a
# reference puts something in the report that tells an admin what will break
# on import which will not break. Applied to every kind, not only to super
# metrics, so the exclusion is one rule rather than a special case.
PROSE_KEYS = {"description"}

# What a widget's scope is called, in each shape the corpus writes it in. The
# object shape says ``resourceName``; the list shape says ``name``. Reading
# both from the parsed structure is the point: a new sibling shape lands in
# one of these two spellings or is visible as an unhandled shape, where a
# regex for one of them silently matched nothing.
RESOURCE_NAME_KEYS = ("resourceName", "name")

# Only a RelationshipRule names another custom group. A ResourceNameRule or a
# StringMetricPropertyRule carries values like "template", "vms" or "group",
# and taking those as group names would pull a group named any of them into a
# bundle that does not depend on it.
GROUP_RULE_TYPE = "RelationshipRule"

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
    # By-name references that several different objects answer to; every match is
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

def _json(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _xml(raw: bytes):
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        return None


def _json_strings(node, key: str = "", skip_prose: bool = True):
    """Every string leaf of a parsed JSON value, with the key it sat under.

    Structural, so a value that is an object here and a list of objects there
    is walked the same way. The alternative, a regex over the serialized
    document, matches exactly one of those shapes.
    """
    if isinstance(node, str):
        yield key, node
    elif isinstance(node, dict):
        for k, value in node.items():
            if skip_prose and str(k).lower() in PROSE_KEYS:
                continue
            yield from _json_strings(value, str(k), skip_prose)
    elif isinstance(node, list):
        for item in node:
            yield from _json_strings(item, key, skip_prose)


def _xml_strings(el, skip_prose: bool = True):
    """Every attribute value and text node of an element tree, same idea.

    Not ``el.iter()``: a prose element has to be skipped with its children,
    and ``iter`` has no way to prune.
    """
    if el is None:
        return
    if skip_prose and el.tag.lower() in PROSE_KEYS:
        return
    for name, value in el.attrib.items():
        if not (skip_prose and str(name).lower() in PROSE_KEYS):
            yield name, value
    if el.text:
        yield el.tag, el.text
    for child in el:
        yield from _xml_strings(child, skip_prose)


def _sm_refs_in(values, where: str, skip_ident: str = "", skip_name: str = "") -> List[Ref]:
    """Super metric references inside the given *values*, in both spellings.

    ``Super Metric|sm_<uuid>`` is the resolved form. ``Super
    Metric|@supermetric:"<Name>"`` is the by-name form an 8.x export writes in
    a formula, found with the factory's own
    ``vcfcf_core.supermetrics.crossref``, which is where that spelling is
    defined, rather than a second regex that could drift from it.
    """
    out: List[Ref] = []
    seen = set()
    for value in values:
        for match in SM_REF_RE.finditer(value):
            ident = match.group(1)
            if ident in (skip_ident, "") or ident in seen:
                continue
            seen.add(ident)
            out.append(Ref("supermetric", ident, f"{where} Super Metric|sm_<uuid>"))
        for name in crossref_names(value):
            if not name or name in seen or name == skip_name:
                continue
            seen.add(name)
            out.append(Ref("supermetric", name,
                           f'{where} Super Metric|@supermetric:"<name>"'))
    return out


def _resource_names(node) -> List[str]:
    """The names a widget's or a condition's resource scope is written with.

    Every shape the corpus carries, read structurally: an object
    (``{"resourceName": ...}``, with or without a resource kind), a list of
    objects (``[{"name": ..., "id": ...}]``), null, an empty list, or the key
    absent altogether. A shape not in that list still lands here if it spells
    the name with either key, which a regex for one of them would not.
    """
    out: List[str] = []
    for item in node if isinstance(node, list) else [node]:
        if not isinstance(item, dict):
            continue
        # A resource kind that is present and is not a Container is positive
        # evidence this is an ordinary resource, not a group. Absent is not
        # evidence either way: two of the three shapes never carry one.
        kind_id = item.get("resourceKindId")
        if isinstance(kind_id, str) and "Container" not in kind_id:
            continue
        for key in RESOURCE_NAME_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value:
                out.append(value)
                break
    return out


def _dashboard_refs(raw: bytes) -> List[Ref]:
    doc = _json(raw)
    if not isinstance(doc, dict):
        return []
    out: List[Ref] = []
    seen_views, seen_groups = set(), set()
    widgets = doc.get("widgets")
    for widget in widgets if isinstance(widgets, list) else []:
        config = widget.get("config") if isinstance(widget, dict) else None
        config = config if isinstance(config, dict) else {}
        view_id = config.get("viewDefinitionId")
        if isinstance(view_id, str) and view_id and view_id not in seen_views:
            seen_views.add(view_id)
            out.append(Ref("view", view_id, "widget viewDefinitionId"))
        for name in _resource_names(config.get("resource")):
            if name not in seen_groups:
                seen_groups.add(name)
                out.append(Ref("customgroup", name, "widget resource scope, by name",
                               optional=True))
    # A super metric can be addressed from several widget config keys
    # (``metric``, ``metricKey``, ``configs`` all carry one in the corpus), so
    # the metric-key regex runs over every string value rather than over a
    # list of key names that would need extending each time one is found.
    return out + _sm_refs_in([v for _k, v in _json_strings(doc)], "widget metric")


def _view_refs(raw: bytes) -> List[Ref]:
    return _sm_refs_in([v for _n, v in _xml_strings(_xml(raw))], "column attributeKey")


def _symptom_refs(raw: bytes) -> List[Ref]:
    return _sm_refs_in([v for _n, v in _xml_strings(_xml(raw))], "symptom Condition key")


def _supermetric_refs(raw: bytes, self_ident: str, self_name: str = "") -> List[Ref]:
    """Read the formula, and only the formula.

    Stricter than the prose-key exclusion the other kinds get, and it can be:
    the wire format gives a super metric exactly one field a reference can
    live in. These exports quote another super metric's uuid in a description,
    so anything looser reports a dependency that is not one.
    """
    doc = _json(raw)
    formula = doc.get("formula") if isinstance(doc, dict) else None
    if not isinstance(formula, str):
        # Not the shape this tool knows; fall back to every string value
        # rather than silently finding no dependencies at all.
        values = [v for _k, v in _json_strings(doc)] if doc is not None else []
        return _sm_refs_in(values, "formula", self_ident, self_name)
    return _sm_refs_in([formula], "formula", self_ident, self_name)


def _customgroup_refs(raw: bytes) -> List[Ref]:
    """A membership RelationshipRule naming another group, and the policy.

    Custom groups carry no uuid in an export, so the group reference is by
    name and stays one. A value naming no group in this export is *not*
    reported missing: most rule values name ordinary resources, and reporting
    each one would drown the report an admin reads to find out what will
    break. The policy is the opposite case and is always reported: it lives in
    ``policies.xml``, which this tool does not understand and never carries,
    so a carried group's policy is always going to be absent on the target.
    """
    doc = _json(raw)
    if not isinstance(doc, dict):
        return []
    out: List[Ref] = []
    seen = set()
    membership = doc.get("membershipDefinition")
    membership = membership if isinstance(membership, dict) else {}
    rule_groups = membership.get("ruleGroups")
    # ``rules`` directly under membershipDefinition is a sibling shape of
    # ``ruleGroups[].rules``; neither corpus export uses it, and handling it
    # costs one line against another round of this same finding.
    buckets = list(rule_groups if isinstance(rule_groups, list) else [])
    buckets.append(membership)
    for bucket in buckets:
        rules = bucket.get("rules") if isinstance(bucket, dict) else None
        for rule in rules if isinstance(rules, list) else []:
            if not isinstance(rule, dict) or rule.get("ruleType") != GROUP_RULE_TYPE:
                continue
            value = rule.get("ruleStringValue")
            if isinstance(value, str) and value and value not in seen:
                seen.add(value)
                out.append(Ref("customgroup", value,
                               "membership RelationshipRule, by name", optional=True))
    policy = doc.get("policy")
    if isinstance(policy, str) and policy:
        out.append(Ref("policy", policy, "group policy, which lives in policies.xml"))
    return out


def _alert_refs(raw: bytes) -> List[Ref]:
    el = _xml(raw)
    if el is None:
        return []
    out: List[Ref] = []
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
    el = _xml(raw)
    if el is None:
        return []
    out: List[Ref] = []
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


def _rule_refs(raw: bytes) -> List[Ref]:
    """A notification rule's conditions and its endpoint.

    The conditions are walked structurally, so the alert ids come out
    whichever nesting the version used, and a resource condition's scope is
    read with the same ``resourceName`` spelling a widget uses: it is the same
    binding in a different document, under a different key
    (``ResourceID.resourceName``).
    """
    doc = _json(raw)
    if not isinstance(doc, dict):
        return []
    out: List[Ref] = []
    seen_alerts, seen_groups = set(), set()
    entries = doc.get("entry")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        for key, value in _json_strings(entry):
            if key == "AlertDefinitionID" and value not in seen_alerts:
                seen_alerts.add(value)
                out.append(Ref("alert", value, "condition AlertDefinitionID"))
            elif key in RESOURCE_NAME_KEYS and key == "resourceName" \
                    and value not in seen_groups:
                seen_groups.add(value)
                out.append(Ref("customgroup", value,
                               "condition resource scope, by name", optional=True))
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
    "symptom": lambda entry: _symptom_refs(entry.raw),
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
            # One reference, several nodes, is ambiguous only when those
            # nodes are different objects. A dashboard uuid under two owners
            # resolves to two nodes of one object, which is the deliberate
            # two-owner case, not an ambiguity. Comparing the nodes' own
            # idents is what separates them; comparing key suffixes cannot,
            # because a dashboard key ends in ``uuid@owner`` and never equals
            # the bare uuid a reference carries.
            if len({graph.nodes[h].ident for h in hits}) > 1:
                note = (f"{node.label()} names {ref.kind} {ref.ident!r}, which "
                        f"{len(hits)} different objects answer to; all are carried ("
                        + ", ".join(sorted(graph.nodes[h].uuid or graph.nodes[h].ident
                                           for h in hits)) + ")")
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
        lines.append(f"references by name that several objects answer to: {len(graph.ambiguous)}")
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
