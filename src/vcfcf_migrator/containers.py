"""The export's containers, and how to write a smaller one of each.

An export does not store one object per file. All 181 views in one corpus zip
live in a single ``views.zip/content.xml``; all 99 super metrics live in one
``supermetrics.json``; an owner's dashboards share one inner zip. Subsetting
therefore means rebuilding the container and copying the documents, which is
exactly the split the spec draws: "Rewriting the container is expected.
Rewriting a document is not."

Every class here does the same two things:

* ``entries()``: what objects the container carries, each with the raw bytes
  of its own document as the export holds them (``rawdoc`` does the slicing).
* ``rebuild(indexes)``: a fresh container holding only those entries, each
  document copied byte for byte.

Container-level material that is not any one object's document (a dashboard
file's ``entries`` resource table, ``customGroupTypes``,
``outboundsettings.json``'s ``serviceCredentials``) is copied across verbatim
whenever the container is written at all; it is scaffolding, and dropping it
would change how the documents read on import.

Containers are recognised by content, never by member name, because UI
exports and API exports name members differently. The member name the source
used is kept and reused, so a bundle looks like the export it came from.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from vcfcf_migrator import rawdoc
from vcfcf_migrator.rawdoc import RawDocError

# The export format marker: <digits>L.v1, identical across every corpus zip.
# It is scaffolding the bundle writer copies, not content and not unknown.
MARKER_RE = re.compile(r"^\d+L\.v\d+$")

# The alertContent document, its three wrappers and the kind each carries.
# A Recommendation element is a definition only when it is not a reference:
# an AlertDefinition wraps its references in a <Recommendations> of its own,
# and those carry ``ref`` where a definition carries ``key``.
_IS_DEFINITION = lambda attrib: not attrib.get("ref")  # noqa: E731

ALERT_CONTENT_TAGS = (
    ("SymptomDefinitions", "SymptomDefinition", "symptom", None),
    ("AlertDefinitions", "AlertDefinition", "alert", None),
    ("Recommendations", "Recommendation", "recommendation", _IS_DEFINITION),
)


@dataclass
class Entry:
    """One object inside a container, with its document as exported."""
    kind: str
    ident: str        # uuid where the export carries one, else a stable stand-in
    name: str
    uuid: str         # "" when the export carries no uuid for this kind
    index: int        # position inside its container
    raw: bytes        # the document, byte for byte as the export holds it
    owner: str = ""   # dashboards only: the owner member the copy sits under


class Container:
    """Base class: a zip member carrying many objects of one or more kinds."""

    member: str
    kinds: Tuple[str, ...]

    def entries(self) -> List[Entry]:
        raise NotImplementedError

    def rebuild(self, indexes: Sequence[int],
                carried_names: Optional[Dict[str, set]] = None) -> bytes:
        """A fresh container holding only *indexes*, documents copied.

        *carried_names* is what the whole bundle carries, by kind, for the one
        container that needs to look outside itself: the rule-to-template name
        map lives in ``notificationrules.json`` but the templates it names
        usually live in ``payloadtemplates.json``.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# XML containers
# ---------------------------------------------------------------------------

