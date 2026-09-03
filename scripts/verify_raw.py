#!/usr/bin/env python3
"""verify_raw.py — lint the wiki raw/ layer after an ingest.

Supports both layouts (auto-detected via scripts/wiki_layout.py):

karpathy (astro-han/karpathy-llm-wiki):
  raw/*.md checks:
    1. body non-empty (>=50 chars) — no empty/failed captures
    2. sha256 round-trip: sidecar <slug>.json `sha256` matches the
       recomputed hash of the stored body (after the blockquote header)
    3. Source header present
    4. no duplicate Source across separate version chains
  wiki/index.md: no "Total pages" count (topic tables instead) — check
    that every wiki/**/*.md article (excluding index.md/log.md) that has
    a Raw field has resolvable raw links? — NO, that is check_evidence.py's
    job (karpathy-llm-wiki's script). This script checks only what it owns.
  wiki/log.md: last entry matches `## [YYYY-MM-DD] action | subject`

legacy (original Karpathy-gist layout):
    1. body non-empty (>=50 chars)
    2. sha256 frontmatter matches recomputed hash of stored body
    3. source_url present
    4. no duplicate source_url across separate version chains
  index.md "Total pages" == count of page files in entities/ concepts/
  comparisons/ queries/ (only when index.md declares a total)
  log.md last entry matches `## [date] action | subject`

Usage:
  verify_raw.py --wiki <root> [--json]
Exit 0 = all checks pass; 1 = failures (also printed).
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_paths import default_wiki  # noqa: E402
from wiki_layout import detect_layout, raw_root, wiki_dir  # noqa: E402

LEGACY_PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")
LOG_PREFIX_RE = re.compile(r"^## \[\d{4}-\d{2}-\d{2}\] [^|]+\|")
VERSION_RE = re.compile(r"^(?P<slug>.*)-v(?P<num>\d+)$")
MIN_BODY_CHARS = 50


def split_legacy_raw(text: str):
    """Return (frontmatter_dict, body_str) — same contract as capture_raw."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, text
    fm = {}
    for line in lines[1:end]:
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()
    after = "\n".join(lines[end + 1:]).lstrip("\n")
    if after.startswith("# "):
        _, _, body = after.partition("\n\n")
    else:
        body = after
    return fm, body


def split_karpathy_raw(text: str):
    """Return (header_dict, body_str) for a karpathy-format raw file."""
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
    while i < len(lines) and not lines[i].strip():
        i += 1
    body = "\n".join(lines[i:])
    return header, body


def get_chain_key(path: Path) -> str:
    """Return a chain identity key: parent_dir/stem (without -vN suffix).

    Files in the same directory with the same base stem (e.g. doc.md,
    doc-v2.md, doc-v3.md) belong to one version chain.
    """
    m = VERSION_RE.match(path.stem)
    base_stem = m.group("slug") if m else path.stem
    return str(path.parent / base_stem)


def check_karpathy_raw_file(path: Path, failures: list):
    """Run karpathy checks 1-3 on one raw file.

    Returns (source, chain_key) or (None, None).
    """
    header, body = split_karpathy_raw(path.read_text(encoding="utf-8"))

    if len(body.strip()) < MIN_BODY_CHARS:
        failures.append(f"{path}: body too small ({len(body.strip())} chars) "
                        f"— empty capture or extraction failure")

    recomputed = hashlib.sha256(body.encode("utf-8")).hexdigest()
    sidecar = path.with_suffix(".json")
    if sidecar.is_file():
        try:
            stored_sha = json.loads(
                sidecar.read_text(encoding="utf-8")).get("sha256", "")
        except (OSError, json.JSONDecodeError) as e:
            failures.append(f"{path}: unreadable sidecar {sidecar.name}: {e}")
            stored_sha = ""
        if stored_sha and stored_sha != recomputed:
            failures.append(
                f"{path}: sha256 mismatch — sidecar {stored_sha[:12]}… "
                f"!= recomputed {recomputed[:12]}… "
                f"(hash scope: body after title + blockquote header)")
    else:
        # Sidecar written by an older tool or by hand — not an error, but
        # the file cannot be drift-checked. Only flag when the body is
        # otherwise fine so the signal stays clean.
        pass

    source = header.get("Source")
    if not source:
        failures.append(f"{path}: missing Source header")
        return None, None

    return source, get_chain_key(path)


