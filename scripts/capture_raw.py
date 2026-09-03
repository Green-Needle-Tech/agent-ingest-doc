#!/usr/bin/env python3
"""capture_raw.py — capture a document into the LLM wiki raw/ layer.

Supports two layouts (auto-detected via scripts/wiki_layout.py):

  karpathy (astro-han/karpathy-llm-wiki, default):
      raw/<topic>/<slug>.md with a blockquote metadata header:
          # Title
          > Source: {url or origin}
          > Collected: {YYYY-MM-DD}
          > Published: {YYYY-MM-DD or Unknown}
      The sha256 (hash scope: body after the header, i.e. everything
      after the first blank line following the title) is stored in a
      JSON sidecar `<slug>.json` — the .md format has no frontmatter,
      and check_evidence.py treats YAML frontmatter as body content.
      Optional --published-date prefixes the filename (YYYY-MM-DD-slug.md).
      --topic is free-form (any kebab-case topic directory).

  legacy (original Karpathy-gist layout):
      raw/<subdir>/<slug>.md with YAML frontmatter
      (source_url, ingested, sha256) — unchanged from v2.x.

Hash scope contract: sha256 covers the captured body EXACTLY as stored
after the metadata header (karpathy: title + blockquote header; legacy:
frontmatter + `# Title` heading). To verify, strip the header and
recompute — see scripts/verify_raw.py.

Version convention: the first capture is unsuffixed (implicitly v1). On
hash drift the next capture is saved as `<slug>-v<N>.md` where
N = 1 + max(existing version numbers) (gapped chains handled).

Dedup scope: drift/unchanged detection matches by slug within the target
topic dir AND by Source/source_url across ALL raw/ subdirs. Dedup
recomputes the stored body hash rather than trusting the stored hash.

Usage:
  capture_raw.py --wiki <root> --title "Doc Title" --source-url <url-or-path> \
      [--topic machine-learning] [--raw-subdir articles] [--slug custom-slug] \
      [--published-date YYYY-MM-DD] [--stdin | --input-file extracted.txt]

Body text comes from stdin or --input-file (extraction happens upstream
per the extraction ladder). Prints exactly ONE JSON object:
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_paths import default_wiki  # noqa: E402
from wiki_layout import detect_layout, raw_root  # noqa: E402

RAW_SUBDIRS = ("articles", "papers", "transcripts", "assets")
VERSION_RE = re.compile(r"^(?P<slug>.*)-v(?P<num>\d+)$")
SUFFIX_RE = re.compile(r"^-[a-zA-Z0-9][a-zA-Z0-9._-]{0,39}$")
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,59}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
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


def validate_topic(topic: str) -> str:
    """Validate a karpathy --topic directory name (kebab-case, no traversal)."""
    if not topic:
        return topic
    if not TOPIC_RE.match(topic):
        raise SystemExit(
            f"error: --topic must match ^[a-z0-9][a-z0-9._-]{{0,59}}$ "
            f"(got {topic!r}) — lowercase kebab-case, no spaces, no path "
            f"separators, no '..'")
    return topic


def validate_date(value: str) -> str:
    if not value:
        return value
    if not DATE_RE.match(value):
        raise SystemExit(
            f"error: --published-date must be YYYY-MM-DD (got {value!r})")
    return value


def validate_title(title: str) -> str:
    """Sanitize title for use in the # heading — strip control chars."""
    # Remove control characters but keep normal whitespace
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", title).strip()


# ---------- karpathy-layout raw file parsing ----------

def split_karpathy_raw(text: str):
    """Return (header_dict, body_str) for a karpathy-format raw file.

    Body is everything after the contiguous block of `# Title` +
    `> metadata` lines (blank lines between them are tolerated) — the
    exact hash scope. Line-based, no backtracking regexes.
    """
    lines = text.split("\n")
    i = 0
    if i < len(lines) and lines[i].startswith("# "):
        i += 1
    # tolerate blank lines between the title and the metadata block
    while i < len(lines) and not lines[i].strip():
        i += 1
    header = {}
    while i < len(lines) and lines[i].startswith(">"):
        line = lines[i].lstrip(">").strip()
        if ":" in line:
            key, _, value = line.partition(":")
            header[key.strip()] = value.strip()
        i += 1
    # skip blank line(s) between header and body
    while i < len(lines) and not lines[i].strip():
        i += 1
    body = "\n".join(lines[i:])
    # The stored body's trailing newline is part of the hash scope only if
    # present; normalize: hash scope = body as joined above.
    return header, body


def load_raw_file(path: Path, layout: str):
    if layout == "karpathy":
        return split_karpathy_raw(path.read_text(encoding="utf-8"))
    return split_legacy_raw(path.read_text(encoding="utf-8"))


# ---------- legacy-layout raw file parsing ----------

def parse_fm_lines(lines) -> dict:
    """Parse `key: value` frontmatter lines into a dict."""
    fm = {}
    for line in lines:
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()
    return fm


