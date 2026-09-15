"""Read a VCF Operations content export zip and list what it carries.

The export layout (from the factory's ``vcfops-api`` skill, wire-formats):

    <digits>L.v1                  marker, contents = owner user uuid
    configuration.json            manifest
    views.zip                     nested zip holding content.xml (ViewDefs)
    usermappings.json             owners referenced by dashboards
    dashboards/<ownerUserId>      nested zip per owner, dashboard/dashboard.json
    dashboardsharings/<ownerUserId>
    supermetrics.json             dict keyed by uuid

A full export (the corpus zips, 8.18.7 and 9.0.2, share this layout) adds
``symptomdefs.xml``, ``alertdefs.xml`` and ``recommendationdefs.xml`` (each
an ``alertContent`` document), ``customgroups.json`` (``customGroups``),
``notificationrules.json`` (``NotificationRules``), ``payloadtemplates.json``
(``NotificationTemplate``), ``outboundsettings.json`` (``plugins``) and
``reports.zip`` whose ``content.xml`` carries ``ReportDef`` elements. The
reader dispatches on content, not on member name. Everything not recognised
(policies, users, roles, solution config, cost drivers) is listed as
"carried, not inspected" and left alone.

No export carries a product version anywhere (checked on both corpus zips;
the ``L.v1`` marker is a format marker and is identical across instances),
so the admin declares it with ``--source-version`` and the 8.10 floor is
enforced on the declared value only.

The zip readers for dashboards, super metrics and view XML come from
``vcfcf_core.extractor.extractor``; this module adds the walk and the
per-type listing on top. Nothing here writes a file.
"""
from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from vcfcf_core.extractor.extractor import (
    _content_xml_from_export_zip,
    _dashboards_from_export_zip,
    _supermetrics_from_export_zip,
)

VERSION_FLOOR = (8, 10)
VERSION_FLOOR_TEXT = "8.10"

# Listing order for inspect output.
KIND_ORDER = [
    "dashboard",
    "view",
    "supermetric",
    "customgroup",
    "symptom",
    "alert",
    "recommendation",
    "report",
    "notificationrule",
    "notificationtemplate",
    "outboundsetting",
]

_MARKER_RE = re.compile(r"^(\d+)L\.v(\d+)$")
_SOURCE_VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")


class UnsupportedExport(Exception):
    """The declared source version is below the floor."""


class NotAnExport(Exception):
    """The path is not a readable zip, or the zip is not a content export."""


class BadSourceVersion(ValueError):
    """A declared source version that is not major.minor[.patch]."""


@dataclass
class Item:
    kind: str
    name: str
    uuid: str
    source: str  # zip member the item was read from

    def as_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "uuid": self.uuid, "source": self.source}


@dataclass
class Export:
    path: str
    marker: Optional[str] = None
    marker_format: Optional[str] = None
    owner: Optional[str] = None
    source_version: Optional[str] = None  # declared by the admin, never sniffed
    manifest: dict = field(default_factory=dict)
    items: List[Item] = field(default_factory=list)
    carried: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # Dashboard navigation links whose target is not in this export.
    navigation_gaps: int = 0

    def sorted_items(self) -> List[Item]:
        def key(it: Item):
            rank = KIND_ORDER.index(it.kind) if it.kind in KIND_ORDER else len(KIND_ORDER)
            return (rank, it.name.lower(), it.uuid)
        return sorted(self.items, key=key)

    def counts(self) -> dict:
        out: dict = {}
        for it in self.items:
            out[it.kind] = out.get(it.kind, 0) + 1
        return out

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "marker": self.marker,
            "marker_format": self.marker_format,
            "owner": self.owner,
            "source_version": self.source_version,
            "manifest": self.manifest,
            "counts": self.counts(),
            "items": [it.as_dict() for it in self.sorted_items()],
            "carried": list(self.carried),
            "navigation_gaps": self.navigation_gaps,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Version handling
# ---------------------------------------------------------------------------

