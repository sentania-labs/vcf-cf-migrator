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
from vcfcf_migrator import runlog
from vcfcf_migrator import containers as _containers
from vcfcf_migrator.containers import Container, zip_directory_entry, zip_entry
from vcfcf_migrator.graph import Graph
from vcfcf_migrator.selection import Selection, refuse_missing_dependencies

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
    directories: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"path": self.path, "counts": self.counts, "members": self.members,
                "skipped_members": self.skipped_members, "notes": self.notes,
                "directories": self.directories}


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
        try:
            group_doc = json.loads(group.raw(text))
        except ValueError:
            continue
        # Only local groups can be expected to exist without a source directory.
        if not isinstance(group_doc, dict) or group_doc.get("sourceType") != "LOCAL":
            continue
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


def _merge_owner_archives(archives, sharings):
    """Merge owner archives, renaming only colliding file-local tokens."""
    import re

    token_pattern = re.compile(r"(?:resourceKind|adapterKind|resource):id:(\d+)_::_")
    containers = [_containers.DashboardsContainer(name, data, owner=name.split('/', 1)[1])
                  for name, data in archives]
    reserved = set()
    for container in containers:
        doc = json.loads(container.text)
        for table in doc.get('entries', {}).values():
            if isinstance(table, list):
                reserved.update(x.get('internalId') for x in table if isinstance(x, dict)
                                and isinstance(x.get('internalId'), str))
    used = set()
    merged_entries = {}
    dashboards = []
    extras = {}
    changed = []
    merged_sharings = []
    for container in containers:
        doc = json.loads(container.text)
        entries = doc.get('entries', {})
        mapping = {}
        owner_tokens = set()
        for table in entries.values():
            if not isinstance(table, list):
                continue
            for item in table:
                token = item.get('internalId') if isinstance(item, dict) else None
                if not isinstance(token, str):
                    continue
                if token in owner_tokens:
                    raise ValueError('Cannot merge duplicate tokens in one dashboard owner file')
                owner_tokens.add(token)
                if token in used:
                    match = token_pattern.fullmatch(token)
                    if not match:
                        raise ValueError('Cannot merge dashboard entries with an unknown colliding token')
                    prefix = token.split(':id:', 1)[0]
                    number = 0
                    while f'{prefix}:id:{number}_::_' in reserved:
                        number += 1
                    replacement = f'{prefix}:id:{number}_::_'
                    reserved.add(replacement)
                    mapping[token] = replacement
        used.update(owner_tokens)
        def rewrite(text):
            return token_pattern.sub(lambda match: mapping.get(match.group(), match.group()), text)
        for key, table in entries.items():
            if not isinstance(table, list):
                if key in merged_entries and merged_entries[key] != table:
                    raise ValueError('Cannot merge incompatible dashboard entry tables')
                merged_entries[key] = table
                continue
            merged_entries.setdefault(key, []).extend(json.loads(rewrite(json.dumps(table))))
        for entry in container.entries():
            original = entry.raw.decode('utf-8')
            rewritten = rewrite(original)
            dashboards.append(rewritten)
            if rewritten != original:
                changed.append(entry.name)
        sharing = sharings.get(container.owner, b'[]').decode('utf-8')
        merged_sharings.extend(json.loads(rewrite(sharing)))
        for name, data in container.extra.items():
            if name in extras and extras[name] != data:
                if not name.endswith('.properties'):
                    raise ValueError('Cannot merge conflicting dashboard archive members')
                extras[name] = extras[name].rstrip(b'\n') + b'\n' + data
            else:
                extras[name] = data
    first = containers[0]
    pairs = [(v.key or '', json.dumps(merged_entries, ensure_ascii=False) if v.key == 'entries'
              else rawdoc.build_array(dashboards) if v.key == 'dashboards' else v.raw(first.text))
             for v in first.top]
    if not any(key == 'entries' for key, _ in pairs) and merged_entries:
        pairs.append(('entries', json.dumps(merged_entries, ensure_ascii=False)))
    inner = rawdoc.build_object(pairs).encode('utf-8')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(zip_entry(first.INNER), inner)
        for name, data in extras.items():
            z.writestr(zip_entry(name), data)
    return output.getvalue(), json.dumps(merged_sharings).encode('utf-8'), changed



