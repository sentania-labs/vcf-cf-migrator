"""Inventory workspace and bundle review, using the application's form actions."""
from __future__ import annotations

from pathlib import Path

from vcfcf_migrator import preview
from vcfcf_migrator.cli import version_lines
from vcfcf_migrator.graph import KIND_ORDER
from vcfcf_migrator.uipage import e, _button, anchor

KIND_LABELS = {
    'dashboard': 'Dashboards', 'view': 'Views', 'supermetric': 'Super metrics',
    'customgroup': 'Groups', 'symptom': 'Symptoms', 'alert': 'Alerts',
    'recommendation': 'Recommendations', 'report': 'Reports',
    'notificationrule': 'Notification rules', 'notificationtemplate': 'Notification templates',
    'outboundsetting': 'Outbound settings',
}

STYLE = """
header.top > form { flex:0 0 auto }
header.top .ver { font-family:inherit }
.workspace { display:grid; grid-template-columns:170px minmax(300px,1.25fr) minmax(300px,1fr);
  gap:16px; padding:16px 18px 110px; align-items:start }
.workspace > * { min-width:0 }
.rail { position:sticky; top:76px }
.rail form { margin:3px 0 }
.rail button { width:100%; text-align:left; padding:9px; display:flex; justify-content:space-between }
.rail button { gap:8px }
.rail button span:first-child { min-width:0; overflow-wrap:anywhere }
.rail button span:last-child { flex-shrink:0 }
.rail button.on { background:var(--accent-soft); border-color:var(--accent) }
.inventory .row { display:grid; grid-template-columns:22px minmax(0,1fr) auto; padding:10px 4px;
  border-bottom:1px solid var(--line2); align-items:center; scroll-margin-top:100px }
.inventory .row form { display:block; min-width:0 }
.inventory .name-button { text-align:left; background:none; border:0; padding:0; width:100%;
  color:var(--accent); font-weight:600; overflow-wrap:anywhere }
.inventory .kindtag { display:block; overflow-wrap:anywhere; font-size:12px; color:var(--ink2) }
.inventory .status { font-size:11px; color:var(--ink2); max-width:110px; overflow-wrap:anywhere }
.inventory .status.warn { color:var(--warn) }
.toolbar { margin:12px 0; display:flex; gap:6px; flex-wrap:wrap }
.inspector { scroll-margin-top:76px; position:sticky; top:76px; max-height:calc(100vh - 180px); overflow:auto }
.inspector .card { margin:0 }
.preview-page { padding:16px 18px 130px }
.inspector.expanded { position:static; max-height:none }
.inspector summary, .review summary { cursor:pointer; padding:10px 0; font-weight:600 }
.inspector details { border-top:1px solid var(--line) }
.inspector dd { margin:0 0 10px; overflow-wrap:anywhere }
.inspector dt { color:var(--ink2); font-size:12px }
.inspector .pv-grid { min-width:0 }
.selection-bar { position:fixed; bottom:0; left:0; right:0; z-index:6; background:var(--card);
  border-top:1px solid var(--line); padding:12px 24px; display:flex; gap:18px;
  align-items:center; justify-content:space-between; flex-wrap:wrap }
.selection-bar p { margin:0 }
.review, .support, .welcome { max-width:1100px; padding:20px; margin:auto auto 100px }
.review-row { display:flex; gap:12px; align-items:center; padding:10px 0; border-bottom:1px solid var(--line2) }
.review-row > span { flex:1; min-width:0; overflow-wrap:anywhere }
.review .err, .review .msg { margin:12px 0 }
.review li { overflow-wrap:anywhere }
.export-name { min-width:0; overflow-wrap:anywhere; flex:1 1 200px }
.top details { max-width:100%; min-width:0 }
.top details[open] { flex-basis:100% }
.top summary { cursor:pointer }
@media(max-width:1100px) {
  .workspace { grid-template-columns:140px minmax(260px,1fr) minmax(260px,1fr); gap:10px; padding:12px 12px 120px }
  .workspace .card { padding:12px }
}
@media(max-width:800px) {
  .workspace { grid-template-columns:minmax(0,1fr); padding-bottom:170px }
  .rail,.inspector { position:static; max-height:none }
  .rail { display:flex; gap:5px; flex-wrap:wrap }
  .rail h2 { flex-basis:100% }
  .rail button { gap:12px }
  .selection-bar { gap:8px; padding:10px 12px }
  .review,.support { margin-bottom:170px }
}
"""