def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """``"8.10.2"`` -> ``(8, 10, 2)``; None unless the text is a dotted
    major.minor[.patch] string. A bare integer is never a product version."""
    if not _SOURCE_VERSION_RE.match(str(text).strip()):
        return None
    return tuple(int(p) for p in str(text).strip().split("."))


def check_source_version(declared: Optional[str]) -> Optional[str]:
    """Validate a declared source version against the floor.

    Returns the normalised string, or None when nothing was declared. Raises
    ``BadSourceVersion`` on a malformed value and ``UnsupportedExport`` when
    it is below the floor.
    """
    if declared is None or not str(declared).strip():
        return None
    parsed = parse_version(declared)
    if parsed is None:
        raise BadSourceVersion(
            f"source version {declared!r} is not major.minor[.patch] (for example 8.18.7)"
        )
    if parsed < VERSION_FLOOR:
        raise UnsupportedExport(
            f"refused: declared source version {str(declared).strip()} is below the floor {VERSION_FLOOR_TEXT}"
        )
    return str(declared).strip()


# ---------------------------------------------------------------------------
# Per-type readers
# ---------------------------------------------------------------------------

def _items_from_content_xml(xml_bytes: bytes, source: str) -> List[Item]:
    """ViewDef and ReportDef elements from a views or reports content.xml."""
    out: List[Item] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out
    for tag, kind in (("ViewDef", "view"), ("ReportDef", "report")):
        for el in root.iter(tag):
            title = el.findtext("Title") or el.get("name") or "(untitled)"
            out.append(Item(kind, title.strip(), el.get("id") or "", source))
    return out


def _items_from_alert_content(root: ET.Element, source: str) -> List[Item]:
    out: List[Item] = []
    for el in root.iter("SymptomDefinition"):
        out.append(Item("symptom", el.get("name") or "(unnamed)", el.get("id") or "", source))
    for el in root.iter("AlertDefinition"):
        out.append(Item("alert", el.get("name") or "(unnamed)", el.get("id") or "", source))
    for el in root.iter("Recommendation"):
        # Inside an AlertDefinition a Recommendation is a ref; the definitions
        # themselves live under <Recommendations> and carry a key.
        if el.get("ref"):
            continue
        name = el.get("name") or el.get("description") or (el.findtext("Description") or "").strip() or "(unnamed)"
        out.append(Item("recommendation", name, el.get("key") or el.get("id") or "", source))
    return out


