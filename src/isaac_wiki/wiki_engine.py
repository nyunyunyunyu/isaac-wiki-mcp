"""
Wiki Engine — full-text search and page retrieval over llmwiki-style markdown pages.

Provides keyword search, page reading, and category browsing without any
external ML dependencies (no FAISS, no SentenceTransformer, no numpy).

Design:
- All .md files under ``wiki/`` are lazy-loaded into an in-memory dict on
  first access (~200 files, < 5 MB total — negligible overhead).
- Search uses token-aware scoring with positional weighting (title > heading > body).
- CJK queries automatically use character-bigram matching for Chinese text.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# CJK text helpers
# ---------------------------------------------------------------------------
_CJK_RANGE = ("一", "鿿")


def _is_cjk(c: str) -> bool:
    return _CJK_RANGE[0] <= c <= _CJK_RANGE[1]


def _cjk_ratio(text: str) -> float:
    """Fraction of characters in *text* that are CJK."""
    if not text:
        return 0.0
    cjk_count = sum(1 for c in text if _is_cjk(c))
    return cjk_count / len(text)


def _tokenize(text: str) -> list[str]:
    """Tokenize *text* for search.

    If the text is predominantly CJK, use character bigrams.
    Otherwise, split on whitespace and punctuation, lowercased.
    """
    if _cjk_ratio(text) > 0.3:
        # Character bigrams for CJK fuzzy matching
        chars = [c for c in text if _is_cjk(c)]
        bigrams = ["".join(chars[i:i + 2]) for i in range(len(chars) - 1)]
        # Also include individual chars for single-char queries
        bigrams.extend(chars)
        return bigrams

    # English / mixed: split on non-alphanumeric, lowercase, filter short tokens
    tokens = re.findall(r"[a-zA-Z0-9_一-鿿]+", text.lower())
    return [t for t in tokens if len(t) >= 2 or _is_cjk(t)]


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_RGON_BLOCK_RE = re.compile(
    r"\n*<div class=\"rgon-(?:extension|only)\"[^>]*>.*?</div>\n*",
    re.DOTALL,
)


def _default_wiki_dir(module_file: Path) -> Path:
    """Locate bundled wiki data in an installed wheel or a source checkout."""
    bundled_dir = module_file.resolve().parent / "wiki"
    if bundled_dir.exists():
        return bundled_dir
    return module_file.resolve().parents[2] / "wiki"


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Extract YAML-style frontmatter from a markdown page."""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}
    fm_text = m.group(1)
    meta: dict[str, Any] = {}
    for line in fm_text.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            # Parse list: "[a, b, c]"
            if value.startswith("[") and value.endswith("]"):
                value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
            # Parse integer
            elif value.isdigit():
                value = int(value)
            meta[key] = value
    return meta


# ---------------------------------------------------------------------------
# WikiEngine
# ---------------------------------------------------------------------------

