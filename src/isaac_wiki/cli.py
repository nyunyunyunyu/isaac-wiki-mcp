"""
CLI tool for the Isaac Wiki system.

Single-invocation, shell-friendly — usable by any coding agent
(Claude Code, Copilot, Codex, etc.) or directly from a terminal.

Usage::

    isaac-wiki search "player health" --top-k 3 --category classes
    isaac-wiki read EntityPlayer
    isaac-wiki list --category classes
    isaac-wiki stats
    isaac-wiki build
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from isaac_wiki.facade import WikiFacade


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="isaac-wiki",
        description="Isaac Wiki — full-text search over Isaac modding API docs",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # search
    p = sub.add_parser("search", help="Full-text search across wiki pages")
    p.add_argument("query", help="Search keywords")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--summary", action="store_true", help="Return page metadata without page bodies")
    p.add_argument("--category", choices=["classes", "enums", "tutorials", "reference"], default=None)
    _add_profile_arguments(p, include_incompatible=True)
    p.add_argument("--format", choices=["text", "json"], default="text")

    # read
    p = sub.add_parser("read", help="Read a complete wiki page")
    p.add_argument("page", help="Page name (e.g. EntityPlayer, classes/Game)")
    p.add_argument("--section", help="Read only this method or heading, including its subsections")
    _add_profile_arguments(p, include_incompatible=True)
    p.add_argument("--format", choices=["text", "json"], default="text")

    # list
    p = sub.add_parser("list", help="List wiki pages by category")
    p.add_argument("--category", choices=["classes", "enums", "tutorials", "reference"], default=None)
    _add_profile_arguments(p)
    p.add_argument("--format", choices=["text", "json"], default="text")

    # stats
    p = sub.add_parser("stats", help="Show wiki statistics")
    p.add_argument("--format", choices=["text", "json"], default="text")

    # build
    p = sub.add_parser("build", help="Rebuild wiki pages from data sources")

    p = sub.add_parser("sync-reference", help="Bundle a generated isaac-api-edition snapshot")
    p.add_argument("source_root", type=Path, help="Local isaac-api-edition checkout")
    p.add_argument("--output", type=Path, default=Path("wiki/reference"))

    return parser


def _add_profile_arguments(parser: argparse.ArgumentParser, *, include_incompatible: bool = False) -> None:
    parser.add_argument("--game", choices=["rep", "rep+"], default=None)
    parser.add_argument(
        "--dependency", dest="dependencies", action="append", choices=["rgon"], default=None,
        help="Enable an API overlay; repeat for each dependency.",
    )
    parser.add_argument("--language", choices=["en", "zh", "auto"], default="auto")
    if include_incompatible:
        parser.add_argument("--include-incompatible", action="store_true")


def _dispatch(facade: WikiFacade, args: argparse.Namespace) -> dict[str, Any]:
    cmd = args.command
    if cmd == "search":
        return facade.search(
            args.query, top_k=args.top_k, category=args.category,
            game=args.game, dependencies=args.dependencies, language=args.language,
            include_incompatible=args.include_incompatible,
            summary=args.summary,
        )
    elif cmd == "read":
        return facade.read_page(
            args.page, game=args.game, dependencies=args.dependencies, language=args.language,
            include_incompatible=args.include_incompatible,
            section=args.section,
        )
    elif cmd == "list":
        return facade.list_pages(
            category=args.category, game=args.game, dependencies=args.dependencies, language=args.language,
        )
    elif cmd == "stats":
        return facade.stats()
    elif cmd == "build":
        from isaac_wiki.wiki_builder import WikiBuilder
        builder = WikiBuilder()
        stats = builder.build_all()
        return {
            "status": "ok",
            "message": (
                f"Wiki rebuilt: {stats['classes']} classes, "
                f"{stats['total_methods']} methods, "
                f"{stats['enums']} enums, "
                f"{stats['tutorials']} tutorials"
            ),
        }
    elif cmd == "sync-reference":
        from isaac_wiki.snapshot import sync_snapshot
        metadata = sync_snapshot(args.source_root, args.output)
        return {"status": "ok", "snapshot": metadata}
    return {"status": "error", "error": f"Unknown command: {cmd}"}


# ---------------------------------------------------------------------------
# Text formatters
# ---------------------------------------------------------------------------
def _text_search(r: dict[str, Any]) -> None:
    if r.get("status") == "error":
        print(f"❌ {r.get('error')}")
        return
    print(f'\U0001f50d Search: "{r.get("query")}"')
    print(f"   Results: {r.get('total_results')}  |  Time: {r.get('search_time_ms', 0):.2f} ms\n")
    for i, item in enumerate(r.get("results", [])):
        title = item.get("title", "?")
        path = item.get("path", "")
        score = item.get("score", 0)
        cat = item.get("category", "")
        words = item.get("word_count", 0)
        print(f"  {i+1}. [{cat}] {title}  (score: {score}, words: {words})")
        print(f"     path: {path}")
        content = item.get("content", "")
        if content:
            preview = content.split("---", 2)[-1].strip() if "---" in content else content
            print(f"     {preview[:200]}...\n")


def _text_read(r: dict[str, Any]) -> None:
    if r.get("status") == "error":
        print(f"❌ {r.get('error')}")
        return
    print(r.get("content", ""))


def _text_list(r: dict[str, Any]) -> None:
    if r.get("status") == "error":
        print(f"❌ {r.get('error')}")
        return
    pages = r.get("pages", [])
    print(f"\U0001f4da Wiki Pages ({len(pages)}):\n")
    for p in pages:
        title = p.get("title", "?")
        cat = p.get("category", "")
        mc = p.get("method_count", "") or ""
        dlc = p.get("dlc_versions", [])
        dlc_str = f"DLC: {', '.join(dlc)}" if dlc else ""
        print(f"  [{cat}] {title}  methods={mc}  {dlc_str}")


def _text_stats(r: dict[str, Any]) -> None:
    if r.get("status") == "error":
        print(f"❌ {r.get('error')}")
        return
    print("\U0001f4ca Wiki Statistics")
    print(f"  total_pages: {r.get('total_pages')}")
    print(f"  classes: {r.get('classes')}")
    print(f"  enums: {r.get('enums')}")
    print(f"  tutorials: {r.get('tutorials')}")
    print(f"  total_methods: {r.get('total_methods')}")
    print(f"  avg_methods_per_class: {r.get('avg_methods_per_class')}")
    print(f"  wiki_dir: {r.get('wiki_dir')}")


_PRINTERS = {
    "search": _text_search,
    "read":   _text_read,
    "list":   _text_list,
    "stats":  _text_stats,
}


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = _build_parser()
    args = parser.parse_args(argv)

    facade = WikiFacade()
    result = _dispatch(facade, args)

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        printer = _PRINTERS.get(args.command)
        if printer:
            printer(result)
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))

    if result.get("status") == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
