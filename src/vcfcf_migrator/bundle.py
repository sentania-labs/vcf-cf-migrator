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
from typing import Dict, List, Optional, Sequence

from vcfcf_migrator import rawdoc
from vcfcf_migrator import runlog
from vcfcf_migrator import containers as _containers
from vcfcf_migrator.containers import Container, zip_directory_entry, zip_entry
from vcfcf_migrator.graph import (Graph, dashboard_binding_lines, is_directory_group, object_label,
                                  same_id_dashboard_lines)
from vcfcf_migrator.selection import Selection, refuse_missing_dependencies, content_kind_label

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
    checklist_path: str = ''
    checklist: str = ''
    support_details: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"path": self.path, "counts": self.counts, "members": self.members,
                "skipped_members": self.skipped_members, "notes": self.notes,
                "directories": self.directories, "checklist_path": self.checklist_path,
                "checklist": self.checklist}



def post_import_checklist(graph, selection):
    metrics = [graph.nodes[k] for k in selection.keys if graph.nodes[k].kind == 'supermetric']
    groups = [graph.nodes[k] for k in selection.keys if graph.nodes[k].kind == 'customgroup']
    if not metrics and not groups:
        return ''
    lines = ['Post-import checklist', '',
             'Policies are not included. Check policy assignments and enable super metrics on the target.']
    for node in metrics:
        kinds = graph.metric_kinds.get(node.ident, [])
        policies = graph.metric_policies.get(node.ident, [])
        policy_text = ', '.join(graph.policy_names.get(p, p) for p in dict.fromkeys(policies))
        if not policy_text:
            if graph.policy_data_available:
                policy_text = ('not found in policies.xml; verify on the source instance which '
                               'policies enable these super metrics')
            elif graph.policy_file_present:
                policy_text = ('the policy file could not be read as expected; verify on the source '
                               'instance which policies enable these super metrics')
            else:
                policy_text = 'source policy data unavailable'
        lines.extend(['', f'Super metric: {object_label(graph, node)}',
                      '  Resource kinds: ' + (', '.join(kinds) if kinds else 'none declared'),
                      '  Enabled in source policies: ' + policy_text,
                      '  Enable it in a policy on the target.'])
    for node in groups:
        policies = [r.ident for r in node.refs if r.kind == 'policy']
        lines.extend(['', f'Custom group: {object_label(graph, node)}',
                      '  Source policy: ' + (', '.join(graph.policy_names.get(p, f'Unknown policy (ID: {p})') for p in policies)
                                             if policies else 'not specified'),
                      '  Check its policy assignment on the target.'])
    return '\n'.join(lines) + '\n'

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


def _narrow_sharings(data: bytes, dashboard_ids, keep_directory=False) -> Optional[bytes]:
    """``dashboardsharings/<owner>`` with only the carried dashboards in it.
    A sharing entry naming a dashboard the bundle does not hold would point
    the import at an absent object."""
    try:
        text = data.decode("utf-8")
        groups = rawdoc.json_items(text)
    except (UnicodeDecodeError, rawdoc.RawDocError):
        return None
    wanted = set(dashboard_ids)
    out = []
    for group in groups:
        members = rawdoc.json_members(text, group.start)
        try:
            group_doc = json.loads(group.raw(text))
        except ValueError:
            continue
        # Only local groups can be expected to exist without a source directory.
        if not isinstance(group_doc, dict) or (is_directory_group(group_doc) and not keep_directory):
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
                changed.append((entry.ident, container.owner))
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



