"""The screens: one question each, in the order the job is done.

Open an export, choose content, dashboard owner, review, save, import on the
target, and help. Each function returns the middle of the window and its
footer; ``body`` puts them in the frame from ``uipage``.

Nothing here changes state. A screen reads ``PageState`` and writes markup;
every control names an action in ``ui.ACTIONS``.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from vcfcf_migrator import __version__
from vcfcf_migrator import insight as _insight
from vcfcf_migrator import preview as _preview
from vcfcf_migrator import tasks as _tasks
from vcfcf_migrator.graph import KIND_ORDER
from vcfcf_migrator.ui import (DOWNLOAD_STAGES, LIST_LIMIT, STEP_NAMES, STEPS, can_go)
from vcfcf_migrator.uipage import anchor, button, e, link
from vcfcf_migrator.wording import KINDS, kind_counts, kind_many, kind_one, kind_what, listing, plural

HOW_TO_EXPORT = ('<ol class="how"><li>In VCF Operations on the source, open Administration, then Content, '
                 'then Export.</li><li>Tick what you want to move, and tick the option to include what it '
                 'depends on.</li><li>If it asks you to set a password, write it down. The target asks for '
                 'it later.</li><li>Export, and save the ZIP file to this computer.</li></ol>')
# How many things the "needs" tree draws before it says how many more there are.
TREE_LIMIT = 60
USED_BY_LIMIT = 30

GLOSSARY = ([('Source, target', 'The VCF Operations you move content out of, and the one you move it into.'),
             ('Bundle', 'The smaller ZIP this app saves, holding only what you chose.')]
            + [(one, what) for one, _many, what in KINDS.values()]
            + [('Widget', 'One box on a dashboard: a chart, a table or a number.'),
               ('Policy', 'The set of rules VCF Operations applies to your objects: what is measured and '
                          'which alerts are on.'),
               ('Adapter', 'An add-on that lets VCF Operations read data from another system, such as '
                           'vCenter or SQL Server.'),
               ('Management pack', 'An add-on for VCF Operations that brings its own adapter, dashboards, '
                                   'alerts and symptoms.'),
               ('Company directory', 'Where your organisation keeps its user accounts and groups, such as '
                                     'Active Directory.'),
               ('vCenter', 'The system that manages your virtual machines. VCF Operations reads most of '
                           'its data from it.'),
               ('Certificate', 'A file a server uses to prove who it is. Companies often issue their own.'),
               ('Export password', 'The password an export is locked with. The target asks for it at import.'),
               ('Never finished loading', 'A dashboard that VCF Operations could not finish setting up on '
                                          'the source, usually because something it reads from was missing '
                                          'there. It shows nothing there either.'),
               ('Comes with VCF Operations', 'A dashboard that every VCF Operations has from the start. '
                                             'Importing one overwrites the target’s own.')])


def _label(state, key: str) -> str:
    """A name as markup: the part that only tells copies apart is quieter."""
    node = state.graph.nodes[key]
    text = state.label(key)
    if text != node.name and text.startswith(node.name + ' ('):
        return (e(node.name) + ' <span class="muted" style="font-weight:400">'
                + e(text[len(node.name) + 1:]) + '</span>')
    return e(text)


_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
           'October', 'November', 'December')


def _when(modified: str) -> str:
    """``3 November 2026`` from the date the export records, time left off."""
    parts = (modified or '').split(' ')[0].split('-')
    if len(parts) == 3 and all(part.isdigit() for part in parts) and 1 <= int(parts[1]) <= 12:
        return f'{int(parts[2])} {_MONTHS[int(parts[1]) - 1]} {parts[0]}'
    return modified or ''


def _note(state, extra_class: str = '', style: str = '') -> str:
    """What the last action said, with Undo where there is something to undo."""
    if not state.note:
        return ''
    role = 'alert' if state.note.kind == 'bad' else 'status'
    undo = ' ' + link('/undo', 'Undo') if state.undo else ''
    return (f'<div class="lat-banner lat-banner--{state.note.kind}{" " + extra_class if extra_class else ""}" '
            f'role="{role}"' + (f' style="{style}"' if style else '') + f'>{state.note.html}{undo}</div>')


def _folder(path: Path) -> str:
    """A folder as a person names it: Downloads, not a path."""
    if path == Path.home():
        return 'home'
    return path.name or str(path)


# ---------------------------------------------------------------------------
# The frame

def header(state) -> str:
    source = f'Working from <b>{e(state.source_name)}</b>' if state.graph is not None else ''
    return ('<header class="lat-header"><h1>VCF Content Migrator</h1>'
            f'<div class="src grow">{source}</div>'
            f'<span id="update-slot">{update_button(state)}</span>'
            + ('' if state.screen == 'help' else button('/help', 'Help and settings', ident='help-button'))
            + '</header>')


def update_button(state) -> str:
    if not state.update_tag:
        return ''
    return button('/get-update', f'Update available: version {state.update_tag.lstrip("v")}')


def steps(state) -> str:
    if state.screen == 'help':
        return ('<div class="steps">' + button('/back', '← Back to where I was', ident='help-back')
                + '<h1 style="margin-left:8px">Help and settings</h1></div>')
    current = 'open' if state.screen == 'connect' else state.screen
    at = STEPS.index(current)
    reach = min(state.max_step, 1) if at == 0 else state.max_step
    skip_owner = bool(state.picked) and not state.dashboards()
    saved = state.save_state == 'saved'
    out = ['<nav class="steps" aria-label="Steps">']
    for number, step in enumerate(STEPS):
        if number:
            out.append('<span class="bar"></span>')
        name = STEP_NAMES[step]
        if step == 'owner' and skip_owner:
            out.append('<span class="step"><span class="n">–</span>Dashboard owner '
                       '<span class="sm">(not needed, no dashboards chosen)</span></span>')
        elif number == at:
            done = (step == 'save' and saved) or (step == 'target' and state.finished)
            out.append(f'<span class="step now" aria-current="step"><span class="n">'
                       f'{"✓" if done else number + 1}</span>{name}</span>')
        elif number <= reach and state.job is None and can_go(state, step):
            out.append(f'<button type="button" class="step" data-act="/go" data-to="{step}" '
                       f'id="step-{step}" title="Go to this step"><span class="n">✓</span>{name}</button>')
        else:
            out.append(f'<span class="step"><span class="n">{number + 1}</span>{name}</span>')
    return ''.join(out) + '</nav>'


def body(state) -> str:
    job = state.job
    if job is not None and job.kind == 'read':
        main, foot = reading(state)
    elif job is not None and job.kind == 'download':
        main, foot = downloading(state)
    elif job is not None and job.kind == 'save':
        main, foot = saving(state)
    else:
        if state.graph is None and state.screen in STEPS:
            # Every step after the first needs an export to be open.
            state.screen = 'open'
        main, foot = VIEWS[state.screen](state)
    out = [header(state), steps(state), f'<main>{main}</main>',
           f'<div class="foot">{foot}</div>' if foot else '<div></div>']
    if job is None:
        if state.preview_key:
            out.append(preview_overlay(state))
        if state.doc:
            out.append(doc_overlay(state))
        if state.gloss and state.screen == 'help':
            out.append(glossary_overlay())
        if state.ask:
            out.append(ask_overlay(state))
    return ''.join(out)


# ---------------------------------------------------------------------------
# 1. Open an export

def reading(state) -> Tuple[str, str]:
    name = e(state.job.name)
    main = ('<div class="center"><h2>Open an export</h2><div class="lat-card working" role="status">'
            f'<span class="spin"></span><div><b>Reading {name}</b><div class="muted sm">Listing what is '
            f'inside and what depends on what. <span id="secs" data-job="read-{state.version}">0</span> '
            'seconds so far; large exports take up to a minute.</div></div></div></div>')
    return main, (button('/cancel-job', 'Cancel', ident='cancel-job', focus=True)
                  + '<span class="say">Cancelling changes nothing.</span>')


def _error_card(state) -> str:
    error = state.open_error
    name = e(error.get('name') or 'That file')
    keep = ''
    if state.graph is not None:
        keep = f'<p class="keep">✓ {e(state.source_name)} is still open'
        if state.picked:
            keep += f' and your {plural(len(state.picked), "choice is", "choices are")} untouched'
        keep += '.</p>'
    report = ('If it happens again, save a support report: '
              + link('/help', 'open Help and settings') + '.')
    kind = error['kind']
    tone, mark = 'bad', '!'
    if kind == 'not_zip':
        title = 'That file could not be opened'
        what = (f'{name} is not a complete ZIP file. This usually means the download or copy was cut off, '
                'or it is a different kind of file.')
        do = 'Download or copy the export again, then choose it here.'
    elif kind == 'unreadable':
        title = 'That file could not be opened'
        what = (f'The computer would not let the app read {name}. It may have been moved or deleted, or '
                'you may not be allowed to open it.')
        do = 'Check that the file is still where it was, then choose it again.'
    elif kind == 'incomplete':
        declared, found = error.get('declared', 0), error.get('found', 0)
        title = 'This export is missing its dashboards'
        inside = ('none of them are inside' if not found else f'only {found} of them could be read')
        what = (f'It says it holds {plural(declared, "dashboard")}, but {inside}. That happens when an '
                'export is taken while VCF Operations is still getting its dashboards ready, for example '
                'soon after a restart or an upgrade.')
        do = ('Give the source some time to finish, then take the export again there, or let the app '
              'fetch one for you with "Sign in and download" below. If the new export has the same '
              'problem, save a support report: ' + link('/help', 'open Help and settings') + '.')
    elif kind == 'empty':
        tone, mark = 'warn', 'i'
        title = 'That export has nothing in it to move'
        what = (f'{name} is a real export, but it holds no dashboards, views or anything else this app '
                'can move.')
        do = 'Export again on the source and tick the content you want before exporting.'
    elif kind == 'unreadable_inside':
        title = 'That export could not be read'
        what = (f'{name} is an export, but part of it is damaged or written in a way this app does not '
                'understand.')
        do = 'Take the export again on the source and open the new one. ' + report
    elif kind == 'unexpected':
        title = 'Something went wrong reading that file'
        what = f'The app hit a problem it did not expect while reading {name}. Nothing was changed.'
        do = 'Try once more. ' + report
    else:
        title = 'That ZIP is not a content export'
        what = f'{name} opened fine, but it is not something VCF Operations exported.'
        do = 'Choose the ZIP that was saved from Administration, Content, Export on the source.'
    return (f'<div class="lat-card problem" role="alert" id="open-error" style="margin-bottom:14px;'
            f'border-color:var(--{tone})"><span class="ico {tone}">{mark}</span><div>'
            f'<h3 style="font-size:16px">{title}</h3><p>{what}</p>{keep}'
            f'<p style="margin:0"><b>What to do:</b> {do}</p></div></div>')


def open_screen(state) -> Tuple[str, str]:
    has = state.graph is not None
    out = ['<div class="center">',
           f'<h2>{"Open a different export" if has else "What do you want to move?"}</h2><p class="muted">']
    if not state.open_error:
        out.append('Your current export stays open until another one has been read successfully.' if has
                   else 'Start with a content export from the VCF Operations you are moving content out of.')
    out.append('</p>')
    out.append(_note(state, style='margin-bottom:12px'))
    if state.want and not state.open_error:
        out.append('<div class="lat-banner lat-banner--info" style="margin-bottom:12px"><b>You are looking '
                   f'for an export that also contains {e(state.want)}.</b> On the source, tick it as well '
                   'before exporting, or tick the option to include what things depend on.</div>')
    if state.open_error:
        out.append(_error_card(state))
    tight = bool(state.open_error or state.want)
    out.append('<div class="two"><div class="lat-card pick">'
               f'<h3>I have {"another" if tight else "an"} export file</h3>'
               + button('/pick-export', 'Choose another file…' if tight else 'Choose the export file…',
                        primary=True, large=True, ident='pick-export', focus=True))
    if state.typed_path:
        out.append('<div data-form="open" style="align-self:stretch"><label class="lat-label" for="zip">'
                   'The file window could not be opened. Type or paste where the export file is:</label>'
                   '<div class="searchrow"><input class="lat-input" id="zip" name="zip" data-keep '
                   'placeholder="For example Downloads/my-export.zip">'
                   + button('/open', 'Open this file', enter='1') + '</div></div>')
    elif not tight:
        out.append('<button type="button" class="drop" data-act="/pick-export" id="drop-zone">'
                   'or drop the ZIP file here</button>')
    out.append(f'<div><b class="sm">{"How to make an export:" if tight else "No export yet? Make one like this:"}'
               f'</b>{HOW_TO_EXPORT}</div></div>'
               '<div class="lat-card pick"><h3>Get it from VCF Operations for me</h3>'
               + button('/go', 'Sign in and download…', large=True, ident='to-connect', to='connect')
               + '<p class="muted sm" style="margin:0">Sign in to the source. The app asks it for an export '
                 'of your custom content and downloads it. Nothing on the source is changed.</p></div></div></div>')
    foot = ((button('/go', f'← Back to {state.source_name}', to='choose') if has else '')
            + '<span class="say">Everything happens on this computer. The export file is only read, never '
              'changed.</span>')
    return ''.join(out), foot


# ---------------------------------------------------------------------------
# 1b. Sign in to the source

def downloading(state) -> Tuple[str, str]:
    stage = state.job.stage
    rows = []
    for number, text in enumerate(DOWNLOAD_STAGES):
        if number < stage:
            rows.append(f'<li class="done"><span class="dot">✓</span>{text}</li>')
        elif number == stage:
            rows.append('<li class="now"><span class="dot"><span class="spin" style="width:14px;'
                        f'height:14px;border-width:2px"></span></span>{text}…</li>')
        else:
            rows.append(f'<li><span class="dot">{number + 1}</span>{text}</li>')
    main = ('<div class="center"><h2>Getting your content</h2><div class="lat-card" role="status">'
            f'<ul class="stages">{"".join(rows)}</ul><p class="muted sm" style="margin-top:8px">This can '
            'take a few minutes on a busy system. Do not start another export with the same account until '
            'it finishes, or the two can get mixed up.</p></div></div>')
    return main, (button('/cancel-job', 'Cancel', ident='cancel-job', focus=True)
                  + '<span class="say">Cancelling leaves the source untouched.</span>')


# What the two hints say once the address has been looked up.
ADDRESS_HINTS = {'ok': '✓ Found it. Sign-in choices loaded below.',
                 'failed': 'Could not reach that address yet. "Test the sign-in" says why.'}
SOURCE_HINTS = {'ok': ('These are the choices this server offers. Not sure? Leave it as it is, and if the '
                       'sign-in fails, try the other.'),
                'failed': 'Not sure? Leave it as it is.'}


def source_options(names) -> str:
    """The choices under "This account is": local, then what the server offers."""
    out = '<option value="Local">A local account on VCF Operations</option>'
    for name in names or []:
        if name.strip().lower() != 'local':
            out += f'<option value="{e(name)}">An account from the company directory {e(name)}</option>'
    return out


def connect_screen(state) -> Tuple[str, str]:
    conn = state.conn
    tested, cert, error = conn.get('tested'), conn.get('cert'), conn.get('error')
    cert_open = tested == 'cert' and not cert
    sources = conn.get('sources') or []
    out = ['<div class="center"><h2>Sign in to the source</h2><p class="muted">The app signs in, asks for '
           'an export of your custom content, and downloads it. Your user name and password are used '
           'once and never saved.</p>', _note(state, style='margin-bottom:12px')]
    if error and error[0] == 'auth':
        out.append('<div class="lat-banner lat-banner--bad" role="alert" style="margin-bottom:12px">'
                   f'<b>Could not sign in.</b> The user name or password was not accepted by '
                   f'{e(error[1] or "the server")}. Nothing was exported. Type the password again. If you '
                   'are sure it is right, try the other choice under "This account is".</div>')
    elif error:
        out.append('<div class="lat-banner lat-banner--bad" role="alert" style="margin-bottom:12px">'
                   f'<b>Could not get the export.</b> {e(error[1])} Nothing was opened.</div>')
    stale = '1' if (tested or error) else ''
    lookup = conn.get('lookup')
    out.append(f'<div class="two" data-form="connect" data-cert="{"open" if cert_open else ""}" '
               f'data-certmode="{e((cert or {}).get("mode", ""))}" data-stale="{stale}"><div class="lat-card">'
               '<div class="field"><label class="lat-label" for="c-addr">Address of the source VCF '
               'Operations</label><input class="lat-input" id="c-addr" name="address" data-keep '
               'data-autofocus placeholder="https://operations.example.com">'
               '<div class="lat-hint" id="c-addr-hint">'
               + (ADDRESS_HINTS[lookup] if lookup in ADDRESS_HINTS
                  else 'The address you type in your browser to open it.') + '</div></div>'
               '<div class="field"><label class="lat-label" for="c-user">User name</label>'
               '<input class="lat-input" id="c-user" name="username" data-keep autocomplete="off"></div>'
               '<div class="field"><label class="lat-label" for="c-pw">Password</label>'
               '<input class="lat-input" id="c-pw" name="password" type="password" data-keep'
               + (' data-fresh' if error and error[0] == 'auth' else '') + ' autocomplete="off"></div>'
               '<div class="field" style="margin:0"><label class="lat-label" for="c-src">This account is'
               f'</label><select class="lat-select" id="c-src" name="auth_source" data-keep>'
               f'{source_options(sources)}</select><div class="lat-hint" id="c-src-hint">'
               + (SOURCE_HINTS[lookup] if lookup in SOURCE_HINTS else
                  'Not sure? Leave it as it is. More choices appear once the address is filled in.')
               + '</div></div></div>')
    out.append('<div><div class="lat-card" style="margin-bottom:12px"><div class="field" style="margin:0">'
               '<label class="lat-label" for="c-epw">Make up a password for the export</label>'
               '<input class="lat-input" id="c-epw" name="export_password" type="password" data-keep '
               'autocomplete="off"><label class="lat-label" for="c-epw2" style="margin-top:8px">Type it '
               'again</label><input class="lat-input" id="c-epw2" name="export_password2" type="password" '
               'data-keep autocomplete="off">'
               '<div class="lat-hint" id="c-mismatch" style="color:var(--bad-ink)" hidden>The two do not '
               'match yet.</div>'
               '<div class="lat-hint">An export fetched this way has to be locked with a password. Write '
               'it down: the target asks for it when you import.</div></div></div>')
    if tested == 'ok':
        status = '<span class="lat-badge lat-badge--ok">✓ Signed in fine. Nothing was exported.</span>'
    elif cert_open:
        status = '<span class="lat-badge lat-badge--bad">The server could not be verified</span>'
    elif tested == 'auth':
        status = '<span class="lat-badge lat-badge--bad">The user name or password was not accepted</span>'
    elif tested == 'other':
        status = f'<span class="lat-badge lat-badge--bad" style="white-space:normal">{e(conn.get("tested_text"))}</span>'
    else:
        status = ('<span class="muted sm" id="conn-test-hint">Fill in the address, user name and password '
                  'to test them.</span>')
    out.append('<div class="lat-card"><h3>Check before you start</h3><div class="rowbtns" style="margin-top:0">'
               + button('/conn-test', 'Test the sign-in', ident='conn-test', off=True) + status + '</div>')
    if cert:
        how = (f'Using the certificate file {e(Path(cert.get("path", "")).name)}.' if cert.get('mode') == 'file'
               else 'Connecting without checking the certificate.')
        out.append(f'<div class="lat-banner lat-banner--info" style="margin-top:10px">{how} '
                   + link('/conn-cert-undo', 'Undo') + '</div>')
    elif cert_open:
        out.append('<div class="lat-banner lat-banner--warn" style="margin-top:10px"><b>This server uses a '
                   'certificate your computer does not recognise.</b> That is normal when a company issues '
                   'its own. The safe fix is to give the app your company’s certificate file (your IT team '
                   'has it).<div class="rowbtns">'
                   + button('/conn-cert-file', 'Use my company certificate file…', primary=True)
                   + '</div><div class="sm" style="margin-top:8px">No file to hand? '
                   + link('/conn-cert-skip', 'Connect without checking')
                   + '. The app then cannot prove it is the real server, so only do this on a network you '
                     'trust.</div></div>')
    else:
        out.append('<p class="sm muted" style="margin:10px 0 0">A certificate problem, if there is one, gets '
                   'its fix right here.</p>')
    out.append('</div></div></div></div>')
    foot = (button('/go', '← Back', to='open') + '<span class="say" id="conn-say">'
            + ('Choose one of the two certificate options above to continue.' if cert_open else
               'Fill in the address, user name and password, and type the export password twice, to continue.')
            + '</span>' + button('/connect', 'Sign in and download', primary=True, large=True, off=True,
                                 ident='conn-go', uses='connect'))
    return ''.join(out), foot


# ---------------------------------------------------------------------------
# 2. Choose content

def _rail_button(state, key: str, text: str, count: int, tip: str = '') -> str:
    on = state.filter_kind == key
    return (f'<button type="button" class="{"on" if on else ""}" data-act="/filter" data-f="{e(key)}" '
            f'id="rail-{e(key)}" aria-pressed="{str(on).lower()}"' + (f' title="{e(tip)}"' if tip else '')
            + f'><span>{e(text)}</span><span class="cnt">{count}</span></button>')


def rail(state) -> str:
    counts = state.graph.counts()
    unfinished = len(state.graph.hollow_dashboards())
    out = ['<div class="rail" role="navigation" aria-label="Show"><div class="cap">Show</div>',
           _rail_button(state, 'all', 'Everything', len(state.graph.nodes)),
           _rail_button(state, 'in', 'In your bundle', len(state.selection_keys()))]
    if unfinished:
        out.append(_rail_button(state, 'unf', 'Never finished loading', unfinished))
    out.append('<div class="sep"></div><div class="cap">By type</div>')
    for kind in KIND_ORDER + sorted(set(counts) - set(KIND_ORDER)):
        if counts.get(kind):
            out.append(_rail_button(state, kind, kind_many(kind), counts[kind], kind_what(kind)))
    return ''.join(out) + '</div>'


def _row(state, node, included, picked) -> str:
    graph, ins = state.graph, state.insight
    key = node.key
    is_in, mine = key in included, key in picked
    auto = is_in and not mine
    name = _label(state, key)
    plain = e(state.label(key))
    tags = []
    if auto:
        tags.append('<span class="lat-badge lat-badge--accent">Added automatically</span>')
    if node.hollow:
        tags.append('<span class="lat-badge lat-badge--warn">Never finished loading</span>')
    if key in ins.blocked:
        tags.append('<span class="lat-badge lat-badge--bad">Needs something not in this export</span>')
    else:
        for catch in _insight.catches(graph, node):
            if catch.listed:
                tags.append(f'<span class="lat-badge lat-badge--warn">{e(catch.badge)}</span>')
    if node.stock_or_derived:
        tags.append('<span class="lat-badge lat-badge--warn">Replaces a built-in dashboard</span>')
    ident = anchor(key)
    if auto:
        box = (f'<input type="checkbox" id="tick-{ident}" checked disabled '
               f'aria-label="{plain} was added automatically">')
    else:
        box = (f'<input type="checkbox" id="tick-{ident}" data-act="/select" data-key="{e(key)}"'
               + (' checked' if mine else '')
               + f' aria-label="{"Remove " if mine else "Add "}{plain}">')
    sub = kind_one(node.kind)
    if auto:
        sub += ' · needed by ' + e(state._names(state.needed_by(key, included)))
    if key in ins.twins:
        sub += f' · one of {len(ins.twins[key]) + 1} copies'
    if node.hollow and not is_in:
        sub += ' · left out unless ticked'
    focused = state.focus_key == key
    return (f'<div class="it{" in" if is_in else ""}{" focus" if focused else ""}" data-act="/focus" '
            f'data-key="{e(key)}" id="{ident}"' + (' data-into-view' if focused and state.reveal else '')
            + f'><label class="chk">{box}</label><span><button type="button" class="nmb" data-act="/focus" '
            f'data-key="{e(key)}" id="name-{ident}">{name}</button><div class="sub">{sub}</div></span>'
            f'<span class="tags">{"".join(tags)}</span></div>')


def list_parts(state) -> dict:
    """The list, its count and its bulk button: drawn with the screen, and
    again on their own as the search box is typed in."""
    nodes = state.visible_nodes()
    total = len(state.graph.nodes)
    included, picked = state.selection_keys(), state.selected_keys()
    count = f'All {len(nodes)}' if len(nodes) == total else f'{len(nodes)} of {total}'
    addable = state.addable(nodes)
    addall = button('/add-shown', 'Add all of these', ident='add-shown') if len(addable) > 1 else ''
    if nodes:
        rows = ''.join(_row(state, node, included, picked) for node in nodes[:LIST_LIMIT])
        if len(nodes) > LIST_LIMIT:
            rows += (f'<div class="more">Showing the first {LIST_LIMIT} of {len(nodes)}. Type part of a name '
                     'in "Find by name" to narrow the list. "Add all of these" still adds every one of the '
                     f'{len(nodes)}.</div>')
    else:
        empty_bundle = state.filter_kind == 'in' and not state.filter_text
        rows = ('<div class="empty"><b>' + ('Your bundle is empty' if empty_bundle else 'Nothing matches')
                + '</b>' + ('Tick things in the list to add them. ' if empty_bundle else
                            f'No names contain "{e(state.filter_text)}"'
                            + ('' if state.filter_kind == 'all' else ' in this part of the list') + '. ')
                + '<div class="rowbtns" style="justify-content:center">'
                + button('/show-all', 'Show everything') + '</div></div>')
    return {'list': rows, 'cnt': count, 'addall': addall}


def _tree(state, key: str, seen: set, budget: List[int]) -> str:
    graph = state.graph
    out = []
    children = [k for k in graph.edges.get(key, []) if k not in seen and k in graph.nodes]
    seen.update(children)
    for child in children:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        node = graph.nodes[child]
        below = _tree(state, child, seen, budget)
        out.append(f'<li><span class="ty">{kind_one(node.kind)}</span><span>'
                   + link('/focus', state.label(child), key=child, reveal='1') + '</span>'
                   + (f'<ul>{below}</ul>' if below else '') + '</li>')
    node = graph.nodes[key]
    for gap in _insight.hard_gaps(graph, key):
        kind, name = _insight.missing_thing(gap)
        out.append(f'<li class="gone"><span class="ty">{e(kind)}</span><span><b>{e(name)}</b> '
                   '<span class="lat-badge lat-badge--bad">Not in this export</span></span></li>')
    for catch in _insight.catches(graph, node):
        for kind, name, why in catch.gone:
            out.append(f'<li class="gone soft"><span class="ty">{e(kind)}</span><span><b>{e(name)}</b> '
                       f'<span class="lat-badge lat-badge--warn">{e(why)}</span></span></li>')
    return ''.join(out)


def detail(state) -> str:
    graph, ins = state.graph, state.insight
    node = graph.nodes.get(state.focus_key)
    if node is None:
        return ('<div class="empty" style="padding-top:60px"><b>Tick what you want to move</b>Anything it '
                'depends on is added for you.<br><br>Click a name to see what it is, what it needs and '
                'what it looks like.</div>')
    key = node.key
    included = state.selection_keys()
    mine, is_in = key in state.picked, key in included
    closure = _insight.reach(graph, key)
    hard = [(graph.nodes[k], gap) for k in closure for gap in _insight.hard_gaps(graph, k)]
    gone_soft = sum(len(c.gone) for k in closure for c in _insight.catches(graph, graph.nodes[k]))
    own = _insight.catches(graph, node)
    gone = len(hard) + gone_soft
    total = len(closure) - 1 + gone
    nested = any(k not in graph.edges.get(key, []) for k in closure[1:]) or any(n.key != key for n, _g in hard)
    owner = graph.owner_names.get(node.owner, '') if node.owner else ''
    out = [f'<div class="detail"><h3 style="font-size:17px;margin-bottom:2px">{_label(state, key)}</h3>'
           f'<div class="kind">{kind_one(node.kind)}'
           + (f', owned by {e(owner)} on the source' if owner else '') + f'. {kind_what(node.kind)}</div>']
    twins = ins.twins.get(key, [])
    if twins:
        def changed(n):
            return f' (changed {e(_when(n.modified))})' if n.modified else ''
        others = [graph.nodes[k] for k in twins]
        out.append(f'<div class="lat-banner lat-banner--info" style="margin-bottom:10px"><b>One of '
                   f'{len(twins) + 1} copies.</b> This one belongs to {e(owner)}{changed(node)}; '
                   + ('the other to ' if len(others) == 1 else 'the others to ')
                   + listing([e(graph.owner_names.get(o.owner, 'Unknown owner')) + changed(o) for o in others])
                   + '. You normally want only one. '
                   + link('/focus', 'Show the other copy', key=others[0].key, reveal='1') + '</div>')
    if node.hollow:
        waiting = [w for w in node.waiting_on if w]
        out.append('<div class="lat-banner lat-banner--warn" style="margin-bottom:10px"><b>Never finished '
                   'loading on the source.</b> VCF Operations could not finish setting this dashboard up '
                   'there' + (f': it was waiting for {e(listing(waiting))}.' if waiting
                              else ', and the export does not say why.')
                   + ' It is empty there and would arrive just as empty. Best fixed on the source first, '
                     'then exported again. It is left out unless you tick it, and Review will ask you to '
                     'confirm.</div>')
    if hard:
        things = [_insight.missing_phrase(gap) for _n, gap in hard]
        shown = listing(things[:3]) + (f' and {plural(len(things) - 3, "other thing")}' if len(things) > 3 else '')
        out.append('<div class="lat-banner lat-banner--bad" style="margin-bottom:10px"><b>Needs something '
                   f'that is not in this export.</b> It needs {e(shown)} (marked in red below). Without '
                   'that it would arrive broken, unless the target already has it. Best to get an export '
                   'that has both. If you add it as it is, Review will ask you to confirm.</div>')
    for catch in own:
        out.append(f'<div class="lat-banner lat-banner--warn" style="margin-bottom:10px"><b>{e(catch.badge)}.'
                   f'</b> {e(catch.text)}</div>')
    if node.stock_or_derived:
        out.append('<div class="lat-banner lat-banner--warn" style="margin-bottom:10px"><b>Replaces a '
                   'built-in dashboard.</b> This dashboard comes with every VCF Operations, so the target '
                   'already has its own. Importing this one replaces that for good. Most people leave it '
                   'out. If you add it, Review will ask you to confirm.</div>')
    warn = node.hollow or node.stock_or_derived or bool(hard)
    out.append('<div class="rowbtns" style="margin:0 0 14px">')
    if mine:
        out.append(button('/select', 'Leave out of the bundle', key=key, on='0'))
    elif is_in:
        out.append('<span class="lat-badge lat-badge--accent" style="white-space:normal">In your bundle, '
                   f'needed by {e(state._names(state.needed_by(key, included)))}</span>'
                   '<span class="muted sm">To leave it out, leave out what needs it.</span>')
    else:
        out.append(button('/select', 'Add to bundle', primary=not warn, key=key, on='1'))
    if hard and not is_in:
        out.append(button('/fuller', 'Open an export that has what is missing…',
                          want=_insight.missing_phrase(hard[0][1])))
    if node.kind in ('dashboard', 'view'):
        out.append(button('/preview', 'See what it looks like', key=key))
    out.append('</div>')
    if node.hollow:
        out.append('<h3>What it needs is unknown</h3><p class="muted sm">Because it never finished loading, '
                   'the export does not record what it uses.</p>')
    elif total:
        out.append(f'<h3>Needs {plural(total, "other thing")}'
                   + (f', {gone} of them missing' if gone else '') + '</h3><p class="muted sm">'
                   + ('The ones that are in this export come along automatically when you add it.' if gone
                      else 'These are in this export and come along automatically when you add it.')
                   + (' An indented item is needed by the one above it.' if nested else '') + '</p>')
    else:
        out.append('<h3>Needs nothing else</h3><p class="muted sm">It stands on its own.</p>')
    budget = [TREE_LIMIT]
    tree = _tree(state, key, {key}, budget)
    if tree:
        out.append(f'<ul class="lat-tree needs">{tree}</ul>')
        if budget[0] <= 0 and len(closure) - 1 > TREE_LIMIT:
            out.append(f'<p class="muted sm" style="margin-top:6px">and {len(closure) - 1 - TREE_LIMIT} more '
                       'that those need.</p>')
    users = ins.used_by.get(key, [])
    if users:
        out.append('<h3 style="margin-top:14px">Used by</h3><ul class="lat-tree needs">')
        for user in users[:USED_BY_LIMIT]:
            out.append(f'<li><span class="ty">{kind_one(graph.nodes[user].kind)}</span><span>'
                       + link('/focus', state.label(user), key=user, reveal='1') + '</span></li>')
        out.append('</ul>')
        if len(users) > USED_BY_LIMIT:
            out.append(f'<p class="muted sm">and {len(users) - USED_BY_LIMIT} more.</p>')
    return ''.join(out) + '</div>'


def choose_screen(state) -> Tuple[str, str]:
    parts = list_parts(state)
    toast = _note(state, 'toast')
    if state.stale:
        toast += ('<div class="lat-banner lat-banner--warn toast" role="status"><b>You changed your choices '
                  'after saving.</b> The bundle saved earlier does not include the change. Save again when '
                  'you are done.</div>')
    middle = ('<div class="lat-card panel"><div class="head"><div class="searchrow">'
              f'<input class="lat-input" id="q" placeholder="Find by name" aria-label="Find by name" '
              f'value="{e(state.filter_text)}" autocomplete="off"><span class="cnt" id="cnt">{parts["cnt"]}'
              f'</span><span id="addall">{parts["addall"]}</span></div></div>'
              f'<div class="body" id="list" data-scroll="list">{parts["list"]}</div>'
              f'<div id="toast">{toast}</div></div>')
    main = (f'<div class="cols">{rail(state)}{middle}<div class="lat-card panel"><div class="body" '
            f'id="detail" data-scroll="detail-{anchor(state.focus_key)}">{detail(state)}</div></div></div>')
    state.reveal = False
    chosen, total = len(state.picked), len(state.selection_keys())
    auto = total - chosen
    if chosen:
        say = (f'<b>In your bundle: {plural(total, "thing")}.</b> {chosen} you chose'
               + (f', {auto} added automatically because '
                  f'{"your choice needs" if chosen == 1 else "your choices need"} them.' if auto else '.')
               + ' ' + link('/filter', 'Show the bundle', f='in') + ' &nbsp; '
               + link('/clear', 'Empty the bundle'))
    else:
        say = 'Tick at least one thing to continue.'
    following = 'owner' if state.dashboards() or not chosen else 'review'
    foot = (button('/go', '← Different export', to='open') + f'<span class="say">{say}</span>'
            + button('/go', f'Next: {STEP_NAMES[following].lower()} →', primary=True, large=True,
                     off=not chosen, ident='next', to=following))
    return main, foot


# ---------------------------------------------------------------------------
# 3. Dashboard owner

def _owners(state, nodes) -> List[str]:
    return _tasks.owner_names(state.graph, nodes)


def _shared_with(state, node) -> str:
    groups = [name for name, _d in state.graph.dashboard_shares.get((node.owner, node.ident), [])]
    return e(listing([g or 'a group' for g in groups])) if groups else 'Nobody (private)'


def _copies_question(state, copies) -> Tuple[str, str]:
    graph = state.graph
    dashboards = state.dashboards()
    name = e(copies[0].name)
    cards = []
    for node in copies:
        owner = e(graph.owner_names.get(node.owner, 'Unknown owner'))
        others = [n.key for n in copies if n.key != node.key]
        if state.can_keep_copy(node.key):
            action = button('/keep-copy', 'Keep this copy', primary=True, key=node.key,
                            ident='keep-' + anchor(node.key))
        else:
            needs = [k for other in others for k in state.needed_by(other)] or others
            action = ('<span class="muted sm">The other copy comes along with '
                      f'{e(state._names(list(dict.fromkeys(needs))))}. To keep only this one, go back and '
                      'leave that out.</span>')
        cards.append(f'<div class="lat-card copy"><h3 style="font-size:15px">The copy owned by {owner}</h3>'
                     f'<dl><dt>Owner on the source</dt><dd>{owner}</dd><dt>Last changed</dt>'
                     f'<dd>{e(_when(node.modified)) or "Not recorded"}</dd><dt>Shared with</dt>'
                     f'<dd>{_shared_with(state, node)}</dd></dl><div class="rowbtns" style="margin-top:auto">'
                     + action + button('/preview', 'See what it looks like', key=node.key) + '</div></div>')
    count = len(copies)
    cards.append('<div class="lat-card copy"><h3 style="font-size:15px">'
                 + ('Both' if count == 2 else f'All {count}') + ', under their original owners</h3>'
                 f'<p style="font-size:13px">{"Both" if count == 2 else "All"} arrive, each under its '
                 'original owner. <b>This also answers the next question for you:</b> every dashboard in '
                 'your bundle then goes to the same user as on the source, and the import or the owner '
                 f'mapping may fail unless those users ({e(listing(_owners(state, dashboards)))}) already '
                 'exist on the target.</p><div class="rowbtns" style="margin-top:auto">'
                 + button('/owner', 'Keep both copies' if count == 2 else f'Keep all {count} copies',
                          mode='source', both='1', ident='keep-both') + '</div></div>')
    main = ('<div class="center scrolls" style="max-width:1100px">' + _note(state, style='margin-bottom:10px')
            + f'<h2>First: which copy of {name} do you want?</h2><p class="muted">'
            + ('Two people' if count == 2 else f'{count} people')
            + ' on the source each have their own copy of this dashboard, and you chose '
            + ('both' if count == 2 else 'more than one')
            + '. A dashboard can exist only once per owner, so if they went to the same owner on the target, '
              'one would replace the other. Keep one, or keep them under their original owners. Nothing is '
              'deleted on the source whichever you pick.</p>'
              f'<div class="three" style="grid-template-columns:repeat({min(count + 1, 4)},1fr)">'
            + ''.join(cards) + '</div></div>')
    return main, (button('/go', '← Back to choosing', to='choose')
                  + f'<span class="say">Pick one of the {count + 1} to continue.</span>')


def _merge_question(state) -> Tuple[str, str]:
    dashboards = state.dashboards()
    main = ('<div class="center">' + _note(state, style='margin-bottom:10px')
            + '<h2>First: these dashboards cannot share one owner</h2><p class="muted">The dashboards you '
              'chose belong to different people on the source, and their files cannot be combined under a '
              'single owner without changing them. Keep each under its original owner, or go back and '
              'choose dashboards from one owner only.</p><div class="two">'
              '<div class="lat-card copy"><h3 style="font-size:15px">Keep each under its original owner</h3>'
              '<p style="font-size:13px"><b>This also answers the next question for you:</b> every '
              'dashboard then goes to the same user as on the source, and the import or the owner mapping '
              f'may fail unless those users ({e(listing(_owners(state, dashboards)))}) already exist on '
              'the target.</p><div class="rowbtns" style="margin-top:auto">'
            + button('/owner', 'Keep the original owners', primary=True, mode='source', ident='keep-both')
            + '</div></div><div class="lat-card copy"><h3 style="font-size:15px">Choose from one owner only'
              '</h3><p style="font-size:13px">Go back and leave out the dashboards of all but one owner. '
              'The list shows who owns each dashboard when you click its name.</p>'
              '<div class="rowbtns" style="margin-top:auto">'
            + button('/go', 'Back to choosing', to='choose') + '</div></div></div></div>')
    return main, (button('/go', '← Back to choosing', to='choose')
                  + '<span class="say">Pick one of the two to continue.</span>')


def _owner_option(state, mode: str, title: str, text: str) -> str:
    on = state.owner_mode == mode
    return (f'<label class="opt{" on" if on else ""}"><input type="radio" name="own" id="own-{mode}" '
            f'data-act="/owner" data-mode="{mode}"{" checked" if on else ""}><span><b>{title}</b>'
            f'<span class="t">{text}</span></span></label>')


def owner_screen(state) -> Tuple[str, str]:
    graph = state.graph
    dashboards = state.dashboards()
    if not dashboards:
        main = ('<div class="center"><h2>No owner to choose</h2><p class="muted">There are no dashboards in '
                'your bundle, so this step is not needed.</p></div>')
        return main, (button('/go', '← Back to choosing', to='choose') + '<span class="say"></span>'
                      + button('/go', 'Next: review →', primary=True, large=True, to='review', ident='next'))
    question = state.owner_question()
    if question == 'copies':
        return _copies_question(state, state.copies_chosen()[0])
    if question == 'merge':
        return _merge_question(state)
    keep = state.owner_mode == 'source'
    mine = sum(1 for d in dashboards if d.key in state.picked)
    owners = e(listing(_owners(state, dashboards)))
    copies = [group for group in _copy_groups(state)]
    left = ['<div class="scrolls">', _note(state),
            f'<h2 style="margin-top:{"10px" if state.note else "0"}">Who should own '
            + ('this dashboard' if len(dashboards) == 1 else f'these {len(dashboards)} dashboards')
            + ' on the target?</h2><p class="muted">'
            + (f'{mine} you chose and {len(dashboards) - mine} added automatically. '
               if len(dashboards) > mine else '')
            + 'Every dashboard belongs to a user. The table shows what each choice does.</p>']
    back = (f' <b>Choosing this takes you back to pick one copy of {e(copies[0][0].name)}.</b>'
            if copies else '')
    left.append(_owner_option(
        state, 'admin', 'The built-in admin user <span class="lat-badge lat-badge--ok">Safest</span>',
        'Works on any target: this user always exists, so every dashboard arrives. The price is that '
        'sharing with company-directory groups has to be set up again by hand.' + back))
    left.append(_owner_option(
        state, 'source', 'The same user as on the source <span class="lat-badge lat-badge--warn">May fail</span>',
        f'Each dashboard goes to the user who owns it on the source. Here that is {owners}. Sharing comes '
        'across as it is.'
        + (f' <b>{"Both" if len(copies[0]) == 2 else "All"} copies of {e(copies[0][0].name)} are in your '
           'bundle because of this choice.</b>' if copies else '')
        + '<span class="w"><b>Warning:</b> The import, or the owner mapping, may fail if one of these users '
          'does not exist on the target. A dashboard whose owner or group is missing there is skipped. This '
          'app works from a file and cannot check the target for you.</span>'))
    left.append('</div>')
    rows = []
    directory_group = ''
    for node in dashboards:
        owner = graph.owner_names.get(node.owner, 'Unknown owner')
        directory = _tasks.directory_groups(graph, node)
        local = _tasks.local_groups(graph, node)
        directory_group = directory_group or (directory[0] if directory else '')
        who = e(owner) if keep else 'admin'
        if keep and owner != 'admin':
            who += (f'<div><span class="lat-badge lat-badge--warn">Skipped if {e(owner)} is not on the '
                    'target</span></div>')
        if directory:
            if keep:
                share = (f'Shared with {e(listing(directory + local))}<div><span class="lat-badge '
                         'lat-badge--warn">Whole dashboard skipped if the target has no such group</span></div>')
            else:
                share = ('<span class="lat-badge lat-badge--warn">Sharing not carried</span> You share it '
                         f'with {e(listing(directory))} again after import'
                         + (f'. Still shared with {e(listing(local))}.' if local else ''))
        elif local:
            share = f'Shared with {e(listing(local))}'
        else:
            share = 'Private, as on the source'
        rows.append(f'<tr><td><b>{_label(state, node.key)}</b>'
                    + ('' if node.key in state.picked else '<div class="muted sm">added automatically</div>')
                    + f'</td><td>{who}</td><td>{share}</td></tr>')
    right = ('<div class="lat-card panel"><div class="head"><h3 style="margin:0">What will happen</h3></div>'
             '<div class="body" data-scroll="owner-table"><table class="lat-table fixed"><thead><tr>'
             '<th style="width:34%">Dashboard</th><th style="width:28%">Owner on the target</th>'
             '<th>Sharing after import</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
             + (f'<p class="sm muted" style="padding:10px 12px 0">{e(directory_group)} is a group from your '
                'company’s staff directory. If the target is not connected to the same directory, it has '
                'no such group to share with. That is why the safest choice drops that sharing and asks '
                'you to set it up again after import.</p>' if directory_group else '')
             + '</div></div>')
    main = f'<div class="split">{"".join(left)}{right}</div>'
    foot = (button('/go', '← Back to choosing', to='choose') + '<span class="say"></span>'
            + button('/go', 'Next: review →', primary=True, large=True, to='review', ident='next'))
    return main, foot


def _copy_groups(state):
    return state.copies_chosen()


# ---------------------------------------------------------------------------
# 4. Review

_GROUP_TITLES = {'must': 'Decide before saving', 'kept': 'Decided',
                 'know': 'Good to know, nothing to decide', 'more': 'For reference'}


def _dot(issue) -> str:
    if issue.group == 'must':
        return '<span class="ico sm2 bad">!</span>'
    if issue.group == 'kept':
        return '<span class="ico sm2 ok">✓</span>'
    if issue.group == 'know':
        return '<span class="ico sm2 info">i</span>'
    return f'<span class="ico sm2 info">{"→" if issue.id == "todo" else "≡"}</span>'


def _chain(state, pick: str, node, gap) -> str:
    graph = state.graph
    keys = _insight.path(graph, pick, node.key)
    lines = []
    for first, second in zip(keys, keys[1:]):
        lines.append(f'{_label(state, first)} uses the {kind_one(graph.nodes[second].kind).lower()} '
                     f'{_label(state, second)}.')
    lines.append(f'{_label(state, node.key)} needs {e(_insight.missing_phrase(gap))}, which is '
                 '<b>not in this export</b>.')
    return '<ul class="chain">' + ''.join(f'<li>{line}</li>' for line in lines) + '</ul>'


def _issue_detail(state, issue, tasks) -> str:
    graph = state.graph
    out = [f'<div class="detail"><h3 style="font-size:16px;margin-bottom:10px">{e(issue.title)}</h3>']
    if issue.task is not None:
        out.append(f'<p>{e(issue.task.why)}</p>')
        if issue.task.id == 'users':
            out.append('<p class="muted">You chose "The same user as on the source". This app works from a '
                       'file and cannot check the target, so checking that these users exist goes first on '
                       'your checklist. To avoid the risk, give the dashboards to the built-in admin user '
                       'instead.</p><div class="rowbtns">'
                       + button('/go', 'Change the owner choice', to='owner') + '</div>')
        else:
            out.append('<p class="muted">Nothing to decide here. Putting it right is on your checklist for '
                       'the target, with the steps to follow. It takes a few minutes.</p>')
    elif issue.share is not None:
        node = issue.share
        groups = e(listing(_tasks.directory_groups(graph, node)))
        out.append(f'<p>On the source, {_label(state, node.key)} is shared with {groups}, from your '
                   'company’s staff directory. With the owner you chose, that sharing does not come '
                   'across, so at first only the owner sees it.</p><p class="muted">Sharing it again is on '
                   'your checklist for the target. Or go back and choose "The same user as on the source", '
                   'which carries the sharing but may fail if the target does not have that user and that '
                   'group.</p><div class="rowbtns">'
                   + button('/go', 'Change the owner choice', to='owner') + '</div>')
    elif issue.merge:
        if issue.merge == 'unresolved':
            out.append('<p>One of its widgets is tied to an object that the export does not describe. '
                       'When dashboards from different owners are put under one owner, that tie cannot be '
                       'kept, so the widget may stay empty on the target.</p><p class="muted">Check that '
                       'widget on the source. Or go back and choose "The same user as on the source", which '
                       'leaves each dashboard as it is.</p>')
        else:
            out.append('<p>A piece of its text has the same internal name as a text in another dashboard '
                       'you chose. So that both keep their own wording under one owner, the app renames one '
                       'of them inside the bundle. What people see does not change.</p>'
                       '<p class="muted">Nothing to decide and nothing to do.</p>')
        out.append('<div class="rowbtns">' + button('/go', 'Change the owner choice', to='owner') + '</div>')
    elif issue.pick:
        pick = issue.pick
        name = _label(state, pick)
        for node, gap in issue.hard:
            out.append(f'<div class="prob">{_chain(state, pick, node, gap)}<p class="muted">Without it '
                       f'{name} would arrive broken, unless the target already has it. An export never '
                       'carries some of the things that come with VCF Operations itself, and the app '
                       'cannot see the target, so it waits for your answer.</p></div>')
        for node in issue.stock:
            same = node.key == pick
            other = _label(state, node.key)
            out.append('<div class="prob"><p>'
                       + ('' if same else f'{name} uses the dashboard {other}, so it comes along. ')
                       + f'{other} is a dashboard that comes with VCF Operations, so the target already has '
                         'its own copy. <b>Importing yours overwrites the target’s copy for good.</b></p><p>'
                       + ('Most people leave it out.' if same else
                          f'{name} cannot go without it, so the choice is about {name} as a whole. Leaving '
                          'it out is the safe answer.')
                       + f' Keep it in only if you want the target to have the source’s version of {other}, '
                         'for example because someone changed it on the source.</p></div>')
        for node in issue.unfinished:
            same = node.key == pick
            waiting = [w for w in node.waiting_on if w]
            out.append('<div class="prob"><p>'
                       + ('On the source this dashboard' if same else
                          f'{name} uses the dashboard {_label(state, node.key)}, which')
                       + ' never finished loading, so its widgets are empty'
                       + (f'. It was waiting for {e(listing(waiting))}' if waiting
                          else '. The export does not say what it was waiting for')
                       + (f'. The source tried {plural(node.import_attempts, "time")}.'
                          if node.import_attempts else '.')
                       + '</p><p><b>It will arrive just as empty.</b> Most people leave it out, repair it '
                         'on the source, and move it later. If you keep it in, repairing it goes on your '
                         'checklist.</p></div>')
        if not issue.hard:
            for node, catch in issue.catches:
                out.append(f'<div class="prob"><p>'
                           + ('' if node.key == pick else f'{name} uses {_label(state, node.key)}. ')
                           + f'{e(catch.text)}</p>'
                           + ('<p class="muted">Nothing to decide. What to do about it is on your checklist '
                              'for the target.</p>' if catch.task else
                              '<p class="muted">Nothing to decide.</p>') + '</div>')
        if issue.group == 'know':
            out.append('<div class="rowbtns">' + button('/leave', f'Leave out {state.label(pick)}', key=pick)
                       + '</div>')
        elif issue.group == 'kept':
            out.append('<h3 style="margin-top:6px">Your decision</h3><div class="rowbtns" style="margin-top:0">'
                       '<span class="lat-badge lat-badge--ok">✓ You chose to keep it in</span>'
                       + button('/leave', 'Leave it out after all', key=pick) + '</div>'
                       + ('<p class="sm muted" style="margin-top:10px">You said that what it needs is '
                          'already on the target. That is written in the report, and confirming it after '
                          'the import is on your checklist.</p>' if issue.hard else ''))
        else:
            out.append('<h3 style="margin-top:6px">Pick one</h3><div class="rowbtns" style="margin-top:0">'
                       + button('/leave', f'Leave out {state.label(pick)} (recommended)', primary=True,
                                key=pick, ident='leave-out', focus=True))
            if issue.hard:
                # One answer covers everything listed above, so it says so
                # when what is listed is more than the missing thing.
                also = bool(issue.stock or issue.unfinished)
                out.append(button('/fuller', 'Open an export that has what is missing…',
                                  want=_insight.missing_phrase(issue.hard[0][1])))
                out.append(button('/keep-in', ('I have checked what is missing already exists on the '
                                               'target, and I accept the rest above. Keep it in anyway'
                                               if also else
                                               'I have checked this already exists on the target. '
                                               'Keep it in anyway'), id=issue.id, ident='keep-in'))
                out.append('</div><p class="sm muted" style="margin-top:10px">Leaving it out only affects '
                           'this bundle. Everything else you chose still goes. If you open another export, '
                           'your choices are ticked again in it wherever the same things exist. If you keep '
                           'it in, your answer is written in the report and a line is added to your '
                           'checklist to confirm it on the target after the import.</p>')
            else:
                out.append(button('/keep-in', 'Keep it in anyway', id=issue.id, ident='keep-in') + '</div>')
    elif issue.id == 'todo':
        out.append('<p class="muted">Nothing to do now. After you save, the last step walks you through '
                   'these one at a time.</p><ol style="padding-left:20px;margin:0">'
                   + ''.join(f'<li style="margin:5px 0">{e(task.title)}</li>' for task in tasks) + '</ol>')
    else:
        included = state.selection_keys()
        chosen = list(state.picked)
        auto = [k for k in state.selection.keys if k not in state.selected_keys()]
        out.append(f'<h3>You chose ({len(chosen)})</h3><table class="lat-table"><tbody>')
        for key in chosen[:LIST_LIMIT]:
            out.append(f'<tr><td class="mid"><b>{_label(state, key)}</b> <span class="muted sm">'
                       f'{kind_one(graph.nodes[key].kind)}</span></td><td class="mid" style="text-align:right">'
                       + button('/leave', 'Leave out', key=key) + '</td></tr>')
        out.append('</tbody></table>')
        if len(chosen) > LIST_LIMIT:
            out.append(f'<p class="muted sm">and {len(chosen) - LIST_LIMIT} more. The Choose step lists them '
                       'all under "In your bundle".</p>')
        if auto:
            out.append(f'<h3 style="margin-top:14px">Added automatically ({len(auto)})</h3>'
                       '<table class="lat-table"><tbody>')
            for key in auto[:LIST_LIMIT]:
                out.append(f'<tr><td>{_label(state, key)} <span class="muted sm">'
                           f'{kind_one(graph.nodes[key].kind)}</span></td><td class="muted sm">needed by '
                           f'{e(state._names(state.needed_by(key, included)))}</td></tr>')
            out.append('</tbody></table>')
            if len(auto) > LIST_LIMIT:
                out.append(f'<p class="muted sm">and {len(auto) - LIST_LIMIT} more.</p>')
    return ''.join(out) + '</div>'


def review_screen(state) -> Tuple[str, str]:
    graph = state.graph
    rows = state.issues()
    tasks = state.tasks()
    open_ = [i for i in rows if i.group == 'must']
    kept = [i for i in rows if i.group == 'kept']
    if not any(i.id == state.issue for i in rows):
        state.issue = (open_[0] if open_ else next(i for i in rows if i.id == 'todo')).id
    current = next(i for i in rows if i.id == state.issue)
    unfinished = [graph.nodes[k] for k in state.selection.keys if graph.nodes[k].hollow]
    confirmed = state.accepted_missing()
    if open_:
        one = len(open_) == 1
        status = ('<div class="lat-card status"><span class="ico bad">!</span><div><div class="big">'
                  f'{plural(len(open_), "thing")} to decide before you can save</div><div class="muted sm">'
                  + ('It is' if one else 'They are') + ' at the top of the list. The app waits for your '
                  'answer because ' + ('it changes' if one else 'they change')
                  + ' what arrives on the target, and leaving something out now is easier than putting it '
                    'right there.</div></div></div>')
    else:
        text = ('Everything the export says this bundle needs is inside it.' if not confirmed else
                f'Everything the export says this bundle needs is inside it, except {plural(len(confirmed), "thing")} '
                'you confirmed '
                + ('is' if len(confirmed) == 1 else 'are') + ' already on the target.')
        if unfinished:
            names = e(listing([state.label(n.key) for n in unfinished]))
            one = len(unfinished) == 1
            text += (f' The exception is {names}: {"it" if one else "they"} never finished loading, so the '
                     f'export does not record what {"it needs" if one else "they need"}.')
        if kept:
            text += ' Kept in by your decision: ' + e(listing([state.label(i.pick) for i in kept])) + '.'
        status = ('<div class="lat-card status"><span class="ico ok">✓</span><div><div class="big">Ready to '
                  f'save</div><div class="muted sm">{text}</div></div></div>')
    items = ['<div class="ilist">']
    group = ''
    for issue in rows:
        if issue.group != group:
            group = issue.group
            items.append(f'<div class="grp">{_GROUP_TITLES[group]}</div>')
        items.append(f'<button type="button" class="{"on" if issue.id == state.issue else ""}" '
                     f'data-act="/issue" data-id="{e(issue.id)}" id="issue-{anchor(issue.id)}">{_dot(issue)}'
                     f'<span>{e(issue.title)}'
                     + (' <span class="muted sm">(you kept it in)</span>' if issue.group == 'kept' else '')
                     + '</span></button>')
    items.append('</div><p class="muted sm bsum" style="padding:12px 12px 0;margin:0"><b style="color:'
                 'var(--ink)">In the bundle:</b> '
                 + kind_counts(state.selection.counts(graph), KIND_ORDER) + '.</p>')
    main = (status + _note(state)
            + '<div class="split wide-right"><div class="lat-card panel"><div class="body" '
              f'data-scroll="issues">{"".join(items)}</div></div><div class="lat-card panel"><div class="body" '
              f'data-scroll="issue-{anchor(current.id)}">{_issue_detail(state, current, tasks)}</div></div></div>')
    foot = (button('/go', '← Back', to='owner' if state.dashboards() else 'choose')
            + '<span class="say">'
            + (f'Decide the {plural(len(open_), "thing")} at the top of the list to continue.' if open_ else
               'This was checked against the export only. The app cannot see the target.')
            + '</span>' + button('/go', 'Next: save →', primary=True, large=True, off=bool(open_),
                                 to='save', ident='next'))
    return main, foot


# ---------------------------------------------------------------------------
# 5. Save

def saving(state) -> Tuple[str, str]:
    count = len(state.selection.keys) if state.selection else 0
    main = ('<div class="center"><h2>Saving the bundle</h2><div class="lat-card working" role="status">'
            f'<span class="spin"></span><div><b>Building {e(state.job.name)}</b><div class="muted sm">Copying '
            f'{plural(count, "thing")} out of the export. The export itself is not changed.</div></div>'
            '</div></div>')
    return main, '<span class="say">This takes a few seconds.</span>'


def _folder_line(state) -> str:
    folder = state.save_folder
    home = Path.home()
    plain = _folder(folder)
    if folder.parent == home or folder == home:
        return e(plain)
    return f'{e(plain)} <span class="muted sm">({e(folder)})</span>'


def save_screen(state) -> Tuple[str, str]:
    graph = state.graph
    name, folder = state.save_name, _folder(state.save_folder)
    files = state.save_files()
    count = len(state.selection.keys)
    out = ['<div class="center">', _note(state, style='margin-bottom:12px')]
    if state.save_state == 'saved':
        described = [(files[0].name, 'The bundle. This is the file you import on the target.', ''),
                     (files[1].name, 'What is in the bundle and what was checked.',
                      button('/doc', 'Read the report', k='report', ident='read-report')),
                     (files[2].name, 'What to do on the target, to print or pass on.',
                      button('/doc', 'Read the checklist', k='checklist', ident='read-checklist'))]
        out.append('<div class="lat-card status" style="margin-bottom:12px"><span class="ico ok">✓</span>'
                   f'<div><div class="big">Bundle saved</div><div class="muted sm">{plural(count, "thing")} '
                   f'saved to your {e(folder)} folder. Nothing has been imported yet: that is the next step, '
                   'on the target.'
                   + (' Keep the export password you made up when you signed in: the target asks for it.'
                      if state.source_snapshot is not None else '') + '</div></div></div>'
                   f'<div class="lat-card"><h3>Three files were saved in {_folder_line(state)}</h3>'
                   '<table class="files"><tbody>'
                   + ''.join(f'<tr><td><b>{e(file)}</b><div class="muted sm">{what}</div></td>'
                             f'<td style="text-align:right;white-space:nowrap">{action}</td></tr>'
                             for file, what, action in described)
                   + '</tbody></table>'
                   + ('<div class="rowbtns">' + button('/show-in-folder', 'Show in folder', which='bundle')
                      + '</div>' if state.show_in_folder else '') + '</div>')
    else:
        out.append('<h2>Save the bundle</h2><p class="muted">The app writes a new, smaller ZIP with only '
                   'what you chose. It does not import anything. You do that on the target, in the next '
                   'step.</p>')
        if state.stale:
            out.append('<div class="lat-banner lat-banner--warn" role="status" style="margin-bottom:12px">'
                       '<b>You changed your choices after the last save.</b> The files saved earlier do not '
                       'include the change. Save again to replace them.</div>')
        if state.save_state == 'error':
            problem = state.save_problem
            if problem == 'same':
                text = (f'<b>That would overwrite the export you opened.</b> {e(name)} in {e(folder)} is '
                        'the export itself, and the app never changes it. Nothing was written. Choose a '
                        'different name, then save again.')
                fix = 'Save with a different name…'
            elif problem.startswith('build:'):
                text = ('<b>The bundle could not be built.</b> Nothing was written. '
                        f'{e(problem[6:])}. Go back to Review, leave out the dashboard it names, and save again. '
                        'If you cannot tell which one, save a support report from Help and settings.')
                fix = ''
            else:
                why = ('That folder does not allow new files.' if problem == 'denied'
                       else f'The computer reported: {e(problem[3:])}.')
                left = ('Nothing was written.' if not state.save_left else
                        'It stopped part-way: ' + e(listing(state.save_left))
                        + (' was' if len(state.save_left) == 1 else ' were')
                        + ' written and the rest was not, so do not use what is there.')
                text = (f'<b>Could not save in {e(folder)}.</b> {why} {left} Choose a '
                        'different folder, then save again.')
                fix = 'Choose a different folder…'
            out.append(f'<div class="lat-banner lat-banner--bad" role="alert" style="margin-bottom:12px">{text}'
                       + ('<div class="rowbtns">' + button('/pick-save', fix, primary=True, focus=True)
                          + '</div>' if fix else '') + '</div>')
        if state.save_state == 'exists':
            out.append('<div class="lat-banner lat-banner--warn" role="alert" style="margin-bottom:12px">'
                       f'<b>{e(folder)} already has a file called {e(state.save_problem)}.</b> Replacing it '
                       'overwrites the old file, its report and its checklist for good.<div class="rowbtns">'
                       + button('/pick-save', 'Save with a different name…', primary=True, focus=True)
                       + button('/save', 'Replace the old file', force='1', ident='replace') + '</div></div>')
        out.append(f'<div class="lat-card"><dl class="kv"><dt>Save as</dt><dd><b>{e(name)}</b></dd>'
                   f'<dt>In folder</dt><dd>{_folder_line(state)} &nbsp; '
                   + (button('/pick-save', 'Change name or folder…', ident='pick-save') if state.dialog else '')
                   + f'</dd><dt>What is inside</dt><dd>{e(_tasks.inside_line(graph, state.selection))}</dd>'
                     '<dt>Saved with it</dt><dd>A report of what is inside, and your checklist for the '
                     'target.</dd></dl></div>')
    out.append('</div>')
    back = button('/go', '← Back to review', to='review')
    if state.save_state == 'saved':
        foot = back + '<span class="say"></span>' + button('/go', 'Next: import on the target →',
                                                           primary=True, large=True, to='target', ident='next')
    else:
        foot = back + '<span class="say"></span>' + button(
            '/save', 'Save the bundle', primary=True, large=True, ident='save',
            off=state.save_state in ('error', 'exists'))
    return ''.join(out), foot


# ---------------------------------------------------------------------------
# 6. Import on the target

def target_screen(state) -> Tuple[str, str]:
    tasks = state.tasks()
    done = sum(1 for task in tasks if state.ticks.get(task.id))
    everything = done == len(tasks)
    folder = _folder(state.save_folder)
    stem = Path(state.save_name).stem
    if state.finished:
        left = len(tasks) - done
        main = ('<div class="center"><div class="lat-card status" style="margin-bottom:12px">'
                + ('<span class="ico ok">✓</span>' if everything else '<span class="ico info">…</span>')
                + '<div><div class="big">'
                + ('You have ticked everything' if everything
                   else f'Stopped for now, {plural(left, "task")} still to do')
                + '</div><div class="muted sm">'
                + ('Every item on the checklist is ticked. This app cannot see the target, so it is going '
                   'by your ticks.' if everything else
                   f'{plural(left, "checklist item is", "checklist items are")} still open. They are in '
                   'the saved checklist file, so you can do them later.')
                + '</div></div></div><div class="lat-card"><h3>Where things are</h3><p>The bundle, its report '
                  f'and the checklist are in your {e(folder)} folder. The export you opened was not changed.'
                  '</p><p class="muted">You can close the app now.</p><div class="rowbtns">'
                + button('/again', 'Make another bundle from this export')
                + button('/go', 'Open a different export', to='open')
                + button('/finish', 'Back to the checklist', on='0') + '</div></div></div>')
        return main, ''
    if not any(task.id == state.task for task in tasks):
        state.task = next((t for t in tasks if not state.ticks.get(t.id)), tasks[0]).id
    current = next(t for t in tasks if t.id == state.task)
    at = tasks.index(current)
    rows = []
    for number, task in enumerate(tasks):
        ticked = bool(state.ticks.get(task.id))
        rows.append(f'<div class="tk{" on" if task.id == state.task else ""}{" done" if ticked else ""}">'
                    f'<label class="c"><input type="checkbox" data-act="/tick" data-id="{e(task.id)}" '
                    f'id="tick-{anchor(task.id)}"{" checked" if ticked else ""} '
                    f'aria-label="Done: {e(task.title)}"></label><button type="button" data-act="/task" '
                    f'data-id="{e(task.id)}" id="task-{anchor(task.id)}">{number + 1}. {e(task.title)}'
                    '</button></div>')
    left = f'<div class="lat-card panel"><div class="body" data-scroll="tasks">{"".join(rows)}</div></div>'
    if state.ticks.get(current.id):
        answer = ('<span class="lat-badge lat-badge--ok">✓ Done</span>'
                  + button('/tick', 'Not done after all', id=current.id, on='0'))
    else:
        answer = button('/tick', 'I have done this', primary=True, id=current.id, on='1', ident='done')
    right = ('<div class="lat-card panel"><div class="body" data-scroll="task-' + anchor(current.id)
             + f'"><div class="detail"><div class="muted sm">Step {at + 1} of {len(tasks)}</div>'
             f'<h3 style="font-size:17px;margin:2px 0 8px">{e(current.title)}</h3><p>{e(current.why)}</p>'
             '<h3 style="margin-top:12px">How</h3><ol class="dosteps">'
             + ''.join(f'<li>{e(step)}</li>' for step in current.steps)
             + f'</ol><div class="rowbtns" style="margin-top:16px">{answer}</div></div></div></div>')
    lead = ('Every task is ticked.' if everything else
            'You have ticked the import. Work through the rest in order.' if state.ticks.get('import') else
            'The bundle is saved but not imported. Do these in order in the VCF Operations you are moving '
            'content into.')
    main = ('<div>' + _note(state, style='margin-bottom:8px')
            + f'<h2>Now, on the target</h2><p class="muted" style="margin:0">{lead} The same list is saved '
              f'as {e(stem)}-checklist.txt.</p></div><div class="split wide-right">{left}{right}</div>')
    foot = (button('/go', '← Back', to='save') + f'<span class="say"><b>{done} of {len(tasks)} done.</b> '
            + ('Nothing left to do.' if everything else
               'You can stop here and do the rest later from the saved checklist.') + '</span>'
            + button('/finish', 'Finish' if everything else 'Stop for now', primary=everything, large=True,
                     on='1', ident='finish'))
    return main, foot


# ---------------------------------------------------------------------------
# Help and settings

def help_screen(state) -> Tuple[str, str]:
    destination, destination_from = state.log_settings()[:2]
    log_on = bool(destination)
    fixed = destination_from in ('command line',) or destination_from.startswith('environment')
    log = ['<div class="field" style="margin:0"><label class="radio"><input type="checkbox" '
           'data-act="/log" id="log-on" style="width:20px;height:20px"'
           + (' checked' if log_on else '') + (' disabled' if fixed else '')
           + '><b>Keep a log file on this computer</b></label>']
    if log_on:
        where = Path(destination)
        log.append(f'<div class="sm" style="margin-top:4px">Writing to <b>{e(_folder(where.parent))} / '
                   f'{e(where.name)}</b>'
                   + (' <span class="muted">(set when the app was started, so it cannot be changed here)</span>'
                      if fixed else '') + '</div>')
    log.append('<div class="lat-hint" style="margin-top:4px">Only needed if support asks for it. A log file '
               'holds real names, so keep it private and send the support report instead.</div></div>')
    support = ['<div class="lat-card"><h3>Something not working?</h3><p>Save a support report and attach it '
               'when you report the problem.</p>', _note(state, style='margin-bottom:10px')]
    if state.report_saved:
        saved = Path(state.report_saved)
        support.append('<div class="lat-banner lat-banner--ok" role="status" style="margin-bottom:10px">'
                       f'<b>Saved {e(saved.name)} to your {e(_folder(saved.parent))} folder.</b>'
                       '<div class="rowbtns" style="margin-top:6px">'
                       + (button('/show-in-folder', 'Show in folder', which='report')
                          if state.show_in_folder else '')
                       + button('/support-report', 'Save another', ident='support-report') + '</div></div>')
    else:
        support.append('<div class="rowbtns" style="margin:0 0 10px">'
                       + button('/support-report', 'Save a support report (diagnostics)', primary=True,
                                ident='support-report') + '</div>')
    support.append('<p class="muted sm">It lists what the app did and how long each step took. Real names, '
                   'file locations and ids are swapped for stand-ins such as "dashboard 3". Passwords are '
                   'never recorded.</p><div class="rowbtns">'
                   + button('/report-problem', 'Report a problem (opens your browser)', ident='report-problem')
                   + '</div><p class="muted sm" style="margin:6px 0 12px">This goes to the project’s page '
                     'on GitHub. You need a free GitHub account to post there.</p>' + ''.join(log) + '</div>')
    version = e(__version__)
    if state.update_tag:
        latest = (f'Version {e(state.update_tag.lstrip("v"))} is available. '
                  + button('/get-update', 'Get it', ident='get-update'))
    elif state.update_checked and state.update_known:
        latest = 'That is the latest version.'
    else:
        latest = ''
    settings = ('<div class="lat-card"><h3>Settings</h3><div class="field" style="margin-bottom:6px">'
                '<span class="lat-label">Appearance</span><div style="display:flex">'
                + ''.join(f'<label class="radio"><input type="radio" name="theme" id="theme-{name}" '
                          f'data-act="/theme" data-choice="{name}"{" checked" if state.theme == name else ""}>'
                          f'{name.capitalize()}</label>' for name in ('light', 'dark'))
                + f'</div></div><p class="muted sm" style="margin:10px 0 0">You have version {version}. '
                  f'{latest}</p></div>')
    how = ('<div class="lat-card"><h3>How this works</h3><ol class="how" style="font-size:13.5px;'
           'color:var(--ink)"><li><b>Open an export</b> from the source.</li><li><b>Choose</b> what to '
           'move. What it depends on is added for you.</li><li><b>Pick an owner</b> for the dashboards.</li>'
           '<li><b>Review</b> anything that needs a decision.</li><li><b>Save</b> a small ZIP with just your '
           'choices.</li><li><b>Import</b> it on the target, following the checklist.</li></ol>'
           '<p class="muted sm" style="margin:8px 0 10px">The app never changes the source or the target. '
           'It reads an export and writes a new file.</p><h3>Not sure what a word means?</h3>'
           '<div class="rowbtns" style="margin-top:0">' + button('/gloss', 'What the words mean', ident='gloss')
           + '</div></div>')
    main = (f'<div class="helpgrid"><div class="hcol">{"".join(support)}</div><div class="hcol">{settings}'
            f'</div><div class="hcol">{how}</div></div>')
    return main, ''


# ---------------------------------------------------------------------------
# Overlays

def glossary_overlay() -> str:
    return ('<div class="pv-over" role="dialog" aria-label="What the words mean"><div class="lat-header">'
            '<h1>What the words mean</h1><span class="grow"></span>'
            + button('/gloss', 'Close', primary=True, ident='close-gloss', focus=True)
            + '</div><div class="pvbody"><dl class="gloss two">'
            + ''.join(f'<dt>{e(word)}</dt><dd>{e(meaning)}</dd>' for word, meaning in GLOSSARY)
            + '</dl></div></div>')


def preview_overlay(state) -> str:
    node = state.graph.nodes.get(state.preview_key)
    if node is None:
        return ''
    if node.hollow:
        picture = ('<div class="lat-banner lat-banner--warn">Nothing to draw. This dashboard never finished '
                   'loading on the source, so its widgets are empty.</div>')
    else:
        try:
            picture = _preview.fragment(state.graph, node, plain=True)
        except _preview.PreviewError:
            picture = ('<div class="lat-banner lat-banner--warn">This one cannot be drawn. The export does '
                       'not hold enough about how it looks.</div>')
    return ('<div class="pv-over" role="dialog" aria-label="Preview"><div class="lat-header">'
            f'<h1>{_label(state, node.key)}</h1><span class="lat-badge lat-badge--accent">Real layout, '
            'made-up numbers</span><span class="grow"></span>'
            + button('/close-preview', 'Close preview', primary=True, ident='close-preview', focus=True)
            + '</div><div class="pvbody" data-scroll="preview"><p class="muted sm">This is a picture to help '
              'you recognise it. The widget titles, columns and layout come from the export. The names and '
              f'numbers are invented, because the app has no live data.</p>{picture}</div></div>')


def _blocks_html(blocks) -> str:
    out = []
    for block in blocks:
        if block.kind == 'title':
            out.append(f'<h2>{e(block.text)}</h2>')
        elif block.kind == 'heading':
            out.append(f'<h3>{e(block.text)}</h3>')
        elif block.kind == 'text':
            out.append(f'<p>{e(block.text)}</p>')
        elif block.kind == 'muted':
            out.append(f'<p class="muted">{e(block.text)}</p>')
        elif block.kind == 'list':
            out.append('<ul>' + ''.join(f'<li>{e(item)}</li>' for item in block.items) + '</ul>')
        elif block.kind == 'steps':
            out.append('<ol>' + ''.join(f'<li>{e(item)}</li>' for item in block.items) + '</ol>')
    return ''.join(out)


def doc_overlay(state) -> str:
    stem = Path(state.save_name).stem
    if state.doc == 'checklist':
        tasks = state.tasks()
        text = (f'<h2>Checklist for the target</h2><p class="muted">For {e(state.save_name)}</p><ol>'
                + ''.join(f'<li style="margin:10px 0"><b>{e(task.title)}</b><div>{e(task.why)}</div>'
                          '<ol type="a" style="margin-top:4px">'
                          + ''.join(f'<li>{e(step)}</li>' for step in task.steps) + '</ol></li>'
                          for task in tasks) + '</ol>')
        file = stem + '-checklist.txt'
    else:
        text = (_blocks_html(state.report_blocks())
                + '<p class="muted">The saved file ends with a technical section for support.</p>')
        file = stem + '-report.txt'
    return ('<div class="pv-over" role="dialog" aria-label="Saved file"><div class="lat-header">'
            f'<h1>{e(file)}</h1><span class="lat-badge">Saved in {e(_folder(state.save_folder))}</span>'
            '<span class="grow"></span>'
            + button('/close-doc', 'Close', primary=True, ident='close-doc', focus=True)
            + '</div><div class="pvbody" data-scroll="doc"><div class="lat-card center doc" '
              f'style="max-width:820px">{text}</div></div></div>')


def ask_overlay(state) -> str:
    ask = state.ask
    count = len(state.picked)
    if ask['kind'] == 'clear':
        return ('<div class="veil"><div class="dlg" role="alertdialog" aria-modal="true" aria-label="Empty '
                'the bundle?"><h3>Empty the bundle?</h3><p>This unticks '
                + ('the one thing' if count == 1 else f'all {count} things')
                + ' you chose. Nothing is deleted from the export.</p><div class="rowbtns">'
                + button('/ask-no', 'Keep my choices', primary=True, ident='ask-no', focus=True)
                + button('/ask-yes', 'Empty the bundle', ident='ask-yes') + '</div></div></div>')
    name, same = e(ask['name']), ask.get('same')
    return ('<div class="veil"><div class="dlg" role="alertdialog" aria-modal="true" aria-label="Switch '
            f'export?"><h3>{f"Read {name} again?" if same else f"Switch to {name}?"}</h3><p>'
            + ('This is the export you already have open. Reading it again picks up any changes to the file.'
               if same else f'It was read successfully. Switching closes {e(state.source_name)}.')
            + f' Your {plural(count, "choice is", "choices are")} ticked again wherever the same things '
              'exist in it.</p><div class="rowbtns">'
            + button('/ask-no', 'No, keep working' if same else f'Keep working on {state.source_name}',
                     primary=True, ident='ask-no', focus=True)
            + button('/ask-yes', 'Read it again' if same else 'Switch', ident='ask-yes') + '</div></div></div>')


VIEWS = {
    'open': open_screen,
    'connect': connect_screen,
    'choose': choose_screen,
    'owner': owner_screen,
    'review': review_screen,
    'save': save_screen,
    'target': target_screen,
    'help': help_screen,
}
