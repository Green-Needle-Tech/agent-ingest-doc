#!/usr/bin/env python3
"""verify_raw.py — lint the wiki raw/ layer after an ingest.

Executes the skill's Verification section as code. Stdlib only.

Checks (all raw/*.md under wiki/raw/):
  1. body non-empty (>=50 chars) — no empty/failed captures
  2. sha256 in frontmatter matches the recomputed hash of the stored body
     (frontmatter + `# Title` heading stripped, per the hash-scope contract)
  3. source_url present
  4. no duplicate source_url across subdirs (same doc captured twice)

Checks (wiki navigation, when present):
  5. index.md "Total pages" == count of page files in entities/ concepts/
     comparisons/ queries/ (only when index.md declares a total)
  6. log.md last entry matches the parseable `## [date] action | subject`
     prefix format

Usage:
  verify_raw.py --wiki ~/wiki [--json]
Exit 0 = all checks pass; 1 = failures (also printed).
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")
LOG_PREFIX_RE = re.compile(r"^## \[\d{4}-\d{2}-\d{2}\] [^|]+\|")
MIN_BODY_CHARS = 50


def split_raw_file(text: str):
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


def check_raw_file(path: Path, seen_urls: dict, failures: list):
    """Run checks 1-4 on one raw file. Returns nothing; appends failures."""
    fm, body = split_raw_file(path.read_text(encoding="utf-8"))

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
    elif url in seen_urls:
        failures.append(f"{path}: duplicate source_url {url} "
                        f"(also in {seen_urls[url]})")
    else:
        seen_urls[url] = path


def check_index_total(wiki: Path, failures: list):
    index = wiki / "index.md"
    if not index.is_file():
        return
    m = re.search(r"Total pages:\s*(\d+)", index.read_text(encoding="utf-8"))
    if not m:
        return
    declared = int(m.group(1))
    on_disk = sum(len(list((wiki / d).glob("*.md"))) for d in PAGE_DIRS
                 if (wiki / d).is_dir())
    if declared != on_disk:
        failures.append(
            f"index.md: Total pages {declared} != {on_disk} page files on "
            f"disk (entities/concepts/comparisons/queries)")


def check_log_format(wiki: Path, failures: list):
    log = wiki / "log.md"
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
    ap.add_argument("--wiki", default="~/wiki")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    wiki = Path(args.wiki).expanduser()
    raw_root = wiki / "raw"
    failures = []
    checked = 0
    seen_urls = {}

    if raw_root.is_dir():
        for p in sorted(raw_root.rglob("*.md")):
            if p.name.startswith("."):
                continue
            checked += 1
            check_raw_file(p, seen_urls, failures)
    else:
        failures.append(f"{raw_root}: raw/ directory not found")

    check_index_total(wiki, failures)
    check_log_format(wiki, failures)

    if args.as_json:
        print(json.dumps({
            "wiki": str(wiki),
            "raw_files_checked": checked,
            "failures": failures,
            "status": "ok" if not failures else "fail",
        }, indent=2))
    else:
        print(f"verify_raw: {checked} raw files checked in {wiki}")
        for f in failures:
            print(f"FAIL {f}")
        print("OK" if not failures else f"{len(failures)} failure(s)")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
