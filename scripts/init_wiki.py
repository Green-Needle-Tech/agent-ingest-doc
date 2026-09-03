#!/usr/bin/env python3
"""init_wiki.py — bootstrap a new LLM wiki from templates.

Supports two layouts:

  karpathy (astro-han/karpathy-llm-wiki, DEFAULT):
      <root>/raw/           (with .gitkeep)
      <root>/wiki/          (with .gitkeep)
      <root>/wiki/index.md  — heading `# Knowledge Base Index`
      <root>/wiki/log.md    — heading `# Wiki Log`
      Per the karpathy-llm-wiki spec, index/log start EMPTY (no template
      body) and only directories that are missing get created.

  legacy (original Karpathy-gist layout):
      raw/{articles,papers,transcripts,assets}/
      {entities,concepts,comparisons,queries}/
      SCHEMA.md, index.md, log.md copied from templates/

Safe to re-run — will not overwrite existing files.

Usage:
  python3 scripts/init_wiki.py [--wiki PATH] [--layout karpathy|legacy]
  Exit 0 = ready, 1 = error.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_paths import default_wiki  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"

LEGACY_DIRS = [
    "raw/articles",
    "raw/papers",
    "raw/transcripts",
    "raw/assets",
    "entities",
    "concepts",
    "comparisons",
    "queries",
]

KARPATHY_INDEX = "# Knowledge Base Index\n"
KARPATHY_LOG = "# Wiki Log\n"


def init_karpathy(root: Path):
    created = []
    skipped = []
    for d in ("raw", "wiki"):
        target = root / d
        keep = target / ".gitkeep"
        if target.exists():
            skipped.append(str(target))
        else:
            target.mkdir(parents=True, exist_ok=True)
            keep.touch()
            created.append(str(target))
    for name, content in (("index.md", KARPATHY_INDEX),
                          ("log.md", KARPATHY_LOG)):
        target = root / "wiki" / name
        if target.exists():
            skipped.append(str(target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            created.append(str(target))
    return created, skipped


def init_legacy(wiki: Path):
    import shutil
    created = []
    skipped = []
    for d in LEGACY_DIRS:
        target = wiki / d
        if target.exists():
            skipped.append(str(target))
        else:
            target.mkdir(parents=True, exist_ok=True)
            created.append(str(target))
    for fname in ("SCHEMA.md", "index.md", "log.md"):
        src = TEMPLATES / fname
        dst = wiki / fname
        if not src.is_file():
            continue
        if dst.exists():
            skipped.append(str(dst))
        else:
            shutil.copy2(str(src), str(dst))
            created.append(str(dst))
    return created, skipped


def main():
    ap = argparse.ArgumentParser(description="Initialize a new LLM wiki")
    ap.add_argument("--wiki", default=str(default_wiki()),
                    help="wiki root (default: $WIKI_PATH or <real home>/wiki). "
                         "karpathy layout: the PROJECT ROOT that will contain "
                         "raw/ and wiki/.")
    ap.add_argument("--layout", default="karpathy",
                    choices=["karpathy", "legacy"],
                    help="wiki layout (default: karpathy)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    root = Path(args.wiki).expanduser()

    if args.layout == "karpathy":
        created, skipped = init_karpathy(root)
    else:
        created, skipped = init_legacy(root)

    if args.as_json:
        print(json.dumps({
            "wiki": str(root),
            "layout": args.layout,
            "created": created,
            "skipped": skipped,
            "status": "ok",
        }, indent=2))
    else:
        print(f"Wiki [{args.layout}] at {root}")
        for f in created:
            print(f"  created: {f}")
        for f in skipped:
            print(f"  exists:  {f}")
        if not created:
            print("  (all files already present)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
