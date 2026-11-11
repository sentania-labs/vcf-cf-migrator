"""Inventory workspace and bundle review, using the application's form actions."""
from __future__ import annotations

from pathlib import Path

from vcfcf_migrator import __version__
from vcfcf_migrator import preview
from vcfcf_migrator import wording
from vcfcf_migrator.releases import RELEASE_PAGE
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
#release-notice { font-size:12px; white-space:nowrap; text-decoration:none }
#release-notice:hover { text-decoration:underline }
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
  color:var(--accent-ink); font-weight:600; overflow-wrap:anywhere }
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
.connect-form { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px }
.connect-form input:not([type=checkbox]) { width:100%; box-sizing:border-box }
.connect-form input[type=checkbox] { width:auto }
.connect-form fieldset { border:1px solid var(--line); border-radius:6px; padding:16px; min-width:0 }
.connect-form legend { font-weight:600; padding:0 5px }
.connect-form label { margin-top:8px }
.source-page { max-width:1000px; margin:24px auto 120px; padding:0 20px }
.source-choices { display:grid; grid-template-columns:1fr 1fr; gap:16px }
.source-choices button { width:100%; padding:14px; font-size:16px }
.source-form { margin-top:20px }
.required-help { grid-column:2 / -1; font-size:12px; color:var(--ink2) }
.required-help form { display:inline-block!important; margin:3px }
.blocker-actions { display:flex; gap:8px; flex-wrap:wrap; margin:8px 0 }
.connect-form .note { grid-column:1/-1 }
.top details { max-width:100%; min-width:0 }
.top details[open] { flex-basis:100% }
.top summary { cursor:pointer }
@media(max-width:1100px) {
  .workspace { grid-template-columns:140px minmax(260px,1fr) minmax(260px,1fr); gap:10px; padding:12px 12px 120px }
  .workspace .card { padding:12px }
}
@media(max-width:800px) {
  .source-choices,.connect-form { grid-template-columns:1fr }
  .workspace { grid-template-columns:minmax(0,1fr); padding-bottom:170px }
  .rail,.inspector { position:static; max-height:none }
  .rail { display:flex; gap:5px; flex-wrap:wrap }
  .rail h2 { flex-basis:100% }
  .rail button { gap:12px }
  .selection-bar { gap:8px; padding:10px 12px }
  .review,.support { margin-bottom:170px }
}
"""


def action(path, label, fields=None, primary=False, pressed=None):
    hidden = ''.join(f"<input type='hidden' name='{e(k)}' value='{e(v)}'>"
                     for k, v in (fields or {}).items())
    button = _button(label, primary=primary)
    if pressed is not None:
        button = button.replace("<button ", f"<button aria-pressed='{str(pressed).lower()}' ", 1)
    return f"<form method='post' action='{path}'>{hidden}{button}</form>"


def identity(state, node):
    detail = node.kind
    if node.kind == 'dashboard' and node.stock_or_derived:
        detail += ' · stock or derived from stock'
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
    title = ('VCF Operations · ' + state.source_address if state.source_snapshot is not None
             else 'Export zip · ' + Path(state.zip_path).name) if state.graph else 'Choose your content source'
    return ("<header class='top'><h1>VCF content migrator</h1>"
            + f"<span class='ver'>{e(__version__)}</span>"
            + f"<a id='release-notice' href='{RELEASE_PAGE}' hidden></a>"
            + f"<span class='export-name'>{e(title)}</span>"
            + (action('/tab', 'Inventory', {'tab': 'preview'})
               + action('/tab', 'Change source', {'tab': 'source'}) if state.graph else
               action('/tab', 'Choose source', {'tab': 'source'}) if state.tab == 'settings' else '')
            + action('/tab', 'Help & diagnostics', {'tab': 'settings'}) + '</header>')


def connection_form():
    return ("<h2>Connect to VCF Operations</h2>"
               "<p class='note'>Credentials are never saved. Download an export from VCF Operations. Do not run another export with this account while connecting.</p>"
               "<form class='connect-form' method='post' action='/connect' autocomplete='off'>"
               "<fieldset><legend>Connection</legend><label for='source-address'>VCF Operations URL</label>"
               "<input id='source-address' name='address' type='url' placeholder='https://operations.example.com' required></fieldset>"
               "<fieldset><legend>Sign in</legend><label for='source-username'>Username</label>"
               "<input id='source-username' name='username' type='text' required><label for='source-password'>Login password</label>"
               "<input id='source-password' name='password' type='password' required>"
               "<label for='source-auth_source'>Login source</label>"
               "<input id='source-auth_source' name='auth_source' type='text' list='login-sources' value='Local'>"
               "<datalist id='login-sources'><option value='Local'></datalist>"
               f"<p class='note'>{wording.LOGIN_SOURCE_HELP}</p><p id='source-status' role='status'></p></fieldset>"
               "<fieldset><legend>Connection security</legend>"
               "<label for='source-ca_file'>Trusted CA certificate bundle (PEM, optional)</label>"
               "<input id='source-ca_file' name='ca_file' type='text'><button type='button' data-pick='ca' data-target='source-ca_file'>Browse…</button>"
               "<p class='note'>The system trust store is used unless you choose a PEM bundle for a private CA.</p>"
               "<label><input id='source-ignore' type='checkbox' name='ignore_ssl' value='1'> Ignore SSL validation</label>"
               "<p id='ssl-warning' role='status' hidden>The server's identity will not be verified.</p></fieldset>"
               "<fieldset><legend>Export password</legend><label for='source-export_password'>Export encryption password</label>"
               "<input id='source-export_password' name='export_password' type='password' required>"
               f"<p class='note'>{wording.EXPORT_PASSWORD_HELP}</p></fieldset>"
               "<button type='button' id='test-connection'>Test connection</button>"
               "<p id='test-status' role='status'></p>"
               + _button('Connect and load', primary=True) + '</form>')


def source_page(state):
    choices = "<div class='source-choices'>"
    for mode, title, help_text in (
            ('zip', 'Open an export zip', 'Choose an existing content export stored on this computer.'),
            ('connect', 'Connect to VCF Operations', 'Sign in to download a fresh content export from your server.')):
        choices += ("<section class='card'>" + action('/source-mode', title, {'mode': mode}, True)
                    + f"<p>{help_text}</p></section>")
    choices += '</div>'
    out = "<main class='source-page'><h2>Choose a source</h2>" + choices
    if state.graph:
        out += action('/tab', 'Keep current source', {'tab': 'preview'})
        out += f"<p class='note'>{wording.SOURCE_SWITCH_HELP} You will be asked to confirm before your selection is discarded.</p>"
    if state.source_mode == 'connect':
        out += "<section class='card source-form'>" + connection_form() + '</section>'
    elif state.source_mode == 'zip':
        out += ("<section class='card source-form'><h2>Open an export zip</h2>"
                "<form method='post' action='/open'><div class='field'>"
                "<label for='zip'>Export zip path</label>"
                "<input type='text' id='zip' name='zip' required></div>"
                + _button('Open export', primary=True) + '</form>')
        if state.file_picker is not None:
            out += action('/pick-export', 'Browse…')
        out += '<p class="note">Processing stays on this computer. The source export is unchanged.</p></section>'
    return out + '</main>'


def footer(state):
    if state.graph is None:
        return ''
    picked = len(state.picked)
    included = len(state.selection.keys) if state.selection else 0
    missing = len(state.selection.missing_dependencies()) if state.selection else 0
    status = f'{missing} missing references' if missing else ('Ready to review' if included else 'Choose content')
    return ("<footer class='selection-bar'><div>"
            f"<strong>{picked} you selected + {included - picked} automatically required = {included} included</strong>"
            f"<p class='note'>{e(status)}. Dependencies are included automatically.</p></div>"
            + action('/tab', f'Review bundle · {included}', {'tab': 'review'}, True) + '</footer>')


def rail(state):
    counts = state.graph.counts()
    types = [('all', 'All content', len(state.graph.nodes)),
             ('selected', 'Included', len(state.selection_keys()))]
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
           + action('/clear', 'Clear selection') + '</div>'
           + "<p class='note'>Adds shown items to your current selection. Select the checkbox to include; select the name to preview.</p>"]
    picked, included = state.selected_keys(), state.selection_keys()
    for node in nodes:
        detail = identity(state, node)
        checked = node.key in included
        required = checked and node.key not in picked
        gaps = state.graph.missing_for(node.key)
        status = 'You selected' if node.key in picked else ('Automatically required' if checked else 'Available')
        if gaps:
            status += f' · {len(gaps)} missing'
        out.append(f"<div class='row{' sel' if checked else ''}{' preview-on' if state.preview_key == node.key else ''}' id='{e(anchor(node.key))}'>"
                   "<form method='post' action='/select'>"
                   f"<input type='hidden' name='key' value='{e(node.key)}'>"
                   f"<input type='hidden' name='on' value='{'0' if checked else '1'}'>"
                   f"<input type='checkbox' id='tick-{e(anchor(node.key))}' class='tick' name='tick'{' checked' if checked else ''} "
                   f"aria-label='{e(('Automatically required: ' if required else 'deselect ' if checked else 'select ') + node.name)}' "
                   + (f"disabled aria-describedby='required-{e(anchor(node.key))}' " if required else '') +
                   "onchange='this.form.requestSubmit ? this.form.requestSubmit() : this.form.submit()'>"
                   f"<noscript>{_button('Remove' if checked else 'Add')}</noscript></form>"
                   "<form method='post' action='/preview'>"
                   f"<input type='hidden' name='key' value='{e(node.key)}'>"
                   f"<button class='name-button' id='inspect-{e(anchor(node.key))}'>{e(node.name)}</button>"
                   f"<span class='kindtag'>{e(detail)}</span></form>"
                   f"<span class='status{' warn' if gaps else ''}'>{e(status)}</span>"
                   + (f"<div class='required-help' id='required-{e(anchor(node.key))}'>Required by your selections: "
                      + ''.join(action('/preview', display_name(state, n), {'key': n.key, 'panel': 'dependencies'})
                                for n in affected_picks(state, node.key)) + '</div>' if required else '') + '</div>')
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
        out.append(action('/inspector-tab', label, {'panel': key}, state.inspector_tab == key, pressed=state.inspector_tab == key))
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
        out.append('<h3>Requires</h3>')
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
            out.append('<h3>Required by these selected or included objects</h3>')
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
        return ''.join(out) + '<p>Select at least one object before building.</p></div></main>'
    dashboards = [state.graph.nodes[k] for k in selection.keys
                  if state.graph.nodes[k].kind == "dashboard" and state.graph.nodes[k].owner]
    if dashboards:
        out.append("<p class='note'>Dashboard ownership will map to admin on the target.</p>")
        directory = [n.name for n in dashboards if n.owner in state.graph.directory_owners]
        if directory:
            out.append("<p class='note'>Directory-owned dashboards: "
                       + e(", ".join(directory)) + ". Check their sharing on the target.</p>")
        shared = [n.name for n in dashboards
                  if (n.owner, n.ident) in state.graph.directory_shared_dashboards]
        if shared:
            out.append("<p class='note'>Directory sharing will be removed for: "
                       + e(", ".join(shared)) + ". Set sharing on the target.</p>")
    if any(state.graph.nodes[k].stock_or_derived for k in selection.keys):
        out.append("<p class='note'>Stock or derived dashboards stay selectable. Importing one can replace the dashboard with the same ID on the target.</p>")
    from vcfcf_migrator.graph import dashboard_binding_lines
    for key in selection.keys:
        node = state.graph.nodes[key]
        if node.kind != 'dashboard':
            continue
        requirements = dashboard_binding_lines(state.graph, key)
        if requirements:
            out.append('<h3>Target requirements for ' + e(node.name) + '</h3><ul>'
                       + ''.join('<li>' + e(line) + '</li>' for line in requirements) + '</ul>')
    if selection.missing_dependencies():
        out.append("<div class='err'><strong>Build is blocked by missing references</strong>"
                   '<p>Remove the affected selections or open an export that includes their dependencies. '
                   'A target instance might already have these objects, but this export cannot verify that.</p><ul>')
        for gap in selection.missing_dependencies():
            source = state.graph.nodes[gap.source_key]
            out.append(f'<li>{e(display_name(state, source))} requires {e(gap.kind)} {e(gap.ident)}. '
                       'This reference is missing from the export. Affected selections:')
            for pick in affected_picks(state, gap.source_key):
                out.append("<div class='blocker-actions'>"
                           + action('/preview', display_name(state, pick), {'key': pick.key, 'panel': 'dependencies'})
                           + action('/remove-pick', 'Remove affected selection', {'key': pick.key}) + '</div>')
            out.append('</li>')
        out.append('</ul></div>')
    else:
        out.append("<p class='msg'>Ready to build. All recorded dependencies are included.</p>")
    if selection.not_carried():
        from vcfcf_migrator.graph import missing_line
        out.append('<h3>Referenced but not carried</h3><ul>' + ''.join(
            f'<li>{e(missing_line(state.graph, gap))}</li>'
            for gap in selection.not_carried()) + '</ul>')
    for warnings, title in [(selection.ambiguous, 'Ambiguous references'),
                            (selection.unhandled, 'References not understood')]:
        if warnings:
            out.append(f'<h3>{title}</h3><ul>' + ''.join(f'<li>{e(w)}</li>' for w in warnings) + '</ul>')
    out.append('<p class="note">This checks the export, not whether the target will accept the bundle.</p><h3>You selected</h3>')
    from vcfcf_migrator.graph import dashboard_supermetrics
    for key in state.picked:
        node = state.graph.nodes[key]
        metric_count = len(dashboard_supermetrics(state.graph, key)) if node.kind == 'dashboard' else 0
        noun = 'super metric' if metric_count == 1 else 'super metrics'
        metric_note = (f"<small>Uses {metric_count} {noun} that must be enabled in a policy on the target.</small>"
                       if metric_count else '')
        out.append(f"<div class='review-row'><span>{e(node.name)} <small>{e(identity(state, node))}</small>{metric_note}</span>"
                   + action('/remove-pick', 'Remove from selection', {'key': key}) + '</div>')
    out.append(f'<details><summary>{len(selection.added)} automatically required objects</summary><ul>')
    for addition in selection.added:
        node = state.graph.nodes[addition.key]
        count = len(dashboard_supermetrics(state.graph, addition.key)) if node.kind == 'dashboard' else 0
        noun = 'super metric' if count == 1 else 'super metrics'
        note = f' uses {count} {noun} that must be enabled in a policy on the target' if count else ''
        out.append(f'<li>{e(display_name(state, node))}{note}</li>')
    out.append('</ul></details>')
    from vcfcf_migrator.uipage import _build_form
    out.append(_build_form(state))
    if state.built_path:
        out.append(f"<p class='msg'>Saved to {e(state.built_path)}</p>")
        if state.show_in_folder:
            out.append(action('/show-in-folder', 'Show in folder'))
    if state.build_report:
        out.append("<h2>Last build</h2><details><summary>Bundle report</summary>"
                   f'<pre>{e(state.build_report)}</pre></details>')
    return ''.join(out) + '</div></main>'


def body(state):
    from vcfcf_migrator.uipage import _messages, _settings_panel
    out = [header(state), _messages(state)]
    if state.tab == 'source' or (state.graph is None and state.tab != 'settings'):
        out.append(source_page(state))
    elif state.tab == 'review':
        out.append(review(state))
    elif state.tab == 'settings':
        out.append("<main class='support'><div class='stack'>"
                   + '</div>')
        out.append(_settings_panel(state))
        out.append('<details><summary>Versions</summary><pre>' + e('\n'.join(version_lines())) + '</pre></details></main>')
    elif state.preview_expanded:
        out.append("<main class='preview-page'>" + inspector(state) + "</main>")
    else:
        out.append("<main class='workspace'>" + rail(state) + inventory(state) + inspector(state) + '</main>')
    return ''.join(out) + footer(state)
