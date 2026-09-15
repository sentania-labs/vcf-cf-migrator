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
dashboard to supermetric      uuid and name  any string in the widget subtree
dashboard to customgroup      name           widget scope, ``entryKeys.resource``
view to supermetric           uuid and name  any attribute or text value
supermetric to supermetric    uuid and name  the ``formula`` field
symptom to supermetric        uuid and name  any attribute or text value
alert to symptom              uuid           ``<SymptomSet ref>``, ``<Symptom ref>``
alert to recommendation       uuid           ``<Recommendation ref>``
customgroup to customgroup    name           membership ``RelationshipRule``
customgroup to policy         uuid           ``policy``, always reported missing
notificationrule to alert     uuid           condition ``AlertDefinitionID``
notificationrule to group     name           condition ``ResourceID``, any shape
notificationrule to outbound  name           rule ``PluginID``
notificationrule to template  name           ``ruleNameToTemplateNameMap``
report to view, dashboard     uuid           section ``ContentKey``
============================  =============  =====================================

Seven are named in the spec's scope section. The rest are here because the
corpus carries them, and leaving one out lets a closed selection still produce
a bundle referencing something it does not carry, which is the one failure
mode subsetting can introduce on its own.

Four deliberate exclusions, each a decision rather than an oversight:

* **The container's own ``entries.resource``.** The dashboard container writes
  the same binding in two more places than a widget does: ``entries.resource``
  at the top of the owner's file, and ``entryKeys.resource`` on each dashboard.
  The per-dashboard one is read. The container-level one is not: it sits
  outside every dashboard document, shared by all of that owner's dashboards,
  so attributing its names to any one of them would carry a group into a
  bundle that does not use it. Safe because all six group names it carries
  across the corpus are also bound by a widget scope in the same container,
  which is followed, and the block is copied verbatim into the rebuilt inner
  zip either way.

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
from vcfcf_migrator import runlog
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
        """Kind, name, uuid and, for a dashboard, the owner whose copy this is.

        Custom groups and outbound settings carry no uuid on any corpus
        export, so for those the name is the identity and there is no bracket
        to print. The owner is part of the label rather than something each
        caller appends, so a note about one of two same-uuid copies stays
        distinct from a note about the other wherever it is printed.
        """
        return (f"{self.kind} {self.name}"
                + (f" [{self.uuid}]" if self.uuid else "")
                + (f" owner {self.owner}" if self.owner else ""))

    def as_dict(self) -> dict:
        return {
            "key": self.key, "kind": self.kind, "name": self.name,
            "uuid": self.uuid, "ident": self.ident, "member": self.member,
            "owner": self.owner or None,
            "refs": [{"kind": r.kind, "ident": r.ident, "via": r.via} for r in self.refs],
        }


@dataclass
class Note:
    """Something the admin is told, attributed to the node that caused it.

    Two channels use this. An *ambiguity* is a by-name reference several
    different objects answer to. An *unhandled shape* is a field whose value is
    a shape this tool has never seen, which is the one thing a structural walk
    must not do silently: the whole reason for walking the parsed document
    rather than matching its text is that a shape nobody enumerated announces
    itself instead of resolving to nothing.
    """
    source_key: str
    text: str


@dataclass(frozen=True)
class MissingEdge:
    """An edge whose target is not in the export.

    Information, not an error, and the wording matters: an export carries
    custom content only, so an edge to anything that ships with the product
    or inside a management pack lands here by construction and the target
    instance is expected to have it already. Nothing in an export tells that
    apart from an object that is genuinely gone, so this says what is not in
    the export and leaves the judgement to the admin.
    """
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
    # By-name references that several different objects answer to; every match
    # is carried, and the admin is told rather than left to find out.
    ambiguous: List[Note] = field(default_factory=list)
    # Field values in a shape this tool does not handle. Never silent.
    unhandled: List[Note] = field(default_factory=list)
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

    def notes_for(self, key: str) -> List[Note]:
        return [n for n in self.ambiguous + self.unhandled if n.source_key == key]


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


