"""Writing the import bundle: containers rebuilt, documents copied.

The whole milestone rests on one line of the spec: "a selected object's
document goes into the output bundle as the bytes the export carried, never
as a re-serialization of a parsed model". So nothing in here writes a
document. It writes containers (``containers.py`` does the rebuilding) and
the scaffolding a bundle needs around them:

* the ``<digits>L.v1`` marker, copied byte for byte from the source;
* ``configuration.json``, written fresh with counts of what was actually
  carried, because the source's counts describe the source;
* ``usermappings.json`` and ``dashboardsharings/<owner>``, narrowed to the
  owners and dashboards the bundle carries, so no piece of scaffolding points
  at something the bundle does not hold.

Members whose content this tool does not understand are never carried. They
cannot be selected, so carrying them would be the tool deciding for the
admin, and the build report names every one it left behind.
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from vcfcf_migrator import rawdoc
from vcfcf_migrator.containers import Container, zip_entry
from vcfcf_migrator.graph import Graph
from vcfcf_migrator.selection import Selection

# Content kind -> the key configuration.json counts it under.
MANIFEST_KEYS = {
    "dashboard": "dashboards",
    "view": "views",
    "supermetric": "superMetrics",
    "customgroup": "customGroups",
    "symptom": "symptomDefs",
    "alert": "alertDefs",
    "recommendation": "recommendations",
    "report": "reports",
    "notificationrule": "notificationRules",
    "notificationtemplate": "payloadTemplates",
    "outboundsetting": "outboundSettings",
}


@dataclass
class BuildResult:
    path: str
    counts: Dict[str, int] = field(default_factory=dict)
    members: List[str] = field(default_factory=list)
    skipped_members: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"path": self.path, "counts": self.counts, "members": self.members,
                "skipped_members": self.skipped_members, "notes": self.notes}


def _entry_key(kind: str, ident: str, owner: str) -> str:
    if kind == "dashboard" and owner:
        return f"{kind}:{ident}@{owner}"
    return f"{kind}:{ident}"


def _picked_indexes(containers: Sequence[Container], keys: Sequence[str]
                    ) -> Dict[int, List[int]]:
    """container position -> entry indexes to carry.

    A node can have a document in two containers at once (a full export
    writes each notification template into ``notificationrules.json`` and
    again into ``payloadtemplates.json``). Both copies are carried: the
    export held both, and dropping one would be this tool deciding which one
    the target's import wants.
    """
    wanted = set(keys)
    out: Dict[int, List[int]] = {}
    for pos, container in enumerate(containers):
        for entry in container.entries():
            if _entry_key(entry.kind, entry.ident, entry.owner) in wanted:
                out.setdefault(pos, []).append(entry.index)
    return out


def _carried_dashboards(graph: Graph, keys: Sequence[str]) -> Tuple[Dict[str, int], List[str]]:
    """``owner -> count`` and the carried dashboard uuids, for the manifest
    and for narrowing the sharing members."""
    by_owner: Dict[str, int] = {}
    uuids: List[str] = []
    for key in keys:
        node = graph.nodes.get(key)
        if node is None or node.kind != "dashboard":
            continue
        # A UI export's archive names no owner, and dashboardsByOwner is a
        # per-owner count: an entry with an empty owner is worse than none.
        if node.owner:
            by_owner[node.owner] = by_owner.get(node.owner, 0) + 1
        if node.uuid not in uuids:
            uuids.append(node.uuid)
    return by_owner, uuids


def _narrow_usermappings(data: bytes, owners: Sequence[str]) -> Optional[bytes]:
    """``usermappings.json`` with only the owners whose dashboards are
    carried. Each user object is copied, not rebuilt."""
    try:
        text = data.decode("utf-8")
        top = rawdoc.json_members(text)
    except (UnicodeDecodeError, rawdoc.RawDocError):
        return None
    users = rawdoc.member(top, "users")
    if users is None:
        return data
    kept = []
    for item in rawdoc.json_items(text, users.start):
        try:
            doc = json.loads(item.raw(text))
        except ValueError:
            continue
        if isinstance(doc, dict) and str(doc.get("userId") or "") in owners:
            kept.append(item.raw(text))
    if not kept:
        return None
    pairs = [(v.key or "", rawdoc.build_array(kept) if v.key == "users" else v.raw(text))
             for v in top]
    return rawdoc.build_object(pairs).encode("utf-8")


def _narrow_sharings(data: bytes, dashboard_uuids: Sequence[str]) -> Optional[bytes]:
    """``dashboardsharings/<owner>`` with only the carried dashboards in it.
    A sharing entry naming a dashboard the bundle does not hold would point
    the import at an absent object."""
    try:
        text = data.decode("utf-8")
        groups = rawdoc.json_items(text)
    except (UnicodeDecodeError, rawdoc.RawDocError):
        return None
    wanted = set(dashboard_uuids)
    out = []
    for group in groups:
        members = rawdoc.json_members(text, group.start)
        dashboards = rawdoc.member(members, "dashboards")
        if dashboards is None:
            out.append(group.raw(text))
            continue
        kept = []
        for item in rawdoc.json_items(text, dashboards.start):
            try:
                doc = json.loads(item.raw(text))
            except ValueError:
                continue
            if isinstance(doc, dict) and str(doc.get("dashboardId") or "") in wanted:
                kept.append(item.raw(text))
        if not kept:
            continue
        pairs = [(v.key or "", rawdoc.build_array(kept) if v.key == "dashboards"
                  else v.raw(text)) for v in members]
        out.append(rawdoc.build_object(pairs))
    if not out:
        return None
    return rawdoc.build_array(out).encode("utf-8")


def build_bundle(members: Dict[str, bytes], member_order: Sequence[str], graph: Graph,
                 selection: Selection, out_path, marker: Optional[str] = None) -> BuildResult:
    """Write the bundle for *selection* and report what went into it."""
    result = BuildResult(path=str(out_path))
    picked = _picked_indexes(graph.containers, selection.keys)
    by_owner, dashboard_uuids = _carried_dashboards(graph, selection.keys)

    carried_names: Dict[str, set] = {}
    for key in selection.keys:
        node = graph.nodes.get(key)
        if node is not None:
            carried_names.setdefault(node.kind, set()).add(node.name)

    written: Dict[str, bytes] = {}
    for pos, indexes in picked.items():
        container = graph.containers[pos]
        data = container.rebuild(sorted(set(indexes)), carried_names)
        # A container that had to judge material it does not fully understand
        # says so; nothing is dropped quietly.
        result.notes.extend(getattr(container, "notes", []))
        if data:
            written[container.member] = data

    if marker and marker in members:
        written[marker] = members[marker]
    else:
        result.notes.append("the source carried no <digits>L.v1 marker, so the bundle has none")

    owners = [o for o in by_owner if o]
    if owners and "usermappings.json" in members:
        narrowed = _narrow_usermappings(members["usermappings.json"], owners)
        if narrowed is not None:
            written["usermappings.json"] = narrowed
    for name, data in members.items():
        if not name.startswith("dashboardsharings/"):
            continue
        if name.split("/", 1)[1] not in owners:
            continue
        narrowed = _narrow_sharings(data, dashboard_uuids)
        if narrowed is not None:
            written[name] = narrowed

    counts = selection.counts(graph)
    manifest: Dict[str, object] = {"type": "CUSTOM"}
    for kind, key in MANIFEST_KEYS.items():
        if counts.get(kind):
            manifest[key] = counts[kind]
    if by_owner:
        manifest["dashboardsByOwner"] = [{"owner": o, "count": c}
                                         for o, c in sorted(by_owner.items())]
    written["configuration.json"] = (json.dumps(manifest, indent=3) + "\n").encode("utf-8")
    result.notes.append(
        "configuration.json is written fresh with the counts actually carried, and with no "
        "signature: the factory's own content-import path writes it the same way "
        "(vcfcf_core.dashboards.packager, vcfcf_core.reports.render) and those zips import")
    if marker and marker in members:
        result.notes.append(
            "the marker is copied byte for byte, and its content is the source instance's "
            "owner uuid; whether the target accepts a foreign owner uuid there is on the M5 "
            "verification list")

    ordered = [n for n in member_order if n in written]
    ordered += [n for n in sorted(written) if n not in ordered]

    out_path = Path(out_path)
    if out_path.parent and str(out_path.parent):
        out_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ordered:
            z.writestr(zip_entry(name), written[name])
    out_path.write_bytes(buf.getvalue())

    result.counts = counts
    result.members = ordered
    result.skipped_members = [n for n in member_order
                              if n not in written and n in graph.unknown_members]
    if result.skipped_members:
        result.notes.append("members this tool does not understand were not carried "
                            "(they cannot be selected): " + ", ".join(result.skipped_members))
    return result


def render(result: BuildResult) -> str:
    lines = [f"bundle: {result.path}"]
    lines.append("carrying: " + (", ".join(f"{k}={v}" for k, v in sorted(result.counts.items()))
                                 or "nothing"))
    lines.append(f"members: {len(result.members)}")
    for name in result.members:
        lines.append(f"  {name}")
    for note in result.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines) + "\n"
