"""``vcfcf-migrator`` command line.

Commands: ``version``, ``inspect``, ``tree``, ``preview``, ``build``,
``corpus-check``, ``ui``, ``log-render``. Every option here has a control on the ``ui``
page (house rule: every setting has a GUI option).

Every command can write a run log: ``--log FILE`` (``-`` for stderr), with
``--log-level`` and ``--log-format``. The log is off until it is asked for,
and what it may and may not carry is ``vcfcf_migrator.runlog``.

Exit codes: 0 ok, 1 refused or unreadable input, 2 usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

import vcfcf_core

from vcfcf_migrator import __version__
from vcfcf_migrator import bundle as _bundle
from vcfcf_migrator import graph as _graph
from vcfcf_migrator import selection as _selection
from vcfcf_migrator import runlog as _runlog
from vcfcf_migrator import settings as _settings
from vcfcf_migrator.rawdoc import RawDocError
from vcfcf_migrator.export_reader import (
    NotAnExport,
    read_export,
    read_members,
    render_text,
)

SPEC_POINTER = "knowledge/designs/content-migrator-v1.md in the factory repo"


def version_lines() -> List[str]:
    """What this build is, and what it can do on this machine.

    The window line is not decoration. A one-file binary can be built without
    the webview backend inside it, and the only visible symptom is that ``ui``
    quietly opens a browser instead: the feature is gone and nothing says so.
    Printing it here gives the release smoke something to assert against, so a
    build that lost the window fails in CI rather than in front of a user.
    """
    from vcfcf_migrator import desktop

    ok, why = desktop.available()
    window = "available" if ok else f"unavailable ({desktop.short_reason(why)})"
    return [
        f"vcfcf-migrator {__version__}",
        f"vcfcf_core {vcfcf_core.__version__} (vcf-cf-tooling-core)",
        f"native window: {window}",
    ]


class _SubParser(argparse.ArgumentParser):
    """Every subcommand gets the log flags, without thirteen ``parents=``."""

    parents: List[argparse.ArgumentParser] = []

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("parents", list(_SubParser.parents))
        super().__init__(*args, **kwargs)


def log_flags() -> argparse.ArgumentParser:
    """The options every subcommand shares: the corpus directory and the three
    log settings.

    They sit on the main parser *and* on every subcommand, because
    ``ui my-export.zip --log run.log`` is a shape an admin types, and argparse
    refuses an option after the subcommand unless the subcommand has it too.
    That command failed with "unrecognized arguments" until this parent carried
    them. The subcommand copies default to SUPPRESS, so a flag given before the
    subcommand is not overwritten by the subcommand's own default.
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--corpus", metavar="DIR", default=argparse.SUPPRESS,
                        help=f"corpus directory holding real export zips (also "
                             f"{_settings.ENV_CORPUS}; default ./corpus)")
    parent.add_argument("--log", metavar="FILE", default=argparse.SUPPRESS,
                        help=f"write a run log to FILE (- for stderr; also {_runlog.ENV_LOG})")
    parent.add_argument("--log-level", metavar="LEVEL", default=argparse.SUPPRESS,
                        choices=_runlog.LEVEL_NAMES,
                        help="how much of the run to log: " + ", ".join(_runlog.LEVEL_NAMES)
                             + f" (default {_runlog.DEFAULT_LEVEL})")
    parent.add_argument("--log-format", metavar="FORMAT", default=argparse.SUPPRESS,
                        choices=_runlog.FORMATS,
                        help="jsonl, one JSON object per line, or text, the same events as "
                             f"lines a person reads (default {_runlog.DEFAULT_FORMAT})")
    return parent


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vcfcf-migrator",
        description="Move custom VCF Operations content between instances from a content export zip.",
    )
    p.add_argument("--corpus", metavar="DIR", default=None,
                   help=f"corpus directory holding real export zips (also {_settings.ENV_CORPUS}; default ./corpus)")
    p.add_argument("--log", metavar="FILE", default=None,
                   help=f"write a run log to FILE (- for stderr; also {_runlog.ENV_LOG}, "
                        "or the ui page). Off until asked for")
    p.add_argument("--log-level", metavar="LEVEL", default=None, choices=_runlog.LEVEL_NAMES,
                   help="how much of the run to log: "
                        + ", ".join(_runlog.LEVEL_NAMES)
                        + f" (default {_runlog.DEFAULT_LEVEL}; also {_runlog.ENV_LOG_LEVEL})")
    p.add_argument("--log-format", metavar="FORMAT", default=None, choices=_runlog.FORMATS,
                   help="jsonl, one JSON object per line, or text, the same events as "
                        f"lines a person reads (default {_runlog.DEFAULT_FORMAT}; also "
                        f"{_runlog.ENV_LOG_FORMAT})")
    logs = log_flags()
    sub = p.add_subparsers(dest="command", metavar="command", parser_class=_SubParser)
    sub.required = False
    _SubParser.parents = [logs]

    sub.add_parser("version", help="print the tool and library versions")

    sp = sub.add_parser("inspect", help="list every content item in an export zip")
    sp.add_argument("zip", help="path to the content export zip")
    sp.add_argument("--json", action="store_true", help="emit the listing as JSON")

    sp = sub.add_parser("tree", help="show the dependency tree over the export's own documents")
    sp.add_argument("zip", help="path to the content export zip")
    sp.add_argument("--json", action="store_true", help="emit the tree as JSON")

    sp = sub.add_parser("preview", help="write an HTML preview of one object so it can be "
                                        "recognised before it is carried")
    sp.add_argument("zip", help="path to the content export zip")
    sp.add_argument("object", metavar="OBJECT",
                    help="the object: a uuid, kind:uuid, kind:name, or dashboard:uuid@owner")
    sp.add_argument("--out", metavar="FILE",
                    help="where to write the HTML (default: preview-<kind>-<id>.html here)")
    sp.add_argument("--print", dest="to_stdout", action="store_true",
                    help="write the HTML to stdout instead of to a file")

    sp = sub.add_parser("build", help="write an import bundle carrying only the closed selection")
    sp.add_argument("zip", help="path to the content export zip")
    sp.add_argument("--select", metavar="FILE",
                    help="selection file: one uuid or kind:uuid per line, # comments allowed")
    sp.add_argument("--select-all", action="store_true",
                    help="carry every content object the export holds")
    sp.add_argument("--out", metavar="BUNDLE", required=True, help="bundle zip to write")
    sp.add_argument("--json", action="store_true", help="emit the build report as JSON")

    sp = sub.add_parser("corpus-check",
                        help="run inspect, tree and a select-all build over every zip in a directory")
    sp.add_argument("dir", nargs="?", help="corpus directory (default: the corpus setting)")

    sp = sub.add_parser("log-render", help="print a run log written as jsonl as the lines "
                                           "a person reads")
    sp.add_argument("file", help="the log file, or - for stdin")

    sp = sub.add_parser("ui", help="open the page in a window (or the browser with --server)")
    sp.add_argument("zip", nargs="?", help="export zip to show on the page")
    sp.add_argument("--server", action="store_true",
                    help="use the browser instead: serve on 127.0.0.1 and open it")
    sp.add_argument("--port", type=int, default=0,
                    help="listen port for --server (default: a free one)")
    sp.add_argument("--no-browser", action="store_true",
                    help="with --server, do not open the browser")
    return p