def _id_text(value) -> str:
    """An export id as text. 8.x JSON members carry ids as dicts
    (``{"@UUID": "...", "@ObjectType": "NOTIFICATION_TEMPLATE"}``); 9.x
    and the UI exports carry plain strings."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("@UUID", "UUID", "uuid", "id", "@id"):
            if value.get(key):
                return str(value[key])
        return ""
    return str(value)


def _items_from_customgroups(doc: dict, source: str) -> List[Item]:
    out: List[Item] = []
    for group in doc.get("customGroups") or []:
        if not isinstance(group, dict):
            continue
        uuid = _id_text(group.get("id") or group.get("identifier"))
        out.append(Item("customgroup", str(group.get("name") or "(unnamed)"), uuid, source))
    return out


def _items_from_notifications(doc: dict, source: str) -> List[Item]:
    out: List[Item] = []
    block = doc.get("NotificationRules") or {}
    if not isinstance(block, dict):
        return out
    for entry in block.get("notificationRules") or []:
        # 8.x: one rule per entry ({"NotificationRule": {...}}). 9.1.1: one
        # entry whose NotificationRule key holds the list of rules.
        rules = entry.get("NotificationRule") if isinstance(entry, dict) else None
        if isinstance(rules, dict):
            rules = [rules]
        for rule in rules or []:
            if isinstance(rule, dict):
                out.append(Item("notificationrule", str(rule.get("Name") or rule.get("name") or "(unnamed)"),
                                _id_text(rule.get("id")), source))
    for entry in block.get("notificationTemplateDataSet") or []:
        tpls = entry.get("NotificationTemplateData") if isinstance(entry, dict) else None
        if isinstance(tpls, dict):
            tpls = [tpls]
        for tpl in tpls or []:
            if isinstance(tpl, dict):
                out.append(Item("notificationtemplate", str(tpl.get("Name") or tpl.get("name") or "(unnamed)").strip(),
                                _id_text(tpl.get("id")), source))
    return out


def _items_from_payload_templates(doc: dict, source: str) -> List[Item]:
    """``payloadtemplates.json``: ``{"NotificationTemplate": {"notificationTemplateData": [...]}}``."""
    out: List[Item] = []
    block = doc.get("NotificationTemplate") or {}
    if not isinstance(block, dict):
        return out
    for entry in block.get("notificationTemplateData") or []:
        # One template per entry, or one entry whose NotificationTemplateData
        # key holds the list of templates (both seen on 9.x exports).
        tpls = entry.get("NotificationTemplateData") if isinstance(entry, dict) else None
        if isinstance(tpls, dict):
            tpls = [tpls]
        for tpl in tpls or []:
            if isinstance(tpl, dict):
                out.append(Item("notificationtemplate", str(tpl.get("Name") or tpl.get("name") or "(unnamed)").strip(),
                                _id_text(tpl.get("id")), source))
    return out


def _items_from_outbound_settings(doc: dict, source: str) -> List[Item]:
    """``outboundsettings.json``: ``plugins[]`` with ``pluginType`` and a
    ``pluginConfig`` carrying ``pluginName``. Values ride through untouched
    (spec: outbound settings are passed along as exported)."""
    out: List[Item] = []
    for plugin in doc.get("plugins") or []:
        if not isinstance(plugin, dict):
            continue
        cfg = plugin.get("pluginConfig") if isinstance(plugin.get("pluginConfig"), dict) else {}
        name = cfg.get("pluginName") or plugin.get("pluginName") or plugin.get("pluginType") or "(unnamed)"
        uuid = _id_text(cfg.get("id") or plugin.get("id"))
        out.append(Item("outboundsetting", f"{name} ({plugin.get('pluginType', 'unknown plugin type')})", uuid, source))
    return out


def _dashboards_from_inner_zip(inner: bytes) -> List[dict]:
    """A nested dashboard zip found under any member name (UI exports name
    them ``Dashboard-<timestamp>.zip``): wrap it under ``dashboards/`` so the
    core reader can take it."""
    wrapper = io.BytesIO()
    with zipfile.ZipFile(wrapper, "w") as z:
        z.writestr("dashboards/inner", inner)
    return _dashboards_from_export_zip(wrapper.getvalue())


def _dashboard_items(dashes: List[dict], source: str) -> List[Item]:
    return [Item("dashboard", str(d.get("name") or "(unnamed)"), str(d.get("id") or ""), source) for d in dashes]


def navigation_gaps(dashes: List[dict]) -> int:
    """How many dashboard navigation targets are not a widget of the document
    that names them.

    A navigation is a link: click a row here, land on a dashboard there. Every
    target in the corpus resolves to nothing in any of the five exports, which
    means those links point at dashboards that live on the source instance and
    were not exported, and after an import they will not land. That is worth
    saying in the listing and not only in a preview, since it is the listing
    an admin reads before deciding what to carry. Whether a target is a
    dependency the bundle should chase is M5's question (migrator issue #3):
    nothing in the corpus resolves, so nothing here can name what it would be.
    """
    gaps = 0
    for dash in dashes:
        navigations = dash.get("dashboardNavigations")
        if not isinstance(navigations, dict):
            continue
        widget_ids = {str(w.get("id") or "") for w in dash.get("widgets") or []
                      if isinstance(w, dict)}
        for targets in navigations.values():
            for target in targets if isinstance(targets, list) else []:
                if isinstance(target, dict) and str(target.get("id") or "") not in widget_ids:
                    gaps += 1
    return gaps


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------

def read_export(path, source_version: Optional[str] = None) -> Export:
    """Walk the export at *path* and return what it carries.

    *source_version* is the admin's declaration (``--source-version``); it is
    checked against the floor first. Raises ``NotAnExport`` when the file is
    not a zip or carries neither a marker nor ``configuration.json``,
    ``BadSourceVersion`` on a malformed declaration and ``UnsupportedExport``
    when the declared version is below the floor. With no declaration the
    export is read anyway and the listing says so.
    """
    declared = check_source_version(source_version)
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise NotAnExport(f"cannot read {path}: {e}") from e
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise NotAnExport(f"{path} is not a zip file")

    export = Export(path=str(path), source_version=declared)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/") and not n.startswith("__MACOSX/")]

        # Marker and manifest first: they decide whether to continue.
        for name in names:
            m = _MARKER_RE.match(Path(name).name)
            if m and "/" not in name:
                export.marker = name
                export.marker_format = f"v{m.group(2)}"
                export.owner = zf.read(name).decode("utf-8", "replace").strip() or None
            elif name == "configuration.json":
                try:
                    manifest = json.loads(zf.read(name))
                    if isinstance(manifest, dict):
                        export.manifest = manifest
                except ValueError:
                    export.notes.append("configuration.json is not valid JSON")

        if export.marker is None and not export.manifest:
            raise NotAnExport(f"{path} is not a content export: no <digits>L.v1 marker and no configuration.json")
        if export.marker is None:
            export.notes.append("no <digits>L.v1 marker found")
        elif export.marker_format != "v1":
            export.notes.append(f"marker format {export.marker_format} is not the known v1")
        if declared is None:
            export.notes.append("source version not declared (--source-version); the 8.10 floor was not checked")

        # Dashboards: one core-reader pass per owner member, so a dashboard
        # shared by two owners (same uuid under dashboards/<a> and
        # dashboards/<b>; five such on a real 9.x export) lists once per
        # owner, as the manifest's dashboardsByOwner counts it.
        for name in names:
            if name.startswith("dashboards/") and not name.endswith("/"):
                try:
                    dashes = _dashboards_from_inner_zip(zf.read(name))
                    export.items.extend(_dashboard_items(dashes, name))
                    export.navigation_gaps += navigation_gaps(dashes)
                except ValueError:
                    export.carried.append(name)
        if "supermetrics.json" in names:
            for sm in _supermetrics_from_export_zip(data).values():
                export.items.append(Item("supermetric", str(sm.get("name") or "(unnamed)"), str(sm.get("id") or ""), "supermetrics.json"))

        for name in names:
            lower = name.lower()
            if name == export.marker or name in ("configuration.json", "usermappings.json", "supermetrics.json"):
                continue
            if name.startswith("dashboards/") or name.startswith("dashboardsharings/"):
                continue
            member = zf.read(name)

            if name == "dashboard/dashboard.json":
                dashes = _dashboards_from_inner_zip(_wrap_dashboard_json(member))
                export.items.extend(_dashboard_items(dashes, name))
                export.navigation_gaps += navigation_gaps(dashes)
                continue

            if lower.endswith(".zip"):
                xml = _content_xml_from_export_zip(member)
                if xml is not None:
                    found = _items_from_content_xml(xml, name)
                    if found:
                        export.items.extend(found)
                        continue
                try:
                    dashes = _dashboards_from_inner_zip(member)
                except ValueError:
                    dashes = []
                if dashes:
                    export.items.extend(_dashboard_items(dashes, name))
                    export.navigation_gaps += navigation_gaps(dashes)
                    continue
                export.carried.append(name)
                continue

            if lower.endswith(".xml"):
                try:
                    root = ET.fromstring(member)
                except ET.ParseError:
                    export.carried.append(name)
                    continue
                if root.tag.lower() == "alertcontent":
                    export.items.extend(_items_from_alert_content(root, name))
                    continue
                found = _items_from_content_xml(member, name)
                if found:
                    export.items.extend(found)
                    continue
                export.carried.append(name)
                continue

            if lower.endswith(".json"):
                try:
                    doc = json.loads(member)
                except ValueError:
                    export.carried.append(name)
                    continue
                if isinstance(doc, dict) and "customGroups" in doc:
                    export.items.extend(_items_from_customgroups(doc, name))
                    continue
                if isinstance(doc, dict) and "NotificationRules" in doc:
                    export.items.extend(_items_from_notifications(doc, name))
                    continue
                if isinstance(doc, dict) and "NotificationTemplate" in doc:
                    export.items.extend(_items_from_payload_templates(doc, name))
                    continue
                if isinstance(doc, dict) and isinstance(doc.get("plugins"), list) and "serviceCredentials" in doc:
                    export.items.extend(_items_from_outbound_settings(doc, name))
                    continue
                export.carried.append(name)
                continue

            export.carried.append(name)

    # The same object can appear in two members (a full export embeds the
    # notification template inside notificationrules.json and again in
    # payloadtemplates.json). One listing per kind+uuid; the first wins.
    # Dashboards are the exception: the same uuid under two owners is two
    # listings (keyed by owner member), matching dashboardsByOwner.
    seen = set()
    unique: List[Item] = []
    for it in export.items:
        key = (it.kind, it.uuid, it.source if it.kind == "dashboard" else "") if it.uuid else None
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        unique.append(it)
    export.items = unique
    return export


@dataclass
class Members:
    """An export's members in memory: the bytes, the order the zip wrote them
    in, and the marker's name. ``tree`` and ``build`` work from this, so both
    read the zip exactly once."""
    path: str
    data: dict = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    marker: Optional[str] = None


def read_members(path, source_version: Optional[str] = None) -> Members:
    """Load every member of the export at *path*.

    Same refusals as ``read_export``: not a zip, no marker and no
    ``configuration.json``, a declared version below the floor.
    """
    check_source_version(source_version)
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise NotAnExport(f"cannot read {path}: {e}") from e
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        raise NotAnExport(f"{path} is not a zip file")
    members = Members(path=str(path))
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            if name.endswith("/") or name.startswith("__MACOSX/"):
                continue
            members.data[name] = zf.read(name)
            members.order.append(name)
            if "/" not in name and _MARKER_RE.match(Path(name).name):
                members.marker = name
    if members.marker is None and "configuration.json" not in members.data:
        raise NotAnExport(
            f"{path} is not a content export: no <digits>L.v1 marker and no configuration.json")
    return members


def _wrap_dashboard_json(dashboard_json: bytes) -> bytes:
    """A bare ``dashboard/dashboard.json`` at the top of the zip, wrapped as
    the inner zip the core reader expects."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("dashboard/dashboard.json", dashboard_json)
    return inner.getvalue()