class XmlElementContainer(Container):
    """``views.zip``, ``reports.zip`` and the three ``alertContent`` files.

    ``inner`` is set when the XML sits inside a nested zip (``views.zip``
    holds ``content.xml``), in which case the rebuilt member is a fresh zip
    with the same inner name.
    """

    def __init__(self, member: str, data: bytes, tag: str, kind: str,
                 inner: Optional[str] = None, id_attr: str = "id",
                 name_path: Optional[str] = None, name_attr: Optional[str] = None,
                 uuid_is_ident: bool = True):
        self.member = member
        self.kinds = (kind,)
        self.kind = kind
        self.data = data
        self.tag = tag
        self.inner = inner
        self.id_attr = id_attr
        self.name_path = name_path
        self.name_attr = name_attr
        self.uuid_is_ident = uuid_is_ident
        self.container = rawdoc.xml_container(data, [tag])

    def entries(self) -> List[Entry]:
        import xml.etree.ElementTree as ET

        out: List[Entry] = []
        for i, el in enumerate(self.container.elements):
            ident = el.attrib.get(self.id_attr, "")
            name = el.attrib.get(self.name_attr or "", "") if self.name_attr else ""
            if not name and self.name_path:
                try:
                    parsed = ET.fromstring(el.raw(self.data))
                except ET.ParseError:
                    parsed = None
                if parsed is not None:
                    name = (parsed.findtext(self.name_path) or "").strip()
            out.append(Entry(kind=self.kind, ident=ident, name=name or "(unnamed)",
                             uuid=ident if self.uuid_is_ident else "",
                             index=i, raw=el.raw(self.data)))
        return out

    def rebuild(self, indexes: Sequence[int], carried_names=None) -> bytes:
        picked = [self.container.elements[i] for i in sorted(indexes)]
        xml = self.container.rebuild(picked, self.data)
        if self.inner is None:
            return xml
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(self.inner, xml)
        return buf.getvalue()


class AlertContentContainer(Container):
    """One ``alertContent`` document that may carry symptoms, alerts and
    recommendations at once (the corpus splits them across three members, but
    the format does not require that)."""

    def __init__(self, member: str, data: bytes, tags: Sequence[Tuple]):
        self.member = member
        self.data = data
        self.parts: List[Tuple[str, str, rawdoc.XmlContainer]] = []
        for wrapper, tag, kind, where in tags:
            container = rawdoc.xml_container(data, [tag], parent=wrapper, where=where)
            if container.elements:
                self.parts.append((tag, kind, container))
        self.kinds = tuple(kind for _tag, kind, _c in self.parts)

    def entries(self) -> List[Entry]:
        out: List[Entry] = []
        index = 0
        for _tag, kind, container in self.parts:
            for el in container.elements:
                ident = el.attrib.get("id") or el.attrib.get("key") or ""
                name = el.attrib.get("name") or ""
                if not name and kind == "recommendation":
                    import xml.etree.ElementTree as ET
                    try:
                        name = (ET.fromstring(el.raw(self.data)).findtext("Description") or "").strip()
                    except ET.ParseError:
                        name = ""
                out.append(Entry(kind=kind, ident=ident, name=name or "(unnamed)",
                                 uuid=ident, index=index, raw=el.raw(self.data)))
                index += 1
        return out

    def rebuild(self, indexes: Sequence[int], carried_names=None) -> bytes:
        wanted = set(indexes)
        # One rebuilt document with each wrapper that still has something in
        # it, in the order the source wrote them.
        parts = []
        index = 0
        first_container = self.parts[0][2]
        for _tag, _kind, container in self.parts:
            picked = []
            for el in container.elements:
                if index in wanted:
                    picked.append(el)
                index += 1
            if picked:
                parts.append((container, picked))
        if not parts:
            return b""
        body = []
        for container, picked in parts:
            # Only the innermost wrapper differs between the parts; the root
            # start tag is shared, so it is written once around them all.
            body.append(container.open_tags[-1])
            body.append(b"\n")
            for el in picked:
                body.append(el.raw(self.data))
                body.append(b"\n")
            body.append(container.close_tags[0])
            body.append(b"\n")
        out = [first_container.prologue, first_container.open_tags[0], b"\n"]
        out.extend(body)
        out.append(first_container.close_tags[-1])
        out.append(b"\n")
        return b"".join(out)


# ---------------------------------------------------------------------------
# JSON containers
# ---------------------------------------------------------------------------

class JsonContainer(Container):
    """Shared plumbing: the member decoded once as UTF-8, sliced by
    ``rawdoc`` so every value re-encodes to the bytes it came from."""

    def __init__(self, member: str, data: bytes):
        self.member = member
        self.data = data
        try:
            self.text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise RawDocError(f"{member} is not UTF-8: {e}") from e

    def _raw(self, value: rawdoc.RawValue) -> bytes:
        return value.raw(self.text).encode("utf-8")


