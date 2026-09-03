#!/usr/bin/env python3
"""doctor.py — health check for doc-ingest dependencies.

Checks:
  1. Python version (>= 3.10)
  2. Writable wiki path
  3. Layout detection (karpathy = astro-han/karpathy-llm-wiki, or legacy)
  4. karpathy layout: wiki/index.md + wiki/log.md present
     legacy layout: SCHEMA.md present
  5. Hindsight health (default http://localhost:8888, or $HINDSIGHT_URL)
  6. Extraction tools: pymupdf4llm, docling, marker, tesseract
  7. LLM wiki skill present (karpathy-llm-wiki or llm-wiki) in the Hermes
     skills directory, OR check_evidence.py runnable (the karpathy-llm-wiki
     lint script)

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
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_paths import default_hindsight_url, default_wiki, hermes_home  # noqa: E402
from wiki_layout import detect_layout, wiki_dir  # noqa: E402

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


def check_layout(root: Path):
    layout, detected_root = detect_layout(root)
    return True, f"layout: {layout} (root {detected_root})"


def check_structure(layout: str, root: Path):
    wdir = wiki_dir(layout, root)
    if layout == "karpathy":
        missing = [n for n in ("index.md", "log.md")
                   if not (wdir / n).is_file()]
        if missing or not wdir.is_dir():
            return False, f"karpathy wiki structure incomplete at {wdir} " \
                          f"(missing: {', '.join(missing) or 'wiki/'}; " \
                          f"run init_wiki.py)"
        return True, f"karpathy wiki at {wdir} (index.md + log.md present)"
    schema = root / "SCHEMA.md"
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


def check_wiki_skill():
    """LLM wiki skill: karpathy-llm-wiki (preferred) or llm-wiki, under the
    Hermes skills dir; or a runnable check_evidence.py next to either."""
    skills_root = hermes_home() / "skills"
    candidates = []
    for pattern in ("karpathy-llm-wiki", "llm-wiki"):
        for p in skills_root.rglob(pattern):
            if p.is_dir():
                candidates.append(p)
    for skill_dir in candidates:
        script = skill_dir / "scripts" / "check_evidence.py"
        if script.is_file():
            return True, f"karpathy-llm-wiki lint at {script}"
    if candidates:
        return True, f"LLM wiki skill at {candidates[0]} (no check_evidence.py)"
    # check_evidence.py shipped alongside this skill (repo checkout)
    local = Path(__file__).resolve().parent / "check_evidence.py"
    if local.is_file():
        return True, f"check_evidence.py at {local}"
    return False, f"LLM wiki skill not found under {skills_root} and no " \
                  f"check_evidence.py available"


def main():
    ap = argparse.ArgumentParser(description="doc-ingest health check")
    ap.add_argument("--wiki", default=str(default_wiki()))
    ap.add_argument("--hindsight-url", default=default_hindsight_url())
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    root = Path(args.wiki).expanduser()
    layout, detected_root = detect_layout(root)

    checks = [
        check("Python version", check_python),
        check("Wiki path writable", lambda: check_wiki_writable(root)),
        check("Wiki layout", lambda: check_layout(root)),
        check("Wiki structure",
              lambda: check_structure(layout, detected_root)),
        check("Hindsight health", lambda: check_hindsight(args.hindsight_url)),
        check("pymupdf4llm", lambda: check_python_pkg("pymupdf4llm")),
        check("docling", lambda: check_python_pkg("docling")),
        check("marker", lambda: check_python_pkg("marker")),
        check("tesseract", lambda: check_tool("tesseract", ["tesseract", "--version"])),
        check("LLM wiki skill", check_wiki_skill),
    ]

    passed = sum(1 for c in checks if c["ok"])
    failed = sum(1 for c in checks if not c["ok"])

    if args.as_json:
        print(json.dumps({
            "wiki": str(detected_root),
            "layout": layout,
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
