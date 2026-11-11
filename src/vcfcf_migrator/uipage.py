"""Shared page shell, activity feedback and support controls.

Inventory and review rendering lives in workspace.py. Controls remain forms,
submitted through the native bridge, with no remote assets.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import html
from typing import List

from vcfcf_migrator import preview as _preview
from vcfcf_migrator import runlog as _runlog
from vcfcf_migrator import theme, wording
from vcfcf_migrator.graph import Graph, Node

STYLE = theme.CSS + """
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--ink);
  font:14px/1.5 system-ui,"Segoe UI",Roboto,sans-serif; }
a { color:var(--link) }
h1 { font-size:17px; margin:0; font-weight:650; letter-spacing:-0.01em }
h2 { font-size:13px; margin:0 0 10px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--ink3); font-weight:650 }
h3 { font-size:14px; margin:0 0 6px; font-weight:650 }
code, pre { font-family:ui-monospace,Menlo,Consolas,monospace }
pre { background:var(--line2); padding:10px 12px; border-radius:6px; overflow-x:auto;
  font-size:12px; margin:0 }
button { font:inherit; font-size:13px; padding:5px 11px; border-radius:6px;
  border:1px solid var(--line); background:var(--card); color:var(--ink); cursor:pointer }
button:hover { border-color:var(--ink3) }
button.primary { background:var(--accent); border-color:var(--accent); color:var(--on-accent); font-weight:600 }
button.primary:hover { background:var(--accent-strong) }
button.ghost { border-color:transparent; color:var(--ink2); padding:4px 7px }
button.ghost:hover { border-color:var(--line); background:var(--card) }
input:not([type=checkbox]):not([type=hidden]), select, textarea { font:inherit; font-size:13px; padding:6px 9px; width:100%;
  border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--ink) }
textarea { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12px }
input::placeholder, textarea::placeholder { color:var(--ink-muted); opacity:1 }
button:disabled, input:disabled { opacity:.6; cursor:not-allowed }
label { display:block; font-size:12px; color:var(--ink2); margin-bottom:3px }
/* Keep focus visible after native dialogs and full-body redraws as well as Tab. */
:focus { outline:2px solid var(--focus); outline-offset:1px }
button:focus-visible, input:focus-visible, select:focus-visible,
summary:focus-visible, a:focus-visible { outline:2px solid var(--accent); outline-offset:2px }
small { color:var(--ink3); font-size:11.5px }

header.top { position:sticky; top:0; z-index:5; background:var(--card);
  border-bottom:1px solid var(--line); padding:10px 18px;
  display:flex; align-items:center; gap:14px; flex-wrap:wrap }
header.top .ver { color:var(--ink3); font-size:11.5px; font-family:ui-monospace,monospace }
header.top form { display:flex; gap:8px; align-items:flex-end; flex:1 1 380px }
header.top form.nogrow { flex:0 0 auto }
header.top form .grow { flex:1 1 auto }

.msg, .err { margin:10px 18px 0; padding:9px 12px; border-radius:6px; font-size:13px }
.msg { background:var(--ok-soft); border-left:3px solid var(--ok); color:var(--ok-ink);
  white-space:pre-wrap }
.err { background:var(--bad-soft); border-left:3px solid var(--bad); color:var(--bad-ink);
  white-space:pre-wrap }
/* Lattice's dark danger color is a status accent; use body ink for long text. */
[data-theme="dark"] .err { color:var(--ink) }

.card { background:var(--card); border:1px solid var(--line); border-radius:9px;
  padding:14px 16px; margin-bottom:16px; min-width:0 }
.card pre { max-width:100% }
.row { border-radius:6px; gap:7px }
.row:hover { background:var(--line2) }
.row.sel { background:var(--accent-soft) }
.row.preview-on { box-shadow:inset 0 0 0 2px var(--accent) }
.tick { width:16px; height:16px; margin:0; accent-color:var(--link) }
.missing { color:var(--warn); font-size:12px; overflow-wrap:anywhere }

.stack { display:flex; gap:8px; flex-wrap:wrap; align-items:flex-end }
.stack .grow { flex:1 1 200px }
.field { margin-bottom:10px }
.note { color:var(--ink2); font-size:12px; margin:6px 0 0 }