class SuperMetricsContainer(JsonContainer):
    """``supermetrics.json``: a flat object keyed by super metric uuid."""

    kinds = ("supermetric",)

    def __init__(self, member: str, data: bytes):
        super().__init__(member, data)
        self.members = rawdoc.json_members(self.text)

    def entries(self) -> List[Entry]:
        out: List[Entry] = []
        for i, value in enumerate(self.members):
            try:
                doc = json.loads(value.raw(self.text))
            except ValueError:
                doc = {}
            name = str(doc.get("name") or "(unnamed)") if isinstance(doc, dict) else "(unnamed)"
            out.append(Entry("supermetric", value.key or "", name, value.key or "", i,
                             self._raw(value)))
        return out

    def rebuild(self, indexes: Sequence[int], carried_names=None) -> bytes:
        pairs = [(self.members[i].key or "", self.members[i].raw(self.text)) for i in sorted(indexes)]
        return rawdoc.build_object(pairs).encode("utf-8")


class CustomGroupsContainer(JsonContainer):
    """``customgroups.json``: ``customGroups[]`` plus a ``customGroupTypes``
    registry. The groups carry no uuid on any corpus export, so the group's
    name is its identity."""

    kinds = ("customgroup",)

    def __init__(self, member: str, data: bytes):
        super().__init__(member, data)
        self.top = rawdoc.json_members(self.text)
        groups = rawdoc.member(self.top, "customGroups")
        self.items = rawdoc.json_items(self.text, groups.start) if groups else []

    def entries(self) -> List[Entry]:
        out: List[Entry] = []
        for i, value in enumerate(self.items):
            try:
                doc = json.loads(value.raw(self.text))
            except ValueError:
                doc = {}
            name = str(doc.get("name") or "(unnamed)") if isinstance(doc, dict) else "(unnamed)"
            uuid = ""
            if isinstance(doc, dict) and isinstance(doc.get("id"), str):
                uuid = doc["id"]
            out.append(Entry("customgroup", uuid or name, name, uuid, i, self._raw(value)))
        return out

    def rebuild(self, indexes: Sequence[int], carried_names=None) -> bytes:
        picked = rawdoc.build_array([self.items[i].raw(self.text) for i in sorted(indexes)])
        pairs = []
        for value in self.top:
            pairs.append((value.key or "", picked if value.key == "customGroups"
                          else value.raw(self.text)))
        return rawdoc.build_object(pairs).encode("utf-8")


class OutboundSettingsContainer(JsonContainer):
    """``outboundsettings.json``: ``plugins[]``, plus ``serviceCredentials``
    and ``exportId``. Values ride through untouched, encrypted ones included
    (spec: the tool never decrypts, edits or strips them). Plugins carry no
    uuid, so identity is ``<pluginType>/<pluginName>``."""

    kinds = ("outboundsetting",)

    def __init__(self, member: str, data: bytes):
        super().__init__(member, data)
        self.top = rawdoc.json_members(self.text)
        plugins = rawdoc.member(self.top, "plugins")
        self.items = rawdoc.json_items(self.text, plugins.start) if plugins else []

    def entries(self) -> List[Entry]:
        out: List[Entry] = []
        for i, value in enumerate(self.items):
            try:
                doc = json.loads(value.raw(self.text))
            except ValueError:
                doc = {}
            doc = doc if isinstance(doc, dict) else {}
            cfg = doc.get("pluginConfig") if isinstance(doc.get("pluginConfig"), dict) else {}
            ptype = str(doc.get("pluginType") or "unknown")
            pname = str(cfg.get("pluginName") or doc.get("pluginName") or "(unnamed)")
            out.append(Entry("outboundsetting", f"{ptype}/{pname}", f"{pname} ({ptype})",
                             str(cfg.get("id") or doc.get("id") or ""), i, self._raw(value)))
        return out

    def rebuild(self, indexes: Sequence[int], carried_names=None) -> bytes:
        picked = rawdoc.build_array([self.items[i].raw(self.text) for i in sorted(indexes)])
        pairs = [(v.key or "", picked if v.key == "plugins" else v.raw(self.text))
                 for v in self.top]
        return rawdoc.build_object(pairs).encode("utf-8")


