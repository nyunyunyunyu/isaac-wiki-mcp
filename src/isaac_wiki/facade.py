"""
WikiFacade — unified public API with JSON-safe returns.

All methods return plain dicts (never raise), making the facade safe
for direct use in MCP tools, CLI commands, and third-party scripts.

Provides full-text search, page reading, and category browsing over
the wiki/ directory, with zero ML dependencies.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

from isaac_wiki.catalog import EnvironmentProfile
from isaac_wiki.wiki_engine import _cjk_ratio


class WikiFacade:
    """Entry point for MCP server and CLI.

    Wraps :class:`WikiEngine` so every public method returns a
    ``{"status": "ok", ...}`` or ``{"status": "error", "error": "..."}``
    dict — callers never need a try/except.
    """

    def __init__(self, wiki_dir: str | None = None):
        self._engine = None  # type: ignore[assignment]
        self._wiki_dir = wiki_dir

    def _get_engine(self):
        if self._engine is None:
            from isaac_wiki.wiki_engine import WikiEngine
            self._engine = WikiEngine(wiki_dir=self._wiki_dir)
        return self._engine

    @staticmethod
    def _ok(data: dict[str, Any] | None = None) -> dict[str, Any]:
        d = dict(data) if data else {}
        d.setdefault("status", "ok")
        return d

    @staticmethod
    def _err(msg: str) -> dict[str, Any]:
        logger.error(msg)
        return {"status": "error", "error": msg}

    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int = 5,
        category: str | None = None,
        game: str | None = None,
        dependencies: list[str] | None = None,
        language: str = "auto",
        include_incompatible: bool = False,
        summary: bool = False,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            profile, resolved_language = self._resolve_profile(query, game, dependencies, language)
            engine = self._get_engine()
            use_reference = self._uses_reference(game, dependencies, language)
            results = engine.search(
                query,
                top_k,
                category=category,
                language=resolved_language if use_reference else None,
                include_rgon="rgon" in profile.dependencies,
            )
            if summary:
                results = [{k: v for k, v in item.items() if k != "content"} for item in results]
            elapsed = (time.perf_counter() - t0) * 1000
            return self._ok({
                "query": query,
                "results": results,
                "total_results": len(results),
                "search_time_ms": round(elapsed, 3),
                "profile": self._profile_payload(profile, resolved_language),
                "include_incompatible": include_incompatible,
            })
        except Exception as exc:
            return self._err(str(exc))

    def read_page(
        self,
        page: str,
        game: str | None = None,
        dependencies: list[str] | None = None,
        language: str = "auto",
        include_incompatible: bool = False,
        section: str | None = None,
    ) -> dict[str, Any]:
        try:
            profile, resolved_language = self._resolve_profile(page, game, dependencies, language)
            engine = self._get_engine()
            use_reference = self._uses_reference(game, dependencies, language)
            result = engine.read_page(
                page,
                language=resolved_language if use_reference else None,
                include_rgon="rgon" in profile.dependencies,
            )
            if result is None:
                return self._err(f"Page not found: {page}")
            if section is not None:
                result["content"] = self._section(result["content"], section)
                result["section"] = section
                result["word_count"] = len(result["content"].split())
            result["profile"] = self._profile_payload(profile, resolved_language)
            result["include_incompatible"] = include_incompatible
            return self._ok(result)
        except Exception as exc:
            return self._err(str(exc))

    @staticmethod
    def _section(content: str, name: str) -> str:
        if not name.strip():
            raise ValueError("section must not be empty")
        lines = content.splitlines(keepends=True)
        headings = []
        fence = None
        for index, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                fence = None if fence == marker else marker if fence is None else fence
            if fence is not None:
                continue
            match = re.match(r"^(#{1,6})\s+(.+)", line)
            if match:
                title = re.sub(r"\{:[^}]*\}", "", match[2]).replace("·", "").strip()
                headings.append((index, len(match[1]), title))
        parts, end = [], 0
        pattern = re.compile(r"(?<!\w)" + re.escape(name.strip()) + r"(?!\w)", re.IGNORECASE)
        for pos, (start, level, title) in enumerate(headings):
            if start < end or not pattern.search(title):
                continue
            end = next((row[0] for row in headings[pos + 1:] if row[1] <= level), len(lines))
            parts.append("".join(lines[start:end]).strip())
        if not parts:
            raise ValueError(f"Section not found: {name}; use a method or heading name")
        return "\n\n".join(parts)

    def list_pages(
        self,
        category: str | None = None,
        game: str | None = None,
        dependencies: list[str] | None = None,
        language: str = "auto",
    ) -> dict[str, Any]:
        try:
            profile, resolved_language = self._resolve_profile("", game, dependencies, language)
            pages = self._get_engine().list_pages(
                category,
                language=resolved_language if self._uses_reference(game, dependencies, language) else None,
            )
            return self._ok({
                "pages": pages,
                "count": len(pages),
                "profile": self._profile_payload(profile, resolved_language),
            })
        except Exception as exc:
            return self._err(str(exc))

    def stats(self) -> dict[str, Any]:
        try:
            return self._ok(self._get_engine().get_stats())
        except Exception as exc:
            return self._err(str(exc))

    def categories(self) -> dict[str, Any]:
        try:
            cats = self._get_engine().list_categories()
            return self._ok({"categories": cats, "count": len(cats)})
        except Exception as exc:
            return self._err(str(exc))

    def sources(self) -> dict[str, Any]:
        reference_root = Path(self._get_engine().wiki_dir) / "reference"
        metadata_path = reference_root / "source-release.json"
        snapshot: dict[str, Any] = {}
        if metadata_path.exists():
            try:
                snapshot = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                snapshot = {"status": "invalid metadata"}
        return self._ok({
            "repository": "https://github.com/3113y/isaac-api-edition",
            "profiles": {
                "games": ["rep", "rep+"],
                "dependencies": ["rgon"],
            },
            "snapshot_path": "wiki/reference",
            "snapshot": snapshot,
        })

    @staticmethod
    def _resolve_profile(
        query: str,
        game: str | None,
        dependencies: list[str] | None,
        language: str,
    ) -> tuple[EnvironmentProfile, str]:
        if language not in {"auto", "en", "zh"}:
            raise ValueError("language must be en, zh, or auto")
        resolved_language = (
            "zh" if _cjk_ratio(query) > 0.3 else "en"
        ) if language == "auto" else language
        return EnvironmentProfile.from_values(game or "rep", dependencies), resolved_language

    @staticmethod
    def _profile_payload(profile: EnvironmentProfile, language: str) -> dict[str, Any]:
        return {
            "game": profile.game,
            "dependencies": sorted(profile.dependencies),
            "language": language,
        }

    @staticmethod
    def _uses_reference(
        game: str | None, dependencies: list[str] | None, language: str
    ) -> bool:
        """Keep legacy calls byte-for-byte compatible until a profile is explicit."""
        return game is not None or bool(dependencies) or language != "auto"