def _json_values(node, wanted_key: str):
    """Every value stored under *wanted_key*, at any depth of a parsed value.

    A match is yielded *and* descended into, because the keys this is used for
    nest inside themselves: a widget list holds widgets whose configs hold
    widget lists of their own.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == wanted_key:
                yield value
            yield from _json_values(value, wanted_key)
    elif isinstance(node, list):
        for item in node:
            yield from _json_values(item, wanted_key)


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


def _resource_names(node, where: str, unhandled: List[str]) -> List[str]:
    """The names a resource scope is written with, in every shape it takes.

    One reader for one binding, used by every field that carries it: a widget's
    ``config.resource``, a dashboard's ``entryKeys.resource``, a notification
    rule's ``ResourceID``. Reading the same thing two ways in two places is the
    asymmetry that produced the shape this function exists to stop missing.

    Shapes handled: an object, with a resource kind or without one; a list of
    objects; a bare string, and a list of bare strings, which no corpus export
    writes but which cost one branch against another silent drop; null; an
    empty list; the key absent. Anything else is appended to *unhandled* and
    reaches the report, because a shape that resolves to nothing quietly is
    indistinguishable from a scope that is genuinely empty.
    """
    out: List[str] = []
    if node is None:
        return out
    items = node if isinstance(node, list) else [node]
    for item in items:
        if isinstance(item, str):
            if item:
                out.append(item)
            continue
        if not isinstance(item, dict):
            unhandled.append(f"{where}: a resource scope written as "
                             f"{type(item).__name__}, which this tool does not read")
            continue
        # A resource kind that is present and is not a Container is positive
        # evidence this is an ordinary resource, not a group. Absent is not
        # evidence either way: two of the corpus shapes never carry one. When
        # both kind keys are present and disagree, the Container one wins: a
        # scope that says Container anywhere is a scope worth resolving, and
        # over-carrying a group is the recoverable error. No object in any of
        # the five corpus exports carries both keys, so this says what is
        # intended rather than settling anything observed.
        kinds = [item[k] for k in ("resourceKindId", "adapterKindKey")
                 if isinstance(item.get(k), str)]
        if kinds and all("Container" not in kind for kind in kinds):
            continue
        if True:
            for key in RESOURCE_NAME_KEYS:
                value = item.get(key)
                if isinstance(value, str) and value:
                    out.append(value)
                    break
            else:
                unhandled.append(f"{where}: a resource scope object with no "
                                 f"{' or '.join(RESOURCE_NAME_KEYS)} key "
                                 f"({', '.join(sorted(item)) or 'no keys'})")
    return out


def _dashboard_refs(raw: bytes, unhandled: List[str]) -> List[Ref]:
    """A dashboard's views, super metrics and custom group scopes.

    ``widgets`` is walked **wherever the key appears**, not only at the top and
    not only through ``config.widgets``. A dashboard document holds widget
    lists in at least three places: its own ``widgets``, a tab or group
    widget's ``config.widgets``, and each entry of ``dashboardNavigations``,
    which is keyed by widget uuid and whose values are ``{id, widgets}``
    objects (15 widget-shaped objects across 8 dashboards on this corpus,
    carrying no content reference today). Enumerating the places a field can
    appear is the same trap as enumerating the shapes a value can take, so the
    walk takes the key by name at any depth instead.

    A string in a widget list is a widget id naming a sibling inline: a tab
    widget lists its members that way rather than repeating them (36 such on
    this corpus, every one resolving to a widget id in the same document).
    That is checked, not assumed. A string that is not one of the document's
    own widget ids is reported, because an export writing a nested widget by
    name or by a content uuid would otherwise be dropped in silence, which is
    how three reference classes were missed before.

    ``entryKeys.resource`` is read too. It is per dashboard and carries the
    resource binding in a third shape (``{resourceKindKey, internalId,
    adapterKindKey, identifiers, name}``); on this corpus its ten values are
    all worlds and adapter instances, but a dashboard that scoped a group
    there and not in a widget would be missed exactly as one was before.
    """
    doc = _json(raw)
    if not isinstance(doc, dict):
        unhandled.append("dashboard: a document that is not a JSON object")
        return []
    out: List[Ref] = []
    seen_views, seen_groups = set(), set()
    metric_values: List[str] = []

    widget_lists = [v for v in _json_values(doc, "widgets")]
    widget_ids = {w["id"] for lst in widget_lists if isinstance(lst, list)
                  for w in lst if isinstance(w, dict) and isinstance(w.get("id"), str)}

    def add_group(name: str, where: str) -> None:
        if name not in seen_groups:
            seen_groups.add(name)
            out.append(Ref("customgroup", name, where, optional=True))

    for widgets in widget_lists:
        if widgets is None:
            continue
        if not isinstance(widgets, list):
            unhandled.append(f"dashboard: widgets written as "
                             f"{type(widgets).__name__}, which this tool does not read")
            continue
        for widget in widgets:
            if isinstance(widget, str):
                if widget not in widget_ids:
                    unhandled.append(
                        f"dashboard: a widget list holds {widget!r}, which is not a "
                        "widget id in this document, so this tool does not know what "
                        "it names")
                continue
            if not isinstance(widget, dict):
                unhandled.append("dashboard: a widget that is neither an object "
                                 "nor a widget id")
                continue
            config = widget.get("config")
            if config is not None and not isinstance(config, dict):
                unhandled.append("dashboard: a widget config that is not an object")
                config = None
            config = config or {}
            view_id = config.get("viewDefinitionId")
            if isinstance(view_id, str) and view_id and view_id not in seen_views:
                seen_views.add(view_id)
                out.append(Ref("view", view_id, "widget viewDefinitionId"))
            for name in _resource_names(config.get("resource"),
                                        "dashboard widget config.resource", unhandled):
                add_group(name, "widget resource scope, by name")
            metric_values.extend(v for _k, v in _json_strings(widget))

    if "widgets" not in doc:
        unhandled.append("dashboard: a document with no widgets key")
    entry_keys = doc.get("entryKeys")
    if entry_keys is not None:
        if not isinstance(entry_keys, dict):
            unhandled.append("dashboard: entryKeys written as "
                             f"{type(entry_keys).__name__}, which this tool does not read")
        else:
            for name in _resource_names(entry_keys.get("resource"),
                                        "dashboard entryKeys.resource", unhandled):
                add_group(name, "dashboard entryKeys resource, by name")
    # A super metric can be addressed from several widget config keys
    # (``metric``, ``metricKey``, ``configs`` all carry one in the corpus), so
    # the metric-key regex runs over every string value in the widget subtree
    # rather than over a list of key names that would need extending each time
    # a new one is found.
    return out + _sm_refs_in(metric_values, "widget metric")


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


def _customgroup_refs(raw: bytes, unhandled: List[str]) -> List[Ref]:
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
        unhandled.append("custom group: a document that is not a JSON object")
        return []
    out: List[Ref] = []
    seen = set()
    membership = doc.get("membershipDefinition")
    if membership is not None and not isinstance(membership, dict):
        unhandled.append("custom group: membershipDefinition written as "
                         f"{type(membership).__name__}, which this tool does not read")
    membership = membership if isinstance(membership, dict) else {}
    rule_groups = membership.get("ruleGroups")
    if rule_groups is not None and not isinstance(rule_groups, list):
        unhandled.append("custom group: ruleGroups written as "
                         f"{type(rule_groups).__name__}, which this tool does not read")
    # ``rules`` directly under membershipDefinition is a sibling shape of
    # ``ruleGroups[].rules``; neither corpus export uses it, and handling it
    # costs one line against another round of this same finding.
    buckets = list(rule_groups if isinstance(rule_groups, list) else [])
    buckets.append(membership)
    for bucket in buckets:
        rules = bucket.get("rules") if isinstance(bucket, dict) else None
        if rules is not None and not isinstance(rules, list):
            unhandled.append("custom group: rules written as "
                             f"{type(rules).__name__}, which this tool does not read")
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


def _rule_refs(raw: bytes, unhandled: List[str]) -> List[Ref]:
    """A notification rule's conditions and its endpoint.

    The conditions are walked structurally, so the alert ids come out whichever
    nesting the version used. A resource condition's scope goes through
    ``_resource_names``, the same reader the widget path uses: it is the same
    binding in a different document under a different key, and reading it with
    a weaker rule here than there is how a shape gets missed on one side only.
    """
    doc = _json(raw)
    if not isinstance(doc, dict):
        unhandled.append("notification rule: a document that is not a JSON object")
        return []
    out: List[Ref] = []
    seen_alerts, seen_groups = set(), set()
    entries = doc.get("entry")
    if entries is not None and not isinstance(entries, list):
        unhandled.append(f"notification rule: entry written as "
                         f"{type(entries).__name__}, which this tool does not read")
        entries = None
    for entry in entries or []:
        if not isinstance(entry, dict):
            unhandled.append("notification rule: a condition that is not an object")
            continue
        for key, value in _json_strings(entry):
            if key == "AlertDefinitionID" and value not in seen_alerts:
                seen_alerts.add(value)
                out.append(Ref("alert", value, "condition AlertDefinitionID"))
        for resource_id in _json_values(entry, "ResourceID"):
            for name in _resource_names(resource_id,
                                        "notification rule ResourceID", unhandled):
                if name not in seen_groups:
                    seen_groups.add(name)
                    out.append(Ref("customgroup", name,
                                   "condition resource scope, by name", optional=True))
    plugin = doc.get("PluginID")
    if isinstance(plugin, dict):
        ptype = plugin.get("@pluginType") or doc.get("PluginType")
        pname = plugin.get("@pluginName")
        if ptype and pname:
            out.append(Ref("outboundsetting", f"{ptype}/{pname}", "rule PluginID"))
    return out


_REF_EXTRACTORS = {
    "dashboard": lambda entry, notes: _dashboard_refs(entry.raw, notes),
    "view": lambda entry, notes: _view_refs(entry.raw),
    "supermetric": lambda entry, notes: _supermetric_refs(entry.raw, entry.ident, entry.name),
    "customgroup": lambda entry, notes: _customgroup_refs(entry.raw, notes),
    # A symptom's threshold can be on a super metric attribute:
    # <Condition key="Super Metric|sm_<uuid>" type="metric" .../>.
    "symptom": lambda entry, notes: _symptom_refs(entry.raw),
    "alert": lambda entry, notes: _alert_refs(entry.raw),
    "report": lambda entry, notes: _report_refs(entry.raw),
    "notificationrule": lambda entry, notes: _rule_refs(entry.raw, notes),
}


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def build_graph(members: Dict[str, bytes]) -> Graph:
    """Every content object in *members*, with its edges resolved."""
    with runlog.phase("graph", members=len(members)):
        return _build_graph(members)


def _build_graph(members: Dict[str, bytes]) -> Graph:
    found, unknown = _containers.discover(members)
    graph = Graph(containers=found, unknown_members=unknown)
    for container in found:
        runlog.detail("container.found", member=container.member,
                      container=type(container).__name__,
                      entries=len(container.entries()))
    for name in unknown:
        runlog.detail("member.unknown", member=name,
                      reason="this tool does not read this member's content, so nothing "
                             "in it can be selected and none of it is carried")

    for container in found:
        for entry in container.entries():
            node = Node(kind=entry.kind, ident=entry.ident, name=entry.name,
                        uuid=entry.uuid, member=container.member, index=entry.index,
                        owner=entry.owner)
            # Identifiers reach the log through here, where the tool knows they
            # came out of a content document rather than out of a user record.
            runlog.content_id(node.ident)
            runlog.content_id(node.uuid)
            shapes: List[str] = []
            node.refs = list(
                _REF_EXTRACTORS.get(entry.kind, lambda _e, _n: [])(entry, shapes))
            for ref in node.refs:
                runlog.content_id(ref.ident)
            runlog.debug("node.found", kind=node.kind, uuid=node.uuid or "",
                         ident=node.ident, name=node.name, member=node.member,
                         index=node.index, owner=node.owner or None,
                         refs=len(node.refs))
            for text in shapes:
                runlog.warn("shape.unhandled", kind=node.kind, uuid=node.uuid or "",
                            name=node.name, note=text,
                            reason="a field value in a shape this tool does not read; it "
                                   "is named rather than resolved, never dropped quietly")
                note = Note(node.key, f"{node.label()}: {text}")
                if note not in graph.unhandled:
                    graph.unhandled.append(note)
            if node.key in graph.nodes:
                runlog.detail("node.duplicate", kind=node.kind, uuid=node.uuid or "",
                              name=node.name, member=container.member,
                              reason="the same object in a second member; the first member "
                                     "keeps the node and the bundle still carries both copies")
                # The same object in two members (a full export writes the
                # notification templates into both notificationrules.json and
                # payloadtemplates.json). The first member wins the node; the
                # bundle writer still carries both copies.
                continue
            graph.nodes[node.key] = node
    runlog.count("nodes", len(graph.nodes))

    _add_rule_template_refs(found, graph)

    index = _resolve_index(graph.nodes)
    seen_gaps: set = set()
    for node in graph.nodes.values():
        targets: List[str] = []
        for ref in node.refs:
            hits = index.get((ref.kind, ref.ident))
            if not hits:
                # Two widgets on one dashboard can name the same absent
                # object, which is one thing to tell the admin about, not
                # two. Membership is checked against a set rather than by
                # scanning the list, which on a 430-object export with 201
                # such edges is the difference between linear and quadratic.
                gap = MissingEdge(node.key, ref.kind, ref.ident, ref.via)
                if not ref.optional and gap not in seen_gaps:
                    seen_gaps.add(gap)
                    graph.missing.append(gap)
                    runlog.detail("ref.missing", kind=node.kind, uuid=node.uuid or "",
                                  name=node.name, owner=node.owner or None,
                                  wants=ref.kind, ident=ref.ident,
                                  spelling=_spelling(ref.ident), via=ref.via,
                                  reason=missing_reason(gap))
                elif ref.optional:
                    runlog.debug("ref.optional_miss", kind=node.kind, name=node.name,
                                 wants=ref.kind, ident=ref.ident, via=ref.via,
                                 reason="this reference is only sometimes an object in the "
                                        "export, so not resolving it is normal")
                continue
            # One reference, several nodes, is ambiguous only when those
            # nodes are different objects. A dashboard uuid under two owners
            # resolves to two nodes of one object, which is the deliberate
            # two-owner case, not an ambiguity. Comparing the nodes' own
            # idents is what separates them; comparing key suffixes cannot,
            # because a dashboard key ends in ``uuid@owner`` and never equals
            # the bare uuid a reference carries.
            if len({graph.nodes[h].ident for h in hits}) > 1:
                note = Note(node.key, (
                    f"{node.label()} names {ref.kind} {ref.ident!r}, which "
                    f"{len(hits)} different objects answer to; all are carried ("
                    + ", ".join(sorted(graph.nodes[h].uuid or graph.nodes[h].ident
                                       for h in hits)) + ")"))
                if note not in graph.ambiguous:
                    graph.ambiguous.append(note)
                    runlog.warn("ref.ambiguous", kind=node.kind, uuid=node.uuid or "",
                                name=node.name, wants=ref.kind, ident=ref.ident,
                                spelling=_spelling(ref.ident), via=ref.via,
                                answered_by=sorted(graph.nodes[h].uuid or graph.nodes[h].ident
                                                   for h in hits),
                                reason="several different objects answer to this name; every "
                                       "one is carried rather than one being guessed at")
            for hit in hits:
                if hit != node.key and hit not in targets:
                    targets.append(hit)
                    child = graph.nodes[hit]
                    runlog.detail("ref.resolved", kind=node.kind, uuid=node.uuid or "",
                                  name=node.name, owner=node.owner or None,
                                  to_kind=child.kind, to_uuid=child.uuid or "",
                                  to_name=child.name, to_owner=child.owner or None,
                                  spelling=_spelling(ref.ident), via=ref.via)
        graph.edges[node.key] = targets
        runlog.count("edges", len(targets))
    runlog.info("graph.built", counts=graph.counts(),
                nodes=len(graph.nodes),
                edges=sum(len(v) for v in graph.edges.values()),
                missing=len(graph.missing), ambiguous=len(graph.ambiguous),
                unhandled_shapes=len(graph.unhandled),
                unknown_members=len(graph.unknown_members))
    return graph


def _spelling(ident: str) -> str:
    """How the export wrote this reference. Which spelling was used is half of
    what makes a missing edge diagnosable: an 8.x document names a super metric
    where a 9.x one gives its uuid, and the two fail differently."""
    return "uuid" if _UUID_SHAPE.match(str(ident)) else "name"


_UUID_SHAPE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


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
        # Several objects can share a display name, so this is a list per
        # name, not a dict keyed by it: a last-wins dict silently picked one
        # rule of several sharing a name and left the rest without the edge.
        by_name: Dict[Tuple[str, str], List[Node]] = {}
        for node in graph.nodes.values():
            by_name.setdefault((node.kind, node.name), []).append(node)
        for rule_name, template_name in pairs:
            for rule in by_name.get(("notificationrule", rule_name), []):
                # The reference carries the template's *name*, not the ident of
                # whichever node happened to be found first. Resolution then
                # does what it does for every other by-name reference: carry
                # every object that answers to the name, report the ambiguity,
                # and report a miss when nothing does.
                ref = Ref("notificationtemplate", template_name,
                          "ruleNameToTemplateNameMap")
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
        lines.append(pad + node.label())
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
        lines.append(f"edges to objects a bundle cannot carry: {len(graph.missing)}")
        for gap in graph.missing:
            source = graph.nodes.get(gap.source_key)
            lines.append(f"  {gap.kind} [{gap.ident}] wanted by "
                         f"{source.label() if source else gap.source_key} "
                         f"(via {gap.via}); {missing_reason(gap)}")
    if graph.ambiguous:
        lines.append("")
        lines.append(f"references by name that several objects answer to: {len(graph.ambiguous)}")
        for note in graph.ambiguous:
            lines.append(f"  {note.text}")
    if graph.unhandled:
        lines.append("")
        lines.append(f"field values in a shape this tool does not read: {len(graph.unhandled)}")
        for note in graph.unhandled:
            lines.append(f"  {note.text}")
    if graph.unknown_members:
        lines.append("")
        lines.append("members this tool does not understand (never carried unless selected, "
                     "and they cannot be selected): " + ", ".join(graph.unknown_members))
    return "\n".join(lines) + "\n"


def missing_reason(gap: MissingEdge) -> str:
    """Why the bundle will not hold this, in the words that are true of it.

    Most of these are objects the export itself does not carry, usually
    out-of-the-box content the target already has. A policy is the other case:
    ``policies.xml`` is right there in the export, and is a member this tool
    does not understand and never carries into a bundle. Printing both under
    "not in this export" would be wrong about the second.
    """
    if gap.kind == "policy":
        return ("policies.xml is in the export but is a member this tool never "
                "carries into a bundle")
    return "it is not in this export"


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
        "ambiguous": [n.text for n in graph.ambiguous],
        "unhandled_shapes": [n.text for n in graph.unhandled],
        "unknown_members": list(graph.unknown_members),
    }
