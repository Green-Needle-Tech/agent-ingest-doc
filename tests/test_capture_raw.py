#!/usr/bin/env python3
"""Tests for scripts/capture_raw.py and scripts/verify_raw.py.

Run: python3 -m pytest tests/ -q   (or ./tests/run_tests.sh for stdlib fallback)
Each test drives the script as a subprocess — the exact consumer path.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "capture_raw.py"
VERIFY = REPO / "scripts" / "verify_raw.py"

BODY = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do "
        "eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim "
        "ad minim veniam, quis nostrud exercitation ullamco laboris nisi.")


def run_capture(wiki, title, url, body=BODY, extra=None, stdin=None):
    cmd = [sys.executable, str(SCRIPT), "--wiki", str(wiki),
           "--title", title, "--source-url", url]
    if extra:
        cmd += extra
    r = subprocess.run(cmd, input=body if stdin is None else stdin,
                       capture_output=True, text=True)
    lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    return r.returncode, [json.loads(ln) for ln in lines], r.stderr


def test_first_capture(tmp_path):
    rc, objs, _ = run_capture(tmp_path, "Test Doc", "https://example.com/a")
    assert rc == 0 and len(objs) == 1
    assert objs[0]["status"] == "captured"
    assert objs[0]["file"].endswith("test-doc.md")
    assert (tmp_path / "raw/articles/test-doc.md").is_file()


def test_identical_reingest_unchanged(tmp_path):
    run_capture(tmp_path, "Test Doc", "https://example.com/a")
    rc, objs, _ = run_capture(tmp_path, "Test Doc", "https://example.com/a")
    assert rc == 0 and objs[0]["status"] == "unchanged"
    # no second file created
    files = list((tmp_path / "raw/articles").glob("*.md"))
    assert len(files) == 1


def test_drift_single_json_output(tmp_path):
    """Bug 1: drift used to print one JSON line per existing version."""
    run_capture(tmp_path, "Test Doc", "https://example.com/a")
    run_capture(tmp_path, "Test Doc", "https://example.com/a", body=BODY + " X")
    rc, objs, _ = run_capture(tmp_path, "Test Doc", "https://example.com/a",
                              body=BODY + " Y")
    assert rc == 0
    assert len(objs) == 1, f"expected exactly 1 JSON object, got {len(objs)}"
    assert objs[0]["status"] == "drift"
    assert objs[0]["old_sha256"] != objs[0]["sha256"]


def test_drift_version_naming_v2(tmp_path):
    """Bug 3: first drift save must be -v2 (original unsuffixed = v1)."""
    run_capture(tmp_path, "Test Doc", "https://example.com/a")
    rc, objs, _ = run_capture(tmp_path, "Test Doc", "https://example.com/a",
                             body=BODY + " X")
    assert rc == 0 and objs[0]["status"] == "drift"
    assert objs[0]["file"].endswith("test-doc-v2.md"), objs[0]["file"]
    # third version
    rc, objs, _ = run_capture(tmp_path, "Test Doc", "https://example.com/a",
                             body=BODY + " Y")
    assert objs[0]["file"].endswith("test-doc-v3.md"), objs[0]["file"]


def test_no_prefix_collision(tmp_path):
    """Bug 2: 'Test Doc' must not match 'Test Documentation'."""
    run_capture(tmp_path, "Test Doc", "https://example.com/a")
    rc, objs, _ = run_capture(tmp_path, "Test Documentation",
                              "https://example.com/b")
    assert rc == 0 and objs[0]["status"] == "captured"
    assert objs[0]["file"].endswith("test-documentation.md")
    # and re-ingesting the first doc still detects unchanged correctly
    rc, objs, _ = run_capture(tmp_path, "Test Doc", "https://example.com/a")
    assert objs[0]["status"] == "unchanged"


def test_hash_scope_roundtrip(tmp_path):
    """Bug 4: sha256 in frontmatter must match recomputed hash of stored body."""
    rc, objs, _ = run_capture(tmp_path, "Test Doc", "https://example.com/a")
    assert rc == 0
    raw = tmp_path / "raw/articles/test-doc.md"
    text = raw.read_text(encoding="utf-8")
    import re as _re
    m = _re.match(r"^---\n(.*?)\n---\n(.*)$", text, _re.DOTALL)
    fm = dict(ln.partition(":")[::2] for ln in m.group(1).splitlines())
    stored_sha = fm["sha256"].strip()
    body = _re.sub(r"^#\s+[^\n]*\n\n?", "",
                   m.group(2).lstrip("\n"), count=1)
    import hashlib
    assert hashlib.sha256(body.encode()).hexdigest() == stored_sha


def test_tiny_body_error(tmp_path):
    rc, objs, _ = run_capture(tmp_path, "Tiny", "https://x.com", body="short")
    assert rc == 1 and objs[0]["status"] == "error"


def test_slug_override(tmp_path):
    rc, objs, _ = run_capture(tmp_path, "Ñoño 東京 2026", "https://example.com/c",
                              extra=["--slug", "tokyo-report-2026"])
    assert rc == 0
    assert objs[0]["file"].endswith("tokyo-report-2026.md")


def test_cross_subdir_dedupe_unchanged(tmp_path):
    """Same URL + same content in a different subdir → unchanged, not a duplicate."""
    run_capture(tmp_path, "Doc A", "https://example.com/same")
    rc, objs, _ = run_capture(tmp_path, "Doc B", "https://example.com/same",
                              extra=["--raw-subdir", "papers"])
    assert rc == 0 and objs[0]["status"] == "unchanged", objs


def test_cross_subdir_drift_saves_next_to_original(tmp_path):
    """Same URL, changed content, different subdir → drift saved as -v2 next
    to the original, not orphaned in the new subdir."""
    run_capture(tmp_path, "Doc A", "https://example.com/same")
    rc, objs, _ = run_capture(tmp_path, "Doc B", "https://example.com/same",
                              body=BODY + " v2", extra=["--raw-subdir", "papers"])
    assert rc == 0 and objs[0]["status"] == "drift"
    assert objs[0]["file"].endswith("raw/articles/doc-a-v2.md"), objs[0]["file"]
    # nothing orphaned in papers/
    assert not (tmp_path / "raw/papers").exists() or \
        not list((tmp_path / "raw/papers").glob("*.md"))


def test_version_suffix_override(tmp_path):
    run_capture(tmp_path, "Test Doc", "https://example.com/a")
    rc, objs, _ = run_capture(tmp_path, "Test Doc", "https://example.com/a",
                             body=BODY + " X", extra=["--version-suffix=-final"])
    assert rc == 0 and objs[0]["file"].endswith("test-doc-final.md")


# ---------- verify_raw.py ----------

def run_verify(wiki, as_json=True):
    cmd = [sys.executable, str(VERIFY), "--wiki", str(wiki)]
    if as_json:
        cmd.append("--json")
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, json.loads(r.stdout) if as_json else r.stdout


def test_verify_clean_wiki_passes(tmp_path):
    run_capture(tmp_path, "Test Doc", "https://example.com/a")
    rc, out = run_verify(tmp_path)
    assert rc == 0 and out["status"] == "ok", out
    assert out["raw_files_checked"] == 1


def test_verify_detects_hash_mismatch(tmp_path):
    run_capture(tmp_path, "Test Doc", "https://example.com/a")
    raw = tmp_path / "raw/articles/test-doc.md"
    raw.write_text(raw.read_text().replace("Lorem", "LOREM"), encoding="utf-8")
    rc, out = run_verify(tmp_path)
    assert rc == 1 and out["status"] == "fail"
    assert any("sha256 mismatch" in f for f in out["failures"])


def test_verify_detects_missing_source_url(tmp_path):
    run_capture(tmp_path, "Test Doc", "https://example.com/a")
    raw = tmp_path / "raw/articles/test-doc.md"
    raw.write_text(raw.read_text().replace(
        "source_url: https://example.com/a\n", ""), encoding="utf-8")
    rc, out = run_verify(tmp_path)
    assert rc == 1
    assert any("source_url" in f for f in out["failures"])


def test_verify_detects_duplicate_source_url(tmp_path):
    run_capture(tmp_path, "Doc A", "https://example.com/same")
    run_capture(tmp_path, "Doc B", "https://example.com/same",
                body=BODY + " different", extra=["--raw-subdir", "papers"])
    rc, out = run_verify(tmp_path)
    assert rc == 1
    assert any("duplicate source_url" in f for f in out["failures"])


def test_verify_detects_bad_log_format(tmp_path):
    run_capture(tmp_path, "Test Doc", "https://example.com/a")
    (tmp_path / "log.md").write_text("## Some random heading\n", encoding="utf-8")
    rc, out = run_verify(tmp_path)
    assert rc == 1
    assert any("log.md" in f for f in out["failures"])


def test_verify_index_total_mismatch(tmp_path):
    run_capture(tmp_path, "Test Doc", "https://example.com/a")
    (tmp_path / "index.md").write_text("Total pages: 5\n", encoding="utf-8")
    rc, out = run_verify(tmp_path)
    assert rc == 1
    assert any("Total pages" in f for f in out["failures"])