class WikiEngine:
    """File-system-native wiki engine for Isaac API documentation.

    Provides full-text search, page reading, and category listing over the
    ``wiki/`` directory. No embeddings, no vector index, no external ML models.

    Usage::

        engine = WikiEngine()
        results = engine.search("player health", top_k=5)
        page = engine.read_page("EntityPlayer")
        pages = engine.list_pages("classes")
        stats = engine.get_stats()
    """

    def __init__(self, wiki_dir: str | Path | None = None):
        if wiki_dir is None:
            wiki_dir = _default_wiki_dir(Path(__file__))
        self.wiki_dir = Path(wiki_dir)

        # In-memory index: relative_path -> full_content
        self._index: dict[str, str] = {}
        # Parsed frontmatter cache: relative_path -> dict
        self._page_meta: dict[str, dict[str, Any]] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Lazy loading
    # ------------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        """Load all wiki .md files into memory on first access."""
        if self._loaded:
            return

        if not self.wiki_dir.exists():
            logger.warning(f"Wiki directory not found: {self.wiki_dir}")
            self._loaded = True
            return

        t0 = time.perf_counter()
        count = 0
        for md_file in self.wiki_dir.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                rel_path = md_file.relative_to(self.wiki_dir).as_posix()
                self._index[rel_path] = content
                self._page_meta[rel_path] = _parse_frontmatter(content)
                count += 1
            except Exception as exc:
                logger.warning(f"Skipping {md_file}: {exc}")

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(f"WikiEngine loaded {count} pages in {elapsed:.1f} ms")
        self._loaded = True

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        category: str | None = None,
        language: str | None = None,
        include_rgon: bool = True,
    ) -> list[dict[str, Any]]:
        """Full-text keyword search across all wiki pages.

        Returns complete page content for each match — not chunks.
        The LLM gets the full class context.

        Args:
            query: Search keywords (Chinese or English).
            top_k: Maximum number of results (default 5, max 20).
            category: Limit to a subdirectory (``"classes"``, ``"enums"``,
                      ``"tutorials"``).

        Returns:
            List of result dicts with ``path``, ``title``, ``content``,
            ``score``, ``category``, and ``word_count``.
        """
        self._ensure_loaded()

        if not self._index:
            return []

        t0 = time.perf_counter()
        tokens = _tokenize(query)
        if not tokens:
            return []

        # A bundled API Edition snapshot is organized by language.  Retain the
        # legacy wiki as a fallback until a release includes that snapshot.
        reference_prefix = f"reference/{language}/" if language else ""
        has_reference = bool(reference_prefix) and any(
            path.startswith(reference_prefix) for path in self._index
        )
        prefix = reference_prefix if has_reference else (f"{category}/" if category else "")

        scored: list[tuple[float, str]] = []
        for path, content in self._index.items():
            if prefix and not path.startswith(prefix):
                continue
            content = self._filtered_content(content, include_rgon)
            score = self._score_page(content, path, tokens)
            if score > 0:
                scored.append((score, path))

        # Sort by score descending
        scored.sort(key=lambda x: -x[0])
        top_k = min(top_k, 20)
        scored = scored[:top_k]

        results: list[dict[str, Any]] = []
        for score, path in scored:
            content = self._filtered_content(self._index[path], include_rgon)
            meta = self._page_meta.get(path, {})
            cat = path.split("/")[0] if "/" in path else "root"
            results.append({
                "path": path,
                "title": meta.get("title", Path(path).stem),
                "category": cat,
                "score": round(score, 2),
                "word_count": len(content.split()),
                "content": content,
            })

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(
            f"Wiki search '{query[:60]}' → {len(results)} results "
            f"(category={category or 'all'}) in {elapsed:.1f} ms",
        )
        return results

    def _score_page(self, content: str, path: str, tokens: list[str]) -> float:
        """Score a page's content against query tokens.

        Positional weighting:
        - H1 title match: +15 per token
        - H2-H4 heading match: +8 per token
        - Body text match: +1 per occurrence (capped at 10)

        Exact heading tokens outrank incidental mentions, regardless of page length.
        """
        score = 0.0
        heading_tokens = set()

        # Identify sections
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip()
            # Detect headings
            h_match = re.match(r"^(#{1,4})\s+(.+)", stripped)
            if h_match:
                level = len(h_match.group(1))
                heading_text = h_match.group(2).lower().replace("·", "")
                for token in tokens:
                    token_lower = token.lower()
                    if re.search(r"(?<!\w)" + re.escape(token_lower) + r"(?!\w)", heading_text):
                        heading_tokens.add(token_lower)
                    if level == 1 and token_lower in heading_text:
                        score += 15
                    elif token_lower in heading_text:
                        score += 8
                continue

            # Body line
            for token in tokens:
                token_lower = token.lower()
                count = stripped.lower().count(token_lower)
                if count > 0:
                    score += min(count, 10) * 1

        length_factor = max(len(content) ** 0.4, 1.0)
        return len(heading_tokens) + score / (length_factor + score)

    # ------------------------------------------------------------------
    # Page reading
    # ------------------------------------------------------------------
    def read_page(
        self,
        name_or_path: str,
        *,
        language: str | None = None,
        include_rgon: bool = True,
    ) -> dict[str, Any] | None:
        """Read a complete wiki page by name or relative path.

        Resolution order:
        1. Exact relative path match (e.g. ``"classes/EntityPlayer.md"``)
        2. Name match in ``classes/`` (e.g. ``"EntityPlayer"``)
        3. Name match in ``enums/``, ``tutorials/``, root

        Args:
            name_or_path: Page identifier.

        Returns:
            Dict with ``path``, ``title``, ``content``, ``frontmatter``,
            ``word_count``, or ``None`` if not found.
        """
        self._ensure_loaded()

        path = self._resolve_path(name_or_path, language=language)
        if path is None:
            logger.warning(f"Page not found: {name_or_path}")
            return None

        content = self._filtered_content(self._index.get(path, ""), include_rgon)
        meta = self._page_meta.get(path, {})
        cat = path.split("/")[0] if "/" in path else "root"

        return {
            "path": path,
            "title": meta.get("title", Path(path).stem),
            "category": cat,
            "word_count": len(content.split()),
            "frontmatter": meta,
            "content": content,
        }

    @staticmethod
    def _filtered_content(content: str, include_rgon: bool) -> str:
        return content if include_rgon else _RGON_BLOCK_RE.sub("\n", content)

    def _resolve_path(self, name: str, *, language: str | None = None) -> str | None:
        """Resolve a page name to its relative path in the index.

        Explicit profiles resolve inside the requested snapshot language first.
        Legacy calls try direct paths, class/enum/tutorial prefixes, then names.
        """
        if language:
            reference_prefix = f"reference/{language}/"
            normalized_name = name.removesuffix(".md").replace("\\", "/")
            normalized_name = re.sub(r"^reference/(?:en|zh)/", "", normalized_name)
            normalized_name = normalized_name.removeprefix("classes/")
            candidate = f"{reference_prefix}{normalized_name}.md"
            if candidate in self._index:
                return candidate
            name_lower = normalized_name.lower()
            for path in self._index:
                if not path.startswith(reference_prefix):
                    continue
                if path.removeprefix(reference_prefix).removesuffix(".md").lower() == name_lower:
                    return path
                if Path(path).stem.lower() == name_lower:
                    return path
            if any(path.startswith(reference_prefix) for path in self._index):
                return None

        # Explicit profiles must not fall through to a different snapshot or legacy page.
        if name in self._index:
            return name
        if f"{name}.md" in self._index:
            return f"{name}.md"

        # Try subdirectories
        for prefix in ("classes", "enums", "tutorials"):
            candidate = f"{prefix}/{name}.md"
            if candidate in self._index:
                return candidate
            candidate = f"{prefix}/{name}"
            if candidate in self._index:
                return candidate

        # Case-insensitive search
        name_lower = name.lower()
        for path in self._index:
            if path.lower() == name_lower:
                return path
            stem = Path(path).stem.lower()
            if stem == name_lower:
                return path

        return None

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------
    def list_pages(
        self, category: str | None = None, *, language: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all wiki pages, optionally filtered by category.

        Args:
            category: Filter by subdirectory (``"classes"``, ``"enums"``,
                      ``"tutorials"``).  ``None`` returns all pages.

        Returns:
            List of page summaries (path, title, category, method_count,
            dlc_versions, word_count).  Does *not* include full content.
        """
        self._ensure_loaded()

        reference_prefix = f"reference/{language}/" if language else ""
        has_reference = bool(reference_prefix) and any(
            path.startswith(reference_prefix) for path in self._index
        )
        prefix = reference_prefix if has_reference else (f"{category}/" if category else "")
        result: list[dict[str, Any]] = []

        for path in sorted(self._index.keys()):
            if prefix and not path.startswith(prefix):
                continue
            meta = self._page_meta.get(path, {})
            cat = path.split("/")[0] if "/" in path else "root"
            result.append({
                "path": path,
                "title": meta.get("title", Path(path).stem),
                "category": cat,
                "method_count": meta.get("method_count"),
                "dlc_versions": meta.get("dlc_versions"),
                "word_count": len(self._index[path].split()),
            })

        return result

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def get_stats(self) -> dict[str, Any]:
        """Return wiki statistics."""
        self._ensure_loaded()

        classes = sum(1 for p in self._index if p.startswith("classes/"))
        enums = sum(1 for p in self._index if p.startswith("enums/"))
        tutorials = sum(1 for p in self._index if p.startswith("tutorials/"))
        total = len(self._index)

        total_methods = 0
        for path in self._index:
            meta = self._page_meta.get(path, {})
            mc = meta.get("method_count")
            if isinstance(mc, int):
                total_methods += mc

        return {
            "total_pages": total,
            "classes": classes,
            "enums": enums,
            "tutorials": tutorials,
            "total_methods": total_methods,
            "wiki_dir": str(self.wiki_dir),
            "avg_methods_per_class": (
                round(total_methods / max(classes, 1), 1)
                if classes > 0
                else 0
            ),
        }

    def list_categories(self) -> list[str]:
        """List available top-level categories (subdirectories of wiki/)."""
        self._ensure_loaded()
        cats: set[str] = set()
        for path in self._index:
            if "/" in path:
                cats.add(path.split("/")[0])
        cats.add("root")  # pages at wiki/ level
        return sorted(cats)

    # ------------------------------------------------------------------
    # Reload (for when wiki files change)
    # ------------------------------------------------------------------
    def reload(self) -> None:
        """Clear the in-memory cache and reload all pages from disk."""
        self._index.clear()
        self._page_meta.clear()
        self._loaded = False
        self._ensure_loaded()
        logger.info("WikiEngine reloaded from disk")