def action(path, label, fields=None, primary=False):
    hidden = ''.join(f"<input type='hidden' name='{e(k)}' value='{e(v)}'>"
                     for k, v in (fields or {}).items())
    return f"<form method='post' action='{path}'>{hidden}{_button(label, primary=primary)}</form>"


def identity(state, node):
    detail = node.kind
    if state.name_counts.get((node.kind, node.name), 0) > 1:
        detail += " · " + (node.uuid or node.ident)
        if node.owner:
            detail += " · owner " + node.owner
    return detail


def display_name(state, node):
    if state.name_counts.get((node.kind, node.name), 0) > 1:
        return node.name + " (" + identity(state, node) + ")"
    return node.name


def header(state):
    change = ("<form method='post' action='/open'><div class='grow'>"
              "<label for='zip'>Export zip path</label>"
              f"<input type='text' id='zip' name='zip' value='{e(state.zip_path)}'></div>"
              + _button('Open export', primary=True) + '</form>')
    if state.file_picker is not None:
        change += action('/pick-export', 'Browse…')
    title = e(Path(state.zip_path).name) if state.graph else 'Open a content export to begin'
    return ("<header class='top'><h1>VCF content migrator</h1>"
            f"<span class='export-name'>{title}</span><span class='ver'>Local session</span>"
            + action('/tab', 'Inventory', {'tab': 'preview'})
            + action('/tab', 'Help & diagnostics', {'tab': 'settings'})
            + f"<details{' open' if not state.graph else ''}><summary>"
            + ('Change export' if state.graph else 'Open export')
            + '</summary>' + change + '</details></header>')


def footer(state):
    if state.graph is None:
        return ''
    picked = len(state.picked)
    included = len(state.selection.keys) if state.selection else 0
    missing = len(state.selection.missing) if state.selection else 0
    status = f'{missing} missing references' if missing else ('Ready to review' if included else 'Choose content')
    return ("<footer class='selection-bar'><div>"
            f"<strong>{picked} picked + {included - picked} required = {included} total objects</strong>"
            f"<p class='note'>{e(status)}. Dependencies are included automatically.</p></div>"
            + action('/tab', f'Review bundle · {included}', {'tab': 'review'}, True) + '</footer>')


def rail(state):
    counts = state.graph.counts()
    types = [('all', 'All content', len(state.graph.nodes)),
             ('selected', 'Selected', len(state.selection_keys()))]
    types += [(kind, KIND_LABELS.get(kind, kind), counts[kind])
              for kind in KIND_ORDER + sorted(set(counts) - set(KIND_ORDER)) if counts.get(kind)]
    out = ["<nav class='rail' aria-label='Content types'><h2>Inventory</h2>"]
    for key, label, count in types:
        here = state.inventory_kind == key
        out.append("<form method='post' action='/category'>"
                   f"<input type='hidden' name='kind' value='{e(key)}'>"
                   f"<button class='{'on' if here else ''}' aria-pressed='{str(here).lower()}'>"
                   f"<span>{e(label)}</span><span>{count}</span></button></form>")
    return ''.join(out) + '</nav>'


