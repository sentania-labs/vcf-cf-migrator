"""The selection page's markup: the tree, the selection, the preview, the build.

Split out of ``ui.py`` so the server is handlers and the page is rendering.
Two rules shaped what is here.

**No script file, no external anything, and one inline handler.** Every
control is a form that posts and gets a page back, so the page works with
JavaScript off, works with the keyboard alone, and needs no asset from
anywhere. The single exception is an ``onchange`` on each tree checkbox that
submits its own form, so a click on the box acts at once; every one of those
forms also carries an add/remove button that does the same thing, which is
what makes the handler an accelerator rather than a requirement. That costs a
round trip per click, which on a loopback socket is not a cost an admin can
feel.

**The tree has to stay readable at 430 objects.** Sixty-six dashboards is the
real corpus shape, so the tree groups by kind, collapses each group, and each
object's dependencies sit in a nested disclosure rather than in a wall of
indented rows. A filter box narrows the whole thing to what the admin typed.
"""
from __future__ import annotations

import html
from typing import Dict, List, Optional, Sequence

from vcfcf_migrator import preview as _preview
from vcfcf_migrator import runlog as _runlog
from vcfcf_migrator import settings as _settings
from vcfcf_migrator.cli import version_lines
from vcfcf_migrator.graph import KIND_ORDER, Graph, Node
from vcfcf_migrator.wording import plural