class DashboardsContainer(JsonContainer):
    """One owner's ``dashboards/<owner>`` inner zip.

    The inner ``dashboard/dashboard.json`` is
    ``{"entries": {...}, "dashboards": [...], "uuid": "..."}``; only the
    ``dashboards`` array is subset. The same dashboard uuid can appear under
    two owners (five such pairs on one corpus export), and each copy is its
    own entry, so selecting both keeps both.

    The other inner members (localisation ``.properties`` files) are copied
    verbatim: they are the container's, not any one dashboard's, and dropping
    them would change how a carried dashboard reads.
    """

    kinds = ("dashboard",)

    def __init__(self, member: str, data: bytes, owner: str):
        self.member = member
        self.owner = owner
        self.outer = data
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.inner_names = [n for n in zf.namelist() if not n.endswith("/")]
            self.extra = {n: zf.read(n) for n in self.inner_names if n != "dashboard/dashboard.json"}
            inner = zf.read("dashboard/dashboard.json")
        JsonContainer.__init__(self, member, inner)
        self.top = rawdoc.json_members(self.text)
        dashboards = rawdoc.member(self.top, "dashboards")
        self.items = rawdoc.json_items(self.text, dashboards.start) if dashboards else []

    def entries(self) -> List[Entry]:
        out: List[Entry] = []
        for i, value in enumerate(self.items):
            try:
                doc = json.loads(value.raw(self.text))
            except ValueError:
                doc = {}
            doc = doc if isinstance(doc, dict) else {}
            uuid = str(doc.get("id") or "")
            out.append(Entry("dashboard", uuid, str(doc.get("name") or "(unnamed)"),
                             uuid, i, self._raw(value), owner=self.owner))
        return out

    def rebuild(self, indexes: Sequence[int], carried_names=None) -> bytes:
        picked = rawdoc.build_array([self.items[i].raw(self.text) for i in sorted(indexes)])
        pairs = [(v.key or "", picked if v.key == "dashboards" else v.raw(self.text))
                 for v in self.top]
        inner = rawdoc.build_object(pairs).encode("utf-8")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("dashboard/dashboard.json", inner)
            for name in self.inner_names:
                if name in self.extra:
                    z.writestr(name, self.extra[name])
        return buf.getvalue()


def _nested_list(text: str, entries: List[rawdoc.RawValue], key: str
                 ) -> List[Tuple[int, bool, List[rawdoc.RawValue]]]:
    """Walk ``[{<key>: [...] or {...}}, ...]``, the shape both notification
    members use, and report per outer entry whether the inner value was a
    list, plus the values in it. The shape is preserved on rebuild: 8.x
    writes one object per entry, 9.1.1 writes one entry holding the list."""
    out: List[Tuple[int, bool, List[rawdoc.RawValue]]] = []
    for i, entry in enumerate(entries):
        try:
            members = rawdoc.json_members(text, entry.start)
        except RawDocError:
            continue
        inner = rawdoc.member(members, key)
        if inner is None:
            out.append((i, True, []))
            continue
        head = text[inner.start]
        if head == "[":
            out.append((i, True, rawdoc.json_items(text, inner.start)))
        elif head == "{":
            out.append((i, False, [inner]))
        else:
            out.append((i, True, []))
    return out