def inventory(state):
    nodes = state.visible_nodes()
    out = ["<section class='inventory card'><h2>Choose content</h2>"
           "<form method='post' action='/filter' class='stack'><div class='grow'>"
           "<label for='filter'>Search name, kind or identifier</label>"
           f"<input id='filter' name='filter' type='text' value='{e(state.filter_text)}'></div>"
           + _button('Search') + _button('Clear search', 'clear-filter', '1') + '</form>',
           f"<p class='note'>{len(nodes)} shown / {len(state.graph.nodes)} in export</p>",
           "<div class='toolbar'>" + action('/select-shown', f'Select shown ({len(nodes)})')
           + action('/select-all', f'Select all {len(state.graph.nodes)} objects')
           + action('/clear', 'Clear selection') + '</div>']
    picked, included = state.selected_keys(), state.selection_keys()
    for node in nodes:
        detail = identity(state, node)
        checked = node.key in included
        gaps = state.graph.missing_for(node.key)
        status = 'Picked' if node.key in picked else ('Required' if checked else 'Available')
        if gaps:
            status += f' · {len(gaps)} missing'
        out.append(f"<div class='row{' sel' if checked else ''}{' preview-on' if state.preview_key == node.key else ''}' id='{e(anchor(node.key))}'>"
                   "<form method='post' action='/select'>"
                   f"<input type='hidden' name='key' value='{e(node.key)}'>"
                   f"<input type='hidden' name='on' value='{'0' if checked else '1'}'>"
                   f"<input type='checkbox' id='tick-{e(anchor(node.key))}' class='tick' name='tick'{' checked' if checked else ''} "
                   f"aria-label='{e(('deselect ' if checked else 'select ') + node.name)}' "
                   "onchange='this.form.requestSubmit ? this.form.requestSubmit() : this.form.submit()'>"
                   f"<noscript>{_button('Remove' if checked else 'Add')}</noscript></form>"
                   "<form method='post' action='/preview'>"
                   f"<input type='hidden' name='key' value='{e(node.key)}'>"
                   f"<button class='name-button' id='inspect-{e(anchor(node.key))}'>{e(node.name)}</button>"
                   f"<span class='kindtag'>{e(detail)}</span></form>"
                   f"<span class='status{' warn' if gaps else ''}'>{e(status)}</span></div>")
    if not nodes:
        out.append("<p>No matching content. Change the type or clear your search.</p>")
    return ''.join(out) + '</section>'


def inspector(state):
    out = [f"<aside class='inspector{' expanded' if state.preview_expanded else ''}' id='inspector'><div class='card'><h2>Preview</h2>"]
    node = state.graph.nodes.get(state.preview_key)
    if node is None:
        return ''.join(out) + '<p>Select an object name to inspect its preview, dependencies and details.</p></div></aside>'
    out.append(f'<h3>{e(node.name)}</h3>')
    out.append("<nav class='toolbar' aria-label='Object inspector'>")
    for key, label in [('preview', 'Preview'), ('dependencies', 'Dependencies'), ('details', 'Details')]:
        out.append(action('/inspector-tab', label, {'panel': key}, state.inspector_tab == key))
    out.append('</nav>')
    if state.inspector_tab == 'preview':
        out.append(action('/expand-preview', 'Return to workspace' if state.preview_expanded else 'Expand preview',
                          {'expanded': '0' if state.preview_expanded else '1'}))
        out.append('<p class="note">Preview uses illustrative values.</p>')
        try:
            out.append(preview.fragment(state.graph, node))
        except preview.PreviewError as err:
            out.append(f'<p class="err">{e(err)}</p>')
    elif state.inspector_tab == 'dependencies':
        if state.preview_expanded:
            out.append(action('/expand-preview', 'Return to workspace', {'expanded': '0'}))
        out.append('<h3>Depends on</h3>')
        targets = state.graph.edges.get(node.key, [])
        gaps = state.graph.missing_for(node.key)
        for target in targets:
            child = state.graph.nodes.get(target)
            if child:
                out.append(action('/preview', display_name(state, child), {'key': child.key, 'panel': 'dependencies'}))
        for gap in gaps:
            out.append(f"<p class='missing'>missing {e(gap.kind)} {e(gap.ident)} (via {e(gap.via)})</p>")
        if not targets and not gaps:
            out.append('<p>No dependencies recorded in this export.</p>')
        required = state.required_by(node.key)
        if required:
            out.append('<h3>Required by:</h3>')
            for key in required:
                parent = state.graph.nodes[key]
                out.append(action('/preview', display_name(state, parent), {'key': key, 'panel': 'dependencies'}))
    else:
        if state.preview_expanded:
            out.append(action('/expand-preview', 'Return to workspace', {'expanded': '0'}))
        out.append('<dl>')
        for label, value in [('Kind', node.kind), ('Identifier', node.uuid or node.ident), ('Owner', node.owner)]:
            out.append(f'<dt>{label}</dt><dd>{e(value) or "Not recorded"}</dd>')
        out.append('</dl>')
    out.append(action('/preview', 'Close the preview', {'key': ''}))
    return ''.join(out) + '</div></aside>'