# ---------------------------------------------------------------------------
# Text rendering shared by the CLI and the UI
# ---------------------------------------------------------------------------

def render_text(export: Export) -> str:
    lines = [f"export: {export.path}"]
    if export.marker:
        lines.append(f"marker: {export.marker} (format {export.marker_format}, owner {export.owner or 'unknown'})")
    else:
        lines.append("marker: none")
    if export.source_version:
        lines.append(f"source version: {export.source_version} (declared; floor {VERSION_FLOOR_TEXT} passed)")
    else:
        lines.append("source version: not declared")
    if export.manifest:
        summary = " ".join(f"{k}={v}" for k, v in export.manifest.items() if not isinstance(v, (dict, list)))
        lines.append(f"manifest: {summary}")
    for note in export.notes:
        lines.append(f"note: {note}")
    counts = export.counts()
    lines.append(f"items: {len(export.items)}" + (" (" + ", ".join(f"{k}={counts[k]}" for k in KIND_ORDER if k in counts) + ")" if counts else ""))
    for it in export.sorted_items():
        lines.append(f"  {it.kind:<21} {it.uuid or '(no uuid)':<40} {it.name}")
    if export.navigation_gaps:
        lines.append(f"dashboard navigation links pointing outside this export: "
                     f"{export.navigation_gaps} (they will not land on the target unless it "
                     "already has what they point at)")
    if export.carried:
        lines.append(f"carried, not inspected: {len(export.carried)}")
        for name in export.carried:
            lines.append(f"  {name}")
    return "\n".join(lines) + "\n"
