"""
Command line tools, run with `python -m firedantic` or `firedantic`.

    firedantic export-indexes myapp.models                         # print a new file
    firedantic export-indexes myapp.models --update firestore.indexes.json
    firedantic export-indexes myapp.models --check firestore.indexes.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from firedantic.index_export import find_models, merge_firestore_indexes


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="firedantic")
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser(
        "export-indexes",
        help="Export the indexes and TTL policies declared in models to firestore.indexes.json.",
        description=(
            "Export the __composite_indexes__, __field_indexes__ and __ttl_field__ of the "
            "models in the given modules in the firestore.indexes.json format. Import the "
            "module that configures firedantic first, or list it, since collection names "
            "include the configured prefix."
        ),
    )
    export.add_argument("modules", nargs="+", help="Modules that define the models.")
    export.add_argument("--database", help="Only export models that use this database.")
    mode = export.add_mutually_exclusive_group()
    mode.add_argument(
        "--update", metavar="FILE", help="Add missing declared indexes to FILE, keeping the rest."
    )
    mode.add_argument(
        "--check",
        metavar="FILE",
        help="Exit with status 1 if FILE is missing any declared index, without changing it.",
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(Path.cwd()))
    models = find_models(args.modules)
    path = Path(args.update or args.check) if (args.update or args.check) else None
    existing = json.loads(path.read_text()) if path and path.exists() else {}
    merged, changes = merge_firestore_indexes(existing, models, args.database)

    if args.check:
        if changes:
            print(f"{args.check} is missing declared indexes:", file=sys.stderr)
            for change in changes:
                print(f"  Missing {change}", file=sys.stderr)
            print(f"Run with --update {args.check} to add them.", file=sys.stderr)
            return 1
        print(f"{args.check} has all declared indexes.")
        return 0

    output = json.dumps(merged, indent=2) + "\n"
    if args.update:
        Path(args.update).write_text(output)
        for change in changes:
            print(f"Added {change}")
        print(f"Updated {args.update}: added {len(changes)} entries or settings.")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