def _validate_dashboard_owners(written, by_owner, target_user="admin", keep_ownership=False):
    """Reject an archive whose ownership scaffolding does not match."""
    if not by_owner:
        return
    mapping = json.loads(written['usermappings.json'])
    users = mapping.get('users', [])
    if keep_ownership:
        if len(users) != len(by_owner) or {str(u.get('userId')) for u in users if isinstance(u, dict)} != set(by_owner):
            raise ValueError('Dashboard owner mapping is inconsistent')
        sources = mapping.get('sources')
        if not isinstance(sources, list):
            raise ValueError('The export does not contain usable directory information')
        source_ids = {str(source[k]) for source in sources if isinstance(source, dict)
                      for k in ('id', 'sourceId', 'userSourceId', 'uuid') if source.get(k)}
        needed = {str(u['userSourceId']) for u in users if u.get('userSourceId')}
        if not needed.issubset(source_ids):
            raise ValueError('The export is missing directory information for a selected owner')
    elif (mapping.get('sources') != [] or len(users) != 1
          or users[0] != {'userId': next(iter(by_owner)), 'userName': target_user}):
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
            if not isinstance(group, dict) or (is_directory_group(group) and not keep_ownership):
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
                 directory_order: Optional[Dict[str, int]] = None,
                 target_user: str = 'admin', keep_ownership: bool = False) -> BuildResult:
    """Refuse missing dependencies before creating or touching an output file."""
    if not keep_ownership and not target_user.strip():
        raise ValueError('Enter a target user name')
    target_user = target_user.strip()
    refuse_missing_dependencies(graph, selection)
    same_id = same_id_dashboard_lines(graph, selection.keys, keep_ownership)
    if same_id:
        raise ValueError('; '.join(same_id))
    return _write_bundle(members, member_order, graph, selection, out_path, marker,
                         directories, directory_order, target_user, keep_ownership)


def _write_bundle(members: Dict[str, bytes], member_order: Sequence[str], graph: Graph,
                 selection: Selection, out_path, marker: Optional[str] = None,
                 directories: Sequence[str] = (),
                 directory_order: Optional[Dict[str, int]] = None,
                 target_user: str = 'admin', keep_ownership: bool = False) -> BuildResult:
    """Write the bundle for *selection* and report what went into it."""
    with runlog.phase("build", out=str(out_path), carrying=len(selection.keys)):
        return _build_bundle(members, member_order, graph, selection, out_path, marker,
                             directories, directory_order or {}, target_user, keep_ownership)


