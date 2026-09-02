#!/usr/bin/env python3
"""capture_raw.py — capture a document into the LLM wiki raw/ layer.

Computes body sha256, writes frontmatter (source_url, ingested, sha256),
and detects drift on re-ingest. Stdlib only.

Hash scope: sha256 covers the captured body EXACTLY as stored in the file
after the frontmatter block and the `# Title` heading. To verify, read the
file, strip frontmatter + heading, and recompute — see scripts/verify_raw.py.

Version convention: the first capture is unsuffixed (implicitly v1). On hash
drift the next capture is saved as `<slug>-v<N>.md` where N = 1 + max(existing
version numbers) (so the second version is -v2, and gapped chains are handled
correctly).

Dedup scope: drift/unchanged detection matches by slug within the target
subdir AND by source_url across ALL raw/ subdirs (same URL ingested into a
different subdir is still detected). Dedup recomputes the stored body hash
rather than trusting the frontmatter hash, so a file edited after ingest is
detected as drift, not falsely reported as unchanged.

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
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

RAW_SUBDIRS = ("articles", "papers", "transcripts", "assets")
VERSION_RE = re.compile(r"^(?P<slug>.*)-v(?P<num>\d+)$")
SUFFIX_RE = re.compile(r"^-[a-zA-Z0-9][a-zA-Z0-9._-]{0,39}$")
MIN_BODY_CHARS = 50


def slugify(title: str) -> str:
    """Unicode-safe slugify: normalizes, keeps CJK/alphanumeric, hyphenates."""
    s = unicodedata.normalize("NFKC", title).lower()
    s = re.sub(r"[^\w\-]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    s = s[:80]
    if s:
        return s
    # All non-word characters (e.g. pure CJK that \w doesn't cover in some
    # Python builds) — fall back to a short hash of the title for uniqueness.
    return "untitled-" + hashlib.sha256(title.encode("utf-8")).hexdigest()[:8]


def validate_no_control_chars(value: str, field_name: str) -> str:
    """Reject newlines and control characters in metadata fields."""
    if not value:
        return value
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise SystemExit(
            f"error: --{field_name} must not contain control characters "
            f"or newlines (got {value!r})")
    return value


def validate_suffix(suffix: str) -> str:
    """Validate --version-suffix against a safe pattern.

    Rejects path separators, '..', control chars, spaces, and other
    characters that could corrupt filenames or crash the script.
    Empty string is valid (means 'no suffix, use auto').
    """
    if not suffix:
        return suffix
    if not SUFFIX_RE.match(suffix):
        raise SystemExit(
            f"error: --version-suffix must match ^-[a-zA-Z0-9][a-zA-Z0-9._-]{{0,39}}$ "
            f"(got {suffix!r}) — no spaces, path separators, '..', or "
            f"control characters allowed")
    return suffix


def validate_title(title: str) -> str:
    """Sanitize title for use in the # heading — strip control chars."""
    # Remove control characters but keep normal whitespace
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", title).strip()


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


def max_version_number(candidates) -> int:
    """Return the maximum version number in a candidate list (unsuffixed = 1)."""
    highest = 0
    for path, _ in candidates:
        m = VERSION_RE.match(path.stem)
        num = int(m.group("num")) if m else 1
        if num > highest:
            highest = num
    return highest


def recompute_stored_hash(path: Path) -> str:
    """Recompute the sha256 of the stored body (after frontmatter + heading)."""
    _, body = load_raw_file(path)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def read_body(args) -> str:
    if not args.input_file:
        return sys.stdin.read()
    path = Path(args.input_file).expanduser().resolve()
    # validate before touching the filesystem (SonarCloud S8707):
    # must be an existing regular file, not a directory or special node
    if not path.is_file():
        raise SystemExit(
            f"error: --input-file must be an existing regular file "
            f"(got {args.input_file!r})")
    return path.read_text(encoding="utf-8")


def safe_wiki_path(raw_wiki: str) -> Path:
    """Expand ~ in the wiki path but reject path traversal — the slug, subdir,
    and output filename are all agent-controlled, so only the --wiki root
    needs guarding (SonarCloud S8707: LLM-driven CLI args must not escape
    the intended wiki directory)."""
    wiki = Path(raw_wiki).expanduser()
    resolved = wiki.resolve()
    if ".." in Path(raw_wiki).parts:
        raise SystemExit(
            f"error: --wiki path must not contain '..' (got {raw_wiki!r})")
    return resolved


def find_unchanged(candidates, url_matches, sha):
    """Return the path of an existing file with identical content, or None.

    Recomputes the stored body hash instead of trusting the frontmatter hash,
    so a file that was edited after ingest (without updating frontmatter) is
    NOT falsely reported as unchanged.
    """
    for path, fm in url_matches:
        if fm.get("sha256") == sha:
            # Verify the stored body actually matches the frontmatter hash
            try:
                actual = recompute_stored_hash(path)
            except OSError:
                continue
            if actual == sha:
                return path, "source_url"
    for path, fm in candidates:
        if fm.get("sha256") == sha:
            try:
                actual = recompute_stored_hash(path)
            except OSError:
                continue
            if actual == sha:
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


def atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write text atomically: temp file in same dir, flush, rename.

    Refuses to overwrite an existing target.
    """
    if path.exists():
        raise SystemExit(
            f"error: target file already exists: {path} "
            f"(use a different --version-suffix or remove the existing file)")
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(parent), prefix=".tmp_", suffix=".md", text=True)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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

    # Validate metadata inputs before any filesystem work
    validate_no_control_chars(args.source_url, "source-url")
    validate_suffix(args.version_suffix)
    title = validate_title(args.title)

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
    wiki = safe_wiki_path(args.wiki)
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
        if not candidates:
            suffix = ""
        else:
            # Use max version + 1, not count + 1, to handle gapped chains
            suffix = f"-v{max_version_number(candidates) + 1}"

    out = target_dir / f"{stem}{suffix}.md"
    front = (
        "---\n"
        f"source_url: {args.source_url}\n"
        f"ingested: {today}\n"
        f"sha256: {sha}\n"
        "---\n\n"
        f"# {title}\n\n"
    )
    atomic_write(out, front + body)

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
