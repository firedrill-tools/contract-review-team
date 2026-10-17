"""Private model-free document import/inspection/revision; no runtime capability."""

import argparse
import json

from .domain import make_document
from .managed import ManagedSource
from .strict import canonical, parse_json, read_original


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--db", required=True)
    inputs = init.add_mutually_exclusive_group()
    inputs.add_argument("--contract")
    inputs.add_argument("--fixture")
    init.add_argument("--document-id", default="D-701")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--db", required=True)
    intent = commands.add_parser("intent")
    intent.add_argument("--db", required=True)
    intent.add_argument("--document-id", required=True)
    intent.add_argument("--request", required=True)
    update = commands.add_parser("replace")
    update.add_argument("--db", required=True)
    update.add_argument("--document-id", required=True)
    update.add_argument("--contract", required=True)
    update.add_argument("--source-json")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            if args.fixture:
                source = ManagedSource.seed(args.db, args.fixture)
            elif args.contract:
                raw, _ = read_original(args.contract)
                fixture = {
                    "documents": [make_document(args.document_id, raw)],
                    "reviews": [],
                    "audit": [],
                }
                source = ManagedSource.seed_bytes(
                    args.db, canonical(fixture).encode("utf-8", errors="strict")
                )
            else:
                from .assets import read_asset

                source = ManagedSource.seed_bytes(args.db, read_asset("starter.json"))
        else:
            source = ManagedSource(args.db)
        try:
            if args.command == "intent":
                result = source.intent(args.document_id, args.request)
            elif args.command == "replace":
                raw, _ = read_original(args.contract)
                before = (
                    parse_json(args.source_json.encode("utf-8", errors="strict"))
                    if args.source_json
                    else None
                )
                result = source.replace_document(args.document_id, raw, source=before)
            else:
                result = source.snapshot()
        finally:
            cleanup = source.close()
            if not cleanup["database_closed"] or not cleanup["descriptor_closed"]:
                raise RuntimeError("private contract source cleanup unconfirmed")
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
