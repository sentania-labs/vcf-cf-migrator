"""What to do on the target after a bundle is saved, as one list of tasks.

The same list is the last screen of the app, the saved checklist file and the
"to do" section of the saved report, so the three cannot disagree. Each task
is a title, why it matters, and numbered steps.

Everything here is worked out from the export alone. The app never sees the
target, so a task says what to check there rather than claiming to know.

The menu names are the ones VCF Operations 9 uses. They were written from the
product's page names and were not checked against a running system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from vcfcf_migrator import insight as _insight
from vcfcf_migrator.graph import KIND_ORDER, Graph, Node, object_label
from vcfcf_migrator.wording import kind_counts, kind_many, listing, plural, short_listing


@dataclass
class Task:
    id: str
    title: str
    why: str
    steps: List[str] = field(default_factory=list)
    # A one-line version for the "good to know" list before saving. Empty when
    # the task has nothing to warn about ahead of time.
    know: str = ''


# The systems a dashboard's data comes from, in words. Keyed by the adapter
# kind the export records. Anything not listed is named as the export names it.
SYSTEMS = {
    'VMWARE': 'vCenter (the system that manages your virtual machines)',
    'VirtualAndPhysicalSANAdapter': 'vSAN (your storage)',
    'NSXTAdapter': 'NSX (your network virtualisation)',
}
# Not systems to connect: a group container, and VCF Operations itself.
_NOT_SYSTEMS = {'', 'Container', 'vCenter Operations Adapter'}


def _owned_dashboards(graph: Graph, selection) -> List[Node]:
    return [graph.nodes[k] for k in selection.keys
            if graph.nodes[k].kind == 'dashboard' and graph.nodes[k].owner]


def owner_names(graph: Graph, dashboards: Sequence[Node]) -> List[str]:
    return list(dict.fromkeys(graph.owner_names.get(n.owner, 'Unknown owner') for n in dashboards))


def directory_groups(graph: Graph, node: Node) -> List[str]:
    """The company-directory groups a dashboard is shared with on the source."""
    return [name or 'a directory group'
            for name, directory in graph.dashboard_shares.get((node.owner, node.ident), []) if directory]


def local_groups(graph: Graph, node: Node) -> List[str]:
    return [name or 'a local group'
            for name, directory in graph.dashboard_shares.get((node.owner, node.ident), []) if not directory]


def systems(graph: Graph, dashboards: Sequence[Node]) -> List[str]:
    """What the chosen dashboards read from, in plain words, without repeats."""
    out: List[str] = []
    for node in dashboards:
        for row in graph.dashboard_bindings.get(node.key, []):
            entry = row.get('entry')
            if not isinstance(entry, dict):
                continue
            adapter = str(entry.get('adapterKindKey') or entry.get('adapterKind') or '')
            if row.get('family') == 'adapterKind' and not adapter:
                adapter = str(entry.get('name') or '')
            if adapter in _NOT_SYSTEMS:
                continue
            # No plain name is known for it, so it is named the way the
            # target's Integrations page lists its adapter.
            text = SYSTEMS.get(adapter, f'the system that the adapter "{adapter}" reads from')
            if text not in out:
                out.append(text)
    return out


def _single_objects(graph: Graph, dashboards: Sequence[Node]) -> List[str]:
    """Widgets tied to one named object, which the target may not have."""
    groups = {n.name for n in graph.nodes.values() if n.kind == 'customgroup'}
    out: List[str] = []
    for node in dashboards:
        for row in graph.dashboard_bindings.get(node.key, []):
            entry = row.get('entry')
            if row.get('family') != 'resource' or not isinstance(entry, dict):
                continue
            name = str(entry.get('name') or '')
            adapter = str(entry.get('adapterKindKey') or '')
            kind = str(entry.get('resourceKindKey') or '')
            if not name or adapter == 'Container' or name in groups:
                continue
            if name.lower().endswith(' world') or kind.lower().endswith('world'):
                continue
            line = (f'{object_label(graph, node)}: the widget "{row.get("widget")}" shows one particular '
                    f'object, "{name}". If the target has no object of that name, edit the widget and '
                    'pick another.')
            if line not in out:
                out.append(line)
    return out


def build_tasks(graph: Graph, selection, keep_ownership: bool = False, target_user: str = 'admin',
                bundle_name: str = 'bundle.zip', folder_name: str = '',
                from_source: bool = False, confirmed=()) -> List[Task]:
    """The ordered list of things to do on the target for this bundle.

    *confirmed* is the missing dependencies the person kept in after saying
    each already exists on the target. Every one becomes a task to confirm it
    there after the import.
    """
    nodes = [graph.nodes[k] for k in selection.keys if k in graph.nodes]
    dashboards = _owned_dashboards(graph, selection)
    all_dashboards = [n for n in nodes if n.kind == 'dashboard']
    metrics = [n for n in nodes if n.kind == 'supermetric']
    groups = [n for n in nodes if n.kind == 'customgroup']
    out: List[Task] = []

    if keep_ownership and dashboards:
        users = listing(owner_names(graph, dashboards))
        shared = [(n, directory_groups(graph, n)) for n in dashboards]
        out.append(Task(
            'users', f'First, make sure these users exist on the target: {users}',
            'Each dashboard goes to the same user as on the source. If one of these users is not on the '
            'target, the import or the owner mapping may fail, and a dashboard whose owner is missing is '
            'skipped.',
            ['In VCF Operations on the target, open Administration, then Access Control.',
             f'Look for each of these in the list of user accounts: {users}.']
            + [f'Look for the group {listing(names)} there as well. {object_label(graph, n)} is shared with it, '
               'and is skipped if the target has no such group.' for n, names in shared if names]
            + ['If anything is missing, do not import yet. Come back to this app and choose '
               '"The built-in admin user" instead.'],
            know='The import may fail if a dashboard owner is not on the target'))
    elif not keep_ownership and dashboards and target_user != 'admin':
        out.append(Task(
            'users', f'First, make sure the user {target_user} exists on the target',
            f'Every dashboard goes to the user {target_user}. If that user is not on the target, the '
            'dashboards are skipped.',
            ['In VCF Operations on the target, open Administration, then Access Control.',
             f'Look for {target_user} in the list of user accounts.',
             'If it is missing, do not import yet. Create the user first, or build the bundle again for '
             'the built-in admin user.'],
            know=f'The dashboards are skipped if the user {target_user} is not on the target'))

    where = f' from your {folder_name} folder' if folder_name else ''
    out.append(Task(
        'import', f'Import {bundle_name}',
        'This is the step that actually moves your content. Nothing has been changed on the target '
        'until you do it.',
        ['In VCF Operations on the target, open Administration, then Content, then Import.',
         f'Choose {bundle_name}{where}.',
         ('When it asks for a password, type the export password you made up when you signed in to the source.'
          if from_source else
          'If it asks for a password, it is the one that was typed when the export was made on the source. '
          'This app never sees that password, so if someone else made the export, ask them.'),
         'Start the import and wait until it says it has finished.']))

    if metrics:
        count = plural(len(metrics), 'super metric')
        on_source = []
        for node in metrics:
            policies = list(dict.fromkeys(graph.policy_names.get(p, '') for p in
                                          graph.metric_policies.get(node.ident, [])))
            policies = [p for p in policies if p]
            if policies:
                on_source.append(f'On the source, {object_label(graph, node)} was switched on in the '
                                 f'{"policy" if len(policies) == 1 else "policies"} {listing(policies)}.')
        out.append(Task(
            'sm', f'Switch on {count}',
            'Super metrics arrive switched off. Until they are switched on, the widgets that use them '
            'stay empty.',
            ['In VCF Operations on the target, open the Operations menu, then Configurations, then '
             'Policy Definition.',
             'Select Default Policy and choose Edit. (A policy is the set of rules VCF Operations applies '
             'to your objects.)',
             'Open Metrics and Properties, then expand Super Metrics.',
             f'Tick {short_listing([object_label(graph, n) for n in metrics], 12)}.',
             'Choose Actions, then State, then Activate. Save.',
             'If Policy Definition lists more than one policy and you do not know which one your objects '
             'use, ask whoever looks after the target before changing the others.']
            + on_source[:12],
            know=(f'{count} arrives switched off' if len(metrics) == 1 else f'{count} arrive switched off')))

    if groups:
        own = [(n, [graph.policy_names.get(r.ident, '') for r in n.refs if r.kind == 'policy'])
               for n in groups if any(r.kind == 'policy' for r in n.refs)]
        standard = [n for n in groups if not any(r.kind == 'policy' for r in n.refs)]
        steps = []
        if standard:
            steps.append((f'{object_label(graph, standard[0])} had no policy of its own' if len(standard) == 1
                          else f'{len(standard)} of the groups had no policy of their own')
                         + ' on the source. Nothing to do.')
        for node, policies in own[:12]:
            named = [p for p in policies if p]
            steps.append(f'{object_label(graph, node)} used the policy {listing(named)} on the source.'
                         if named else
                         f'{object_label(graph, node)} had a policy of its own on the source. The export '
                         'does not carry its name.')
        if len(own) > 12:
            steps.append(f'{len(own) - 12} more groups had a policy of their own.')
        if own:
            steps += ['If you know which policy such a group should use: open Inventory, then Custom Groups, '
                      'edit the group and pick it in the Policy box.',
                      'If you do not know, leave it. The group works with the standard settings, and this '
                      'can be changed later.']
        one = len(groups) == 1
        out.append(Task(
            'grp', 'Check the policy of ' + (f'the group {object_label(graph, groups[0])}' if one
                                              else f'the {len(groups)} groups'),
            'A policy is the set of rules VCF Operations applies to a set of objects: what is measured and '
            'which alerts are on. Policies are not moved, so every group arrives on the standard one, '
            'called Default Policy. That only matters for a group that had a special policy on the source.',
            steps,
            know=('The group arrives' if one else f'The {len(groups)} groups arrive')
            + ' without a policy of ' + ('its' if one else 'their') + ' own'))

    if not keep_ownership:
        for node in dashboards:
            shared = directory_groups(graph, node)
            if not shared:
                continue
            name, group = object_label(graph, node), listing(shared)
            out.append(Task(
                'share-' + node.key, f'Share {name} with {group} again',
                f'On the source it was shared with {group}, from your company’s staff directory. With the '
                'owner you chose, that sharing is not carried over.',
                [f'Open the dashboard {name} on the target.', 'Choose Share.',
                 f'Add {group}. If the target does not offer it, the target is not connected to your '
                 'company directory; share with the individual users instead.']))

    for node in nodes:
        for catch in _insight.catches(graph, node):
            if catch.task:
                title, why, steps = catch.task
                out.append(Task(f'{catch.code}-{node.key}', title, why, list(steps)))

    carried = set(selection.keys)
    for number, gap in enumerate(g for g in dict.fromkeys(confirmed) if g.source_key in carried):
        node = graph.nodes[gap.source_key]
        name, thing = object_label(graph, node), _insight.missing_phrase(gap)
        out.append(Task(
            f'confirm-{number}-{gap.source_key}', f'Confirm that {name} found {thing}',
            f'{name} needs {thing}, which is not in the bundle. You kept it in because you had checked '
            'that the target already has it.',
            [f'After the import, open {name} on the target.',
             'If it looks complete and works, tick this off.',
             f'If it shows an error or a gap where {thing} should be, the target does not have it after '
             'all. Export it from the source as well and move it too.']))

    for node in all_dashboards:
        if not node.hollow:
            continue
        name = object_label(graph, node)
        waiting = [w for w in node.waiting_on if w]
        out.append(Task(
            'unf-' + node.key, f'Repair {name}',
            'It never finished loading on the source, and you chose to bring it anyway. It arrives just '
            'as empty.',
            ([f'On the target, open Administration, then Integrations, and make sure {listing(waiting)} '
              + ('is' if len(waiting) == 1 else 'are') + ' there and switched on.',
              f'Open {name}. If its widgets are still empty, fix the same thing on the source, wait for '
              'the dashboard to load there, then export and move it again.']
             if waiting else
             ['The export does not say what this dashboard was waiting for.',
              'Open it on the target and look at which widgets are empty.',
              'Expect to rebuild those widgets by hand.'])))

    for node in all_dashboards:
        if not node.stock_or_derived:
            continue
        name = object_label(graph, node)
        out.append(Task(
            'stock-' + node.key, f'Check {name} after import',
            'Your copy replaced the one that comes with VCF Operations on the target.',
            [f'Open {name} on the target. It now shows the version that came from the source.',
             'If that is what you wanted, tick this off.',
             'If it is not, this app cannot undo it. Ask an administrator of the target how to bring '
             'the built-in version back.']))

    if all_dashboards:
        reads = systems(graph, all_dashboards)
        from_what = listing(reads)
        names = [object_label(graph, n) for n in all_dashboards]
        out.append(Task(
            'look', 'Open each dashboard and look for empty widgets',
            (f'These dashboards show data that VCF Operations reads from {from_what}. If the target is '
             'not connected to it, the widgets stay empty.' if reads else
             'Dashboards show live data that VCF Operations reads from other systems. If the target is '
             'not connected to the same systems, the widgets stay empty.'),
            [(f'Open {listing(names)}.' if len(names) <= 6 else f'Open each of the {len(names)} dashboards.'),
             'Give them a few minutes. Super metrics need a little time after being switched on.']
            + ([f'If the empty widgets are the ones that show a super metric, go back to "Switch on '
                f'{plural(len(metrics), "super metric")}": it is probably still off in the policy those '
                'objects use.'] if metrics else [])
            + ([f'If other widgets stay empty, open Administration, then Integrations. {from_what[0].upper()}'
                f'{from_what[1:]} should be listed there and shown as collecting data.',
                'If it is not listed, the target is not connected to it yet. That has to be set up before '
                'these dashboards can show anything.'] if reads else
               ['If other widgets stay empty, open Administration, then Integrations, and check that the '
                'systems the source reads from are connected on the target too.'])
            + _single_objects(graph, all_dashboards)[:8]))
    return out


def checklist_text(tasks: Sequence[Task], bundle_name: str) -> str:
    """The saved checklist: the same tasks, to print or pass on."""
    lines = ['Checklist for the target', f'For {bundle_name}', '',
             'Do these in order in the VCF Operations you are moving content into.', '']
    for number, task in enumerate(tasks, 1):
        lines.append(f'{number}. [ ] {task.title}')
        lines.append(f'   {task.why}')
        for letter, step in zip(_letters(), task.steps):
            lines.append(f'   {letter}. {step}')
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def _letters():
    alphabet = 'abcdefghijklmnopqrstuvwxyz'
    for first in [''] + list(alphabet):
        for second in alphabet:
            yield first + second


# -- the report ---------------------------------------------------------------

@dataclass
class Block:
    """One piece of a document a person reads: a heading, a paragraph, a
    bulleted or numbered list, or text kept exactly as written."""
    kind: str                       # 'title', 'heading', 'text', 'muted', 'list', 'steps', 'raw'
    text: str = ''
    items: List[str] = field(default_factory=list)


def owner_sentence(keep_ownership: bool, target_user: str = 'admin') -> str:
    if keep_ownership:
        return 'Dashboards will belong to the same users as on the source.'
    if target_user == 'admin':
        return 'Dashboards will belong to the built-in admin user on the target.'
    return f'Dashboards will belong to the user {target_user} on the target.'


def report_blocks(graph: Graph, selection, tasks: Sequence[Task], bundle_name: str,
                  source_name: str = '', keep_ownership: bool = False, target_user: str = 'admin',
                  kept: Optional[Sequence[str]] = None, needed_by=None,
                  checklist_name: str = '', confirmed: bool = False) -> List[Block]:
    """The report in the order a person reads it: what is inside, what was
    added and why, the owner, what was checked, the person's own calls, and
    what is left to do on the target."""
    nodes = [graph.nodes[k] for k in selection.keys if k in graph.nodes]
    picked = [graph.nodes[k] for k in selection.picked if k in graph.nodes]
    picked_keys = {n.key for n in picked}
    added = [n for n in nodes if n.key not in picked_keys]
    unfinished = [n for n in nodes if n.hollow]
    out = [Block('title', 'Bundle report'),
           Block('muted', bundle_name + (f', made from {source_name}' if source_name else ''))]

    out.append(Block('heading', f'What is inside ({len(nodes)})'))
    by_kind = {}
    for node in nodes:
        by_kind.setdefault(node.kind, []).append(object_label(graph, node))
    kinds = [k for k in KIND_ORDER if k in by_kind] + sorted(k for k in by_kind if k not in KIND_ORDER)
    out.append(Block('list', items=[f'{kind_many(k)}: {", ".join(by_kind[k])}' for k in kinds]))

    out.append(Block('heading', f'Chosen by you ({len(picked)})'))
    out.append(Block('list', items=[object_label(graph, n) for n in picked]))

    out.append(Block('heading', f'Added because they are needed ({len(added)})'))
    if added:
        items = []
        for node in added:
            why = needed_by(node.key) if needed_by else ''
            items.append(object_label(graph, node) + (f' (needed by {why})' if why else ''))
        out.append(Block('list', items=items))
    else:
        out.append(Block('text', 'Nothing was added automatically.'))

    if _owned_dashboards(graph, selection):
        out.append(Block('heading', 'Dashboard owner'))
        out.append(Block('text', owner_sentence(keep_ownership, target_user)))

    out.append(Block('heading', 'What was checked'))
    checked = ('Everything the export says this bundle needs is inside the bundle.' if not confirmed else
               'Everything the export says this bundle needs is inside the bundle, except what you '
               'confirmed is already on the target (listed below).')
    if unfinished:
        names = listing([object_label(graph, n) for n in unfinished])
        checked += (f' The exception is {names}, which never finished loading on the source: the export '
                    f'does not record what {"it needs" if len(unfinished) == 1 else "they need"}, so that '
                    'could not be checked.')
    out.append(Block('text', checked + ' The check used the export only. The target was not looked at.'))

    if kept:
        out.append(Block('heading', 'Kept in against the usual advice'))
        out.append(Block('list', items=list(kept)))

    out.append(Block('heading', f'To do on the target ({len(tasks)})'))
    out.append(Block('steps', items=[task.title for task in tasks]))
    if checklist_name:
        out.append(Block('muted', f'The steps for each are in {checklist_name}.'))
    return out


def blocks_text(blocks: Sequence[Block]) -> str:
    """A document as plain text, for the saved file."""
    lines: List[str] = []
    for block in blocks:
        if block.kind == 'title':
            lines += [block.text, '=' * len(block.text)]
        elif block.kind == 'heading':
            lines += ['', block.text, '-' * len(block.text)]
        elif block.kind in ('text', 'muted'):
            lines.append(block.text)
        elif block.kind == 'list':
            lines += [f'  - {item}' for item in block.items]
        elif block.kind == 'steps':
            lines += [f'  {number}. {item}' for number, item in enumerate(block.items, 1)]
        elif block.kind == 'raw':
            lines += ['', block.text.rstrip('\n')]
    return '\n'.join(lines).rstrip() + '\n'


def inside_line(graph: Graph, selection) -> str:
    """``8 things: 2 dashboards, 1 view, ...``"""
    return (plural(len(selection.keys), 'thing') + ': '
            + kind_counts(selection.counts(graph), KIND_ORDER))


__all__ = ['Task', 'Block', 'build_tasks', 'checklist_text', 'report_blocks', 'blocks_text',
           'inside_line', 'owner_sentence', 'owner_names', 'directory_groups', 'local_groups',
           'systems']
