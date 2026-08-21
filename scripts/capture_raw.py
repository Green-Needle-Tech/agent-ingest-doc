#!/usr/bin/env python3
"""capture_raw.py — capture a document into the LLM wiki raw/ layer.

Computes body sha256, writes frontmatter (source_url, ingested, sha256),
and detects drift on re-ingest. Stdlib only.

Usage:
  capture_raw.py --wiki ~/wiki --title "Doc Title" --source-url <url-or-path> \
      [--raw-subdir articles|papers|transcripts] [--stdin | --input-file extracted.txt]

Body text comes from stdin or --input-file (extraction happens upstream per the
extraction ladder). Prints JSON: {"status": "captured"|"unchanged"|"drift", ...}
"""
import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:80] or "untitled"


def parse_existing_frontmatter(path: Path):
    """Return (frontmatter_dict, body_str) for an existing raw file."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, m.group(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default="~/wiki")
    ap.add_argument("--title", required=True)
    ap.add_argument("--source-url", required=True)
    ap.add_argument("--raw-subdir", default="articles",
                    choices=["articles", "papers", "transcripts", "assets"])
    ap.add_argument("--input-file")
    ap.add_argument("--version-suffix", default="")
    args = ap.parse_args()

    wiki = Path(args.wiki).expanduser()
    if args.input_file:
        body = Path(args.input_file).read_text(encoding="utf-8")
    else:
        body = sys.stdin.read()

    if len(body.strip()) < 50:
        print(json.dumps({
            "status": "error",
            "error": "body too small — extraction likely failed; climb the extraction ladder",
            "body_chars": len(body.strip()),
        }))
        return 1

    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    today = datetime.date.today().isoformat()
    stem = slugify(args.title)
    raw_dir = wiki / "raw" / args.raw_subdir
    raw_dir.mkdir(parents=True, exist_ok=True)

    # re-ingest drift check: match any existing file for this title
    candidates = sorted(raw_dir.glob(f"{stem}*.md"))
    for cand in candidates:
        fm, old_body = parse_existing_frontmatter(cand)
        old_sha = fm.get("sha256", "")
        if old_sha == sha:
            print(json.dumps({
                "status": "unchanged",
                "file": str(cand),
                "note": "identical content — skip capture, update wiki pages only if needed",
            }))
            return 0
        if old_sha and old_sha != sha:
            print(json.dumps({
                "status": "drift",
                "file": str(cand),
                "old_sha": old_sha,
                "new_sha": sha,
                "note": "source changed — flag to user, save new version with -v2 suffix",
            }))
            # fall through: caller decides; default save as new version below

    suffix = args.version_suffix
    if not suffix and candidates:
        suffix = f"-v{len(candidates)}"
    out = raw_dir / f"{stem}{suffix}.md"
    front = (
        "---\n"
        f"source_url: {args.source_url}\n"
        f"ingested: {today}\n"
        f"sha256: {sha}\n"
        "---\n\n"
        f"# {args.title}\n\n"
    )
    out.write_text(front + body, encoding="utf-8")
    print(json.dumps({
        "status": "captured",
        "file": str(out),
        "sha256": sha,
        "body_chars": len(body),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