def _build_bundle(members: Dict[str, bytes], member_order: Sequence[str], graph: Graph,
                  selection: Selection, out_path, marker: Optional[str] = None,
                  directories: Sequence[str] = (),
                  directory_order: Optional[Dict[str, int]] = None,
                  target_user: str = 'admin', keep_ownership: bool = False) -> BuildResult:
    result = BuildResult(path=str(out_path))
    for key in selection.keys:
        node = graph.nodes.get(key)
        if node is not None:
            label = object_label(graph, node)
            if 'id …' in label and (node.uuid or node.ident):
                result.support_details.append(f'{label}: {node.uuid or node.ident}')
        if node is not None and node.kind == 'dashboard':
            lines = dashboard_binding_lines(graph, key)
            if lines:
                result.notes.append('Target requirements for dashboard ' + object_label(graph, node) + ':\n  '
                                    + '\n  '.join(lines))
    picked = _picked_indexes(graph.containers, selection.keys)
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
        result.notes.append("The source export is missing its format marker. The target may reject this bundle; create a new export before importing.")

    owners = [o for o in by_owner if o]
    # The keys go through the layer like everything else. This line used to
    # call runlog.owner() itself, which is the "a rule a caller can forget"
    # the layer disclaims: it was correct only because this caller remembered.
    runlog.detail("owners.carried", owners=owners,
                  dashboards_by_owner={o: c for o, c in sorted(by_owner.items())},
                  reason=runlog.prose("one dashboard member per owner, and the manifest counts them "
                         "the same way"))
    if owners:
        dashboard_count = sum(by_owner.values())
        names = ', '.join(dict.fromkeys(object_label(graph, graph.nodes[k]) for k in selection.keys
                                        if graph.nodes[k].kind == 'dashboard' and graph.nodes[k].owner))
        if keep_ownership:
            try:
                original = json.loads(members['usermappings.json'])
                users = [u for u in original['users'] if isinstance(u, dict)
                         and str(u.get('userId')) in owners]
            except (KeyError, TypeError, ValueError):
                raise ValueError('The export does not contain the selected dashboard owner mappings')
            if {str(u.get('userId')) for u in users} != set(owners):
                raise ValueError('The export does not contain every selected dashboard owner mapping')
            written['usermappings.json'] = json.dumps({'sources': original.get('sources', []),
                                                        'users': users}).encode('utf-8')
            owner_labels = ', '.join(graph.owner_names.get(o, 'Unknown owner') for o in sorted(owners))
            result.notes.append(f'Dashboard ownership stays with {owner_labels} ({dashboard_count} '
                                f"{'dashboard' if dashboard_count == 1 else 'dashboards'}).")
            result.notes.append('Source owners and directory sharing are kept for: ' + names
                                + '. These dashboards will be skipped unless the same users and directories exist on the target.')
        else:
            target_owner = sorted(owners)[0]
            written['usermappings.json'] = json.dumps({'sources': [], 'users': [
                {'userId': target_owner, 'userName': target_user}]}).encode('utf-8')
            label = 'local admin' if target_user == 'admin' else target_user
            result.notes.append(f'Dashboard ownership maps to {label} on the target ({dashboard_count} '
                                f"{'dashboard' if dashboard_count == 1 else 'dashboards'}).")
            if target_user != 'admin':
                result.notes.append('These dashboards will be skipped unless the exact target user exists: ' + names + '.')
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
        carried_ids = {entry.ident for entry in _containers.DashboardsContainer(
            f"dashboards/{owner}", written[f"dashboards/{owner}"], owner=owner).entries()}
        narrowed = _narrow_sharings(source, carried_ids, keep_ownership) if source else None
        if narrowed is not None:
            written[member] = narrowed
            runlog.detail("scaffolding.narrowed", member=member, bytes=len(narrowed),
                          dashboards=len(carried_ids),
                          reason=runlog.prose("narrowed to the dashboards the bundle carries"))
            continue
        written[member] = b"[]"
        affected_names = [object_label(graph, graph.nodes[k]) for k in selection.keys
                          if graph.nodes[k].kind == 'dashboard' and graph.nodes[k].owner == owner]
        result.notes.append('No sharing remains for: ' + ', '.join(affected_names)
                            + '. These dashboards will be private; set sharing on the target.')
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

    if len(owners) > 1 and not keep_ownership:
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
            changed_names = [object_label(graph, graph.nodes[f'dashboard:{ident}@{owner}'])
                             for ident, owner in changed if f'dashboard:{ident}@{owner}' in graph.nodes]
            result.notes.append("Resource references were adjusted when combining dashboards: "
                                + ", ".join(changed_names))
    affected = list(dict.fromkeys(
        object_label(graph, graph.nodes[k]) for k in selection.keys
        if (graph.nodes[k].owner, graph.nodes[k].ident) in graph.directory_shared_dashboards))
    if affected and not keep_ownership:
        result.notes.append("Directory sharing removed for: " + ", ".join(affected)
                            + ". Set sharing on the target.")

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
    _validate_dashboard_owners(written, by_owner, target_user, keep_ownership)
    runlog.detail("manifest.written", member="configuration.json",
                  manifest={k: v for k, v in manifest.items() if not isinstance(v, list)},
                  owners=len(by_owner),
                  reason=runlog.prose("written fresh with the counts actually carried, because the "
                         "source's counts describe the source"))
    result.notes.append('Bundle counts reflect the selected content. Target acceptance has not been checked.')

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
    result.checklist = post_import_checklist(graph, selection)
    if result.checklist:
        result.checklist_path = str(out_path.with_name(out_path.stem + '-post-import.txt'))
        result.notes.append('Post-import policy checklist: ' + result.checklist_path)
        result.notes.append(result.checklist.rstrip())
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
        if result.checklist:
            Path(result.checklist_path).write_text(result.checklist, encoding='utf-8')
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
    """A plain-language report; detailed member data remains available as JSON."""
    lines = [f"Bundle: {result.path}"]
    lines.append("Included: " + (", ".join(f"{content_kind_label(k)}: {v}" for k, v in sorted(result.counts.items()))
                                 or "nothing"))
    for note in result.notes:
        if ': pruned ' in note:
            note = note.split(': pruned ', 1)[1]
            note = 'Removed ' + note.replace('resourceKind entries', 'resource kind entries').replace('resource entries', 'object entries')
        elif ': kept unattributed localization keys:' in note:
            note = 'Localization entries kept because they could not be matched: ' + note.split(': kept unattributed localization keys:', 1)[1].strip()
        elif note.startswith('members this tool does not understand'):
            note = 'Some unsupported content was not included.'
        lines.append(note)
    return "\n".join(lines) + "\n"
