#!/usr/bin/env python3
"""wiki_layout.py — detect which LLM wiki layout a path uses.

Two supported layouts:

  karpathy (astro-han/karpathy-llm-wiki):
      <root>/raw/<topic>/...       immutable sources (blockquote header)
      <root>/wiki/<topic>/*.md     compiled articles
      <root>/wiki/index.md         topic tables
      <root>/wiki/log.md           append-only operation log

  legacy (original Karpathy-gist layout):
      <wiki>/raw/{articles,papers,transcripts,assets}/
      <wiki>/{entities,concepts,comparisons,queries}/
      <wiki>/SCHEMA.md, index.md, log.md

Detection rules for a candidate path P:
  1. P has wiki/ AND raw/            -> karpathy, root=P
  2. P has raw/ AND SCHEMA.md or
     any of entities/ concepts/ comparisons/ queries/
                                     -> legacy, root=P
  3. P is itself a wiki dir whose P.parent has raw/ and wiki/
     (i.e. the user passed <root>/wiki as --wiki)
                                     -> karpathy, root=P.parent
  4. nothing matches                 -> default layout (karpathy),
                                        root=P (fresh init target)

Stdlib only.
"""
from __future__ import annotations

from pathlib import Path

KARMATHY = "karpathy"
LEGACY = "legacy"

LEGACY_PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")


def detect_layout(path: Path, default: str = KARMATHY) -> tuple[str, Path]:
    """Return (layout, root) for the given path.

    `root` is the path scripts operate on: the project root for karpathy
    layout, the wiki root for legacy layout.
    """
    p = Path(path).expanduser()
    if (p / "wiki").is_dir() and (p / "raw").is_dir():
        return KARMATHY, p
    if (p / "raw").is_dir():
        if (p / "SCHEMA.md").is_file() or any(
                (p / d).is_dir() for d in LEGACY_PAGE_DIRS):
            return LEGACY, p
        # raw/ but neither wiki/ nor legacy markers — could be a fresh
        # karpathy root that has not run an ingest yet.
        return default, p
    # User passed <root>/wiki (the wiki subdirectory itself)
    if (p.parent / "raw").is_dir() and (p.parent / "wiki").is_dir() \
            and p.name == "wiki":
        return KARMATHY, p.parent
    return default, p


def raw_root(layout: str, root: Path) -> Path:
    """Return the raw/ directory for a layout + root."""
    return root / "raw"


def wiki_dir(layout: str, root: Path) -> Path:
    """Return the directory holding compiled pages + index.md + log.md."""
    return root / "wiki" if layout == KARMATHY else root


def index_file(layout: str, root: Path) -> Path:
    return wiki_dir(layout, root) / "index.md"


def log_file(layout: str, root: Path) -> Path:
    return wiki_dir(layout, root) / "log.md"


if __name__ == "__main__":  # pragma: no cover — debug helper
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    layout, root = detect_layout(target)
    print(f"path:   {target}")
    print(f"layout: {layout}")
    print(f"root:   {root}")