MAX_DEPTH = 6

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
input[type=text], textarea { font:inherit; font-size:13px; padding:6px 9px; width:100%;
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

.cols { display:grid; grid-template-columns:minmax(340px,440px) 1fr; gap:16px;
  padding:16px 18px 40px; align-items:start }
.card { background:var(--card); border:1px solid var(--line); border-radius:9px;
  padding:14px 16px; margin-bottom:16px }
/* A grid item's default min-width is its content, so one long line inside a
   <pre> made the whole page scroll sideways at phone width. */
.cols > div { min-width:0 }
.card pre { max-width:100% }
.left { position:sticky; top:59px; max-height:calc(100vh - 75px); overflow:auto }

.counts { display:flex; flex-wrap:wrap; gap:6px; margin:0 0 10px }
.pill { background:var(--line2); border-radius:999px; padding:2px 10px; font-size:12px;
  color:var(--ink2) }
.pill b { color:var(--ink); font-variant-numeric:tabular-nums }
.pill.on { background:var(--accent-soft); color:#12376f }

.kindgroup { border-top:1px solid var(--line2); padding:2px 0 }
.kindgroup > summary { cursor:pointer; padding:6px 2px; font-weight:600; font-size:13px;
  list-style:none; display:flex; align-items:center; gap:8px }
.kindgroup > summary::-webkit-details-marker { display:none }
.kindgroup > summary::before { content:"\\25B8"; color:var(--ink3); font-size:11px }
.kindgroup[open] > summary::before { content:"\\25BE" }
.kindgroup > summary .n { margin-left:auto; color:var(--ink3); font-weight:400; font-size:12px }

ul.tree { list-style:none; margin:0; padding:0 }
ul.tree ul { list-style:none; margin:2px 0 4px; padding-left:14px;
  border-left:2px solid var(--line2) }
li.node { padding:1px 0 }
.row { display:flex; align-items:baseline; gap:7px; padding:3px 6px; border-radius:6px }
.row:hover { background:var(--line2) }
.row.sel { background:var(--accent-soft) }
.row form { display:contents }
.row .name { flex:1 1 auto; min-width:0; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis }
.row .why { color:var(--warn); font-size:11px; white-space:nowrap }
.row .uuid { color:var(--ink3); font-size:11px; font-family:ui-monospace,monospace }
.row .kindtag { color:var(--ink3); font-size:11px }
.row.preview-on { box-shadow:inset 0 0 0 2px var(--accent) }
.tick { width:15px; height:15px; margin:0; accent-color:var(--accent) }
details.deps > summary { cursor:pointer; font-size:11.5px; color:var(--ink2); padding:2px 6px }
.missing { color:var(--warn); font-size:11.5px; padding:2px 6px }

.stack { display:flex; gap:8px; flex-wrap:wrap; align-items:flex-end }
.stack .grow { flex:1 1 200px }
.field { margin-bottom:10px }
.note { color:var(--ink2); font-size:12px; margin:6px 0 0 }

@media (max-width: 900px) {
  .cols { grid-template-columns:1fr; padding:12px }
  .left { position:static; max-height:none }
  header.top { position:static }
}
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


def render(state) -> str:
    body = "".join([
        _header(state),
        _messages(state),
        "<div class='cols'>",
        f"<div class='left'>{_selection_panel(state)}{_tree_panel(state)}</div>",
        f"<div class='right'>{_right_panel(state)}{_commands_panel(state)}"
        f"{_settings_panel(state)}</div>",
        "</div>",
    ])
    title = "vcfcf-migrator"
    if state.graph is not None and state.zip_path:
        title = f"vcfcf-migrator: {state.zip_path.rsplit('/', 1)[-1]}"
    return PAGE.format(title=e(title), style=STYLE,
                       preview_css=_preview.PREVIEW_CSS, body=body)


def _header(state) -> str:
    return "".join([
        "<header class='top'>",
        "<h1>vcfcf-migrator</h1>",
        f"<span class='ver'>{e(' | '.join(version_lines()))}</span>",
        "<form method='post' action='/open'>",
        "<div class='grow'><label for='zip'>Export zip</label>",
        f"<input type='text' name='zip' id='zip' value='{e(state.zip_path)}' "
        "placeholder='/path/to/export.zip'></div>",
        _button("Open", primary=True),
        "</form>",
        # Its own form, because the dialog names the file and a path typed
        # into the box beside it is not an input to that. The cost is that
        # cancelling redraws the page from state, so anything half-typed in
        # the box is lost. That is the existing render model rather than
        # anything this button introduced, but the button does put the
        # trigger right next to the box, so it is worth knowing.
        # 'nogrow' because header forms otherwise take flex:1 1 380px, which
        # would halve the path box beside it and leave this button alone in a
        # 380px column with a gap to its right.
        ("<form method='post' action='/pick-export' class='nogrow'>"
         "<button type='submit'>Browse\u2026</button></form>"
         if state.file_picker is not None else ""),
        "</header>",
    ])


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

def _selection_panel(state) -> str:
    if state.graph is None:
        return ("<div class='card'><h2>Selection</h2><p class='note'>Open an export zip "
                "above to see what it holds.</p></div>")
    graph = state.graph
    selection = state.selection
    counts = selection.counts(graph) if selection else {}
    total = len(selection.keys) if selection else 0
    pills = [f"<span class='pill{' on' if total else ''}'><b>{total}</b> objects</span>"]
    for kind in KIND_ORDER:
        if counts.get(kind):
            pills.append(f"<span class='pill on'><b>{counts[kind]}</b> {e(kind)}</span>")
    if not total:
        pills.append("<span class='pill'>nothing picked yet</span>")

    added = len(selection.added) if selection else 0
    missing = len(selection.missing) if selection else 0
    lines = [
        "<div class='card'>",
        "<h2>What a build would carry</h2>",
        "<div class='counts'>" + "".join(pills) + "</div>",
    ]
    if added:
        lines.append(f"<p class='note'>{added} pulled in as dependencies of what you "
                     "picked.</p>")
    if missing:
        lines.append(f"<p class='note'>{plural(missing, 'referenced object')} "
                     + ("is" if missing == 1 else "are")
                     + " not in this export; they are listed with each object below and "
                       "in the build report.</p>")
    lines += [
        "<div class='stack'>",
        "<form method='post' action='/select-all'>" + _button("Select everything") + "</form>",
        "<form method='post' action='/clear'>" + _button("Clear the selection") + "</form>",
        "</div>",
        _build_form(state),
        "</div>",
    ]
    return "".join(lines)


def _build_form(state) -> str:
    ready = bool(state.selection and state.selection.keys)
    reasons = []
    if not (state.selection and state.selection.keys):
        reasons.append("pick at least one object")
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


# ---------------------------------------------------------------------------
# The tree
# ---------------------------------------------------------------------------

def _tree_panel(state) -> str:
    if state.graph is None:
        return ""
    graph = state.graph
    parts = [
        "<div class='card'>",
        "<h2>Everything in this export</h2>",
        "<form method='post' action='/filter' class='stack'>",
        "<div class='grow'><label for='filter'>Filter by name, kind or uuid</label>",
        f"<input type='text' name='filter' id='filter' value='{e(state.filter_text)}'></div>",
        _button("Filter"),
        ("<button type='submit' name='clear-filter' value='1'>Clear</button>"
         if state.filter_text else ""),
        "</form>",
    ]
    if state.filter_text:
        matches = _matches(graph, state.filter_text)
        parts.append(f"<p class='note'>{plural(len(matches), 'object')} match "
                     f"{e(state.filter_text)!s}.</p>")
        parts.append("<ul class='tree'>"
                     + "".join(_node_row(state, node, depth=0, with_children=False)
                               for node in matches)
                     + "</ul>")
        parts.append("</div>")
        return "".join(parts)

    roots = graph.roots()
    root_keys = {n.key for n in roots}
    shown: Dict[str, List[Node]] = {}
    for node in roots:
        shown.setdefault(node.kind, []).append(node)
    others = [n for n in graph.ordered() if n.key not in root_keys]
    by_kind_other: Dict[str, List[Node]] = {}
    for node in others:
        by_kind_other.setdefault(node.kind, []).append(node)

    # The list is in two halves, and a per-kind count in the first half is a
    # count of that half, not of the export. Saying so where the numbers are
    # is the difference between "56 views" reading as a split and reading as
    # 125 views that went missing.
    total = len(graph.nodes)
    parts.append(
        f"<p class='note'>{plural(total, 'object')}: {len(roots)} that nothing else "
        "points at, "
        f"listed first, and {len(others)} reached only as a dependency of one of them, "
        "listed under their own heading below. Both halves are here, and a build carries "
        "either.</p>")
    parts.append("<h3 style='margin-top:10px'>Nothing else points at these</h3>")
    for kind in KIND_ORDER + sorted(set(shown) - set(KIND_ORDER)):
        nodes = shown.get(kind)
        if not nodes:
            continue
        parts.append(_kind_group(state, kind, nodes, open_default=len(roots) <= 40))
    if by_kind_other:
        parts.append("<h3 style='margin-top:16px'>Reached only as a dependency of "
                     "something above</h3>")
        for kind in KIND_ORDER + sorted(set(by_kind_other) - set(KIND_ORDER)):
            nodes = by_kind_other.get(kind)
            if not nodes:
                continue
            parts.append(_kind_group(state, kind, nodes, open_default=False,
                                     with_children=False))
    parts.append("</div>")
    return "".join(parts)


def _kind_group(state, kind: str, nodes: Sequence[Node], open_default: bool,
                with_children: bool = True) -> str:
    picked = sum(1 for n in nodes if n.key in state.selected_keys())
    here = len(nodes)
    total = sum(1 for n in state.graph.by_kind(kind)) if state.graph else here
    # "12 of 66 here" rather than a bare 66 when the kind is split across the
    # two halves, so a number smaller than the export's count reads as the
    # split it is.
    count = f"{here}" if here == total else f"{here} of {total} here"
    tail = f"{picked} of {count} selected" if picked else count
    return ("<details class='kindgroup'" + (" open" if open_default else "") + ">"
            f"<summary>{e(kind)}<span class='n'>{e(tail)}</span></summary>"
            "<ul class='tree'>"
            + "".join(_node_row(state, node, 0, with_children) for node in nodes)
            + "</ul></details>")


def _node_row(state, node: Node, depth: int, with_children: bool = True,
              seen: Optional[frozenset] = None) -> str:
    graph = state.graph
    seen = seen or frozenset()
    selected = node.key in state.selection_keys()
    picked = node.key in state.selected_keys()
    required_by = state.required_by(node.key) if selected and not picked else []
    classes = "row" + (" sel" if selected else "") + (
        " preview-on" if state.preview_key == node.key else "")
    checked = " checked" if selected else ""
    # The identifier is abbreviated in the row and complete in the title: a
    # full uuid per row is three lines of wrapping at 66 dashboards, and the
    # abbreviation is enough to tell two objects apart while the admin is
    # scanning. The whole value is a hover and a screen reader away.
    ident = node.uuid or node.ident
    short = (ident[:8] if len(ident) > 12 else ident)
    label = (f"<span class='name' title='{e(node.name)} ({e(ident)})'>{e(node.name)} "
             f"<span class='kindtag'>{e(node.kind)}</span>"
             + (f" <span class='uuid'>{e(short)}</span>" if short else "")
             + (f" <span class='uuid'>owner {e(node.owner[:8])}</span>" if node.owner else "")
             + "</span>")
    why = ""
    if required_by:
        first = graph.nodes.get(required_by[0])
        more = f" +{len(required_by) - 1}" if len(required_by) > 1 else ""
        why = (f"<span class='why' title='required by'>required by "
               f"{e(first.name if first else required_by[0])}{e(more)}</span>")

    row = "".join([
        f"<div class='{classes}' id='{e(anchor(node.key))}'>",
        "<form method='post' action='/select'>",
        f"<input type='hidden' name='key' value='{e(node.key)}'>",
        f"<input type='hidden' name='on' value='{'0' if selected else '1'}'>",
        f"<input type='checkbox' class='tick'{checked} name='tick' "
        # requestSubmit fires a real submit event; submit() does not, and the
        # desktop window drives every action off that event. With submit() the
        # tree checkboxes are dead in the window while the buttons beside them
        # still work, which reads as "the checkboxes are broken". The fallback
        # keeps very old browsers working in --server mode.
        f"onchange='this.form.requestSubmit ? this.form.requestSubmit() : this.form.submit()' "
        f"aria-label='{e(('deselect ' if selected else 'select ') + node.name)}'>",
        label,
        why,
        _button("remove" if selected else "add", ghost=True),
        "</form>",
        "<form method='post' action='/preview'>",
        f"<input type='hidden' name='key' value='{e(node.key)}'>",
        _button("preview", ghost=True),
        "</form>",
        "</div>",
    ])

    children_html = ""
    if with_children and depth < MAX_DEPTH and node.key not in seen:
        targets = [graph.nodes[t] for t in graph.edges.get(node.key, []) if t in graph.nodes]
        gaps = graph.missing_for(node.key)
        if targets or gaps:
            inner = "".join(_node_row(state, child, depth + 1, True, seen | {node.key})
                            for child in targets)
            inner += "".join(
                f"<li class='node'><div class='missing'>missing {e(gap.kind)} "
                f"{e(gap.ident)} (via {e(gap.via)})</div></li>" for gap in gaps)
            children_html = (f"<details class='deps'><summary>depends on "
                             f"{len(targets)}{', ' + str(len(gaps)) + ' not in this export' if gaps else ''}"
                             f"</summary><ul>{inner}</ul></details>")
    return f"<li class='node'>{row}{children_html}</li>"


def anchor(key: str) -> str:
    """The element id of a node's row, and the only definition of it.

    This used to return the bare slug while the row was rendered with a
    "node-" prefix glued on at the point of use, so the anchor an action
    returned named nothing on the page. The browser's redirect to /#<slug>
    matched no element and the desktop window's lookup found none either: in
    both modes every click on a tree halfway down jumped back to the top,
    which is the exact thing the anchor exists to prevent. One function now
    produces the id and the row uses it verbatim.
    """
    return "node-" + "".join(ch if ch.isalnum() else "-" for ch in key)


def _matches(graph: Graph, text: str) -> List[Node]:
    needle = text.strip().lower()
    return [n for n in graph.ordered()
            if needle in n.name.lower() or needle in n.kind.lower()
            or needle in n.uuid.lower() or needle in n.ident.lower()]


# ---------------------------------------------------------------------------
# The right column: preview, listings, build report
# ---------------------------------------------------------------------------

def _right_panel(state) -> str:
    parts = ["<div class='card'>"]
    if state.build_report:
        parts += ["<h2>Last build</h2>", f"<pre>{e(state.build_report)}</pre>",
                  "<hr style='border:none;border-top:1px solid var(--line2);margin:14px 0'>"]
    if state.graph is not None and state.preview_key in state.graph.nodes:
        node = state.graph.nodes[state.preview_key]
        parts.append("<h2>Preview</h2>")
        try:
            parts.append(_preview.fragment(state.graph, node))
        except _preview.PreviewError as err:
            parts.append(f"<p class='err'>{e(str(err))}</p>")
        parts.append("<form method='post' action='/preview' style='margin-top:10px'>"
                     "<input type='hidden' name='key' value=''>"
                     + _button("Close the preview") + "</form>")
    elif state.graph is not None:
        parts.append("<h2>Preview</h2><p class='note'>Choose <b>preview</b> next to any "
                     "object to see it laid out here, with mock values, before deciding "
                     "to carry it.</p>")
    else:
        parts.append("<h2>Start here</h2><p class='note'>Give the page a content export "
                     "zip at the top. Nothing leaves this machine: the tool reads the "
                     "zip, and the page is served on 127.0.0.1 only.</p>")
    if state.listing:
        parts += ["<h2 style='margin-top:18px'>Listing</h2>", f"<pre>{e(state.listing)}</pre>"]
    if state.command_output:
        parts += ["<h2 style='margin-top:18px'>Command output</h2>",
                  f"<pre>{e(state.command_output)}</pre>"]
    parts.append("</div>")
    return "".join(parts)


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
    parts += ["</div>", "</div>"]
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
        "<form method='post' action='/settings' class='field'>",
        "<label for='log_file'>Run log file (empty for none; - writes to the terminal)"
        "</label>",
        f"<input type='text' name='log_file' id='log_file' value='{e(destination or '')}' "
        "placeholder='run.log'>",
        f"<p class='note'><small>current value from: {e(destination_from)}. The log records "
        "what the tool did and why: content names, uuids and metric keys, member names, "
        "counts and timings. It never records credentials, the export password, encrypted "
        "values, or anything about people; dashboard owners appear as owner-1, owner-2. "
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
        "<form method='post' action='/diagnostics' class='field'>",
        "<label for='diag_out'>Diagnostics file</label>",
        f"<input type='text' name='out' id='diag_out' "
        f"value='{e(state.diagnostics_out or state.default_diagnostics_out())}'>",
        _button("Save the run header, the export's fingerprint, every log event and the "
                "bundle's manifest to one file"),
        "<p class='note'><small>One file, ready to attach to a mail. It holds content "
        "names, uuids and metric keys and no people and no credentials, so it can be sent "
        "as it is.</small></p>",
        "</form>",
    ])


def _settings_panel(state) -> str:
    corpus, source = _settings.corpus_dir(state.corpus_cli)
    return "".join([
        "<div class='card'>",
        "<h2>Settings</h2>",
        "<form method='post' action='/settings' class='field'>",
        "<label for='corpus_dir'>Corpus directory (real export zips; never inside the repo)"
        "</label>",
        f"<input type='text' name='corpus_dir' id='corpus_dir' value='{e(str(corpus))}'>",
        f"<p class='note'><small>current value from: {e(source)}. Saved values go to "
        f"{e(str(_settings.settings_path()))}. The {e(_settings.ENV_CORPUS)} environment "
        "variable and the --corpus flag override the saved value.</small></p>",
        _button("Save corpus directory"),
        "</form>",
        _log_controls(state),
        "<p class='note'><small>This page listens on 127.0.0.1 only, and a same-origin "
        "check stops another web page in your browser from driving it. That is a CSRF "
        "control, not an access control: any process on this machine can reach the port "
        "while it is running, and the page reads and writes the paths you give it with "
        "your own rights. Stop it with Ctrl-C in the terminal that started it."
        "</small></p>",
        "</div>",
    ])