def cmd_version(_args) -> int:
    print("\n".join(version_lines()))
    return 0


def cmd_inspect(args) -> int:
    try:
        export = read_export(args.zip)
    except NotAnExport as e:
        print(f"vcfcf-migrator inspect: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(export.as_dict(), indent=2))
    else:
        sys.stdout.write(render_text(export))
    return 0


def _load_graph(zip_path):
    """The export's members and the graph over them, read once."""
    members = read_members(zip_path)
    return members, _graph.build_graph(members.data)


def cmd_tree(args) -> int:
    try:
        _members, graph = _load_graph(args.zip)
    except (NotAnExport, RawDocError) as e:
        print(f"vcfcf-migrator tree: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(_graph.as_dict(graph), indent=2))
    else:
        sys.stdout.write(_graph.render_tree(graph))
    return 0


def preview_filename(node) -> str:
    """The default file name for a preview: the kind, and an identifier a file
    system will take. Names go through the same reduction, because a display
    name can carry a slash, a colon or a script that no file system agrees on."""
    stem = node.uuid or node.ident or node.name
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in str(stem)).strip("-")
    tail = f"-{node.owner[:8]}" if node.kind == "dashboard" and node.owner else ""
    return f"preview-{node.kind}-{safe or 'object'}{tail}.html"


def cmd_preview(args) -> int:
    from vcfcf_migrator import preview as _preview

    try:
        _members, graph = _load_graph(args.zip)
    except (NotAnExport, RawDocError) as e:
        print(f"vcfcf-migrator preview: {e}", file=sys.stderr)
        return 1

    keys = _selection.match_line(graph, args.object)
    if not keys:
        _runlog.error("command.refused", command="preview", asked_for=str(args.object),
                      reason=_runlog.prose("this export carries no object of that name or uuid"))
        print(f"vcfcf-migrator preview: this export carries no object named "
              f"{args.object!r}", file=sys.stderr)
        return 1
    if len(keys) > 1:
        _runlog.error("command.refused", command="preview", asked_for=str(args.object),
                      matches=len(keys),
                      reason=_runlog.prose("the spelling names more than one object, and picking one "
                             "would preview an object the admin did not ask for"))
        # One uuid under two owners, or one name two objects answer to. Picking
        # one would preview an object the admin did not ask for, so it says
        # which spellings name exactly one.
        print(f"vcfcf-migrator preview: {args.object!r} names {len(keys)} objects; "
              "say which with one of: " + ", ".join(keys), file=sys.stderr)
        return 1
    node = graph.nodes[keys[0]]
    try:
        html_text = _preview.render_page(graph, node)
    except _preview.PreviewError as e:
        _runlog.error("command.failed", command="preview", kind=node.kind,
                      uuid=node.uuid or "", name=node.name, reason=str(e))
        print(f"vcfcf-migrator preview: {e}", file=sys.stderr)
        return 1

    if args.to_stdout:
        sys.stdout.write(html_text)
        return 0
    out = Path(args.out) if args.out else Path(preview_filename(node))
    try:
        if out.parent and str(out.parent):
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html_text, encoding="utf-8")
    except OSError as e:
        # Every other refusal in this CLI is a message and an exit code; an
        # unwritable path should not be the one that gives a traceback.
        _runlog.error("output.unwritable", command="preview", path=str(out), reason=str(e))
        print(f"vcfcf-migrator preview: cannot write {out}: {e}", file=sys.stderr)
        return 1
    _runlog.info("preview.written", path=str(out), bytes=len(html_text),
                 kind=node.kind, uuid=node.uuid or "", name=node.name)
    print(str(out))
    return 0