def check_legacy_raw_file(path: Path, failures: list):
    """Run legacy checks 1-3 on one raw file."""
    fm, body = split_legacy_raw(path.read_text(encoding="utf-8"))

    if len(body.strip()) < MIN_BODY_CHARS:
        failures.append(f"{path}: body too small ({len(body.strip())} chars) "
                        f"— empty capture or extraction failure")

    stored_sha = fm.get("sha256")
    if not stored_sha:
        failures.append(f"{path}: missing sha256 in frontmatter")
    else:
        recomputed = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if stored_sha != recomputed:
            failures.append(
                f"{path}: sha256 mismatch — stored {stored_sha[:12]}… "
                f"!= recomputed {recomputed[:12]}… "
                f"(hash scope: body after frontmatter + title heading)")

    url = fm.get("source_url")
    if not url:
        failures.append(f"{path}: missing source_url in frontmatter")
        return None, None

    return url, get_chain_key(path)


def check_duplicate_sources(source_chains: dict, failures: list):
    """No duplicate Source/source_url across separate version chains.

    source_chains maps source -> set of chain_keys. A source appearing in
    multiple distinct chains is a duplicate error. A source in one chain
    (with multiple -vN files) is valid drift.
    """
    for source, chains in source_chains.items():
        if len(chains) > 1:
            failures.append(
                f"duplicate Source {source} in {len(chains)} independent "
                f"chains: {', '.join(sorted(chains))}")


def check_index_total(wiki: Path, failures: list):
    index = wiki / "index.md"
    if not index.is_file():
        return
    m = re.search(r"Total pages:\s*(\d+)", index.read_text(encoding="utf-8"))
    if not m:
        return
    declared = int(m.group(1))
    on_disk = sum(len(list((wiki / d).glob("*.md"))) for d in LEGACY_PAGE_DIRS
                 if (wiki / d).is_dir())
    if declared != on_disk:
        failures.append(
            f"index.md: Total pages {declared} != {on_disk} page files on "
            f"disk (entities/concepts/comparisons/queries)")


def check_log_format(log: Path, failures: list):
    if not log.is_file():
        return
    headings = [ln for ln in log.read_text(encoding="utf-8").splitlines()
                if ln.startswith("## ")]
    if headings and not LOG_PREFIX_RE.match(headings[-1]):
        failures.append(
            f"log.md: last entry '{headings[-1][:60]}' does not match "
            f"'## [YYYY-MM-DD] action | subject' format")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=str(default_wiki()),
                    help="wiki root (default: $WIKI_PATH or <real home>/wiki). "
                         "karpathy layout: the PROJECT ROOT containing raw/ "
                         "and wiki/ (auto-detected).")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    root = Path(args.wiki).expanduser()
    layout, root = detect_layout(root)
    raw = raw_root(layout, root)
    wdir = wiki_dir(layout, root)
    failures = []
    checked = 0
    source_chains: dict = {}

    if raw.is_dir():
        raw_files = [p for p in sorted(raw.rglob("*.md"))
                     if not p.name.startswith(".")]
        checked = len(raw_files)
        for p in raw_files:
            if layout == "karpathy":
                source, chain_key = check_karpathy_raw_file(p, failures)
            else:
                source, chain_key = check_legacy_raw_file(p, failures)
            if source and chain_key:
                source_chains.setdefault(source, set()).add(chain_key)
    else:
        failures.append(f"{raw}: raw/ directory not found")

    check_duplicate_sources(source_chains, failures)
    if layout == "legacy":
        check_index_total(wdir, failures)
    check_log_format(wdir / "log.md", failures)

    if args.as_json:
        print(json.dumps({
            "wiki": str(root),
            "layout": layout,
            "raw_files_checked": checked,
            "failures": failures,
            "status": "ok" if not failures else "fail",
        }, indent=2))
    else:
        print(f"verify_raw [{layout}]: {checked} raw files checked in {root}")
        for f in failures:
            print(f"FAIL {f}")
        print("OK" if not failures else f"{len(failures)} failure(s)")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