def split_legacy_raw(text: str):
    """Return (frontmatter_dict, body_str) for a legacy raw file's text.

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


# ---------- shared helpers ----------

def version_sort_key(item):
    """Sort (path, fm) pairs for a slug: unsuffixed original first, then -v2, ..."""
    path = item[0]
    m = VERSION_RE.match(path.stem)
    num = int(m.group("num")) if m else 1
    return (num, path.stem)


def find_versions(raw_dir: Path, stem: str, layout: str):
    """Return [(path, header)] for this exact slug: `<stem>.md` / `<stem>-vN.md`.

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
            out.append((p, load_raw_file(p, layout)[0]))
    return sorted(out, key=version_sort_key)


def find_by_source(raw_root: Path, source: str, layout: str):
    """Scan ALL raw/ subdirs for files whose Source (karpathy) or
    source_url (legacy) header matches.

    Cross-subdir dedupe: the same URL ingested into a different subdir must
    not escape drift detection. Returns [(path, header)] sorted by path.
    """
    key = "Source" if layout == "karpathy" else "source_url"
    out = []
    if not raw_root.is_dir():
        return out
    for p in sorted(raw_root.rglob("*.md")):
        try:
            header, _ = load_raw_file(p, layout)
        except OSError:
            continue
        if header.get(key) == source:
            out.append((p, header))
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


def stored_hash(path: Path, layout: str, sidecar_dir: Path | None = None) -> str:
    """Return the sha256 of the stored body for a raw file.

    karpathy layout: the hash lives in the JSON sidecar (the .md format has
    no frontmatter). If the sidecar is missing, the hash is unknown —
    return "" so the caller treats the file as unverifiable (never
    falsely reports unchanged).
    legacy layout: the hash is in the frontmatter.
    """
    if layout == "karpathy":
        sidecar = path.with_suffix(".json")
        if sidecar.is_file():
            try:
                return json.loads(sidecar.read_text(encoding="utf-8")).get(
                    "sha256", "")
            except (OSError, json.JSONDecodeError):
                return ""
        return ""
    _, body = load_raw_file(path, layout)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def recompute_stored_hash(path: Path, layout: str) -> str:
    """Recompute the sha256 of the stored body (after the metadata header)."""
    _, body = load_raw_file(path, layout)
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


def find_unchanged(candidates, source_matches, sha, layout):
    """Return the path of an existing file with identical content, or None.

    Recomputes the stored body hash instead of trusting the recorded hash,
    so a file that was edited after ingest (without updating its hash) is
    NOT falsely reported as unchanged.
    """
    def recorded_hash(path, header):
        if layout == "legacy":
            return header.get("sha256", "")
        return stored_hash(path, layout)  # karpathy: sidecar JSON

    for path, header in source_matches:
        if recorded_hash(path, header) == sha:
            try:
                actual = recompute_stored_hash(path, layout)
            except OSError:
                continue
            if actual == sha:
                return path, "source"
    for path, header in candidates:
        if recorded_hash(path, header) == sha:
            try:
                actual = recompute_stored_hash(path, layout)
            except OSError:
                continue
            if actual == sha:
                return path, "slug"
    return None, None


def resolve_prev_version(candidates, source_matches):
    """Return (prev_path, old_sha) of the newest existing version, or (None, None)."""
    if candidates:
        path, header = candidates[-1]
        return path, header.get("sha256", "")
    if source_matches:
        path, header = source_matches[-1]
        return path, header.get("sha256", "")
    return None, None


def resolve_target_dir(prev_file, source_matches, candidates, stem, raw_dir,
                       layout):
    """Decide where a drift version lives: next to the existing chain, else
    the requested subdir. Returns (target_dir, candidates, stem)."""
    if prev_file is not None and source_matches and not candidates:
        # version chain belongs to the SOURCE, not the new title —
        # continue the existing file's stem (doc-a.md → doc-a-v2.md)
        target_dir = prev_file.parent
        m = VERSION_RE.match(prev_file.stem)
        stem = m.group("slug") if m else prev_file.stem
        candidates = find_versions(target_dir, stem, layout)
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


def write_karpathy_raw(out: Path, title: str, source: str, today: str,
                      published: str, body: str, sha: str) -> None:
    """Write a karpathy-format raw file + sha256 JSON sidecar."""
    header = (
        f"# {title}\n\n"
        f"> Source: {source}\n"
        f"> Collected: {today}\n"
        f"> Published: {published}\n\n"
    )
    atomic_write(out, header + body)
    sidecar = out.with_suffix(".json")
    manifest = {
        "schema_version": 2,
        "sha256": sha,
        "source_uri": source,
        "collected": today,
        "published": published,
    }
    atomic_write(sidecar, json.dumps(manifest, indent=2) + "\n")


