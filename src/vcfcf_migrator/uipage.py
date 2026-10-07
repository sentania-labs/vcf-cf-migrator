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
from vcfcf_migrator import settings as _settings
from vcfcf_migrator.graph import Graph, Node

STYLE = """
:root {
  --bg:#f6f7f9; --card:#ffffff; --line:#d9dee5; --line2:#eef1f5; --ink:#1d2430;
  --ink2:#5a6676; --ink3:#8b95a3; --accent:#1f5fbf; --accent-soft:#e8f0fc;
  --ok:#1a7f4b; --ok-soft:#e6f4ec; --warn:#8a6100; --warn-soft:#fdf3d8;
  --bad:#a22c1c; --bad-soft:#fbe9e6;
}
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--ink);
  font:14px/1.5 system-ui,"Segoe UI",Roboto,sans-serif; }
a { color:var(--accent) }
h1 { font-size:17px; margin:0; font-weight:650; letter-spacing:-0.01em }
h2 { font-size:13px; margin:0 0 10px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--ink3); font-weight:650 }
h3 { font-size:14px; margin:0 0 6px; font-weight:650 }
code, pre { font-family:ui-monospace,Menlo,Consolas,monospace }
pre { background:var(--line2); padding:10px 12px; border-radius:6px; overflow-x:auto;
  font-size:12px; margin:0 }
button { font:inherit; font-size:13px; padding:5px 11px; border-radius:6px;
  border:1px solid var(--line); background:#fff; color:var(--ink); cursor:pointer }
button:hover { border-color:var(--ink3) }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600 }
button.primary:hover { background:#1a4fa0 }
button.ghost { border-color:transparent; color:var(--ink2); padding:4px 7px }
button.ghost:hover { border-color:var(--line); background:#fff }
input[type=text], input[type=url], input[type=password], textarea { font:inherit; font-size:13px; padding:6px 9px; width:100%;
  border:1px solid var(--line); border-radius:6px; background:#fff; color:var(--ink) }
textarea { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12px }
label { display:block; font-size:12px; color:var(--ink2); margin-bottom:3px }
:focus-visible { outline:2px solid var(--accent); outline-offset:1px }
small { color:var(--ink3); font-size:11.5px }

header.top { position:sticky; top:0; z-index:5; background:var(--card);
  border-bottom:1px solid var(--line); padding:10px 18px;
  display:flex; align-items:center; gap:14px; flex-wrap:wrap }
header.top .ver { color:var(--ink3); font-size:11.5px; font-family:ui-monospace,monospace }
header.top form { display:flex; gap:8px; align-items:flex-end; flex:1 1 380px }
header.top form.nogrow { flex:0 0 auto }
header.top form .grow { flex:1 1 auto }

.msg, .err { margin:10px 18px 0; padding:9px 12px; border-radius:6px; font-size:13px }
.msg { background:var(--ok-soft); border-left:3px solid var(--ok); color:#14532d;
  white-space:pre-wrap }
.err { background:var(--bad-soft); border-left:3px solid var(--bad); color:#7f1d1d;
  white-space:pre-wrap }

.card { background:var(--card); border:1px solid var(--line); border-radius:9px;
  padding:14px 16px; margin-bottom:16px; min-width:0 }
.card pre { max-width:100% }
.row { border-radius:6px; gap:7px }
.row:hover { background:var(--line2) }
.row.sel { background:var(--accent-soft) }
.row.preview-on { box-shadow:inset 0 0 0 2px var(--accent) }
.tick { width:16px; height:16px; margin:0; accent-color:var(--accent) }
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
    bar.style.cssText = 'position:fixed;inset:0;z-index:9999;background:#ffffffaa;display:grid;place-items:center;cursor:progress';
    var status = document.createElement('div');
    status.setAttribute('role', 'status');
    status.style.cssText = 'background:#fff;border:1px solid #d9dee5;border-radius:9px;padding:24px;color:#1d2430;font:16px system-ui';
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
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{style}
{preview_css}
</style>
</head>
<body>
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
                       preview_css=_preview.PREVIEW_CSS, body=body + ACTIVITY_JS)


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
    ready = bool(state.selection and state.selection.keys and not state.selection.missing_dependencies())
    reasons = []
    if not (state.selection and state.selection.keys):
        reasons.append("pick at least one object")
    if state.selection and state.selection.missing_dependencies():
        reasons.append("resolve the missing references before building")
    return "".join([
        "<hr style='border:none;border-top:1px solid var(--line2);margin:14px 0'>",
        "<form method='post' action='/build'>",
        "<div class='field'><label for='out'>Bundle to write</label>",
        f"<input type='text' name='out' id='out' value='{e(state.build_out or state.default_out())}'>"
        "</div>",
        "<div class='stack'>",
        f"<button type='submit' class='primary'{'' if ready else ' disabled'}>Build the bundle"
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


def _commands_panel(state) -> str:
    """Every command the CLI has, with a control here. The house rule is that
    nothing is configurable only from a file or a flag."""
    corpus, _src = _settings.corpus_dir(state.corpus_cli)
    zip_value = e(state.zip_path)
    parts = [
        "<div class='card'>",
        "<h2>Commands</h2>",
        "<div class='stack'>",
        f"<form method='post' action='/inspect'><input type='hidden' name='zip' value='{zip_value}'>"
        + _button("inspect") + "</form>",
        f"<form method='post' action='/inspect'><input type='hidden' name='zip' value='{zip_value}'>"
        "<input type='hidden' name='json' value='1'>" + _button("inspect --json") + "</form>",
        "<form method='post' action='/tree'>" + _button("tree") + "</form>",
        "<form method='post' action='/tree'><input type='hidden' name='json' value='1'>"
        + _button("tree --json") + "</form>",
        "</div>",
        "<div class='field' style='margin-top:12px'>",
        "<form method='post' action='/corpus-check' class='stack'>",
        "<div class='grow'><label for='corpus_run'>corpus-check directory</label>"
        f"<input type='text' name='dir' id='corpus_run' value='{e(str(corpus))}'></div>",
        _button("Run corpus-check"),
        "</form></div>",
        "<div class='field'>",
        "<form method='post' action='/apply-lines'>",
        "<label for='lines'>Selection, one object per line (the same file "
        "<code>build --select</code> takes)</label>",
        f"<textarea name='lines' id='lines' rows='5'>{e(state.selection_lines())}</textarea>",
        "<div class='stack' style='margin-top:8px'>" + _button("Apply these lines")
        + "</div></form></div>",
        "<p class='note'>The equivalent command line for what this page is set to:</p>",
        "<div class='stack'>",
    ]
    for cmd in ("tree", "preview", "build", "corpus-check"):
        parts.append("<form method='post' action='/run' style='display:inline'>"
                     f"<input type='hidden' name='cmd' value='{cmd}'>"
                     + _button(f"show the {cmd} command", ghost=True) + "</form>")
    parts += ["</div>"]
    # What the buttons above produce. This used to render on the preview
    # panel, which was harmless while everything was on one page and became a
    # bug the moment they were separated: running `tree` moved you here and
    # left the tree behind on the panel you had just left.
    if state.listing:
        parts += ["<h2 style='margin-top:18px'>Listing</h2>",
                  f"<pre>{e(state.listing)}</pre>"]
    if state.command_output:
        parts += ["<h2 style='margin-top:18px'>Command output</h2>",
                  f"<pre>{e(state.command_output)}</pre>"]
    parts += ["</div>"]
    return "".join(parts)


def _log_controls(state) -> str:
    """The run log's destination and level, and the diagnostics file.

    Every setting has a GUI option, and a log an admin cannot turn on from the
    page is a setting that lives only in a flag.
    """
    destination, destination_from, level, level_from = state.log_settings()
    options = "".join(
        f"<option value='{e(name)}'{' selected' if name == level else ''}>{e(name)}</option>"
        for name in _runlog.LEVEL_NAMES)
    return "".join([
        "<details><summary>Local logging</summary>",
        "<form method='post' action='/settings' class='field'>",
        "<label for='log_file'>Run log file (empty for none; - writes to the terminal)"
        "</label>",
        f"<input type='text' name='log_file' id='log_file' value='{e(destination or '')}' "
        "placeholder='run.log'>",
        f"<p class='note'><small>current value from: {e(destination_from)}. The log records "
        "what the tool did and why: content names, uuids and metric keys, member names, "
        "counts and timings. It never records credentials, the export password, encrypted "
        "values; dashboard owners appear as owner-1, owner-2. Local logs are private: "
        "paths and content names can identify people. Share the anonymized diagnostics "
        "file below instead of the local log. "
        "This page keeps the events of this session whether or not a file is set, so "
        "diagnostics can be saved after something goes wrong."
        "</small></p>",
        _button("Save log file"),
        "</form>",
        "<form method='post' action='/settings' class='field'>",
        "<label for='log_level'>Log level</label>",
        f"<select name='log_level' id='log_level'>{options}</select>",
        f"<p class='note'><small>current value from: {e(level_from)}. error is the failure "
        "that ended the command, warn adds every refusal and every swallowed failure, info "
        "adds the run header, the per-phase counts and timings and the fingerprints, detail "
        "adds every decision with its object and its reason, debug adds the per-reference "
        "and per-widget detail behind them.</small></p>",
        _button("Save log level"),
        "</form>",
        "</details>",
        "<form method='post' action='/diagnostics' class='field'>",
        "<label for='diag_out'>Diagnostics file</label>",
        f"<input type='text' name='out' id='diag_out' "
        f"value='{e(state.diagnostics_out or state.default_diagnostics_out())}'>",
        _button("Save anonymized diagnostics"),
        "<p class='note'><small>Share this report: operation codes, counts, timings and "
        "software versions. Paths, names, identifiers and free text become labels "
        "that are consistent within this report. No reverse mapping is included. "
        "Use a filename without personal or customer details.</small></p>",
        "</form>",
    ])


def _settings_panel(state) -> str:
    corpus, source = _settings.corpus_dir(state.corpus_cli)
    return "".join([
        "<div class='card'>",
        "<h2>Settings</h2>",
        _log_controls(state),
        "<details><summary>Advanced settings</summary>",
        "<form method='post' action='/settings' class='field'>",
        "<label for='corpus_dir'>Corpus directory (real export zips; never inside the repo)"
        "</label>",
        f"<input type='text' name='corpus_dir' id='corpus_dir' value='{e(str(corpus))}'>",
        f"<p class='note'><small>current value from: {e(source)}. Saved values go to "
        f"{e(str(_settings.settings_path()))}. The {e(_settings.ENV_CORPUS)} environment "
        "variable and the --corpus flag override the saved value.</small></p>",
        _button("Save corpus directory"),
        "</form>",
        "</details>",
        "<p class='note'><small>The application runs in a native window. "
        "It does not start a local web server. Close the window to stop.</small></p>",
        "</div>",
    ])
