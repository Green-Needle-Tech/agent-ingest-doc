#!/usr/bin/env python3
"""verify_raw.py — lint the wiki raw/ layer after an ingest.

Executes the skill's Verification section as code. Stdlib only.

Checks (all raw/*.md under wiki/raw/):
  1. body non-empty (>=50 chars) — no empty/scam captures
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
Exit 0 = all checks pass; 1 = failures (also prints them).
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")
LOG_PREFIX_RE = re.compile(r"^##\s+\[\d{4}-\d{2}-\d{2}\]\s+\S.*\|.*$")


def strip_frontmatter_and_heading(text: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    body = (m.group(2) if m else text).lstrip("\n")
    return re.sub(r"^#\s+[^\n]*\n\n?", "", body, count=1)


def parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


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
            text = p.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)

            # 1. body non-empty
            body = strip_frontmatter_and_heading(text)
            if len(body.strip()) < 50:
                failures.append(f"{p}: body too small ({len(body.strip())} chars) "
                                f"— empty capture or extraction failure")

            # 2. sha256 matches recomputed hash of stored body
            stored_sha = fm.get("sha256")
            if not stored_sha:
                failures.append(f"{p}: missing sha256 in frontmatter")
            else:
                recomputed = hashlib.sha256(
                    body.encode("utf-8")).hexdigest()
                if stored_sha != recomputed:
                    failures.append(
                        f"{p}: sha256 mismatch — stored {stored_sha[:12]}… "
                        f"!= recomputed {recomputed[:12]}… "
                        f"(hash scope: body after frontmatter + title heading)")

            # 3. source_url present
            if not fm.get("source_url"):
                failures.append(f"{p}: missing source_url in frontmatter")

            # 4. duplicate source_url
            url = fm.get("source_url")
            if url:
                if url in seen_urls:
                    failures.append(f"{p}: duplicate source_url {url} "
                                    f"(also in {seen_urls[url]})")
                else:
                    seen_urls[url] = p
    else:
        failures.append(f"{raw_root}: raw/ directory not found")

    # 5. index.md total pages vs disk
    index = wiki / "index.md"
    if index.is_file():
        idx_text = index.read_text(encoding="utf-8")
        m = re.search(r"Total pages:\s*(\d+)", idx_text)
        if m:
            declared = int(m.group(1))
            on_disk = sum(
                (len(list((wiki / d).glob("*.md"))) for d in PAGE_DIRS
                 if (wiki / d).is_dir()))
            if declared != on_disk:
                failures.append(
                    f"index.md: Total pages {declared} != {on_disk} page files "
                    f"on disk (entities/concepts/comparisons/queries)")

    # 6. log.md prefix format
    log = wiki / "log.md"
    if log.is_file():
        log_text = log.read_text(encoding="utf-8")
        headings = [ln for ln in log_text.splitlines()
                    if ln.startswith("## ")]
        if headings and not LOG_PREFIX_RE.match(headings[-1]):
            failures.append(
                f"log.md: last entry '{headings[-1][:60]}' does not match "
                f"'## [YYYY-MM-DD] action | subject' format")

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
