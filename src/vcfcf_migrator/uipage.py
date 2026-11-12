"""The page shell: one frame, its styles, and the script that drives it.

Every screen sits in the same frame: a header, the six steps, the screen
itself, and a footer whose right-hand button is always the next step. The
frame fills the window and never scrolls; only a list scrolls, inside its own
panel. The screens themselves are in ``screens.py``.

The page carries no remote asset. Buttons name a registered action in
``data-act``; the script hands it to the native bridge and draws what comes
back.
"""
from __future__ import annotations

import hashlib
import html

from vcfcf_migrator import preview as _preview
from vcfcf_migrator import theme

# Lattice components (dist/lattice.css), the ones used here, followed by the
# local additions. Each addition is built from lattice tokens only:
#   * a step bar, because lattice has tabs and no "steps in order";
#   * list rows with a full-height tick target (44 px, the most used control);
#   * hints in ink-muted, which keeps 12 px text above 4.5:1 on tinted grounds;
#   * a disabled button that keeps a readable label in both themes, with a
#     dashed outline so it cannot be mistaken for an enabled one;
#   * controls at least 36 px tall, and the next-step button 40 px.
#   * in dark, a danger badge uses body ink: the danger ink measured 4.4:1 on a
#     selected row.
STYLE = theme.CSS + """
:root { --radius-sm:3px; --radius-md:4px; --radius-lg:6px; --radius-xl:9px; --radius-pill:999px;
  --font-sans:system-ui,'Segoe UI',Roboto,sans-serif; --font-mono:ui-monospace,Menlo,Consolas,monospace; }
*, *::before, *::after { box-sizing:border-box; }
html { height:100%; }
body { margin:0; font:14px/1.5 var(--font-sans); background:var(--bg); color:var(--ink);
  height:100vh; overflow:hidden; position:relative;
  display:grid; grid-template-rows:auto auto minmax(0,1fr) auto; }
body[aria-busy=true] { cursor:progress; }
a { color:var(--link); }
:focus-visible { outline:2px solid var(--focus); outline-offset:1px; }
.lat-btn { font:inherit; font-size:13px; padding:5px 12px; border-radius:var(--radius-lg); border:1px solid var(--line);
  background:var(--surface); color:var(--ink); cursor:pointer; min-height:36px; }
.lat-btn:hover { border-color:var(--ink-subtle); }
.lat-btn--primary { background:var(--accent); border-color:var(--accent); color:var(--on-accent); font-weight:600; }
.lat-btn--primary:hover { background:var(--accent-strong); border-color:var(--accent-strong); }
.lat-btn[disabled] { background:var(--surface-sunken); border:1px dashed var(--ink-subtle); color:var(--ink-muted);
  cursor:not-allowed; font-weight:400; }
.lat-label { display:block; font-size:12px; color:var(--ink-muted); margin-bottom:3px; }
.lat-input, .lat-select { font:inherit; font-size:13px; padding:7px 9px; width:100%; border:1px solid var(--ink-subtle);
  border-radius:var(--radius-lg); background:var(--surface); color:var(--ink); }
.lat-input::placeholder { color:var(--ink-muted); opacity:1; }
.lat-header { background:var(--surface); border-bottom:1px solid var(--line); padding:6px 18px; display:flex;
  align-items:center; gap:14px; min-height:50px; }
.lat-card { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-xl); padding:14px 16px; min-width:0; }
.lat-banner { padding:9px 12px; border-radius:var(--radius-lg); font-size:13px; border-left:3px solid var(--line);
  background:var(--surface-sunken); color:var(--ink-muted); overflow-wrap:anywhere; }
.lat-banner--ok { background:var(--ok-soft); border-left-color:var(--ok); color:var(--ok-ink); }
.lat-banner--warn { background:var(--warn-soft); border-left-color:var(--warn); color:var(--warn-ink); }
.lat-banner--bad { background:var(--bad-soft); border-left-color:var(--bad); color:var(--bad-ink); }
.lat-banner--info { background:var(--accent-soft); border-left-color:var(--accent); color:var(--accent-ink); }
[data-theme="dark"] .lat-banner--bad, [data-theme="dark"] .lat-banner--warn { color:var(--ink); }
.lat-badge { display:inline-block; border-radius:var(--radius-sm); padding:1px 6px; font-size:12px; font-weight:500;
  background:var(--surface-sunken); color:var(--ink-muted); white-space:nowrap; }
.lat-badge--ok { background:var(--ok-soft); color:var(--ok-ink); }
.lat-badge--warn { background:var(--warn-soft); color:var(--warn-ink); }
.lat-badge--bad { background:var(--bad-soft); color:var(--bad-ink); }
.lat-badge--accent { background:var(--accent-soft); color:var(--accent-ink); }
/* On a selected (tinted) row the dark danger ink measures 4.4:1, so body ink is used. */
[data-theme="dark"] .lat-badge--bad { color:var(--ink); }
.lat-table { width:100%; border-collapse:collapse; font-size:13px; }
.lat-table th { text-align:left; color:var(--ink-muted); font-weight:600; font-size:12px; padding:6px 8px; border-bottom:1px solid var(--line); }
.lat-table td { padding:7px 8px; border-bottom:1px solid var(--line-soft); color:var(--ink); vertical-align:top; overflow-wrap:anywhere; }
.lat-table.fixed { table-layout:fixed; }
.lat-table.fixed .lat-badge { white-space:normal; }
.lat-table td.mid { vertical-align:middle; }
.lat-tree, .lat-tree ul { list-style:none; margin:0; padding:0; }
.lat-tree ul { margin:0 0 2px 6px; padding-left:14px; border-left:2px solid var(--line-soft); }
.lat-hint { color:var(--ink-muted); font-size:12px; }

h1 { font-size:17px; margin:0; font-weight:650; letter-spacing:-0.01em; white-space:nowrap; }
h2 { font-size:20px; margin:0 0 4px; font-weight:650; letter-spacing:-0.01em; }
h3 { font-size:14px; margin:0 0 6px; font-weight:650; }
p { margin:0 0 8px; }
.muted { color:var(--ink-muted); }
.sm { font-size:12.5px; }
.grow { flex:1 1 auto; min-width:0; }
.src { color:var(--ink-muted); font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.src b { color:var(--ink); font-weight:600; }
.linkbtn { background:none; border:0; padding:4px 0; font:inherit; color:var(--link); cursor:pointer; text-decoration:underline; }
[data-theme="dark"] .lat-banner .linkbtn { color:var(--ink); }
.btn-lg { padding:8px 18px; font-size:14px; min-height:40px; white-space:nowrap; }
[hidden] { display:none !important; }

.steps { display:flex; gap:4px; padding:6px 18px; background:var(--surface); border-bottom:1px solid var(--line);
  align-items:center; min-height:46px; }
.step { display:flex; align-items:center; gap:8px; font-size:13px; color:var(--ink-muted); padding:5px 12px 5px 5px;
  border-radius:var(--radius-pill); background:none; border:1px solid transparent; font-family:inherit; min-height:34px;
  white-space:nowrap; }
.step .n { width:22px; height:22px; border-radius:50%; display:grid; place-items:center; font-size:12px; font-weight:650;
  background:var(--surface-sunken); color:var(--ink-muted); flex:0 0 auto; }
button.step { color:var(--ink); cursor:pointer; border-color:var(--line); }
button.step .n { background:var(--ok-soft); color:var(--ok-ink); }
button.step:hover { background:var(--surface-sunken); border-color:var(--ink-subtle); }
.step.now { color:var(--accent-ink); background:var(--accent-soft); font-weight:650; }
.step.now .n { background:var(--accent); color:var(--on-accent); }
.steps .bar { flex:0 1 14px; height:1px; background:var(--line); min-width:4px; }

main { min-height:0; overflow:hidden; padding:14px 18px; display:flex; flex-direction:column; gap:10px; }
.foot { background:var(--surface); border-top:1px solid var(--line); padding:8px 18px; display:flex; align-items:center;
  gap:14px; min-height:58px; }
.foot .say { flex:1 1 auto; font-size:13px; color:var(--ink-muted); min-width:0; }
.foot .say b { color:var(--ink); }
.foot .lat-btn { white-space:nowrap; }

.center { max-width:980px; width:100%; margin:0 auto; }
.two { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.pick { display:flex; flex-direction:column; gap:10px; padding:20px; }
.pick .lat-btn { align-self:flex-start; }
.drop { border:2px dashed var(--line); border-radius:var(--radius-xl); padding:12px; text-align:center; color:var(--ink-muted);
  font:inherit; font-size:13px; background:none; cursor:pointer; width:100%; }
body.dragging .drop { border-color:var(--accent); color:var(--accent-ink); background:var(--accent-soft); }
ol.how { margin:0; padding-left:20px; font-size:13px; color:var(--ink-muted); }
ol.how li { margin:2px 0; }
.problem { display:grid; grid-template-columns:auto 1fr; gap:14px; padding:16px 18px; }
.ico { width:34px; height:34px; border-radius:50%; display:grid; place-items:center; font-weight:700; font-size:18px; flex:0 0 auto; }
.ico.sm2 { width:22px; height:22px; font-size:12px; }
.ico.bad { background:var(--bad-soft); color:var(--bad-ink); } .ico.warn { background:var(--warn-soft); color:var(--warn-ink); }
.ico.ok { background:var(--ok-soft); color:var(--ok-ink); } .ico.info { background:var(--accent-soft); color:var(--accent-ink); }
[data-theme="dark"] .ico.bad { background:var(--bad); color:#fff; }
.rowbtns { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; align-items:center; }
.working { display:flex; gap:14px; align-items:center; padding:22px; font-size:15px; }
.spin { width:22px; height:22px; border-radius:50%; border:3px solid var(--line); border-top-color:var(--accent);
  animation:spin 0.9s linear infinite; flex:0 0 auto; }
@keyframes spin { to { transform:rotate(360deg); } }
.field { margin-bottom:12px; }
.field .lat-label { font-size:13px; color:var(--ink); font-weight:600; }
.stages { list-style:none; margin:0; padding:0; font-size:14px; }
.stages li { display:flex; gap:10px; align-items:center; padding:6px 0; color:var(--ink-muted); }
.stages li.done { color:var(--ink); } .stages li.now { color:var(--ink); font-weight:600; }
.stages .dot { width:22px; height:22px; border-radius:50%; display:grid; place-items:center; background:var(--surface-sunken);
  font-size:12px; flex:0 0 auto; }
.stages li.done .dot { background:var(--ok-soft); color:var(--ok-ink); }
.keep { font-weight:600; color:var(--ok-ink); margin:0 0 8px; }

.cols { display:grid; grid-template-columns:196px minmax(0,1.15fr) minmax(0,1fr); gap:14px; min-height:0; flex:1 1 auto; }
.rail { display:flex; flex-direction:column; gap:2px; overflow:auto; }
.rail button { display:flex; justify-content:space-between; gap:8px; width:100%; text-align:left; font:inherit; font-size:13px;
  padding:6px 9px; border:1px solid transparent; border-radius:var(--radius-lg); background:none; color:var(--ink);
  cursor:pointer; min-height:36px; align-items:center; flex:0 0 auto; }
.rail button:hover { background:var(--surface-sunken); }
.rail button.on { background:var(--accent-soft); border-color:var(--accent); color:var(--accent-ink); font-weight:600; }
.rail .cnt { color:var(--ink-muted); font-variant-numeric:tabular-nums; }
.rail .sep { height:1px; background:var(--line); margin:5px 0; flex:0 0 auto; }
.rail .cap { font-size:12px; color:var(--ink-muted); padding:2px 9px; }
.panel { display:flex; flex-direction:column; min-height:0; padding:0; overflow:hidden; }
.panel > .head { padding:10px 12px; border-bottom:1px solid var(--line-soft); }
.panel > .body { overflow:auto; min-height:0; flex:1 1 auto; }
.searchrow { display:flex; gap:8px; align-items:center; }
.searchrow .cnt { font-size:12.5px; color:var(--ink-muted); white-space:nowrap; }
.searchrow .lat-btn { white-space:nowrap; }
.it { display:grid; grid-template-columns:44px minmax(0,1fr) auto; align-items:center; gap:2px; padding:0 12px 0 0;
  border-bottom:1px solid var(--line-soft); cursor:pointer; min-height:48px; }
.it:hover { background:var(--surface-sunken); }
.it.in { background:var(--accent-soft); }
.it.focus { box-shadow:inset 3px 0 0 var(--accent); }
.it .chk { display:grid; place-items:center; width:44px; align-self:stretch; min-height:48px; cursor:pointer; }
.it input { width:20px; height:20px; margin:0; accent-color:var(--accent); cursor:pointer; }
.it input:disabled { cursor:default; }
.it .nm { font-weight:600; overflow-wrap:anywhere; }
.it .sub { font-size:12.5px; color:var(--ink-muted); overflow-wrap:anywhere; }
.it .tags { display:flex; flex-direction:column; gap:3px; align-items:flex-end; }
.more { padding:10px 14px; font-size:13px; color:var(--ink-muted); }
.toast { margin:0 12px 8px; }
#toast { border-top:1px solid var(--line-soft); padding-top:8px; }
#toast:empty { display:none; }
.detail { padding:14px 16px; }
.detail .kind { color:var(--ink-muted); font-size:13px; margin-bottom:10px; }
.needs li { font-size:13px; display:grid; grid-template-columns:136px minmax(0,1fr); column-gap:6px; align-items:baseline; }
.needs li > ul { grid-column:1 / -1; }
.needs .ty { color:var(--ink-muted); font-size:12px; }
.needs li.gone b { color:var(--bad-ink); }
.needs li.gone.soft b { color:var(--warn-ink); }
.nmb { background:none; border:0; padding:0; font:inherit; font-weight:600; color:inherit; text-align:left; cursor:pointer;
  display:block; overflow-wrap:anywhere; }
ul.chain { margin:4px 0 8px; padding-left:18px; } ul.chain li { margin:3px 0; }
.tk { display:grid; grid-template-columns:44px 1fr; border-bottom:1px solid var(--line-soft); min-height:48px; }
.tk.on { background:var(--accent-soft); box-shadow:inset 3px 0 0 var(--accent); }
.tk .c { display:grid; place-items:center; cursor:pointer; }
.tk input { width:20px; height:20px; margin:0; accent-color:var(--accent); cursor:pointer; }
.tk button { text-align:left; font:inherit; background:none; border:0; padding:8px 12px 8px 2px; cursor:pointer;
  color:var(--ink); font-weight:600; overflow-wrap:anywhere; }
.tk.done button { text-decoration:line-through; color:var(--ink-muted); font-weight:400; }
ol.dosteps { margin:8px 0 0; padding-left:22px; font-size:14px; } ol.dosteps li { margin:7px 0; overflow-wrap:anywhere; }
.empty { color:var(--ink-muted); padding:26px 18px; text-align:center; }
.empty b { color:var(--ink); display:block; font-size:15px; margin-bottom:4px; }

.split { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1.05fr); gap:16px; min-height:0; flex:1 1 auto; }
.split.wide-right { grid-template-columns:minmax(0,0.85fr) minmax(0,1.15fr); }
.scrolls { overflow:auto; min-height:0; }
.opt { display:grid; grid-template-columns:28px 1fr; gap:6px; padding:10px 14px; border:1px solid var(--line);
  border-radius:var(--radius-xl); background:var(--surface); cursor:pointer; margin-bottom:8px; }
.opt.on { border-color:var(--accent); background:var(--accent-soft); }
.opt input[type=radio] { margin:2px 0 0; width:18px; height:18px; accent-color:var(--accent); }
.opt b { display:block; }
.opt .t { color:var(--ink-muted); font-size:13px; display:block; }
.opt .w { display:block; margin-top:8px; padding:7px 10px; border-left:3px solid var(--warn); border-radius:var(--radius-lg);
  background:var(--warn-soft); color:var(--warn-ink); }
[data-theme="dark"] .opt .w { color:var(--ink); }
.opt .w b { display:inline; }
.three { display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px; }
.copy { display:flex; flex-direction:column; gap:8px; }
.copy dl { display:grid; grid-template-columns:auto 1fr; gap:3px 10px; margin:0; font-size:13px; }
.copy dt { color:var(--ink-muted); } .copy dd { margin:0; overflow-wrap:anywhere; }

.status { display:flex; gap:12px; align-items:center; padding:10px 16px; }
.status .big { font-size:17px; font-weight:650; }
.ilist button { display:grid; grid-template-columns:26px 1fr; gap:8px; width:100%; text-align:left; font:inherit;
  background:none; border:0; border-bottom:1px solid var(--line-soft); padding:10px 12px; cursor:pointer; color:var(--ink);
  min-height:44px; align-items:center; overflow-wrap:anywhere; }
.ilist button:hover { background:var(--surface-sunken); }
.ilist button.on { background:var(--accent-soft); box-shadow:inset 3px 0 0 var(--accent); }
.ilist .grp { font-size:12px; font-weight:650; color:var(--ink-muted); padding:10px 12px 4px; }
.prob { border-top:1px solid var(--line-soft); padding:10px 0; }
.prob:first-of-type { border-top:0; padding-top:0; }
.files { width:100%; border-collapse:collapse; }
.files td { padding:8px; border-bottom:1px solid var(--line-soft); font-size:13.5px; vertical-align:middle; overflow-wrap:anywhere; }
.kv { display:grid; grid-template-columns:150px 1fr; gap:8px 12px; font-size:13.5px; margin:0; align-items:center; }
.kv dt { color:var(--ink-muted); } .kv dd { margin:0; overflow-wrap:anywhere; }
.gloss { display:grid; grid-template-columns:132px 1fr; gap:3px 10px; font-size:13px; margin:0; }
.gloss.two { grid-template-columns:auto 1fr auto 1fr; gap:10px 16px; font-size:13.5px; max-width:1100px; margin:0 auto; }
.gloss dt { font-weight:600; } .gloss dd { margin:0; color:var(--ink-muted); }
.helpgrid { display:grid; grid-template-columns:1fr 1fr 1fr; grid-template-rows:minmax(0,1fr); gap:14px; min-height:0; flex:1 1 auto; }
.hcol { display:flex; flex-direction:column; gap:12px; min-height:0; overflow:auto; }
.radio { display:flex; align-items:center; gap:8px; min-height:36px; cursor:pointer; padding-right:14px; }
.radio input { width:18px; height:18px; margin:0; accent-color:var(--accent); }
.doc h2 { margin-top:0; } .doc h3 { margin-top:16px; } .doc ul, .doc ol { margin:0 0 8px; padding-left:22px; }
.doc li { margin:3px 0; overflow-wrap:anywhere; }

.veil { position:absolute; inset:0; background:rgba(20,26,36,0.45); display:grid; place-items:center; z-index:20; }
.dlg { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-xl); width:580px; max-width:92%;
  padding:16px 18px; }
.dlg h3 { font-size:16px; }
.pv-over { position:absolute; inset:0; background:var(--bg); z-index:15; display:flex; flex-direction:column; }
.pv-over .pvbody { padding:16px 18px; overflow:auto; flex:1 1 auto; min-height:0; }
#working { position:fixed; left:14px; bottom:70px; z-index:40; background:var(--surface); border:1px solid var(--line);
  border-radius:var(--radius-pill); padding:6px 14px; font-size:13px; display:flex; gap:8px; align-items:center; }
#working .spin { width:14px; height:14px; border-width:2px; }

/* The smallest window the app supports: the same screens, drawn tighter. */
@media (max-width:1150px), (max-height:780px) {
  .step { padding:4px 8px 4px 4px; gap:5px; font-size:12.5px; }
  main { padding:10px 14px; gap:8px; }
  .field { margin-bottom:6px; }
  .lat-input, .lat-select { padding:5px 8px; }
  .pick { padding:14px; gap:8px; }
  .problem { padding:12px 14px; }
  h2 { font-size:18px; }
  .detail { padding:10px 12px; }
  .lat-card { padding:10px 12px; }
  .lat-card.panel { padding:0; }
  .center > p.muted { margin-bottom:2px; font-size:13px; }
  .two { gap:10px; }
  .lat-banner { padding:7px 10px; font-size:12.5px; }
  .cols { grid-template-columns:150px minmax(0,1fr) minmax(0,1.05fr); gap:10px; }
  .status { padding:6px 12px; }
  .status .big { font-size:15px; }
  .ico { width:28px; height:28px; font-size:15px; }
  .ico.sm2 { width:22px; height:22px; font-size:12px; }
  .it .tags .lat-badge { white-space:normal; text-align:right; max-width:120px; }
  .prob { padding:6px 0; }
  .rail button { min-height:28px; padding:2px 8px; }
  .rail { gap:1px; }
  .rail .sep { margin:3px 0; }
  .bsum { display:none; }
  .gloss.two { font-size:13px; gap:7px 12px; }
}
"""