class NotificationRulesContainer(JsonContainer):
    """``notificationrules.json``: rules, optionally the templates they use,
    and a rule-name to template-name map.

    The map is the one thing here rebuilt from a parsed value rather than
    copied: it is a lookup table between names, not an object's document, and
    leaving a mapping to an uncarried template in place would point the
    import at something the bundle does not hold.
    """

    kinds = ("notificationrule", "notificationtemplate")

    def __init__(self, member: str, data: bytes):
        super().__init__(member, data)
        # Anything the name map filter could not judge, for the build report.
        self.notes: List[str] = []
        self.top = rawdoc.json_members(self.text)
        block = rawdoc.member(self.top, "NotificationRules")
        self.block = block
        self.block_members = rawdoc.json_members(self.text, block.start) if block else []
        rules = rawdoc.member(self.block_members, "notificationRules")
        self.rule_entries = rawdoc.json_items(self.text, rules.start) if rules else []
        self.rule_slots = _nested_list(self.text, self.rule_entries, "NotificationRule")
        tpls = rawdoc.member(self.block_members, "notificationTemplateDataSet")
        self.tpl_entries = rawdoc.json_items(self.text, tpls.start) if tpls else []
        self.tpl_slots = _nested_list(self.text, self.tpl_entries, "NotificationTemplateData")

    def _flat(self) -> List[Tuple[str, int, int, rawdoc.RawValue]]:
        flat = []
        for entry_i, _is_list, values in self.rule_slots:
            for j, v in enumerate(values):
                flat.append(("notificationrule", entry_i, j, v))
        for entry_i, _is_list, values in self.tpl_slots:
            for j, v in enumerate(values):
                flat.append(("notificationtemplate", entry_i, j, v))
        return flat

    def entries(self) -> List[Entry]:
        out: List[Entry] = []
        for i, (kind, _entry_i, _j, value) in enumerate(self._flat()):
            try:
                doc = json.loads(value.raw(self.text))
            except ValueError:
                doc = {}
            doc = doc if isinstance(doc, dict) else {}
            uuid = _id_text(doc.get("id"))
            name = str(doc.get("Name") or doc.get("name") or "(unnamed)").strip()
            out.append(Entry(kind, uuid or name, name, uuid, i, self._raw(value)))
        return out

    def rebuild(self, indexes: Sequence[int], carried_names=None) -> bytes:
        self.notes = []
        wanted = set(indexes)
        flat = self._flat()
        keep = {(entry_i, j) for i, (kind, entry_i, j, _v) in enumerate(flat)
                if i in wanted and kind == "notificationrule"}
        keep_tpl = {(entry_i, j) for i, (kind, entry_i, j, _v) in enumerate(flat)
                    if i in wanted and kind == "notificationtemplate"}
        kept_rule_names = {json.loads(flat[i][3].raw(self.text)).get("Name")
                           for i, (kind, _e, _j, _v) in enumerate(flat)
                           if i in wanted and kind == "notificationrule"}
        kept_tpl_names = {json.loads(flat[i][3].raw(self.text)).get("Name")
                          for i, (kind, _e, _j, _v) in enumerate(flat)
                          if i in wanted and kind == "notificationtemplate"}
        # The templates a rule maps to usually live in payloadtemplates.json,
        # not here, so a map entry must be judged against what the whole
        # bundle carries. Judging it against this member alone drops every
        # mapping on an export that keeps its templates in the other member.
        if carried_names:
            kept_rule_names |= set(carried_names.get("notificationrule", ()))
            kept_tpl_names |= set(carried_names.get("notificationtemplate", ()))

        block_pairs = []
        for value in self.block_members:
            if value.key == "notificationRules":
                block_pairs.append((value.key, self._rebuild_entries(
                    self.rule_entries, self.rule_slots, "NotificationRule", keep)))
            elif value.key == "notificationTemplateDataSet":
                block_pairs.append((value.key, self._rebuild_entries(
                    self.tpl_entries, self.tpl_slots, "NotificationTemplateData", keep_tpl)))
            elif value.key == "ruleNameToTemplateNameMap":
                rebuilt, notes = _filter_name_map(self.text, value,
                                                  kept_rule_names, kept_tpl_names)
                self.notes.extend(notes)
                block_pairs.append((value.key, rebuilt))
            else:
                block_pairs.append((value.key or "", value.raw(self.text)))
        block_text = rawdoc.build_object(block_pairs)
        pairs = [(v.key or "", block_text if v.key == "NotificationRules" else v.raw(self.text))
                 for v in self.top]
        return rawdoc.build_object(pairs).encode("utf-8")

    def _rebuild_entries(self, entries, slots, key: str, keep) -> str:
        out = []
        for entry_i, is_list, values in slots:
            picked = [v.raw(self.text) for j, v in enumerate(values) if (entry_i, j) in keep]
            if not picked:
                continue
            members = rawdoc.json_members(self.text, entries[entry_i].start)
            pairs = []
            for v in members:
                if v.key == key:
                    pairs.append((key, rawdoc.build_array(picked) if is_list else picked[0]))
                else:
                    pairs.append((v.key or "", v.raw(self.text)))
            out.append(rawdoc.build_object(pairs))
        return rawdoc.build_array(out)