def _validate_dashboard_owners(written, by_owner):
    """Reject an archive whose ownership scaffolding does not match."""
    if not by_owner:
        return
    mapping = json.loads(written['usermappings.json'])
    users = mapping.get('users', [])
    if (mapping.get('sources') != [] or len(users) != 1
            or users[0] != {'userId': next(iter(by_owner)), 'userName': 'admin'}):
        raise ValueError('Dashboard owner mapping is inconsistent')
    owners = set(by_owner)
    for prefix in ('dashboards/', 'dashboardsharings/'):
        found = {name[len(prefix):] for name in written if name.startswith(prefix)}
        if found != owners:
            raise ValueError('Dashboard owner files do not match the owner mapping')
    manifest = json.loads(written['configuration.json'])
    if manifest.get('dashboardsByOwner') != [
            {'owner': owner, 'count': count} for owner, count in sorted(by_owner.items())]:
        raise ValueError('Dashboard counts do not match the owner mapping')
    for owner, count in by_owner.items():
        container = _containers.DashboardsContainer(
            f'dashboards/{owner}', written[f'dashboards/{owner}'], owner=owner)
        entries = container.entries()
        if len(entries) != count:
            raise ValueError('Dashboard count does not match the owner file')
        ids = {entry.ident for entry in entries}
        groups = json.loads(written[f'dashboardsharings/{owner}'])
        if not isinstance(groups, list):
            raise ValueError('Dashboard sharing is not a list')
        for group in groups:
            if not isinstance(group, dict) or group.get('sourceType') != 'LOCAL':
                raise ValueError('Dashboard sharing is not local')
            for dashboard in group.get('dashboards', []):
                if not isinstance(dashboard, dict) or dashboard.get('dashboardId') not in ids:
                    raise ValueError('Dashboard sharing does not match its owner file')

def directory_entries(written: Sequence[str], source_directories: Sequence[str] = ()
                      ) -> List[str]:
    """The zip directory entries a bundle needs: one per directory it puts a
    member in, plus any the source declared above those.

    A bundle that writes ``dashboards/<owner>`` and no ``dashboards/`` entry is
    refused by VCF Operations before a document is read. The entries are
    derived from what the bundle actually carries rather than copied wholesale,
    so a bundle never declares a directory it has nothing in.
    """
    needed: List[str] = []
    for name in written:
        parts = name.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            entry = "/".join(parts[:depth]) + "/"
            if entry not in needed:
                needed.append(entry)
    for name in source_directories:
        # A source entry above a directory the bundle uses (``a/`` where the
        # bundle writes ``a/b/c``) is kept in the source's own spelling.
        if name in needed:
            continue
        if any(other.startswith(name) for other in needed):
            needed.append(name)
    return needed


def build_bundle(members: Dict[str, bytes], member_order: Sequence[str], graph: Graph,
                 selection: Selection, out_path, marker: Optional[str] = None,
                 directories: Sequence[str] = (),
                 directory_order: Optional[Dict[str, int]] = None) -> BuildResult:
    """Refuse missing dependencies before creating or touching an output file."""
    refuse_missing_dependencies(graph, selection)
    return _write_bundle(members, member_order, graph, selection, out_path, marker,
                         directories, directory_order)


def _write_bundle(members: Dict[str, bytes], member_order: Sequence[str], graph: Graph,
                 selection: Selection, out_path, marker: Optional[str] = None,
                 directories: Sequence[str] = (),
                 directory_order: Optional[Dict[str, int]] = None) -> BuildResult:
    """Write the bundle for *selection* and report what went into it."""
    with runlog.phase("build", out=str(out_path), carrying=len(selection.keys)):
        return _build_bundle(members, member_order, graph, selection, out_path, marker,
                             directories, directory_order or {})