# The preview inside the app is a picture to recognise a dashboard by. The
# internal keys and type codes the standalone preview prints are left out, and
# nothing in it is smaller than 12 px.
PREVIEW_OVERRIDES = """
.pv-over .pv { font-size:13px; }
.pv-over .pv .pv-key, .pv-over .pv .pv-type { display:none; }
.pv-over .pv .pv-flow, .pv-over .pv .pv-drivenby, .pv-over .pv .pv-lbl, .pv-over .pv table.pv-tbl,
.pv-over .pv table.pv-tbl th, .pv-over .pv .pv-tile .l, .pv-over .pv .pv-placeholder, .pv-over .pv .pv-nothing,
.pv-over .pv .pv-nothing b, .pv-over .pv .pv-elsewhere, .pv-over .pv .pv-elsewhere b, .pv-over .pv .pv-wiring,
.pv-over .pv .pv-notes, .pv-over .pv .pv-text, .pv-over .pv .pv-section, .pv-over .pv .pv-facts,
.pv-over .pv pre.pv-code, .pv-over .pv h4.pv-h, .pv-over .pv ul.pv-logic .op, .pv-over .pv code { font-size:12px; }
.pv-over .pv .pv-w > h3 { font-size:13px; }
"""

CLIENT_JS = r"""
<script>
(function () {
  if (window.migrator) { return; }
  var busy = false, keep = {}, pollToken = 0, findTimer = null, findSeq = 0, slowTimer = null;
  var srcTimer = null, srcSeq = 0, lastLookup = '', secsTimer = null, secsStart = 0, secsFor = '';
  function api() { return window.pywebview && window.pywebview.api; }
  function $(id) { return document.getElementById(id); }

  function remember() {
    document.querySelectorAll('[data-keep]').forEach(function (el) { if (el.id) { keep[el.id] = el.value; } });
  }
  function restore() {
    document.querySelectorAll('[data-keep]').forEach(function (el) {
      if (!el.id || !(el.id in keep)) { return; }
      if (el.hasAttribute('data-fresh')) { delete keep[el.id]; return; }
      if (el.tagName === 'SELECT') {
        if (Array.prototype.some.call(el.options, function (o) { return o.value === keep[el.id]; })) { el.value = keep[el.id]; }
      } else { el.value = keep[el.id]; }
    });
    // What was typed into the sign-in form is held only while that form, or
    // the download it started, is on screen.
    if (document.body.dataset.screen !== 'connect') {
      Object.keys(keep).forEach(function (k) { if (k.indexOf('c-') === 0) { delete keep[k]; } });
    }
  }

  function swap(res) {
    if (!res || !res.html) { return; }
    remember();
    var scrolls = {};
    document.querySelectorAll('[data-scroll]').forEach(function (el) { scrolls[el.dataset.scroll] = el.scrollTop; });
    var active = document.activeElement, activeId = active && active.id, caret = null;
    try { caret = active && active.selectionStart; } catch (_) { caret = null; }
    var before = document.body.dataset.screen;
    var doc = new DOMParser().parseFromString(res.html, 'text/html');
    document.body.replaceWith(doc.body);
    document.documentElement.dataset.theme = document.body.dataset.theme;
    if (doc.title) { document.title = doc.title; }
    restore();
    if (document.body.dataset.screen === before) {
      document.querySelectorAll('[data-scroll]').forEach(function (el) {
        if (el.dataset.scroll in scrolls) { el.scrollTop = scrolls[el.dataset.scroll]; }
      });
    }
    var over = document.querySelector('.veil [data-autofocus], .pv-over [data-autofocus]');
    var target = (res.focus && $(res.focus)) || over || (activeId && $(activeId)) || document.querySelector('[data-autofocus]');
    if (target) {
      try {
        target.focus({preventScroll: true});
        if (caret !== null && target.id === activeId && target.setSelectionRange) { target.setSelectionRange(caret, caret); }
      } catch (_) { /* a control that cannot take a caret */ }
    }
    var reveal = document.querySelector('[data-reveal]');
    if (reveal) { reveal.scrollIntoView({block: 'nearest'}); }
    afterDraw();
  }

  function afterDraw() {
    pollToken++;
    connState();
    // The sign-in choices depend on the address and on how the certificate
    // is being checked, so they are asked for again when either has changed.
    var form = document.querySelector('[data-form=connect]'), address = $('c-addr');
    if (form && address && address.value && lastLookup !== address.value + '|' + form.dataset.certmode) {
      loadSources();
    }
    var secs = $('secs');
    if (secs) {
      if (secsFor !== secs.dataset.job) { secsFor = secs.dataset.job; secsStart = Date.now(); }
      secs.textContent = Math.round((Date.now() - secsStart) / 1000);
      if (!secsTimer) {
        secsTimer = setInterval(function () {
          var el = $('secs');
          if (!el) { clearInterval(secsTimer); secsTimer = null; secsFor = ''; return; }
          el.textContent = Math.round((Date.now() - secsStart) / 1000);
        }, 500);
      }
    }
    if (document.body.dataset.busy === '1') { poll(); }
  }

  function poll() {
    var token = pollToken;
    (function loop() {
      if (token !== pollToken || document.body.dataset.busy !== '1' || !api()) { return; }
      api().progress(document.body.dataset.version).then(function (res) {
        if (token !== pollToken) { return; }
        if (res && res.html) { swap(res); } else { loop(); }
      }).catch(function () { setTimeout(loop, 500); });
    })();
  }

  function banner(text) {
    var bar = document.createElement('div');
    bar.setAttribute('role', 'alert');
    bar.className = 'lat-banner lat-banner--bad';
    bar.textContent = text;
    var main = document.querySelector('main') || document.body;
    main.insertBefore(bar, main.firstChild);
  }

  function working(on) {
    var el = $('working');
    if (on && !el) {
      el = document.createElement('div');
      el.id = 'working'; el.setAttribute('role', 'status');
      el.innerHTML = '<span class="spin"></span>Working…';
      document.body.appendChild(el);
    } else if (!on && el) { el.remove(); }
  }

  function call(run, failed) {
    if (busy || !api()) { return Promise.resolve(); }
    busy = true;
    document.body.setAttribute('aria-busy', 'true');
    slowTimer = setTimeout(function () { working(true); }, 500);
    return run().then(swap).catch(function (err) {
      // Without this the window just stops responding to a button, with
      // nothing on screen to say why.
      banner(failed || ('That did not work, and the screen below may be out of date. Try again; if it keeps '
        + 'happening, save a support report from Help and settings. (' + err + ')'));
    }).finally(function () {
      busy = false;
      clearTimeout(slowTimer);
      working(false);
      document.body.removeAttribute('aria-busy');
    });
  }
  function act(path, data) {
    return call(function () { return api().act(path, data || {}); });
  }

  function send(el, extra) {
    var data = {};
    Object.keys(el.dataset).forEach(function (k) {
      if (k !== 'act' && k !== 'keep' && k !== 'autofocus' && k !== 'scroll') { data[k] = el.dataset[k]; }
    });
    // A button in the footer names the form it sends with data-uses.
    var form = el.closest('[data-form]') ||
      (el.dataset.uses ? document.querySelector('[data-form="' + el.dataset.uses + '"]') : null);
    if (form) {
      form.querySelectorAll('input[name], select[name]').forEach(function (f) {
        if (f.type === 'checkbox' || f.type === 'radio') { if (f.checked) { data[f.name] = f.value || '1'; } }
        else { data[f.name] = f.value; }
      });
    }
    var q = $('q');
    if (q) { data.q = q.value; }
    if (extra) { Object.keys(extra).forEach(function (k) { data[k] = extra[k]; }); }
    var path = el.dataset.act;
    if (path === '/conn-test') {
      el.textContent = 'Testing…';
      el.disabled = true;
      return call(function () { return api().test_connection(data); },
                  'The sign-in could not be tested. Check the address and try again.');
    }
    return act(path, data);
  }

  document.addEventListener('click', function (ev) {
    var t = ev.target;
    if (!t.closest) { return; }
    if (t.matches('input[type=checkbox], input[type=radio]')) { return; }   // answered on change
    var el = t.closest('[data-act]');
    if (!el || el.disabled) { return; }
    // A click on a label is passed on to its box by the browser.
    var label = t.closest('label');
    if (label && el.contains(label) && el !== label && label.querySelector('input')) { return; }
    if (el.tagName === 'LABEL') { return; }
    ev.preventDefault();
    send(el);
  });

  document.addEventListener('change', function (ev) {
    var el = ev.target;
    if (!el.matches || !el.matches('[data-act]')) { return; }
    if (el.type === 'checkbox') { send(el, {on: el.checked ? '1' : '0'}); }
    else if (el.type === 'radio') { if (el.checked) { send(el); } }
    else if (el.tagName === 'SELECT') { var extra = {}; extra[el.name || 'value'] = el.value; send(el, extra); }
  });

  function find() {
    var q = $('q');
    if (!q || !api()) { return; }
    var seq = ++findSeq;
    api().find(q.value).then(function (res) {
      if (seq !== findSeq || !res) { return; }
      ['list', 'cnt', 'addall'].forEach(function (id) {
        var el = $(id);
        if (el && (id in res)) { el.innerHTML = res[id]; }
      });
      var list = $('list');
      if (list) { list.scrollTop = 0; }
    }).catch(function () {});
  }

  function connState() {
    var form = document.querySelector('[data-form=connect]');
    if (!form) { return; }
    function v(id) { var el = $(id); return el ? el.value : ''; }
    var filled = v('c-addr') && v('c-user') && v('c-pw');
    var match = v('c-epw') && v('c-epw') === v('c-epw2');
    var certOpen = form.dataset.cert === 'open';
    var go = $('conn-go'), test = $('conn-test'), mismatch = $('c-mismatch'), say = $('conn-say'), hint = $('conn-test-hint');
    if (go) { go.disabled = !(filled && match) || certOpen; }
    if (test) { test.disabled = !filled; }
    if (mismatch) { mismatch.hidden = !(v('c-epw2') && v('c-epw') !== v('c-epw2')); }
    if (say) {
      say.textContent = certOpen ? 'Choose one of the two certificate options above to continue.'
        : (filled && match) ? '' : 'Fill in the address, user name and password, and type the export password twice, to continue.';
    }
    if (hint) {
      hint.textContent = filled ? 'Checks the address, user name and password. Starts nothing.'
        : 'Fill in the address, user name and password to test them.';
    }
  }

  function loadSources() {
    var address = $('c-addr');
    if (!address || !api()) { return; }
    try {
      var url = new URL(address.value);
      if (url.protocol !== 'https:' || !url.hostname) { throw Error(); }
    } catch (_) { return; }
    var seq = ++srcSeq;
    var form = document.querySelector('[data-form=connect]');
    lastLookup = address.value + '|' + (form ? form.dataset.certmode : '');
    api().login_sources(address.value).then(function (res) {
      if (seq !== srcSeq || !res) { return; }
      var select = $('c-src'), was = select ? select.value : '';
      if (select && res.options) {
        select.innerHTML = res.options;
        if (Array.prototype.some.call(select.options, function (o) { return o.value === was; })) { select.value = was; }
      }
      if ($('c-addr-hint') && res.address_hint) { $('c-addr-hint').textContent = res.address_hint; }
      if ($('c-src-hint') && res.source_hint) { $('c-src-hint').textContent = res.source_hint; }
    }).catch(function () {});
  }

  document.addEventListener('input', function (ev) {
    var t = ev.target;
    if (!t.closest) { return; }
    if (t.id === 'q') { clearTimeout(findTimer); findTimer = setTimeout(find, 120); return; }
    if (t.hasAttribute('data-keep') && t.id) { keep[t.id] = t.value; }
    var form = t.closest('[data-form=connect]');
    if (!form) { return; }
    connState();
    if (['c-addr', 'c-user', 'c-pw', 'c-src'].indexOf(t.id) >= 0 && form.dataset.stale === '1') {
      // An earlier test or error no longer says anything about this form.
      form.dataset.stale = '';
      act('/conn-edited', {});
    }
    if (t.id === 'c-addr') { clearTimeout(srcTimer); srcTimer = setTimeout(loadSources, 500); }
  });

  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && document.querySelector('.veil, .pv-over')) { ev.preventDefault(); act('/escape', {}); return; }
    if (ev.key !== 'Enter' || !ev.target.matches || !ev.target.matches('input.lat-input')) { return; }
    var form = ev.target.closest('[data-form]');
    var button = form && form.querySelector('[data-enter]');
    if (!button && form && form.dataset.form === 'connect') { button = $('conn-go'); }
    if (button && !button.disabled) { ev.preventDefault(); button.click(); }
  });

  // A file dragged over the window. The path itself reaches the app from the
  // native side; the page only shows that a drop is welcome.
  ['dragenter', 'dragover'].forEach(function (name) {
    document.addEventListener(name, function (ev) { ev.preventDefault(); document.body.classList.add('dragging'); });
  });
  ['dragleave', 'drop'].forEach(function (name) {
    document.addEventListener(name, function (ev) { ev.preventDefault(); document.body.classList.remove('dragging'); });
  });

  window.migrator = {act: act, refresh: function () { return act('/redraw', {}); }};

  window.addEventListener('pywebviewready', function () {
    afterDraw();
    api().check_release().then(function (res) {
      var slot = $('update-slot');
      if (res && res.html && slot) { slot.innerHTML = res.html; }
    }).catch(function () {});
    if (window.migratorInitialZip) { act('/open', {zip: window.migratorInitialZip}); }
  }, {once: true});
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
<body data-theme="{theme}" data-screen="{screen}" data-busy="{busy}" data-version="{version}">
{body}
</body>
</html>
"""

