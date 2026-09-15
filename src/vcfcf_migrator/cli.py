"""``vcfcf-migrator`` command line.

Commands: ``version``, ``inspect``, ``tree``, ``preview``, ``build``,
``corpus-check``, ``ui``. Every option here has a control on the ``ui`` page (house rule:
every setting has a GUI option).

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
from vcfcf_migrator import settings as _settings
from vcfcf_migrator.rawdoc import RawDocError
from vcfcf_migrator.export_reader import (
    VERSION_FLOOR_TEXT,
    BadSourceVersion,
    NotAnExport,
    UnsupportedExport,
    read_export,
    read_members,
    render_text,
)

SPEC_POINTER = "knowledge/designs/content-migrator-v1.md in the factory repo"


def version_lines() -> List[str]:
    return [
        f"vcfcf-migrator {__version__}",
        f"vcfcf_core {vcfcf_core.__version__} (vcf-cf-tooling-core)",
    ]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vcfcf-migrator",
        description="Move custom VCF Operations content between instances from a content export zip.",
    )
    p.add_argument("--corpus", metavar="DIR", default=None,
                   help=f"corpus directory holding real export zips (also {_settings.ENV_CORPUS}; default ./corpus)")
    p.add_argument("--source-version", metavar="X.Y[.Z]", default=None,
                   help="VCF Operations version the export came from, for example 8.18.7; exports carry none, "
                        f"so you declare it (also {_settings.ENV_SOURCE_VERSION}, or the ui page). "
                        f"Floor {VERSION_FLOOR_TEXT}; without it inspect continues and build refuses")
    sub = p.add_subparsers(dest="command", metavar="command")

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

    sp = sub.add_parser("ui", help="serve the local page on 127.0.0.1 and open the browser")
    sp.add_argument("zip", nargs="?", help="export zip to show on the page")
    sp.add_argument("--port", type=int, default=0, help="listen port (default: a free one)")
    sp.add_argument("--no-browser", action="store_true", help="do not open the browser")
    return p


def cmd_version(_args) -> int:
    print("\n".join(version_lines()))
    return 0


def cmd_inspect(args) -> int:
    declared, _source = _settings.source_version(args.source_version)
    try:
        export = read_export(args.zip, source_version=declared)
    except BadSourceVersion as e:
        print(f"vcfcf-migrator inspect: {e}", file=sys.stderr)
        return 2
    except (NotAnExport, UnsupportedExport) as e:
        print(f"vcfcf-migrator inspect: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(export.as_dict(), indent=2))
    else:
        sys.stdout.write(render_text(export))
    return 0


def _load_graph(zip_path, declared):
    """The export's members and the graph over them, read once."""
    members = read_members(zip_path, source_version=declared)
    return members, _graph.build_graph(members.data)


def cmd_tree(args) -> int:
    declared, _source = _settings.source_version(args.source_version)
    try:
        _members, graph = _load_graph(args.zip, declared)
    except BadSourceVersion as e:
        print(f"vcfcf-migrator tree: {e}", file=sys.stderr)
        return 2
    except (NotAnExport, UnsupportedExport, RawDocError) as e:
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

    declared, _source = _settings.source_version(args.source_version)
    try:
        _members, graph = _load_graph(args.zip, declared)
    except BadSourceVersion as e:
        print(f"vcfcf-migrator preview: {e}", file=sys.stderr)
        return 2
    except (NotAnExport, UnsupportedExport, RawDocError) as e:
        print(f"vcfcf-migrator preview: {e}", file=sys.stderr)
        return 1

    keys = _selection.match_line(graph, args.object)
    if not keys:
        print(f"vcfcf-migrator preview: this export carries no object named "
              f"{args.object!r}", file=sys.stderr)
        return 1
    if len(keys) > 1:
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
        print(f"vcfcf-migrator preview: {e}", file=sys.stderr)
        return 1

    if args.to_stdout:
        sys.stdout.write(html_text)
        return 0
    out = Path(args.out) if args.out else Path(preview_filename(node))
    if out.parent and str(out.parent):
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    print(str(out))
    return 0


def cmd_build(args) -> int:
    declared, _source = _settings.source_version(args.source_version)
    if bool(args.select) == bool(args.select_all):
        print("vcfcf-migrator build: pass exactly one of --select FILE or --select-all",
              file=sys.stderr)
        return 2
    if declared is None:
        print("vcfcf-migrator build: refused, no source version declared. An export carries "
              f"none, so declare it with --source-version (floor {VERSION_FLOOR_TEXT})",
              file=sys.stderr)
        return 1
    try:
        members, graph = _load_graph(args.zip, declared)
    except BadSourceVersion as e:
        print(f"vcfcf-migrator build: {e}", file=sys.stderr)
        return 2
    except (NotAnExport, UnsupportedExport, RawDocError) as e:
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
        print("vcfcf-migrator build: the selection is empty, no bundle written", file=sys.stderr)
        return 1

    result = _bundle.build_bundle(members.data, members.order, graph, picked,
                                  args.out, marker=members.marker)
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
    declared, _src = _settings.source_version(args.source_version)
    return run(directory, source, declared, sys.stdout)


def cmd_ui(args) -> int:
    from vcfcf_migrator.ui import serve

    return serve(zip_path=args.zip, port=args.port, open_browser=not args.no_browser,
                 corpus_cli=args.corpus, source_version_cli=args.source_version)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "version":
        return cmd_version(args)
    if args.command == "inspect":
        return cmd_inspect(args)
    if args.command == "tree":
        return cmd_tree(args)
    if args.command == "preview":
        return cmd_preview(args)
    if args.command == "build":
        return cmd_build(args)
    if args.command == "corpus-check":
        return cmd_corpus_check(args)
    if args.command == "ui":
        return cmd_ui(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