def _build_bundle(members: Dict[str, bytes], member_order: Sequence[str], graph: Graph,
                  selection: Selection, out_path, marker: Optional[str] = None,
                  directories: Sequence[str] = (),
                  directory_order: Optional[Dict[str, int]] = None) -> BuildResult:
    result = BuildResult(path=str(out_path))
    picked = _picked_indexes(graph.containers, selection.keys)
    by_owner, dashboard_uuids = _carried_dashboards(graph, selection.keys)
    # A container can carry duplicate dashboard IDs; the graph represents
    # them as one node, while the importer counts every carried document.
    by_owner = {}
    dashboard_count = 0
    for pos, indexes in picked.items():
        container = graph.containers[pos]
        if isinstance(container, _containers.DashboardsContainer):
            count = len(set(indexes))
            dashboard_count += count
            if container.owner:
                by_owner[container.owner] = by_owner.get(container.owner, 0) + count

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
        for note in getattr(container, "notes", []):
            runlog.warn("container.judged", member=container.member, note=note,
                        reason=runlog.prose("the container had to decide about material this tool does "
                               "not fully understand, and says so rather than dropping it "
                               "quietly"))
        runlog.detail("container.rebuilt", member=container.member,
                      container=type(container).__name__,
                      entries_kept=len(set(indexes)),
                      entries_total=len(container.entries()),
                      bytes=len(data) if data else 0,
                      reason=runlog.prose("containers are rebuilt around the documents kept; the "
                             "documents themselves are copied byte for byte"))
        if data:
            written[container.member] = data

    if marker and marker in members:
        written[marker] = members[marker]
        runlog.detail("marker.copied", marker=marker, bytes=len(members[marker]),
                      reason=runlog.prose("copied byte for byte; its content is the source instance's "
                             "owner uuid, which this log does not carry"))
    else:
        runlog.warn("marker.absent",
                    reason=runlog.prose("the source carried no <digits>L.v1 marker, so the bundle has none"))
        result.notes.append("the source carried no <digits>L.v1 marker, so the bundle has none")

    owners = [o for o in by_owner if o]
    # The keys go through the layer like everything else. This line used to
    # call runlog.owner() itself, which is the "a rule a caller can forget"
    # the layer disclaims: it was correct only because this caller remembered.
    runlog.detail("owners.carried", owners=owners,
                  dashboards_by_owner={o: c for o, c in sorted(by_owner.items())},
                  reason=runlog.prose("one dashboard member per owner, and the manifest counts them "
                         "the same way"))
    if owners:
        # Source owner IDs are file-local keys; the importer maps this one to
        # the source-less target account by name.
        target_owner = sorted(owners)[0]
        written["usermappings.json"] = json.dumps({"sources": [], "users": [
            {"userId": target_owner, "userName": "admin"}]}).encode("utf-8")
        dashboard_count = sum(by_owner.values())
        result.notes.append(f"Dashboard ownership maps to the target local admin account "
                            f"({dashboard_count} {'dashboard' if dashboard_count == 1 else 'dashboards'}).")
    # One rule, one loop: **every carried owner gets a sharing member**,
    # whatever the source held. It used to be two loops, one synthesizing where
    # the source had no member at all and one narrowing where it did, and the
    # gap between them was a real bundle the target refuses: a source member
    # that is empty, that does not parse, or that names only dashboards this
    # selection left behind narrows to nothing, and the old code had already
    # skipped synthesis for that owner. An empty list shares with nobody, which
    # imports the dashboards private to whoever imports them. Choosing who else
    # may see an admin's content is not this tool's decision to make, so where
    # it must write something it writes the smallest thing.
    for owner in owners:
        member = f"dashboardsharings/{owner}"
        source = members.get(member)
        narrowed = _narrow_sharings(source, dashboard_uuids) if source else None
        if narrowed is not None:
            written[member] = narrowed
            runlog.detail("scaffolding.narrowed", member=member, bytes=len(narrowed),
                          dashboards=len(dashboard_uuids),
                          reason=runlog.prose("narrowed to the dashboards the bundle carries"))
            continue
        written[member] = b"[]"
        why = ("carried no sharing member for this owner" if source is None else
               "carried a sharing member with nothing in it for the dashboards this "
               "bundle holds, or one that did not parse")
        result.notes.append(
            f"the source {why}, and the target needs one beside every dashboards "
            f"member, so the bundle carries an empty {member}: the dashboards import "
            "private to whoever imports them, and sharing is set on the target")
        runlog.detail("scaffolding.synthesized", member=member,
                      had_source=source is not None,
                      reason=runlog.prose(
                          "every carried owner needs a sharing member beside its "
                          "dashboards member; nothing in the source could be narrowed "
                          "into one, so this is an empty list, which shares with nobody"))
    for name in members:
        if name.startswith("dashboardsharings/") and name.split("/", 1)[1] not in owners:
            runlog.detail("scaffolding.skipped", member=name,
                          reason=runlog.prose("this owner has no dashboard in the bundle"))

    if len(owners) > 1:
        target_owner = sorted(owners)[0]
        archives = [(f"dashboards/{owner}", written[f"dashboards/{owner}"])
                    for owner in sorted(owners)]
        sharings = {owner: written[f"dashboardsharings/{owner}"] for owner in owners}
        merged, sharing, changed = _merge_owner_archives(archives, sharings)
        for owner in owners:
            del written[f"dashboards/{owner}"]
            del written[f"dashboardsharings/{owner}"]
        written[f"dashboards/{target_owner}"] = merged
        written[f"dashboardsharings/{target_owner}"] = sharing
        by_owner = {target_owner: sum(by_owner.values())}
        if changed:
            result.notes.append("Colliding resource tokens were rewritten in: " + ", ".join(changed))
        selected_ids = [graph.nodes[k].ident for k in selection.keys
                        if graph.nodes[k].kind == "dashboard" and graph.nodes[k].owner]
        if len(selected_ids) != len(set(selected_ids)):
            result.notes.append("Some selected dashboards have the same ID under different owners; "
                                "check the imported dashboards on the target.")
    for owner in owners:
        source = members.get(f"dashboardsharings/{owner}")
        if source:
            try:
                groups = json.loads(source)
            except ValueError:
                groups = []
            if any(isinstance(group, dict) and group.get("sourceType") != "LOCAL"
                   for group in groups if isinstance(groups, list)):
                affected = [graph.nodes[k].name for k in selection.keys
                            if (graph.nodes[k].owner, graph.nodes[k].ident)
                            in graph.directory_shared_dashboards]
                result.notes.append("Directory sharing removed for: " + ", ".join(affected)
                                    + ". Set sharing on the target.")
                break

    counts = selection.counts(graph)
    if dashboard_count:
        counts['dashboard'] = dashboard_count
    manifest: Dict[str, object] = {"type": "CUSTOM"}
    for kind, key in MANIFEST_KEYS.items():
        if counts.get(kind):
            manifest[key] = counts[kind]
    if by_owner:
        manifest["dashboardsByOwner"] = [{"owner": o, "count": c}
                                         for o, c in sorted(by_owner.items())]
    written["configuration.json"] = (json.dumps(manifest, indent=3) + "\n").encode("utf-8")
    _validate_dashboard_owners(written, by_owner)
    runlog.detail("manifest.written", member="configuration.json",
                  manifest={k: v for k, v in manifest.items() if not isinstance(v, list)},
                  owners=len(by_owner),
                  reason=runlog.prose("written fresh with the counts actually carried, because the "
                         "source's counts describe the source"))
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
    # A member the source did not have goes beside the member it belongs to,
    # not on the end: a synthesized dashboardsharings/<owner> sits after its
    # dashboards/<owner>, which is where every export puts it and where the
    # factory's own packager writes it.
    for name in sorted(written):
        if name in ordered:
            continue
        sibling = ("dashboards/" + name.split("/", 1)[1]
                   if name.startswith("dashboardsharings/") else "")
        if sibling and sibling in ordered:
            ordered.insert(ordered.index(sibling) + 1, name)
        else:
            ordered.append(name)

    # Directory entries, in the place the source put them: before the members
    # that sit under them, which is where every corpus export writes them.
    needed_dirs = directory_entries(ordered, directories)
    positions = directory_order or {}
    placed: List[str] = []
    for name in ordered:
        for entry in needed_dirs:
            if entry in placed or not name.startswith(entry):
                continue
            placed.append(entry)
        placed.append(name)
    for entry in needed_dirs:
        if entry not in placed:
            placed.append(entry)
    ordered = placed
    result.directories = [n for n in ordered if n.endswith("/")]
    runlog.detail("bundle.directories", directories=result.directories,
                  from_source=[d for d in result.directories if d in (directories or ())],
                  reason=runlog.prose("a zip directory entry per directory the bundle writes into; "
                         "without them VCF Operations refuses the bundle as an invalid "
                         "file format before it reads a document"))

    out_path = Path(out_path)
    try:
        if out_path.parent and str(out_path.parent):
            out_path.parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ordered:
                if name.endswith("/"):
                    z.writestr(zip_directory_entry(name), b"")
                    continue
                z.writestr(zip_entry(name), written[name])
        out_path.write_bytes(buf.getvalue())
    except OSError as e:
        runlog.error("bundle.unwritable", path=str(out_path), reason=str(e),
                     members=len(ordered))
        raise
    # The spec asks the build fingerprint for "the hash of each carried
    # document", and a member is not a document: views.zip is one entry over
    # every view the bundle carries, so a member hash cannot answer "what did
    # it write for the view Ops just rejected".
    documents = []
    for (member, kind, ident, owner), raw in sorted(
            _containers.documents({k: v for k, v in written.items()
                                   if not k.endswith("/")}).items()):
        node = graph.nodes.get(_entry_key(kind, ident, owner))
        documents.append({"member": member, "kind": kind,
                          "uuid": (node.uuid if node else "") or ident,
                          "name": node.name if node else "",
                          "owner": owner or None,
                          "bytes": len(raw), "sha256": runlog.sha256(raw)})
    runlog.info("output.fingerprint", path=str(out_path),
                zip_bytes=len(buf.getvalue()),
                zip_sha256=runlog.sha256(buf.getvalue()),
                documents=len(documents),
                **runlog.output_fingerprint(written, ordered))
    for document in documents:
        runlog.detail("output.document", **document)

    result.counts = counts
    result.members = ordered
    result.skipped_members = [n for n in member_order
                              if n not in written and n in graph.unknown_members]
    if result.skipped_members:
        result.notes.append("members this tool does not understand were not carried "
                            "(they cannot be selected): " + ", ".join(result.skipped_members))
    for name in result.skipped_members:
        runlog.detail("member.not_carried", member=name,
                      reason=runlog.prose("this tool does not understand the member, so it cannot be "
                             "selected and carrying it would be the tool deciding for the "
                             "admin"))
    runlog.info("bundle.written", path=str(out_path), counts=result.counts,
                members=len(result.members), skipped=len(result.skipped_members),
                notes=len(result.notes))
    runlog.count("members_written", len(result.members))
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