class PayloadTemplatesContainer(JsonContainer):
    """``payloadtemplates.json``:
    ``{"NotificationTemplate": {"notificationTemplateData": [...]}}``."""

    kinds = ("notificationtemplate",)

    def __init__(self, member: str, data: bytes):
        super().__init__(member, data)
        self.top = rawdoc.json_members(self.text)
        block = rawdoc.member(self.top, "NotificationTemplate")
        self.block_members = rawdoc.json_members(self.text, block.start) if block else []
        data_member = rawdoc.member(self.block_members, "notificationTemplateData")
        self.entries_raw = rawdoc.json_items(self.text, data_member.start) if data_member else []
        self.slots = _nested_list(self.text, self.entries_raw, "NotificationTemplateData")

    def _flat(self) -> List[Tuple[int, int, rawdoc.RawValue]]:
        return [(entry_i, j, v) for entry_i, _is_list, values in self.slots
                for j, v in enumerate(values)]

    def entries(self) -> List[Entry]:
        out: List[Entry] = []
        for i, (_e, _j, value) in enumerate(self._flat()):
            try:
                doc = json.loads(value.raw(self.text))
            except ValueError:
                doc = {}
            doc = doc if isinstance(doc, dict) else {}
            uuid = _id_text(doc.get("id"))
            name = str(doc.get("Name") or doc.get("name") or "(unnamed)").strip()
            out.append(Entry("notificationtemplate", uuid or name, name, uuid, i, self._raw(value)))
        return out

    def rebuild(self, indexes: Sequence[int], carried_names=None) -> bytes:
        wanted = set(indexes)
        flat = self._flat()
        keep = {(e, j) for i, (e, j, _v) in enumerate(flat) if i in wanted}
        out = []
        for entry_i, is_list, values in self.slots:
            picked = [v.raw(self.text) for j, v in enumerate(values) if (entry_i, j) in keep]
            if not picked:
                continue
            members = rawdoc.json_members(self.text, self.entries_raw[entry_i].start)
            pairs = []
            for v in members:
                if v.key == "NotificationTemplateData":
                    pairs.append((v.key, rawdoc.build_array(picked) if is_list else picked[0]))
                else:
                    pairs.append((v.key or "", v.raw(self.text)))
            out.append(rawdoc.build_object(pairs))
        block_pairs = [(v.key or "", rawdoc.build_array(out)
                        if v.key == "notificationTemplateData" else v.raw(self.text))
                       for v in self.block_members]
        block_text = rawdoc.build_object(block_pairs)
        pairs = [(v.key or "", block_text if v.key == "NotificationTemplate" else v.raw(self.text))
                 for v in self.top]
        return rawdoc.build_object(pairs).encode("utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _id_text(value) -> str:
    """An export id as text. 8.x JSON members carry ids as dicts
    (``{"@UUID": "...", "@ObjectType": "..."}``); 9.x carries plain strings."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("@UUID", "UUID", "uuid", "id", "@id"):
            if value.get(key):
                return str(value[key])
        return ""
    return str(value)


def _filter_name_map(text: str, value: rawdoc.RawValue, rule_names, template_names
                     ) -> Tuple[str, List[str]]:
    """``ruleNameToTemplateNameMap`` with only the pairs both of whose ends
    are carried, and the notes explaining anything it did.

    Three rules, in order of importance. When nothing is dropped, the source's
    own bytes come back untouched, shape and all: 8.x writes ``entry`` as a
    single object where 9.x writes a list, and a select-all must not reshape
    it. When something is dropped, the surviving blocks keep whichever of
    those two shapes the source used, the way ``_nested_list`` does for the
    rules themselves. And anything this tool does not recognise is carried
    through unchanged and named in the build report, never dropped quietly.

    A pair pointing at a template the bundle does not hold would send the
    import looking for something absent, which is the only reason this
    function exists at all.
    """
    raw = value.raw(text)
    notes: List[str] = []
    try:
        blocks = rawdoc.json_items(text, value.start)
    except RawDocError:
        return raw, ["ruleNameToTemplateNameMap is not a JSON array; carried through unchanged"]

    out_blocks: List[str] = []
    dropped = 0
    for block in blocks:
        if text[block.start] != "{":
            out_blocks.append(block.raw(text))
            notes.append("ruleNameToTemplateNameMap: a block that is not an object "
                         "was carried through unchanged")
            continue
        members = rawdoc.json_members(text, block.start)
        entry = rawdoc.member(members, "entry")
        if entry is None:
            out_blocks.append(block.raw(text))
            notes.append("ruleNameToTemplateNameMap: a block with no entry key "
                         "was carried through unchanged")
            continue
        as_list = text[entry.start] == "["
        pairs = rawdoc.json_items(text, entry.start) if as_list else [entry]
        kept: List[str] = []
        for pair in pairs:
            try:
                doc = json.loads(pair.raw(text))
            except ValueError:
                doc = None
            strings = doc.get("string") if isinstance(doc, dict) else None
            if not (isinstance(strings, list) and len(strings) == 2):
                kept.append(pair.raw(text))
                notes.append("ruleNameToTemplateNameMap: a mapping this tool does not "
                             "recognise was carried through unchanged")
                continue
            if strings[0] in rule_names and strings[1] in template_names:
                kept.append(pair.raw(text))
            else:
                dropped += 1
                notes.append(f"ruleNameToTemplateNameMap: dropped the mapping from rule "
                             f"{strings[0]!r} to template {strings[1]!r}, because the "
                             "bundle does not carry both ends")
        if not kept:
            continue
        rebuilt = rawdoc.build_array(kept) if as_list else kept[0]
        out_blocks.append(rawdoc.build_object(
            [(v.key or "", rebuilt if v.key == "entry" else v.raw(text)) for v in members]))

    if dropped == 0 and len(out_blocks) == len(blocks):
        return raw, notes
    return rawdoc.build_array(out_blocks), notes


def _content_xml(member_bytes: bytes) -> Optional[Tuple[str, bytes]]:
    """``(inner name, xml bytes)`` for a nested zip holding ``content.xml``."""
    try:
        with zipfile.ZipFile(io.BytesIO(member_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith("content.xml"):
                    return name, zf.read(name)
    except (zipfile.BadZipFile, OSError):
        return None
    return None


def discover(members: Dict[str, bytes]) -> Tuple[List[Container], List[str]]:
    """Recognise every container in an export's members.

    Returns the containers and the names of the members that carry content
    this tool does not understand. Those are never written into a bundle: the
    spec's "carried, not inspected" listing is what ``inspect`` shows, and a
    member nobody can select is a member nobody asked for.
    """
    containers: List[Container] = []
    unknown: List[str] = []
    for name in sorted(members):
        data = members[name]
        lower = name.lower()
        if name.startswith("dashboards/"):
            try:
                containers.append(DashboardsContainer(name, data, name.split("/", 1)[1]))
            except (KeyError, zipfile.BadZipFile, RawDocError):
                unknown.append(name)
            continue
        if name.startswith("dashboardsharings/") or name == "usermappings.json":
            continue  # dashboard scaffolding, handled by the bundle writer
        if name == "configuration.json" or MARKER_RE.match(name):
            continue  # the manifest and the marker, both written by the builder
        if lower.endswith(".zip"):
            found = _content_xml(data)
            if found is None:
                unknown.append(name)
                continue
            inner_name, xml = found
            if b"<ViewDef" in xml:
                containers.append(XmlElementContainer(name, xml, "ViewDef", "view",
                                                      inner=inner_name, name_path="Title"))
            elif b"<ReportDef" in xml:
                containers.append(XmlElementContainer(name, xml, "ReportDef", "report",
                                                      inner=inner_name, name_path="Title"))
            else:
                unknown.append(name)
            continue
        if lower.endswith(".xml"):
            if b"<alertContent" in data[:400]:
                try:
                    container = AlertContentContainer(name, data, ALERT_CONTENT_TAGS)
                except RawDocError:
                    unknown.append(name)
                    continue
                if container.parts:
                    containers.append(container)
                    continue
            if b"<ViewDef" in data:
                containers.append(XmlElementContainer(name, data, "ViewDef", "view",
                                                      name_path="Title"))
                continue
            unknown.append(name)
            continue
        if lower.endswith(".json"):
            try:
                doc = json.loads(data)
            except ValueError:
                unknown.append(name)
                continue
            if not isinstance(doc, dict):
                unknown.append(name)
                continue
            try:
                if "customGroups" in doc:
                    containers.append(CustomGroupsContainer(name, data))
                elif "NotificationRules" in doc:
                    containers.append(NotificationRulesContainer(name, data))
                elif "NotificationTemplate" in doc:
                    containers.append(PayloadTemplatesContainer(name, data))
                elif isinstance(doc.get("plugins"), list) and "serviceCredentials" in doc:
                    containers.append(OutboundSettingsContainer(name, data))
                elif doc and all(isinstance(v, dict) and "name" in v for v in doc.values()):
                    containers.append(SuperMetricsContainer(name, data))
                else:
                    unknown.append(name)
            except RawDocError:
                unknown.append(name)
            continue
        unknown.append(name)
    return containers, unknown


def iter_entries(containers: Iterable[Container]) -> Iterable[Tuple[Container, Entry]]:
    for container in containers:
        for entry in container.entries():
            yield container, entry


def documents(members: Dict[str, bytes]) -> Dict[Tuple[str, str, str, str], bytes]:
    """``(member, kind, ident, owner) -> the object's document bytes``.

    The pass-through contract is only a claim until something compares the
    two sides, so this is what both ``corpus-check`` and the test suite use:
    build a bundle, read its documents back, and every one of them must be
    the bytes the source held.
    """
    found, _unknown = discover(members)
    out: Dict[Tuple[str, str, str, str], bytes] = {}
    for container in found:
        for entry in container.entries():
            out[(container.member, entry.kind, entry.ident, entry.owner)] = entry.raw
    return out


def _normalise_xml(data: bytes):
    """An XML document as a comparable tree: tag, attributes, non-blank text
    and children in order. Formatting and whitespace are not part of it,
    because a rebuilt container is allowed to indent differently; structure is
    not allowed to change."""
    import xml.etree.ElementTree as ET

    def walk(el):
        text = (el.text or "").strip()
        return (el.tag, tuple(sorted(el.attrib.items())), text, tuple(walk(c) for c in el))

    return walk(ET.fromstring(data))


def _normalise_member(name: str, data: bytes):
    """One zip member reduced to something two exports can be compared on."""
    if name.lower().endswith(".zip") or name.startswith("dashboards/"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return {n: _normalise_member(n, zf.read(n))
                    for n in sorted(zf.namelist()) if not n.endswith("/")}
    if name.lower().endswith(".xml"):
        return _normalise_xml(data)
    if name.lower().endswith(".json") or name.startswith("dashboardsharings/"):
        return json.loads(data)
    return data


def container_shapes(members: Dict[str, bytes], skip=("configuration.json",)) -> Dict[str, object]:
    """Every member's structure, for comparing a select-all bundle with its
    source. Documents are covered by ``documents()``; this covers the part of
    a container the tool actually rebuilds, which is the part most likely to
    drift. ``configuration.json`` is skipped because it is written fresh on
    purpose.
    """
    out: Dict[str, object] = {}
    for name, data in members.items():
        if name in skip or MARKER_RE.match(name):
            continue
        try:
            out[name] = _normalise_member(name, data)
        except (ValueError, zipfile.BadZipFile):
            continue
    return out
