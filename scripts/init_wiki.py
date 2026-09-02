#!/usr/bin/env python3
"""init_wiki.py — bootstrap a new LLM wiki from templates.

Creates the wiki directory structure and copies template files.
Safe to re-run — will not overwrite existing files.

Usage:
  python3 scripts/init_wiki.py [--wiki PATH]
  Exit 0 = ready, 1 = error.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_paths import default_wiki  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"

WIKI_DIRS = [
    "raw/articles",
    "raw/papers",
    "raw/transcripts",
    "raw/assets",
    "entities",
    "concepts",
    "comparisons",
    "queries",
]


def main():
    ap = argparse.ArgumentParser(description="Initialize a new LLM wiki")
    ap.add_argument("--wiki", default=str(default_wiki()),
                    help="wiki root (default: $WIKI_PATH or <real home>/wiki)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    wiki = Path(args.wiki).expanduser()
    created = []
    skipped = []

    # Create directory structure
    for d in WIKI_DIRS:
        target = wiki / d
        if target.exists():
            skipped.append(str(target))
        else:
            target.mkdir(parents=True, exist_ok=True)
            created.append(str(target))

    # Copy templates (don't overwrite)
    template_files = ["SCHEMA.md", "index.md", "log.md"]
    for fname in template_files:
        src = TEMPLATES / fname
        dst = wiki / fname
        if not src.is_file():
            continue
        if dst.exists():
            skipped.append(str(dst))
        else:
            shutil.copy2(str(src), str(dst))
            created.append(str(dst))

    if args.as_json:
        print(json.dumps({
            "wiki": str(wiki),
            "created": created,
            "skipped": skipped,
            "status": "ok",
        }, indent=2))
    else:
        print(f"Wiki at {wiki}")
        for f in created:
            print(f"  created: {f}")
        for f in skipped:
            print(f"  exists:  {f}")
        if not created:
            print("  (all files already present)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