TITLE = 'VCF Content Migrator'


def e(value) -> str:
    return html.escape("" if value is None else str(value))


def anchor(key: str) -> str:
    """Stable element id for an object, with a digest to tell apart names
    that differ only in characters an id cannot carry."""
    flat = "".join(ch if ("a" <= ch <= "z" or "A" <= ch <= "Z" or "0" <= ch <= "9")
                   else "-" for ch in key)
    if not key.isascii():
        flat += "-" + hashlib.blake2s(key.encode("utf-8"), digest_size=4).hexdigest()
    return "node-" + flat


def button(act: str, text: str, primary: bool = False, large: bool = False, off: bool = False,
           ident: str = '', focus: bool = False, **data) -> str:
    """One button that runs a registered action. *text* is plain text."""
    classes = 'lat-btn' + (' lat-btn--primary' if primary else '') + (' btn-lg' if large else '')
    attrs = ''.join(f' data-{name.replace("_", "-")}="{e(value)}"' for name, value in data.items())
    return (f'<button type="button" class="{classes}" data-act="{e(act)}"{attrs}'
            + (f' id="{e(ident)}"' if ident else '') + (' data-autofocus' if focus else '')
            + (' disabled' if off else '') + f'>{e(text)}</button>')


def link(act: str, text: str, **data) -> str:
    attrs = ''.join(f' data-{name.replace("_", "-")}="{e(value)}"' for name, value in data.items())
    return f'<button type="button" class="linkbtn" data-act="{e(act)}"{attrs}>{e(text)}</button>'


def render(state) -> str:
    from vcfcf_migrator import screens
    return PAGE.format(title=e(TITLE), style=STYLE + PREVIEW_OVERRIDES, preview_css=_preview.PREVIEW_CSS,
                       body=screens.body(state) + CLIENT_JS, theme=state.theme,
                       screen=e('connect' if state.job is not None and state.job.kind == 'download'
                                else state.screen),
                       busy='1' if state.job is not None else '0', version=state.version)