def write_legacy_raw(out: Path, title: str, source_url: str, today: str,
                     body: str, sha: str) -> None:
    """Write a legacy-format raw file (YAML frontmatter)."""
    front = (
        "---\n"
        f"source_url: {source_url}\n"
        f"ingested: {today}\n"
        f"sha256: {sha}\n"
        "---\n\n"
        f"# {title}\n\n"
    )
    atomic_write(out, front + body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=str(default_wiki()),
                    help="wiki root (default: $WIKI_PATH or <real home>/wiki). "
                         "For the karpathy layout this is the PROJECT ROOT "
                         "containing raw/ and wiki/ (auto-detected).")
    ap.add_argument("--title", required=True)
    ap.add_argument("--source-url", required=True,
                    help="source URL or origin description")
    ap.add_argument("--topic", default="articles",
                    help="karpathy layout: free-form raw/<topic> directory "
                         "(default: articles)")
    ap.add_argument("--raw-subdir", default=None, choices=list(RAW_SUBDIRS),
                    help="legacy layout: raw/ subdir (default: articles). "
                         "Ignored in karpathy layout — use --topic.")
    ap.add_argument("--slug", default=None,
                    help="override the filename slug (default: slugified title)")
    ap.add_argument("--published-date", default=None,
                    help="karpathy layout: source published date YYYY-MM-DD; "
                         "prefixes the filename and sets the Published header")
    ap.add_argument("--input-file")
    ap.add_argument("--version-suffix", default="",
                    help="override the version suffix (default: auto -vN on drift)")
    ap.add_argument("--extractor", default="",
                    help="extractor name (e.g. pymupdf4llm, docling, web_extract, manual)")
    ap.add_argument("--source-kind", default="url",
                    choices=["url", "file", "paste"],
                    help="source type (default: url)")
    args = ap.parse_args()

    # Validate metadata inputs before any filesystem work
    validate_no_control_chars(args.source_url, "source-url")
    validate_no_control_chars(args.extractor, "extractor")
    validate_suffix(args.version_suffix)
    topic = validate_topic(args.topic)
    published = validate_date(args.published_date)
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
    root = safe_wiki_path(args.wiki)
    layout, root = detect_layout(root)

    if layout == "karpathy":
        raw_dir = raw_root(layout, root) / topic
    else:
        raw_dir = raw_root(layout, root) / (args.raw_subdir or "articles")
    raw_dir.mkdir(parents=True, exist_ok=True)

    source = args.source_url
    candidates = find_versions(raw_dir, stem, layout)
    source_matches = find_by_source(raw_root(layout, root), source, layout)

    unchanged, match_kind = find_unchanged(candidates, source_matches, sha, layout)
    if unchanged is not None:
        print(json.dumps({
            "status": "unchanged",
            "file": str(unchanged),
            "layout": layout,
            "note": f"identical content (matched by {match_kind}) — "
                    f"skip capture, update wiki pages only if needed",
        }))
        return 0

    prev_file, old_sha = resolve_prev_version(candidates, source_matches)
    if layout == "karpathy" and prev_file is not None:
        old_sha = stored_hash(prev_file, layout)
    target_dir, candidates, stem = resolve_target_dir(
        prev_file, source_matches, candidates, stem, raw_dir, layout)

    suffix = args.version_suffix
    if not suffix:
        if not candidates:
            suffix = ""
        else:
            # Use max version + 1, not count + 1, to handle gapped chains
            suffix = f"-v{max_version_number(candidates) + 1}"

    if layout == "karpathy":
        date_prefix = f"{published}-" if published else ""
        out = target_dir / f"{date_prefix}{stem}{suffix}.md"
        write_karpathy_raw(out, title, source, today,
                           published or "Unknown", body, sha)
        # extraction manifest (separate from the sha sidecar, appended)
        if args.extractor or args.source_kind != "url":
            sidecar = out.with_suffix(".json")
            manifest = json.loads(sidecar.read_text(encoding="utf-8"))
            manifest["source_kind"] = args.source_kind
            manifest["retrieved_at"] = datetime.datetime.now(
                datetime.timezone.utc).isoformat()
            manifest["extraction_sha256"] = sha
            manifest["extractor"] = args.extractor or "unknown"
            sidecar.write_text(json.dumps(manifest, indent=2) + "\n",
                               encoding="utf-8")
    else:
        out = target_dir / f"{stem}{suffix}.md"
        write_legacy_raw(out, title, source, today, body, sha)
        # extraction manifest sidecar
        manifest_path = out.with_suffix(".json")
        manifest = {
            "schema_version": 1,
            "source_uri": source,
            "source_kind": args.source_kind,
            "retrieved_at": datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
            "extraction_sha256": sha,
            "extractor": args.extractor or "unknown",
            "raw_file": out.name,
        }
        if prev_file is not None:
            manifest["parent_version"] = str(prev_file)
        atomic_write(manifest_path, json.dumps(manifest, indent=2))

    result = {
        "status": "drift" if prev_file is not None else "captured",
        "file": str(out),
        "layout": layout,
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
