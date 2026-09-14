"""``vcfcf-migrator`` command line.

Commands: ``version``, ``inspect``, ``tree``, ``build``, ``corpus-check``,
``ui``. In M3 only ``version``, ``inspect`` and ``ui`` do work; the rest exit
2 with a message pointing at the spec. Every option here has a control on
the ``ui`` page (house rule: every setting has a GUI option).

Exit codes: 0 ok, 1 refused or unreadable input, 2 usage or not implemented.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

import vcfcf_core

from vcfcf_migrator import __version__
from vcfcf_migrator import settings as _settings
from vcfcf_migrator.export_reader import (
    VERSION_FLOOR_TEXT,
    BadSourceVersion,
    NotAnExport,
    UnsupportedExport,
    read_export,
    render_text,
)

SPEC_POINTER = "knowledge/designs/content-migrator-v1.md in the factory repo"
NOT_IMPLEMENTED = {"tree", "build", "corpus-check"}


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

    sp = sub.add_parser("tree", help="show the dependency tree of an export (M4)")
    sp.add_argument("zip", nargs="?", help="path to the content export zip")

    sp = sub.add_parser("build", help="write an import bundle from a selection (M4)")
    sp.add_argument("zip", nargs="?", help="path to the content export zip")
    sp.add_argument("--select", metavar="FILE", help="selection file")
    sp.add_argument("--out", metavar="BUNDLE", help="bundle zip to write")

    sub.add_parser("corpus-check", help="run inspect, tree and build over every zip in the corpus (M5)")

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


def cmd_not_implemented(args) -> int:
    print(f"vcfcf-migrator {args.command}: not implemented in M3, see spec ({SPEC_POINTER})", file=sys.stderr)
    return 2


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
    if args.command == "ui":
        return cmd_ui(args)
    if args.command in NOT_IMPLEMENTED:
        return cmd_not_implemented(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