def cmd_build(args) -> int:
    if bool(args.select) == bool(args.select_all):
        _runlog.error("command.refused", command="build",
                      reason=_runlog.prose("pass exactly one of --select FILE or --select-all"))
        print("vcfcf-migrator build: pass exactly one of --select FILE or --select-all",
              file=sys.stderr)
        return 2
    try:
        members, graph = _load_graph(args.zip)
    except (NotAnExport, RawDocError) as e:
        print(f"vcfcf-migrator build: {e}", file=sys.stderr)
        return 1

    try:
        if args.select_all:
            picked = _selection.select_all(graph)
        else:
            lines = _selection.parse_selection_file(args.select)
            picked = _selection.close(graph, _selection.resolve(graph, lines))
    except _selection.BadSelection as e:
        print(f"vcfcf-migrator build: {e}", file=sys.stderr)
        print("vcfcf-migrator build: no bundle written", file=sys.stderr)
        return 1
    if not picked.keys:
        _runlog.error("command.refused", command="build",
                      reason=_runlog.prose("the selection is empty, so no bundle is written"))
        print("vcfcf-migrator build: the selection is empty, no bundle written", file=sys.stderr)
        return 1

    try:
        result = _bundle.build_bundle(members.data, members.order, graph, picked,
                                      args.out, marker=members.marker,
                                      directories=members.directories,
                                      directory_order=members.directory_order)
    except OSError as e:
        print(f"vcfcf-migrator build: cannot write {args.out}: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"selection": _selection.as_dict(graph, picked),
                          "build": result.as_dict()}, indent=2))
    else:
        sys.stdout.write(_selection.render(graph, picked))
        sys.stdout.write(_bundle.render(result))
    return 0


def cmd_corpus_check(args) -> int:
    from vcfcf_migrator.corpus_check import run

    directory, source = _settings.corpus_dir(args.dir or args.corpus)
    return run(directory, source, sys.stdout)