def affected_picks(state, key):
    """Follow included edges backwards to the user's picks, tolerating cycles."""
    todo, visited = [key], set()
    while todo:
        current = todo.pop()
        if current in visited:
            continue
        visited.add(current)
        todo.extend(state.required_by(current))
    return [state.graph.nodes[k] for k in state.picked if k in visited]


def review(state):
    out = ["<main class='review'><div class='card'><h2>Review bundle</h2>",
           action('/tab', 'Back to inventory', {'tab': 'preview'})]
    selection = state.selection
    if not selection or not selection.keys:
        return ''.join(out) + '<p>Pick at least one object before building.</p></div></main>'
    if selection.missing:
        out.append("<div class='err'><strong>Build is blocked by missing references</strong>"
                   '<p>Remove the affected picks or open an export that includes their dependencies. '
                   'A target instance might already have these objects, but this export cannot verify that.</p><ul>')
        for gap in selection.missing:
            source = state.graph.nodes[gap.source_key]
            causes = ', '.join(display_name(state, n) for n in affected_picks(state, gap.source_key))
            out.append(f'<li>{e(display_name(state, source))} requires {e(gap.kind)} {e(gap.ident)} '
                       f'(via {e(gap.via)}). Affected picks: {e(causes)}.</li>')
        out.append('</ul></div>')
    else:
        out.append("<p class='msg'>Ready to build. All recorded dependencies are included.</p>")
    for warnings, title in [(selection.ambiguous, 'Ambiguous references'),
                            (selection.unhandled, 'References not understood')]:
        if warnings:
            out.append(f'<h3>{title}</h3><ul>' + ''.join(f'<li>{e(w)}</li>' for w in warnings) + '</ul>')
    out.append('<p class="note">This checks the export, not whether the target will accept the bundle.</p><h3>Your picks</h3>')
    for key in state.picked:
        node = state.graph.nodes[key]
        out.append(f"<div class='review-row'><span>{e(node.name)} <small>{e(identity(state, node))}</small></span>"
                   + action('/remove-pick', 'Remove pick', {'key': key}) + '</div>')
    out.append(f'<details><summary>{len(selection.added)} automatically required objects</summary><ul>')
    for addition in selection.added:
        out.append(f'<li>{e(display_name(state, state.graph.nodes[addition.key]))}</li>')
    out.append('</ul></details>')
    from vcfcf_migrator.uipage import _build_form
    out.append(_build_form(state))
    if state.build_report:
        out.append("<h2>Last build</h2><details><summary>Bundle report</summary>"
                   f'<pre>{e(state.build_report)}</pre></details>')
    return ''.join(out) + '</div></main>'


def body(state):
    from vcfcf_migrator.uipage import _messages, _settings_panel, _commands_panel
    out = [header(state), _messages(state)]
    if state.tab == 'review':
        out.append(review(state))
    elif state.tab in ('settings', 'commands'):
        out.append("<main class='support'><div class='stack'>"
                   + action('/tab', 'Settings', {'tab': 'settings'})
                   + action('/tab', 'Commands', {'tab': 'commands'}) + '</div>')
        out.append(_settings_panel(state) if state.tab == 'settings' else _commands_panel(state))
        out.append('<details><summary>Versions</summary><pre>' + e('\n'.join(version_lines())) + '</pre></details></main>')
    elif state.graph is None:
        out.append("<main class='welcome card'><h2>Start here</h2><p>Open a content export zip, "
                   'choose the content to carry, then review and build a bundle.</p>'
                   '<p>Processing stays on this machine. The source export is unchanged.</p></main>')
    elif state.preview_expanded:
        out.append("<main class='preview-page'>" + inspector(state) + "</main>")
    else:
        out.append("<main class='workspace'>" + rail(state) + inventory(state) + inspector(state) + '</main>')
    return ''.join(out) + footer(state)
