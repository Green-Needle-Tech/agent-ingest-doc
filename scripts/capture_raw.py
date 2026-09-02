#!/usr/bin/env python3
"""capture_raw.py — capture a document into the LLM wiki raw/ layer.

Computes body sha256, writes frontmatter (source_url, ingested, sha256),
and detects drift on re-ingest. Stdlib only.

Hash scope: sha256 covers the captured body EXACTLY as stored in the file
after the frontmatter and the `# Title` heading. To verify, read the file,
strip frontmatter + heading, and recompute — see scripts/verify_raw.py.

Version convention: the first capture is unsuffixed (implicitly v1). On hash
drift the next capture is saved as `<slug>-v<N>.md` where N = number of
existing versions + 1 (so the second version is -v2).

Dedup scope: drift/unchanged detection matches by slug within the target
subdir AND by source_url across ALL raw/ subdirs (same URL ingested into a
different subdir is still detected).

Usage:
  capture_raw.py --wiki ~/wiki --title "Doc Title" --source-url <url-or-path> \
      [--raw-subdir articles|papers|transcripts|assets] [--slug custom-slug] \
      [--stdin | --input-file extracted.txt]

Body text comes from stdin or --input-file (extraction happens upstream per the
extraction ladder). Prints exactly ONE JSON object:
  {"status": "captured"|"unchanged"|"drift"|"error", ...}
Exit code 0 on captured/unchanged/drift, 1 on error.
"""
import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

RAW_SUBDIRS = ("articles", "papers", "transcripts", "assets")
VERSION_RE = re.compile(r"^(?P<slug>.*)-v(?P<num>\d+)$")


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:80] or "untitled"


def parse_existing_frontmatter(path: Path):
    """Return (frontmatter_dict, body_str) for an existing raw file.

    body_str is everything after the frontmatter block AND the `# Title`
    heading — the exact text the sha256 covers.
    """
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    body = m.group(2).lstrip("\n")
    # strip the injected title heading (first heading line + blank line after)
    body = re.sub(r"^#\s+[^\n]*\n\n?", "", body, count=1)
    return fm, body


def version_sort_key(path: Path):
    """Sort files for a slug: unsuffixed original first, then -v2, -v3, ..."""
    m = VERSION_RE.match(path.stem)
    num = int(m.group("num")) if m else 1
    return (num, path.stem)


def find_versions(raw_dir: Path, stem: str):
    """Return files for this exact slug: `<stem>.md` or `<stem>-vN.md`.

    Exact-stem matching only — `test-doc*.md` must NOT match
    `test-document.md` (prefix collision).
    """
    out = []
    if not raw_dir.is_dir():
        return out
    for p in raw_dir.iterdir():
        if not p.is_file() or p.suffix != ".md":
            continue
        s = p.stem
        m = VERSION_RE.match(s)
        if s == stem or (m and m.group("slug") == stem):
            out.append(p)
    return sorted(out, key=version_sort_key)


def find_by_source_url(raw_root: Path, source_url: str, exclude: Path = None):
    """Scan ALL raw/ subdirs for a file whose frontmatter source_url matches.

    Cross-subdir dedupe: the same URL ingested into a different subdir must
    not escape drift detection. Returns list of (path, frontmatter) sorted
    by version.
    """
    out = []
    if not raw_root.is_dir():
        return out
    for p in sorted(raw_root.rglob("*.md")):
        if exclude is not None and p == exclude:
            continue
        try:
            fm, _ = parse_existing_frontmatter(p)
        except OSError:
            continue
        if fm.get("source_url") == source_url:
            out.append((p, fm))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default="~/wiki")
    ap.add_argument("--title", required=True)
    ap.add_argument("--source-url", required=True)
    ap.add_argument("--raw-subdir", default="articles", choices=list(RAW_SUBDIRS))
    ap.add_argument("--slug", default=None,
                    help="override the filename slug (default: slugified title)")
    ap.add_argument("--input-file")
    ap.add_argument("--version-suffix", default="",
                    help="override the version suffix (default: auto -vN on drift)")
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
    stem = slugify(args.slug) if args.slug else slugify(args.title)
    raw_dir = wiki / "raw" / args.raw_subdir
    raw_dir.mkdir(parents=True, exist_ok=True)

    # --- dedupe check 1: same slug in the target subdir ---
    candidates = find_versions(raw_dir, stem)
    # --- dedupe check 2: same source_url anywhere under raw/ ---
    url_matches = find_by_source_url(wiki / "raw", args.source_url)

    # identical content anywhere → unchanged (skip capture)
    for cand, fm in url_matches:
        if fm.get("sha256") == sha:
            print(json.dumps({
                "status": "unchanged",
                "file": str(cand),
                "note": "identical content (matched by source_url) — skip capture, "
                        "update wiki pages only if needed",
            }))
            return 0
    for cand in candidates:
        fm, _ = parse_existing_frontmatter(cand)
        if fm.get("sha256", "") == sha:
            print(json.dumps({
                "status": "unchanged",
                "file": str(cand),
                "note": "identical content (matched by slug) — skip capture, "
                        "update wiki pages only if needed",
            }))
            return 0

    # drift: newest existing version of this slug (or URL match) differs
    prev_file, old_sha = None, None
    if candidates:
        prev_file = candidates[-1]
        old_sha = parse_existing_frontmatter(prev_file)[0].get("sha256", "")
    elif url_matches:
        prev_file, fm = url_matches[-1]
        old_sha = fm.get("sha256", "")

    # decide where the new version lives:
    #  - drift on an existing URL match → next to the newest existing version
    #  - otherwise → the requested subdir
    if prev_file is not None and url_matches and not candidates:
        target_dir = prev_file.parent
        # the version chain belongs to the SOURCE, not the new title —
        # continue the existing file's stem (doc-a.md → doc-a-v2.md)
        prev_stem = VERSION_RE.match(prev_file.stem)
        stem = (prev_stem.group("slug") if prev_stem
                else prev_file.stem)
        candidates = find_versions(target_dir, stem)
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        target_dir = raw_dir

    # version suffix: original unsuffixed = v1, drift saves -v<N>
    suffix = args.version_suffix
    if not suffix:
        n_versions = len(candidates)
        suffix = "" if n_versions == 0 else f"-v{n_versions + 1}"

    out = target_dir / f"{stem}{suffix}.md"
    front = (
        "---\n"
        f"source_url: {args.source_url}\n"
        f"ingested: {today}\n"
        f"sha256: {sha}\n"
        "---\n\n"
        f"# {args.title}\n\n"
    )
    out.write_text(front + body, encoding="utf-8")

    status = "drift" if prev_file is not None else "captured"
    result = {
        "status": status,
        "file": str(out),
        "sha256": sha,
        "body_chars": len(body),
    }
    if status == "drift":
        result.update({
            "previous_file": str(prev_file),
            "old_sha256": old_sha,
            "note": "source changed — new version saved; flag drift to user and "
                    "update wiki pages",
        })
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
