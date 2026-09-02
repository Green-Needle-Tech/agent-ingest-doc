#!/usr/bin/env python3
"""doctor.py — health check for doc-ingest dependencies.

Checks:
  1. Python version (>= 3.10)
  2. Writable wiki path
  3. SCHEMA.md present in wiki
  4. Hindsight health (default http://localhost:8888, or $HINDSIGHT_URL)
  5. Extraction tools: pymupdf4llm, docling, marker, tesseract
  6. llm-wiki skill present in the Hermes skills directory

Defaults are host-portable (no hardcoded /root): wiki = $WIKI_PATH or
<real home>/wiki; Hermes skills = <hermes home>/skills where hermes home =
$HERMES_HOME or <real home>/.hermes — see hermes_paths.py.

Usage:
  python3 scripts/doctor.py [--wiki PATH] [--hindsight-url URL]
  Exit 0 = all pass, 1 = any fail/warn.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_paths import default_hindsight_url, default_wiki, hermes_home  # noqa: E402

PY_MIN = (3, 10)


def check(name: str, fn) -> dict:
    """Run a check function, return result dict."""
    try:
        ok, detail = fn()
        status = "PASS" if ok else "FAIL"
    except Exception as e:
        status = "FAIL"
        detail = str(e)
        ok = False
    return {"name": name, "status": status, "detail": detail, "ok": ok}


def check_python():
    v = sys.version_info
    ok = v >= PY_MIN
    return ok, f"Python {v.major}.{v.minor}.{v.micro}"


def check_wiki_writable(wiki: Path):
    ok = wiki.exists() and wiki.is_dir() and os.access(str(wiki), os.W_OK)
    if not wiki.exists():
        try:
            wiki.mkdir(parents=True, exist_ok=True)
            ok = True
        except OSError:
            ok = False
    return ok, f"wiki at {wiki} ({'writable' if ok else 'not writable'})"


def check_schema(wiki: Path):
    schema = wiki / "SCHEMA.md"
    ok = schema.is_file()
    return ok, f"SCHEMA.md {'present' if ok else 'missing'} at {schema}"


def check_hindsight(url: str):
    try:
        with urlopen(f"{url}/health", timeout=5) as resp:
            ok = resp.status == 200
            return ok, f"Hindsight at {url} (HTTP {resp.status})"
    except (URLError, OSError, AttributeError) as e:
        return False, f"Hindsight at {url} unreachable: {e}"


def check_tool(name: str, cmd: list):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=5)
        ok = r.returncode == 0
        return ok, f"{name} {'available' if ok else 'unavailable'}"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, f"{name} not found"


def check_python_pkg(name: str):
    try:
        __import__(name)
        return True, f"{name} installed"
    except ImportError:
        return False, f"{name} not installed"


def check_llm_wiki_skill():
    skills_root = hermes_home() / "skills"
    skill_paths = [
        skills_root / "research" / "llm-wiki",
        skills_root / "llm-wiki",
    ]
    for p in skill_paths:
        if p.is_dir():
            return True, f"llm-wiki skill at {p}"
    return False, f"llm-wiki skill not found under {skills_root}"


def main():
    ap = argparse.ArgumentParser(description="doc-ingest health check")
    ap.add_argument("--wiki", default=str(default_wiki()))
    ap.add_argument("--hindsight-url", default=default_hindsight_url())
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    wiki = Path(args.wiki).expanduser()

    checks = [
        check("Python version", check_python),
        check("Wiki path writable", lambda: check_wiki_writable(wiki)),
        check("SCHEMA.md", lambda: check_schema(wiki)),
        check("Hindsight health", lambda: check_hindsight(args.hindsight_url)),
        check("pymupdf4llm", lambda: check_python_pkg("pymupdf4llm")),
        check("docling", lambda: check_python_pkg("docling")),
        check("marker", lambda: check_python_pkg("marker")),
        check("tesseract", lambda: check_tool("tesseract", ["tesseract", "--version"])),
        check("llm-wiki skill", check_llm_wiki_skill),
    ]

    passed = sum(1 for c in checks if c["ok"])
    failed = sum(1 for c in checks if not c["ok"])

    if args.as_json:
        print(json.dumps({
            "total": len(checks),
            "passed": passed,
            "failed": failed,
            "checks": checks,
        }, indent=2))
    else:
        for c in checks:
            print(f"{c['status']:4} {c['name']:20} {c['detail']}")
        print(f"\n{passed} passed, {failed} failed")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