@media (max-width: 900px) {
  header.top { position:static }
}
"""

ACTIVITY_JS = """
<script>
(function () {
  var busy = false, timer = null, stageLabel = null;
  function finish() {
    busy = false;
    if (timer !== null) { clearInterval(timer); timer = null; }
    var bar = document.getElementById('operation-progress');
    if (bar) { bar.remove(); }
    document.body.removeAttribute('aria-busy');
  }
  function start(action) {
    if (busy) { return false; }
    busy = true;
    stageLabel = null;
    document.body.setAttribute('aria-busy', 'true');
    var bar = document.createElement('div');
    bar.id = 'operation-progress';
    bar.style.cssText = 'position:fixed;inset:0;z-index:9999;background:var(--bg);display:grid;place-items:center;cursor:progress';
    var status = document.createElement('div');
    status.setAttribute('role', 'status');
    status.style.cssText = 'background:var(--card);border:1px solid var(--line);border-radius:9px;padding:24px;color:var(--ink);font:16px system-ui';
    var labels = {'/open':'Opening export', '/pick-export':'Choosing and opening export',
      '/connect':'Connecting to Operations', '/select-all':'Selecting content', '/select-shown':'Selecting shown content',
      '/remove-pick':'Updating selection', '/clear':'Clearing selection',
      '/select':'Updating selection', '/build':'Building bundle',
      '/diagnostics':'Saving anonymized diagnostics'};
    var label = labels[action] || 'Updating page';
    var began = Date.now();
    function update() {
      status.textContent = (stageLabel || label) + ' (' + Math.floor((Date.now() - began) / 1000) + ' seconds). Please wait.';
    }
    update();
    bar.appendChild(status); document.body.appendChild(bar);
    timer = setInterval(update, 1000);
    return true;
  }
  window.migratorActivity = {start:start, finish:finish, stage:function(text) { stageLabel = text; }};
  function captureNavigation(action) {
    if (action !== '/select' && !(action === '/preview' && window.innerWidth > 800)) { return null; }
    return {y:window.scrollY, focus:document.activeElement.id || ''};
  }
  function restoreNavigation(saved) {
    if (!saved) { return; }
    var focused = document.getElementById(saved.focus);
    if (focused) { focused.focus({preventScroll:true}); }
    window.scrollTo(0, saved.y);
  }
  window.migratorNavigation = {capture:captureNavigation, restore:restoreNavigation};
  document.addEventListener('submit', function (ev) {
    // The native bridge collects form data and calls start itself.
    if (window.pywebview && window.pywebview.api) { return; }
    var action = ev.target.getAttribute('action');
    if (!start(action)) { ev.preventDefault(); return; }
    try { sessionStorage.setItem('migrator-navigation', JSON.stringify(captureNavigation(action))); }
    catch (err) { /* Storage may be unavailable in a restricted browser. */ }
  }, true);
  window.addEventListener('pageshow', function (ev) {
    finish();
    if (ev.persisted) { window.location.reload(); return; }
    try {
      var saved = JSON.parse(sessionStorage.getItem('migrator-navigation') || 'null');
      sessionStorage.removeItem('migrator-navigation');
      restoreNavigation(saved);
    } catch (err) { /* Navigation still works without storage. */ }
  });
})();
</script>
"""

PAGE = """<!doctype html>
<html lang="en" data-theme="{theme}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{style}
{preview_css}
</style>
</head>
<body data-theme="{theme}">
{body}
</body>
</html>
"""


def e(value) -> str:
    return html.escape("" if value is None else str(value))


def _button(label: str, name: str = "", value: str = "", primary: bool = False,
            ghost: bool = False) -> str:
    klass = "primary" if primary else ("ghost" if ghost else "")
    attrs = f" name='{e(name)}' value='{e(value)}'" if name else ""
    # Built outside the f-string: a backslash inside an f-string expression is
    # a SyntaxError before 3.12, and this package supports 3.9.
    css = f' class="{klass}"' if klass else ""
    return f"<button type='submit'{attrs}{css}>{e(label)}</button>"


class _RenderState:
    """One coherent page snapshot with membership indexes built once."""

    def __init__(self, state):
        self._state = state
        self.name_counts = Counter((n.kind, n.name) for n in state.graph.nodes.values()) if state.graph else {}
        self._picked = state.selected_keys()
        self._included = state.selection_keys()
        self._required = {}
        if state.graph and state.selection:
            for source in state.selection.keys:
                for target in state.graph.edges.get(source, ()):
                    if source != target:
                        self._required.setdefault(target, []).append(source)

    def __getattr__(self, key):
        return getattr(self._state, key)

    def selected_keys(self):
        return self._picked

    def selection_keys(self):
        return self._included

    def required_by(self, key):
        return self._required.get(key, [])


def render(state) -> str:
    state = _RenderState(state)
    from vcfcf_migrator import workspace
    body = workspace.body(state)
    title = "vcfcf-migrator"
    if state.graph is not None and state.zip_path:
        title = f"vcfcf-migrator: {state.zip_path.rsplit('/', 1)[-1]}"
    return PAGE.format(title=e(title), style=STYLE + workspace.STYLE,
                       preview_css=_preview.PREVIEW_CSS, body=body + ACTIVITY_JS, theme=state.theme)


def _messages(state) -> str:
    out = ""
    if state.message:
        out += f"<p class='msg' role='status'>{e(state.message)}</p>"
    if state.error:
        out += f"<p class='err' role='alert'>{e(state.error)}</p>"
    return out


# ---------------------------------------------------------------------------
# The selection panel: what a build would carry, and the build itself
# ---------------------------------------------------------------------------



def _build_form(state) -> str:
    from vcfcf_migrator.graph import same_id_dashboard_lines
    collisions = same_id_dashboard_lines(state.graph, state.selection.keys, state.owner_mode == 'source') if state.selection else []
    ready = bool(state.selection and state.selection.keys and not state.selection.missing_dependencies() and not collisions)
    reasons = []
    if not (state.selection and state.selection.keys):
        reasons.append("select at least one object")
    if state.selection and state.selection.missing_dependencies():
        reasons.append("resolve the missing references before building")
    if collisions:
        reasons.append("choose one copy of each dashboard before building")
    return "".join([
        "<hr style='border:none;border-top:1px solid var(--line2);margin:14px 0'>",
        "<form method='post' action='/build'>",
        "<div class='field'><label for='out'>Save bundle as</label>",
        f"<input type='text' name='out' id='out' value='{e(state.build_out or state.default_out())}'>"
        "<button type='button' data-pick='bundle' data-target='out'>Save As…</button>"
        "<p class='note'>Bundle files use the .zip extension.</p>"
        "</div>",
        "<p class='note'>The app builds the bundle; it does not import it.</p>",
        "<div class='stack'>",
        f"<button type='submit' class='primary'{'' if ready else ' disabled'}>Build bundle"
        "</button>",
        "</div>",
        ("<p class='note'>" + e("; ".join(reasons)) + "</p>") if reasons else "",
        "</form>",
    ])


def anchor(key: str) -> str:
    """Stable row ID, with a digest to distinguish non-ASCII names."""
    flat = "".join(ch if ("a" <= ch <= "z" or "A" <= ch <= "Z" or "0" <= ch <= "9")
                   else "-" for ch in key)
    if not key.isascii():
        flat += "-" + hashlib.blake2s(key.encode("utf-8"), digest_size=4).hexdigest()
    return "node-" + flat


def _matches(graph: Graph, text: str) -> List[Node]:
    needle = text.strip().lower()
    return [n for n in graph.ordered()
            if needle in n.name.lower() or needle in n.kind.lower()
            or needle in n.uuid.lower() or needle in n.ident.lower()]


def _log_controls(state) -> str:
    destination, destination_from, level, level_from = state.log_settings()
    labels = {'error': 'Errors only', 'warn': 'Warnings and errors', 'info': 'Activity summary',
              'detail': 'Detailed decisions', 'debug': 'Troubleshooting detail'}
    options = ''.join(f"<option value='{e(name)}'{' selected' if name == level else ''}>{labels[name]}</option>"
                      for name in _runlog.LEVEL_NAMES)
    return ("<details class='logging'><summary>Local log settings</summary>"
            "<p>File logging is off by default. Session events are still available in diagnostics. "
            "Local logs are private and can contain paths and content names; share anonymized diagnostics instead. The app never records credentials or export passwords.</p>"
            "<form method='post' action='/settings' class='field'>"
            "<label for='log_file'>Log file path (empty turns file logging off)</label>"
            f"<input type='text' name='log_file' id='log_file' value='{e(destination or '')}'>"
            f"<p class='note'>Current destination comes from {e(destination_from)}. "
            "A value of - means terminal output, usually hidden in the desktop app.</p>"
            + _button('Save log destination') + '</form>'
            "<form method='post' action='/settings' class='field'>"
            "<label for='log_level'>Amount of detail</label>"
            f"<select name='log_level' id='log_level'>{options}</select>"
            f"<p class='note'>Current detail level comes from {e(level_from)}. "
            "Activity summary includes counts and timings. Detailed decisions explains content choices. "
            "Troubleshooting detail includes individual references and widgets.</p>"
            + _button('Save log detail') + '</form></details>')


def _settings_panel(state) -> str:
    themes = ''.join(f"<option value='{name}'{' selected' if state.theme == name else ''}>{name.capitalize()}</option>"
                     for name in ('light', 'dark'))
    return ("<div class='card'><h2>Help &amp; diagnostics</h2>"
            "<ol><li>Open an export zip or connect to VCF Operations to download one.</li>"
            "<li>Select content using the checkboxes. Select a name to preview it. Required content is included automatically.</li>"
            "<li>Review missing references, then build and save a bundle. Import that bundle in your target Operations instance.</li></ol>"
            f"<p>{wording.BUILD_PASSWORD_REMINDER}</p>"
            "<h3>Get support</h3><p>Save diagnostics to share an anonymized record of this session.</p>"
            "<form method='post' action='/diagnostics' class='field'>"
            "<label for='diag_out'>Diagnostics destination</label>"
            f"<input type='text' name='out' id='diag_out' value='{e(state.diagnostics_out or state.default_diagnostics_out())}'>"
            + _button('Save diagnostics…', primary=True)
            + "<details><summary>Cannot open the save dialog?</summary>"
            + _button('Save to the entered path', name='manual', value='1') + '</details>'
            + "<p class='note'>Contains operation codes, counts, timings and versions. Paths, names, identifiers and free text "
            "become report-local labels. No reverse mapping is included. Credentials and export passwords are excluded. "
            "Review the report before sharing and avoid personal details in its filename.</p></form></div>"
            "<div class='card'><h2>Settings</h2><form method='post' action='/settings' class='field'>"
            "<label for='theme'>Appearance</label>"
            f"<select id='theme' name='theme'>{themes}</select>"
            + _button('Save appearance') + '</form>' + _log_controls(state) + '</div>')
