"""Read a VCF Operations content export zip and list what it carries.

The export layout (from the factory's ``vcfops-api`` skill, wire-formats):

    <digits>L.v1                  marker, contents = owner user uuid
    configuration.json            manifest
    views.zip                     nested zip holding content.xml (ViewDefs)
    usermappings.json             owners referenced by dashboards
    dashboards/<ownerUserId>      nested zip per owner, dashboard/dashboard.json
    dashboardsharings/<ownerUserId>
    supermetrics.json             dict keyed by uuid

UI exports of the other content types add ``AlertContent.xml`` (symptoms,
alerts, recommendations), a custom group JSON (``customGroups`` list), a
notification settings JSON (``NotificationRules``) and a reports zip whose
``content.xml`` carries ``ReportDef`` elements. Everything not recognised is
listed as "carried, not inspected" and left alone.

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
]

_MARKER_RE = re.compile(r"^(\d+)L\.v(\d+)$")
_VERSION_KEYS = ("version", "productVersion", "buildVersion", "exportVersion", "opsVersion")


class UnsupportedExport(Exception):
    """The export is readable but refused (below the version floor)."""


class NotAnExport(Exception):
    """The path is not a readable zip."""


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
    version: Optional[str] = None
    version_source: Optional[str] = None
    manifest: dict = field(default_factory=dict)
    items: List[Item] = field(default_factory=list)
    carried: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

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
            "version": self.version,
            "version_source": self.version_source,
            "manifest": self.manifest,
            "counts": self.counts(),
            "items": [it.as_dict() for it in self.sorted_items()],
            "carried": list(self.carried),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Version handling
# ---------------------------------------------------------------------------

def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """``"8.10.2"`` -> ``(8, 10, 2)``; None when the text has no leading digits."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", str(text))
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def below_floor(version: str) -> bool:
    parsed = parse_version(version)
    if parsed is None:
        return False
    return parsed < VERSION_FLOOR


def _version_from_manifest(manifest: dict) -> Optional[str]:
    for key in _VERSION_KEYS:
        value = manifest.get(key)
        if isinstance(value, (str, int, float)) and parse_version(str(value)):
            return str(value)
    return None


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
        name = el.get("name") or el.get("description") or (el.text or "").strip() or "(unnamed)"
        out.append(Item("recommendation", name, el.get("key") or el.get("id") or "", source))
    return out


def _items_from_customgroups(doc: dict, source: str) -> List[Item]:
    out: List[Item] = []
    for group in doc.get("customGroups") or []:
        if not isinstance(group, dict):
            continue
        uuid = group.get("id") or group.get("identifier") or ""
        out.append(Item("customgroup", str(group.get("name") or "(unnamed)"), str(uuid), source))
    return out


def _items_from_notifications(doc: dict, source: str) -> List[Item]:
    out: List[Item] = []
    block = doc.get("NotificationRules") or {}
    if not isinstance(block, dict):
        return out
    for entry in block.get("notificationRules") or []:
        rule = entry.get("NotificationRule") if isinstance(entry, dict) else None
        if isinstance(rule, dict):
            out.append(Item("notificationrule", str(rule.get("Name") or rule.get("name") or "(unnamed)"),
                            str(rule.get("id") or ""), source))
    for entry in block.get("notificationTemplateDataSet") or []:
        tpl = entry.get("NotificationTemplateData") if isinstance(entry, dict) else None
        if isinstance(tpl, dict):
            out.append(Item("notificationtemplate", str(tpl.get("Name") or tpl.get("name") or "(unnamed)").strip(),
                            str(tpl.get("id") or ""), source))
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


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------

def read_export(path) -> Export:
    """Walk the export at *path* and return what it carries.

    Raises ``NotAnExport`` when the file is not a zip and
    ``UnsupportedExport`` when the export identifies as older than the floor.
    When the version cannot be determined the export is read anyway and a
    note says so.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise NotAnExport(f"cannot read {path}: {e}") from e
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise NotAnExport(f"{path} is not a zip file")

    export = Export(path=str(path))
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

        version = _version_from_manifest(export.manifest)
        if version:
            export.version, export.version_source = version, "configuration.json"
            if below_floor(version):
                raise UnsupportedExport(
                    f"refused: export identifies as VCF Operations {version}; the floor is {VERSION_FLOOR_TEXT}"
                )
        else:
            export.notes.append("export version could not be determined; continuing")
        if export.marker is None:
            export.notes.append("no <digits>L.v1 marker found; this may not be a content export")
        elif export.marker_format != "v1":
            export.notes.append(f"marker format {export.marker_format} is not the known v1")

        # Core readers over the whole outer zip.
        if any(n.startswith("dashboards/") for n in names):
            export.items.extend(_dashboard_items(_dashboards_from_export_zip(data), "dashboards/"))
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
                export.items.extend(_dashboard_items(_dashboards_from_inner_zip(_wrap_dashboard_json(member)), name))
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
                export.carried.append(name)
                continue

            export.carried.append(name)

    return export


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
    if export.version:
        lines.append(f"version: {export.version} (from {export.version_source})")
    else:
        lines.append("version: undetermined (continuing)")
    if export.manifest:
        summary = " ".join(f"{k}={v}" for k, v in export.manifest.items() if not isinstance(v, (dict, list)))
        lines.append(f"manifest: {summary}")
    for note in export.notes:
        lines.append(f"note: {note}")
    counts = export.counts()
    lines.append(f"items: {len(export.items)}" + (" (" + ", ".join(f"{k}={counts[k]}" for k in KIND_ORDER if k in counts) + ")" if counts else ""))
    for it in export.sorted_items():
        lines.append(f"  {it.kind:<21} {it.uuid or '(no uuid)':<40} {it.name}")
    if export.carried:
        lines.append(f"carried, not inspected: {len(export.carried)}")
        for name in export.carried:
            lines.append(f"  {name}")
    return "\n".join(lines) + "\n"
