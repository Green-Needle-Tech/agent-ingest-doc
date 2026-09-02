#!/usr/bin/env python3
"""capture_raw.py — capture a document into the LLM wiki raw/ layer.

Computes body sha256, writes frontmatter (source_url, ingested, sha256),
and detects drift on re-ingest. Stdlib only.

Hash scope: sha256 covers the captured body EXACTLY as stored in the file
after the frontmatter block and the `# Title` heading. To verify, read the
file, strip frontmatter + heading, and recompute — see scripts/verify_raw.py.

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
MIN_BODY_CHARS = 50


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:80] or "untitled"


def parse_fm_lines(lines) -> dict:
    """Parse `key: value` frontmatter lines into a dict."""
    fm = {}
    for line in lines:
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()
    return fm


def split_raw_file(text: str):
    """Return (frontmatter_dict, body_str) for a raw file's text.

    body_str is the captured body EXACTLY as stored — everything after the
    frontmatter block and the `# Title` heading. Line-based, no regex, so
    the sha256 round-trip in verify_raw.py is exact.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, text
    fm = parse_fm_lines(lines[1:end])
    after = "\n".join(lines[end + 1:]).lstrip("\n")
    if after.startswith("# "):
        _, _, body = after.partition("\n\n")
    else:
        body = after
    return fm, body


def load_raw_file(path: Path):
    return split_raw_file(path.read_text(encoding="utf-8"))


def version_sort_key(item):
    """Sort (path, fm) pairs for a slug: unsuffixed original first, then -v2, ..."""
    path = item[0]
    m = VERSION_RE.match(path.stem)
    num = int(m.group("num")) if m else 1
    return (num, path.stem)


def find_versions(raw_dir: Path, stem: str):
    """Return [(path, frontmatter)] for this exact slug: `<stem>.md` / `<stem>-vN.md`.

    Exact-stem matching only — `test-doc` must NOT match `test-document`
    (prefix collision).
    """
    out = []
    if not raw_dir.is_dir():
        return out
    for p in raw_dir.iterdir():
        if not p.is_file() or p.suffix != ".md":
            continue
        m = VERSION_RE.match(p.stem)
        if p.stem == stem or (m and m.group("slug") == stem):
            out.append((p, load_raw_file(p)[0]))
    return sorted(out, key=version_sort_key)


def find_by_source_url(raw_root: Path, source_url: str):
    """Scan ALL raw/ subdirs for files whose frontmatter source_url matches.

    Cross-subdir dedupe: the same URL ingested into a different subdir must
    not escape drift detection. Returns [(path, frontmatter)] sorted by path.
    """
    out = []
    if not raw_root.is_dir():
        return out
    for p in sorted(raw_root.rglob("*.md")):
        try:
            fm, _ = load_raw_file(p)
        except OSError:
            continue
        if fm.get("source_url") == source_url:
            out.append((p, fm))
    return out


def read_body(args) -> str:
    if args.input_file:
        return Path(args.input_file).read_text(encoding="utf-8")
    return sys.stdin.read()


def find_unchanged(candidates, url_matches, sha):
    """Return the path of an existing file with identical content, or None."""
    for path, fm in url_matches:
        if fm.get("sha256") == sha:
            return path, "source_url"
    for path, fm in candidates:
        if fm.get("sha256") == sha:
            return path, "slug"
    return None, None


def resolve_prev_version(candidates, url_matches):
    """Return (prev_path, old_sha) of the newest existing version, or (None, None)."""
    if candidates:
        path, fm = candidates[-1]
        return path, fm.get("sha256", "")
    if url_matches:
        path, fm = url_matches[-1]
        return path, fm.get("sha256", "")
    return None, None


def resolve_target_dir(prev_file, url_matches, candidates, stem, raw_dir):
    """Decide where a drift version lives: next to the existing chain, else
    the requested subdir. Returns (target_dir, candidates, stem)."""
    if prev_file is not None and url_matches and not candidates:
        # version chain belongs to the SOURCE, not the new title —
        # continue the existing file's stem (doc-a.md → doc-a-v2.md)
        target_dir = prev_file.parent
        m = VERSION_RE.match(prev_file.stem)
        stem = m.group("slug") if m else prev_file.stem
        candidates = find_versions(target_dir, stem)
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir, candidates, stem
    return raw_dir, candidates, stem


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

    body = read_body(args)
    if len(body.strip()) < MIN_BODY_CHARS:
        print(json.dumps({
            "status": "error",
            "error": "body too small — extraction likely failed; "
                     "climb the extraction ladder",
            "body_chars": len(body.strip()),
        }))
        return 1

    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    today = datetime.date.today().isoformat()
    stem = slugify(args.slug) if args.slug else slugify(args.title)
    wiki = Path(args.wiki).expanduser()
    raw_dir = wiki / "raw" / args.raw_subdir
    raw_dir.mkdir(parents=True, exist_ok=True)

    candidates = find_versions(raw_dir, stem)
    url_matches = find_by_source_url(wiki / "raw", args.source_url)

    unchanged, match_kind = find_unchanged(candidates, url_matches, sha)
    if unchanged is not None:
        print(json.dumps({
            "status": "unchanged",
            "file": str(unchanged),
            "note": f"identical content (matched by {match_kind}) — "
                    f"skip capture, update wiki pages only if needed",
        }))
        return 0

    prev_file, old_sha = resolve_prev_version(candidates, url_matches)
    target_dir, candidates, stem = resolve_target_dir(
        prev_file, url_matches, candidates, stem, raw_dir)

    suffix = args.version_suffix
    if not suffix:
        suffix = "" if not candidates else f"-v{len(candidates) + 1}"

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

    result = {
        "status": "drift" if prev_file is not None else "captured",
        "file": str(out),
        "sha256": sha,
        "body_chars": len(body),
    }
    if prev_file is not None:
        result.update({
            "previous_file": str(prev_file),
            "old_sha256": old_sha,
            "note": "source changed — new version saved; flag drift to user "
                    "and update wiki pages",
        })
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
