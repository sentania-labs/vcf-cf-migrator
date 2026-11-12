"""What a screen needs to know about each object, worked out once per export.

The dependency graph answers "what does this point at". The screens ask
different questions: is anything this needs, at any depth, missing from the
export; what uses this; which dashboards are copies of each other; and, for a
gap that does not stop a bundle, what the person will actually see on the
target. Answering those per redraw would walk the graph once per row, so the
sets are built once when an export opens and kept beside the graph.

Nothing here reads a file or decides what a bundle carries. The build's own
gate is still ``selection.refuse_missing_dependencies``.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from vcfcf_migrator.graph import Graph, MissingEdge, Node, is_missing_dependency, object_label
from vcfcf_migrator.wording import kind_one, listing, plural, short_listing


@dataclass
class Insight:
    # target key -> the keys that point at it, in listing order
    used_by: Dict[str, List[str]] = field(default_factory=dict)
    # Is, or needs at any depth, something the export does not hold and a
    # bundle cannot go without.
    blocked: frozenset = frozenset()
    # Is, or brings along, a dashboard the source never finished loading.
    unfinished: frozenset = frozenset()
    # Is, or brings along, a dashboard that comes with VCF Operations.
    stock: frozenset = frozenset()
    # Is, or brings along, something with a catch that needs no decision.
    soft: frozenset = frozenset()
    # dashboard key -> the keys of its other copies (same id, another owner)
    twins: Dict[str, List[str]] = field(default_factory=dict)

    def needs_decision(self, key: str) -> bool:
        """Whether adding this is something the person has to answer for,
        which is what keeps it out of a bulk add."""
        return (key in self.blocked or key in self.unfinished or key in self.stock
                or key in self.twins)


def _upstream(seeds, reverse: Dict[str, List[str]]) -> frozenset:
    """The seeds and everything that reaches one of them."""
    seen = set(seeds)
    todo = list(seeds)
    while todo:
        for parent in reverse.get(todo.pop(), ()):
            if parent not in seen:
                seen.add(parent)
                todo.append(parent)
    return frozenset(seen)


def build(graph: Graph) -> Insight:
    reverse: Dict[str, List[str]] = {}
    order = {node.key: position for position, node in enumerate(graph.ordered())}
    for source in sorted(graph.edges, key=lambda k: order.get(k, 0)):
        for target in graph.edges[source]:
            if source != target and source not in reverse.setdefault(target, []):
                reverse[target].append(source)
    hard = {gap.source_key for gap in graph.missing if is_missing_dependency(gap)}
    soft = {gap.source_key for gap in graph.missing
            if not is_missing_dependency(gap) and gap.kind != 'policy'}
    soft.update(note.source_key for note in graph.ambiguous + graph.unhandled)
    copies: Dict[str, List[Node]] = {}
    for node in graph.ordered():
        if node.kind == 'dashboard' and node.owner:
            copies.setdefault(node.ident, []).append(node)
    twins = {}
    for nodes in copies.values():
        if len({n.owner for n in nodes}) > 1:
            for node in nodes:
                twins[node.key] = [other.key for other in nodes if other.key != node.key]
    return Insight(
        used_by=reverse,
        blocked=_upstream(hard, reverse),
        unfinished=_upstream([n.key for n in graph.nodes.values() if n.hollow], reverse),
        stock=_upstream([n.key for n in graph.nodes.values() if n.stock_or_derived], reverse),
        soft=_upstream(soft, reverse),
        twins=twins,
    )


def reach(graph: Graph, key: str) -> List[str]:
    """*key* and everything it needs, nearest first."""
    seen = {key}
    out = [key]
    queue = deque([key])
    while queue:
        for target in graph.edges.get(queue.popleft(), ()):
            if target not in seen and target in graph.nodes:
                seen.add(target)
                out.append(target)
                queue.append(target)
    return out


def path(graph: Graph, start: str, goal: str) -> List[str]:
    """The shortest chain of "uses" from *start* to *goal*, both included."""
    if start == goal:
        return [start]
    came = {start: None}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for target in graph.edges.get(current, ()):
            if target in came or target not in graph.nodes:
                continue
            came[target] = current
            if target == goal:
                chain = [goal]
                while came[chain[-1]] is not None:
                    chain.append(came[chain[-1]])
                return chain[::-1]
            queue.append(target)
    return [start]


def hard_gaps(graph: Graph, key: str) -> List[MissingEdge]:
    """What this object itself needs that the export does not hold."""
    return [gap for gap in graph.missing_for(key) if is_missing_dependency(gap)]


def _is_name(ident: str) -> bool:
    from vcfcf_migrator.graph import _UUID_SHAPE
    return bool(ident) and not _UUID_SHAPE.match(ident)


def _widget(via: str) -> str:
    """The widget a reference was found on, when the graph recorded one."""
    if via.startswith('widget ') and via.endswith(' viewDefinitionId'):
        return via[len('widget '):-len(' viewDefinitionId')]
    if via.startswith('navigation from widget '):
        return via[len('navigation from widget '):]
    return ''


def missing_thing(gap: MissingEdge) -> Tuple[str, str]:
    """(kind in plain words, what to call it) for something not in the export.

    An export names some things and gives only an id for others. An id is no
    use to a person, so where there is no name the thing is described by the
    place that uses it.
    """
    widget = _widget(gap.via)
    if gap.kind == 'view':
        if widget:
            return 'View', f'The view shown by the widget "{widget}"'
        return 'View', 'A view it shows'
    if gap.kind == 'dashboard':
        if widget:
            return 'Dashboard', f'The dashboard its widget "{widget}" links to'
        return 'Dashboard', 'A dashboard it includes'
    if gap.kind in ('dashboard widget', 'dashboard navigation'):
        return 'Link', 'A link to another dashboard that cannot be followed'
    if gap.kind == 'outboundsetting':
        name = gap.ident.split('/', 1)[-1]
        return 'Outbound setting', name or 'The outbound setting it sends through'
    if gap.kind == 'customgroup':
        return 'Group', gap.ident
    label = kind_one(gap.kind)
    if gap.kind in ('symptom', 'alert', 'recommendation'):
        # These are identified by an internal id either way, never by a name
        # a person chose.
        return label, f'{"An" if label[0] in "AEIOU" else "A"} {label.lower()} it uses'
    if _is_name(gap.ident):
        return label, gap.ident
    return label, f'{"An" if label[0] in "AEIOU" else "A"} {label.lower()} it uses'


def missing_phrase(gap: MissingEdge) -> str:
    """``the super metric "Daily Delta"``, ``a view it shows``: for a sentence."""
    label, name = missing_thing(gap)
    if name.startswith(('A ', 'An ', 'The ')):
        return name[0].lower() + name[1:]
    return f'the {label.lower()} "{name}"'


@dataclass
class Catch:
    """One thing to know about an object that does not stop it being moved."""
    code: str
    badge: str                      # a few words, shown on the row and as a heading
    text: str                       # what will happen, in a sentence or two
    gone: List[Tuple[str, str, str]] = field(default_factory=list)  # (kind, name, why)
    task: Optional[Tuple[str, str, List[str]]] = None               # (title, why, steps)
    listed: bool = True             # whether the row in the list carries the badge


def _quoted(names: Sequence[str]) -> str:
    return listing([f'"{name}"' for name in names])


def catches(graph: Graph, node: Node) -> List[Catch]:
    """The plain consequence of each gap on *node* that does not block a
    bundle: which widget, what will be empty, what must be set up."""
    name = object_label(graph, node)
    gaps = [gap for gap in graph.missing_for(node.key)
            if not is_missing_dependency(gap) and gap.kind != 'policy']
    out: List[Catch] = []

    views = [gap for gap in gaps if gap.classification == 'inferred_dangling' and gap.kind == 'view']
    if views:
        widgets = list(dict.fromkeys(_widget(gap.via) or 'unnamed' for gap in views))
        one = len(widgets) == 1
        out.append(Catch(
            'empty-widget',
            'One widget will be empty' if one else f'{len(widgets)} widgets will be empty',
            (f'Its widget {_quoted(widgets)} shows a view that is not in this export, most likely '
             'because the view was deleted on the source. The dashboard still moves; that one widget '
             'stays empty, as it already is on the source.') if one else
            (f'Its widgets {_quoted(widgets)} show views that are not in this export, most likely '
             'because the views were deleted on the source. The dashboard still moves; those widgets '
             'stay empty, as they already are on the source.'),
            [missing_thing(gap) + ('Deleted on the source',) for gap in views],
            (f'Expect {"one empty widget" if one else plural(len(widgets), "empty widget")} on {name}',
             (f'The widget {_quoted(widgets)} shows a view that no longer exists, so it is empty on the source too.'
              if one else
              f'The widgets {_quoted(widgets)} show views that no longer exist, so they are empty on the source too.'),
             [f'Open {name} on the target.',
              (f'Find the widget {_quoted(widgets)}. It will be empty.' if one
               else f'Find the widgets {_quoted(widgets)}. They will be empty.'),
              'Remove the widget, or edit it and pick another view for it to show.'])))

    links = [gap for gap in gaps if (gap.classification == 'inferred_dangling' and gap.kind == 'dashboard')
             or gap.classification == 'inert']
    if links:
        widgets = list(dict.fromkeys(w for w in (_widget(gap.via) for gap in links) if w))
        one = len(links) == 1
        where = f' (from the widget {_quoted(widgets)})' if widgets else ''
        out.append(Catch(
            'dead-link',
            'One link will not work' if one else f'{len(links)} links will not work',
            (f'One of its links to another dashboard{where} points at something that is not in this '
             'export, most likely because it was deleted on the source. The dashboard still moves; '
             'that link leads nowhere, as it already does on the source.') if one else
            (f'{len(links)} of its links to other dashboards{where} point at things that are not in this '
             'export, most likely because they were deleted on the source. The dashboard still moves; '
             'those links lead nowhere, as they already do on the source.'),
            [missing_thing(gap) + ('Deleted on the source' if gap.classification == 'inferred_dangling'
                                   else 'No longer exists',) for gap in links],
            (f'Check the links on {name}',
             'A link from this dashboard to another one no longer has anything to open.',
             [f'Open {name} on the target.',
              'Click through its links to other dashboards' + (f', starting with the widget {_quoted(widgets)}.'
                                                               if widgets else '.'),
              'Remove a link that goes nowhere, or point it at a dashboard that exists.'])))

    groups = list(dict.fromkeys(gap.ident for gap in gaps if gap.classification == 'target_binding'))
    if groups:
        one = len(groups) == 1
        out.append(Catch(
            'needs-group',
            'Needs a group on the target' if one else f'Needs {len(groups)} groups on the target',
            (f'One of its widgets shows the group {_quoted(groups)}, which is not in this export. The '
             'dashboard still moves, but that widget stays empty until the target has a group with '
             'the same name.') if one else
            (f'Its widgets show the groups {_quoted(groups)}, which are not in this export. The dashboard '
             'still moves, but those widgets stay empty until the target has groups with the same names.'),
            [('Group', group, 'Not in this export') for group in groups],
            ((f'Make sure the group {_quoted(groups)} exists on the target' if one
              else f'Make sure {len(groups)} groups exist on the target'),
             f'{name} has a widget that shows ' + ('this group' if one else 'each of these groups')
             + '. Until the target has a group of the same name, the widget stays empty.',
             ['In VCF Operations on the target, open Inventory, then Custom Groups.',
              (f'Look for a group named exactly {_quoted(groups)}.' if one
               else f'Look for groups named exactly {_quoted(groups)}.'),
              'If one is not there, create it. Or go back to the source, export the group as well, and move it too.'])))

    if any(note.source_key == node.key for note in graph.ambiguous):
        out.append(Catch(
            'same-name', 'Shares a name with something else',
            'It uses something by name, and more than one thing in this export has that name. All of them '
            'are included, so nothing is lost; the bundle may hold one more than it needs.',
            listed=False))
    if any(note.source_key == node.key for note in graph.unhandled):
        out.append(Catch(
            'unread', 'Part of it could not be read',
            'Part of it is written in a way this app does not understand, so the app may have missed '
            'something it uses. It still moves. Check it on the target after import.',
            task=(f'Check {name} after import',
                  'This app could not read part of it, so something it uses may be missing from the bundle.',
                  [f'Open {name} on the target and compare it with the source.',
                   'If something is missing, export that as well on the source and move it too.']),
            listed=False))
    return out


def needed_by(graph: Graph, insight: Insight, included, picked, key: str, limit: int = 4) -> List[str]:
    """The person's own choices that bring *key* into the bundle, nearest
    first and at most *limit* of them: enough to say why it is there."""
    picked = set(picked)
    seen = {key}
    queue = deque([key])
    found: List[str] = []
    while queue and len(found) < limit:
        for parent in insight.used_by.get(queue.popleft(), ()):
            if parent in seen or parent not in included:
                continue
            seen.add(parent)
            if parent in picked:
                found.append(parent)
                if len(found) >= limit:
                    break
            queue.append(parent)
    return found


def names(graph: Graph, keys: Sequence[str], limit: int = 3) -> str:
    """``A``, ``A and B``, ``A, B, C and 4 more`` for a list of keys."""
    return short_listing([object_label(graph, graph.nodes[k]) for k in keys if k in graph.nodes], limit)
