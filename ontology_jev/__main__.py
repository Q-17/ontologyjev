"""Run with python -m ontology_jev (no install required from the repository)."""

import argparse
import json
from pathlib import Path

from .engine import compare
from .fixtures import BASELINE_POLICY, DRAFT_POLICY, load_cases
from .regressions import run_regressions


def main(argv=None):
    parser = argparse.ArgumentParser(description="OntologyJev: inspect decisions before your agent acts.")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("demo", help="Replay the 20 synthetic examples in your terminal")
    serve = commands.add_parser("serve", help="Open a local decision workbench")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--storage-dir", type=Path, default=Path(".ontologyjev"))
    regression = commands.add_parser("regressions", help="Check locally saved decision snapshots")
    regression.add_argument("--storage-dir", type=Path, default=Path(".ontologyjev"))
    args = parser.parse_args(argv)
    if args.command == "demo":
        result = compare(load_cases(), BASELINE_POLICY, DRAFT_POLICY)
        print("OntologyJev | Synthetic fixtures | No live model or payment calls")
        print("Refund window: 7 -> 14 days. The same intent observations are replayed.")
        for row in result["rows"]:
            if row["changed"]:
                print("  {id}: {before} -> {after} | {title}".format(
                    id=row["case"]["id"], before=row["before"]["outcome"],
                    after=row["after"]["outcome"], title=row["case"]["title"]))
        print(json.dumps(result["summary"], indent=2))
        print("Try the workbench: python -m ontology_jev serve")
    elif args.command == "serve":
        from .server import create_server
        with create_server(port=args.port, storage_dir=args.storage_dir) as server:
            print("OntologyJev: http://127.0.0.1:%d" % server.server_port, flush=True)
            print("Synthetic fixtures by default. No action executes a payment.", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    elif args.command == "regressions":
        report = run_regressions(args.storage_dir)
        print(json.dumps(report, indent=2))
        return int(report["failed"] > 0)
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
