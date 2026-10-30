"""
MCP (Model Context Protocol) server — stdio JSON-RPC.

Exposes profile-aware wiki tools to MCP-compatible agents (Claude Code, etc.).

Usage::

    uv run isaac-wiki-mcp
    python -m isaac_wiki.server

Claude Code config (``.claude/mcp.json``)::

    {
      "mcpServers": {
        "isaac-wiki": {
          "command": "uv",
          "args": ["run", "isaac-wiki-mcp"],
          "cwd": "/path/to/isaac-wiki-mcp"
        }
      }
    }
"""

from __future__ import annotations

import json
import sys
from typing import Any

from loguru import logger

from isaac_wiki.facade import WikiFacade

# ---------------------------------------------------------------------------
# Logging — stderr only (stdout is reserved for JSON-RPC)
# ---------------------------------------------------------------------------
logger.remove()
logger.add(sys.stderr, level="INFO", format="<level>{level:7}</level> | {message}")

# ---------------------------------------------------------------------------
# Singleton facade — lazy init so the server starts fast
# ---------------------------------------------------------------------------
_facade: WikiFacade | None = None


def _get_facade() -> WikiFacade:
    global _facade
    if _facade is None:
        logger.info("Initialising Wiki engine (lazy) ...")
        _facade = WikiFacade()
    return _facade


# ---------------------------------------------------------------------------
# MCP tool definitions
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "wiki_search",
        "description": (
            "Full-text search across all Isaac API wiki pages. Returns complete page "
            "content (not fragments) for each match — the LLM gets full class context "
            "with all methods, descriptions, and [[wikilinks]] to related classes. "
            "Set summary=true for metadata only, then read a section. Use this to find API classes, enums, or tutorials."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keywords, e.g. 'player health', 'spawn entity', 'grid entity door'.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results (default: 5, max: 10).",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
                "summary": {
                    "type": "boolean",
                    "default": False,
                    "description": "Return page metadata only; then wiki_read a page or section.",
                },
                "category": {
                    "type": "string",
                    "enum": ["classes", "enums", "tutorials", "reference"],
                    "description": "Limit search to a page category.",
                },
                "game": {
                    "type": "string",
                    "enum": ["rep", "rep+"],
                    "description": "Base API version. Defaults to REP.",
                },
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["rgon"]},
                    "description": "Optional API overlay dependencies.",
                },
                "language": {
                    "type": "string",
                    "enum": ["en", "zh", "auto"],
                    "default": "auto",
                    "description": "Response language. auto uses the query language conservatively.",
                },
                "include_incompatible": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include entries that require a different selected profile when supported by a catalog entry.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "wiki_read",
        "description": (
            "Read a complete Isaac API wiki page by name. Returns the full page with "
            "all methods, descriptions, DLC compatibility badges, and [[wikilinks]] "
            "to related classes. Pass section to read just one method or heading. Use wiki_search first to find the page."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "description": "Page name or path. Examples: 'EntityPlayer', 'Game', 'enums/EntityType'.",
                },
                "section": {"type": "string", "description": "Optional method or heading, e.g. GetHearts; includes subsections."},
                "game": {"type": "string", "enum": ["rep", "rep+"]},
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["rgon"]},
                },
                "language": {"type": "string", "enum": ["en", "zh", "auto"], "default": "auto"},
                "include_incompatible": {"type": "boolean", "default": False},
            },
            "required": ["page"],
        },
    },
    {
        "name": "wiki_list",
        "description": (
            "List all wiki pages, optionally filtered by category. Returns metadata "
            "(title, method_count, DLC versions) for each page — no full content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["classes", "enums", "tutorials", "reference"],
                    "description": "Filter by category.",
                },
                "game": {"type": "string", "enum": ["rep", "rep+"]},
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["rgon"]},
                },
                "language": {"type": "string", "enum": ["en", "zh", "auto"], "default": "auto"},
            },
        },
    },
    {
        "name": "wiki_stats",
        "description": "Get statistics about the wiki knowledge base (page count, method count, categories).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "wiki_sources",
        "description": "Describe the bundled Isaac API Edition source and supported REP/REP+/RGON profiles.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------
def _send(response: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle(request: dict[str, Any]) -> None:
    req_id = request.get("id")
    method = request.get("method", "")

    # --- initialize ---
    if method == "initialize":
        _send({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "isaac-wiki", "version": "0.3.0"},
                "capabilities": {"tools": {}},
            },
        })
        return

    # --- notifications ---
    if req_id is None:
        if method == "notifications/initialized":
            logger.info("MCP handshake complete — server ready")
        return

    # --- tools/list ---
    if method == "tools/list":
        _send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
        return

    # --- tools/call ---
    if method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        facade = _get_facade()
        result: dict[str, Any]

        try:
            if tool_name == "wiki_search":
                result = facade.search(
                    query=arguments.get("query", ""),
                    top_k=arguments.get("top_k", 5),
                    category=arguments.get("category"),
                    game=arguments.get("game"),
                    dependencies=arguments.get("dependencies"),
                    language=arguments.get("language", "auto"),
                    include_incompatible=arguments.get("include_incompatible", False),
                    summary=arguments.get("summary", False),
                )
            elif tool_name == "wiki_read":
                result = facade.read_page(
                    page=arguments.get("page", ""),
                    game=arguments.get("game"),
                    dependencies=arguments.get("dependencies"),
                    language=arguments.get("language", "auto"),
                    include_incompatible=arguments.get("include_incompatible", False),
                    section=arguments.get("section"),
                )
            elif tool_name == "wiki_list":
                result = facade.list_pages(
                    category=arguments.get("category"),
                    game=arguments.get("game"),
                    dependencies=arguments.get("dependencies"),
                    language=arguments.get("language", "auto"),
                )
            elif tool_name == "wiki_stats":
                result = facade.stats()
            elif tool_name == "wiki_sources":
                result = facade.sources()
            else:
                result = {"status": "error", "error": f"Unknown tool: {tool_name}"}

            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{
                        "type": "text",
                        "text": json.dumps(result, indent=2, ensure_ascii=False),
                    }],
                },
            })
        except Exception as exc:
            logger.error(f"Tool '{tool_name}' failed: {exc}")
            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({"status": "error", "error": str(exc)}),
                    }],
                },
            })
        return

    # --- unknown ---
    _send({
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logger.info("isaac-rag MCP server starting on stdio ...")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            _handle(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.error(f"Invalid JSON: {exc}")
        except Exception as exc:
            logger.error(f"Unhandled error: {exc}")
    logger.info("isaac-rag MCP server shutting down.")


if __name__ == "__main__":
    main()