def cmd_log_render(args) -> int:
    """A captured jsonl log as the lines a person reads. The rendering lives
    with the log rather than in a separate tool, so the two cannot drift."""
    if args.file == "-":
        sys.stdout.write(_runlog.render_log(sys.stdin.read().splitlines()))
        return 0
    try:
        text = Path(args.file).read_text(encoding="utf-8")
    except OSError as e:
        print(f"vcfcf-migrator log-render: cannot read {args.file}: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(_runlog.render_log(text.splitlines()))
    return 0


def cmd_ui(args) -> int:
    from vcfcf_migrator import desktop
    from vcfcf_migrator.ui import run_desktop, serve

    # The page is another way in to the same commands, so it takes the same
    # log settings: a flag the tool accepts and ignores is worse than one it
    # refuses.
    log_kwargs = dict(corpus_cli=args.corpus,
                      log_cli=getattr(args, "log", None),
                      log_level_cli=getattr(args, "log_level", None),
                      log_format_cli=getattr(args, "log_format", None))
    if not args.server:
        ok, why = desktop.available()
        if ok:
            return run_desktop(zip_path=args.zip, **log_kwargs)
        # Falling back silently would leave an admin wondering why the tool
        # they were told opens a window opened a browser instead, and a
        # listening socket would appear on a machine whose owner may have
        # reasons to care.
        print(f"vcfcf-migrator ui: no native window available here ({why});"
              " falling back to the browser, which binds a local port."
              " Use --server to ask for this without the warning.",
              file=sys.stderr)
    return serve(zip_path=args.zip, port=args.port, open_browser=not args.no_browser,
                 **log_kwargs)


COMMANDS = {
    "version": cmd_version,
    "inspect": cmd_inspect,
    "tree": cmd_tree,
    "preview": cmd_preview,
    "build": cmd_build,
    "corpus-check": cmd_corpus_check,
    "log-render": cmd_log_render,
    "ui": cmd_ui,
}


def open_run_log(args, argv: List[str]) -> _runlog.Log:
    """The run log for this invocation, with its header already written.

    Opened here rather than inside each command, so every command logs the
    same header and no command can be the one that forgets.
    """
    destination, _from = _runlog.resolve_destination(getattr(args, "log", None))
    if not destination:
        return _runlog.NULL
    level, level_from = _runlog.resolve_level(getattr(args, "log_level", None))
    fmt, _fmt_from = _runlog.resolve_format(getattr(args, "log_format", None))
    log = _runlog.open_log(destination, level=level, fmt=fmt)
    _runlog.set_current(log)
    corpus, corpus_from = _settings.corpus_dir(args.corpus)
    log.header(argv, tool_version=__version__, core_version=vcfcf_core.__version__,
               corpus_dir=corpus, corpus_from=corpus_from)
    _runlog.detail("log.level", level=level, level_from=level_from)
    return log


def say_what_is_ignored() -> None:
    """Name any environment variable that is set and no longer does anything.

    A flag this tool has dropped is a loud usage error, because argparse
    refuses what it does not know. An environment variable cannot be refused
    the same way without making the tool unrunnable for anyone who exported it
    in a shell profile, which is a permanent cost for a variable that is
    merely inert. So it is said once, on stderr, and the run carries on.
    """
    for name, why in _settings.obsolete_env():
        print(f"vcfcf-migrator: {name} is set and is no longer used: {why}",
              file=sys.stderr)
        _runlog.warn("setting.ignored", setting=name, reason=_runlog.prose(why))


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    given = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        log = open_run_log(args, given)
    except (_runlog.BadLogSetting, OSError) as e:
        print(f"vcfcf-migrator: {e}", file=sys.stderr)
        return 2
    say_what_is_ignored()
    command = COMMANDS.get(args.command)
    if command is None:
        parser.print_help()
        return 2
    code, failed = 2, None
    try:
        # The phase is called "command" rather than the command's own name:
        # "build" would otherwise name both this phase and the bundle writer's,
        # and two phases with one name are two phases nobody can tell apart in
        # the log.
        with log.phase("command", command=args.command):
            code = command(args)
            log.count("exit", code)
        return code
    except Exception as e:  # noqa: BLE001 - logged, then raised as it was
        failed = e
        _runlog.error("run.crashed", failure=type(e).__name__, detail=str(e),
                      command=args.command)
        raise
    finally:
        log.finish(code, what=args.command, failed=failed)
        log.close()
        _runlog.set_current(None)


if __name__ == "__main__":
    sys.exit(main())
