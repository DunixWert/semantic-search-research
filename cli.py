from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import Library


def default_library_root() -> Path:
    # 1. Workspace root (honours structure)
    parent_workspace = Path(__file__).resolve().parents[2] / "LiteratureLibrary"
    if parent_workspace.exists():
        return parent_workspace
    # 2. Local cwd LiteratureLibrary
    cwd_lib = Path.cwd() / "LiteratureLibrary"
    if cwd_lib.exists():
        return cwd_lib
    # 3. Inside repo folder
    repo_lib = Path(__file__).resolve().parent / "LiteratureLibrary"
    if repo_lib.exists():
        return repo_lib
    return parent_workspace


def parser() -> argparse.ArgumentParser:
    root = default_library_root()
    app = argparse.ArgumentParser(prog="lit", description="Local evidence-first literature library")
    app.add_argument("--library", type=Path, default=root, help=f"Library directory (default: {root})")
    app.add_argument("--json", action="store_true", help="Machine-readable output")
    commands = app.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    ingest = commands.add_parser("ingest")
    ingest.add_argument("pdf", type=Path)
    ingest.add_argument("--question")
    ingest.add_argument("--doi")
    verify = commands.add_parser("verify")
    verify.add_argument("paper_id")
    assess = commands.add_parser("assess")
    assess.add_argument("paper_id")
    assess.add_argument("--question", required=True)
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=8)
    search.add_argument("--verified-only", action="store_true")
    show = commands.add_parser("show")
    show.add_argument("paper_id")
    extracted = commands.add_parser("text")
    extracted.add_argument("paper_id")
    extracted.add_argument("--page", type=int)
    listing = commands.add_parser("list")
    listing.add_argument("--status", choices=["accepted", "rejected", "needs_review"])
    decide = commands.add_parser("decide")
    decide.add_argument("paper_id")
    decide.add_argument("status", choices=["accepted", "rejected", "needs_review"])
    decide.add_argument("--reason", required=True)
    reject = commands.add_parser("reject")
    reject.add_argument("paper_id")
    reject.add_argument("--reason", required=True)
    purge = commands.add_parser("purge")
    purge.add_argument("paper_id")
    purge.add_argument("--confirm", required=True)
    bench = commands.add_parser("benchmark")
    bench.add_argument("file", type=Path)
    bench.add_argument("--limit", type=int, default=5)
    logs = commands.add_parser("logs", help="View search log history")
    logs.add_argument("--date", help="Specific date folder (YYYY-MM-DD), defaults to latest")
    logs.add_argument("--limit", type=int, default=10, help="Number of search entries to display (default: 10)")
    commands.add_parser("ui", help="Launch native Tkinter desktop UI")
    return app


def human(value):
    if isinstance(value, list) and value and all(isinstance(x, dict) and "match" in x for x in value):
        for item in value:
            match = item["match"]
            warning = f" [{item['warning']}]" if item.get("warning") else ""
            print(f"{item['rank']}. {match['title']} — page {match['page']} (score {item['score']}){warning}")
            print(f"   {match['text'][:700]}\n")
    elif isinstance(value, list) and value and all(isinstance(x, dict) and "query" in x and "file" in x for x in value):
        for item in value:
            zero_warn = " [ZERO RESULTS]" if item.get("zero_results") else ""
            print(f"[{item.get('timestamp')}] Query: \"{item.get('query')}\"{zero_warn}")
            print(f"   Results: {item.get('results_count')} | Max Score: {item.get('max_score')} | Latency: {item.get('latency_ms')}ms | File: {item.get('file')}\n")
    else:
        print(json.dumps(value, indent=2, ensure_ascii=False))


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    # Agents naturally append output-format flags; accept --json in any position.
    if "--json" in argv:
        argv = ["--json"] + [item for item in argv if item != "--json"]
    args = parser().parse_args(argv)
    library = Library(args.library)
    try:
        if args.command == "init":
            library.initialize(); result = {"initialized": str(library.root)}
        elif args.command == "ingest": result = library.ingest(args.pdf, args.question, args.doi)
        elif args.command == "verify": result = library.verify(args.paper_id)
        elif args.command == "assess": result = library.assess(args.paper_id, args.question)
        elif args.command == "search": result = library.search(args.query, args.limit, args.verified_only)
        elif args.command == "show": result = library.show(args.paper_id)
        elif args.command == "text": result = library.text(args.paper_id, args.page)
        elif args.command == "list": result = library.list_papers(args.status)
        elif args.command == "decide": result = library.decide(args.paper_id, args.status, args.reason)
        elif args.command == "reject": result = library.decide(args.paper_id, "rejected", args.reason)
        elif args.command == "purge": result = library.purge(args.paper_id, args.confirm)
        elif args.command == "benchmark": result = library.benchmark(args.file, args.limit)
        elif args.command == "logs": result = library.list_logs(args.date, args.limit)
        elif args.command == "ui":
            from .gui import run_gui
            run_gui(library)
            return 0
        else: raise AssertionError(args.command)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else "", end="" if args.json else "")
        if not args.json: human(result)
        return 0
    except (ValueError, KeyError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
