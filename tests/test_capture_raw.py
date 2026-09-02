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
    objs = []
    for ln in lines:
        try:
            objs.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return r.returncode, objs, r.stderr


# ---------- capture_raw.py: existing tests ----------

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
    """Same URL + same content in a different subdir -> unchanged, not a duplicate."""
    run_capture(tmp_path, "Doc A", "https://example.com/same")
    rc, objs, _ = run_capture(tmp_path, "Doc B", "https://example.com/same",
                              extra=["--raw-subdir", "papers"])
    assert rc == 0 and objs[0]["status"] == "unchanged", objs


def test_cross_subdir_drift_saves_next_to_original(tmp_path):
    """Same URL, changed content, different subdir -> drift saved as -v2 next
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


# ---------- capture_raw.py: new tests for v2.3.1 fixes ----------

def test_gapped_version_allocation(tmp_path):
    """Version allocation uses max, not count — gapped versions get correct next."""
    run_capture(tmp_path, "Gap Test", "https://gap.test")
    raw = tmp_path / "raw/articles/gap-test.md"
    content = raw.read_text()
    # Manually create v2 and v4 (gap at v3)
    (tmp_path / "raw/articles/gap-test-v2.md").write_text(
        content.replace(BODY, BODY + " v2"))
    (tmp_path / "raw/articles/gap-test-v4.md").write_text(
        content.replace(BODY, BODY + " v4"))
    # Next drift should be v5 (max+1), not v4 (count+1=3+1)
    rc, objs, _ = run_capture(tmp_path, "Gap Test", "https://gap.test",
                              body=BODY + " v5")
    assert rc == 0 and objs[0]["status"] == "drift"
    assert objs[0]["file"].endswith("gap-test-v5.md"), (
        f"Expected gap-test-v5.md, got {objs[0]['file']}")


def test_frontmatter_injection_rejected(tmp_path):
    """Multiline source_url with control chars must be rejected cleanly."""
    malicious_url = "https://x.test\nsha256: 0000000000000000000000000000000000000000000000000000000000000000"
    rc, objs, err = run_capture(tmp_path, "Inject Test", malicious_url)
    assert rc != 0  # SystemExit
    # Should not produce a captured file
    assert not (tmp_path / "raw/articles").exists() or \
        not list((tmp_path / "raw/articles").glob("*.md"))


def test_version_suffix_path_traversal_rejected(tmp_path):
    """--version-suffix with path traversal chars must be rejected with JSON error."""
    run_capture(tmp_path, "Suffix Test", "https://s.test")
    rc, objs, err = run_capture(tmp_path, "Suffix Test", "https://s.test",
                                body=BODY + " X",
                                extra=["--version-suffix=../boom"])
    assert rc != 0  # SystemExit


def test_version_suffix_space_rejected(tmp_path):
    """--version-suffix with spaces must be rejected."""
    run_capture(tmp_path, "Suffix Test", "https://s.test")
    rc, objs, err = run_capture(tmp_path, "Suffix Test", "https://s.test",
                                body=BODY + " X",
                                extra=["--version-suffix=-foo bar"])
    assert rc != 0  # SystemExit


def test_atomic_write_no_overwrite(tmp_path):
    """Atomic write refuses to overwrite an existing target."""
    run_capture(tmp_path, "Atomic Test", "https://a.test")
    # Try to overwrite by using explicit suffix that matches existing file
    # The original is atomic-test.md; with --version-suffix='' it should
    # auto-allocate, but we can test collision by providing an explicit
    # suffix that creates the same filename
    rc, objs, err = run_capture(tmp_path, "Atomic Test", "https://a.test",
                                body=BODY + " OVERWRITE",
                                extra=["--version-suffix=-v2"])
    # First time -v2 doesn't exist yet, so this succeeds
    assert rc == 0 and objs[0]["file"].endswith("atomic-test-v2.md")
    # Now try again with same suffix - should fail (file exists)
    rc, objs, err = run_capture(tmp_path, "Atomic Test", "https://a.test",
                                body=BODY + " OVERWRITE2",
                                extra=["--version-suffix=-v2"])
    assert rc != 0  # SystemExit — target exists


def test_unicode_slug_cjk(tmp_path):
    """Pure CJK title should produce a unique slug, not 'untitled'."""
    rc, objs, _ = run_capture(tmp_path, "東京レポート", "https://jp.test/1")
    assert rc == 0
    filename = Path(objs[0]["file"]).name
    # Should NOT be just "untitled.md" — must have a unique hash suffix
    assert filename != "untitled.md", f"Pure CJK collapsed to untitled: {filename}"
    assert filename.startswith("untitled-") or any(
        ord(c) > 0x2000 for c in filename), f"CJK not preserved: {filename}"


def test_unicode_slug_no_collision(tmp_path):
    """Two different CJK titles must not collide."""
    rc1, objs1, _ = run_capture(tmp_path, "東京レポート", "https://jp.test/1")
    rc2, objs2, _ = run_capture(tmp_path, "京都レポート", "https://jp.test/2",
                                body=BODY + " different")
    assert rc1 == 0 and rc2 == 0
    assert objs1[0]["file"] != objs2[0]["file"], (
        f"Two different CJK titles produced the same filename: "
        f"{objs1[0]['file']} == {objs2[0]['file']}")


def test_dedup_recomputes_stored_hash(tmp_path):
    """Dedup recomputes stored body hash — a corrupted file is detected as drift."""
    run_capture(tmp_path, "Corrupt Test", "https://c.test")
    # Corrupt the stored body but keep frontmatter hash the same
    raw = tmp_path / "raw/articles/corrupt-test.md"
    content = raw.read_text()
    raw.write_text(content.replace("Lorem", "LOREM"), encoding="utf-8")
    # Re-ingest the ORIGINAL body — should be drift (stored changed), not unchanged
    rc, objs, _ = run_capture(tmp_path, "Corrupt Test", "https://c.test")
    assert objs[0]["status"] == "drift", (
        f"Expected drift after corruption, got {objs[0]['status']} "
        f"— dedup trusted stale frontmatter hash instead of recomputing")


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


def test_verify_drift_chain_passes(tmp_path):
    """P0 fix: drift versions in one chain share source_url and MUST pass verification."""
    run_capture(tmp_path, "Test Doc", "https://example.com/a")
    run_capture(tmp_path, "Test Doc", "https://example.com/a", body=BODY + " X")
    rc, out = run_verify(tmp_path)
    assert rc == 0 and out["status"] == "ok", (
        f"Drift chain failed verification: {out['failures']}")
    assert out["raw_files_checked"] == 2


def test_verify_detects_duplicate_source_url_separate_chains(tmp_path):
    """Duplicate source_url across SEPARATE chains (different slugs) must fail."""
    run_capture(tmp_path, "Doc A", "https://example.com/same")
    # Create a separate chain with a different slug but same URL
    # by manually writing a file in a different directory
    papers = tmp_path / "raw/papers"
    papers.mkdir(parents=True)
    (papers / "different-slug.md").write_text(
        "---\n"
        "source_url: https://example.com/same\n"
        "ingested: 2026-09-02\n"
        f"sha256: {__import__('hashlib').sha256(BODY.encode()).hexdigest()}\n"
        "---\n\n"
        f"# Different Doc\n\n{BODY}",
        encoding="utf-8")
    rc, out = run_verify(tmp_path)
    assert rc == 1
    assert any("duplicate source_url" in f for f in out["failures"]), (
        f"Expected duplicate source_url failure, got: {out['failures']}")


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


# ---------- v2.4.0: manifest sidecar ----------

def test_manifest_sidecar_created(tmp_path):
    """capture_raw.py writes a JSON manifest sidecar with extraction metadata."""
    rc, objs, _ = run_capture(tmp_path, "Manifest Test", "https://m.test",
                              extra=["--extractor", "pymupdf4llm",
                                     "--source-kind", "url"])
    assert rc == 0
    manifest_path = Path(objs[0]["file"]).with_suffix(".json")
    assert manifest_path.is_file(), f"Manifest not found: {manifest_path}"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == 1
    assert manifest["source_uri"] == "https://m.test"
    assert manifest["source_kind"] == "url"
    assert manifest["extractor"] == "pymupdf4llm"
    assert "extraction_sha256" in manifest
    assert "retrieved_at" in manifest


def test_manifest_default_extractor(tmp_path):
    """Manifest defaults to 'unknown' extractor when --extractor not given."""
    rc, objs, _ = run_capture(tmp_path, "Def Test", "https://d.test")
    assert rc == 0
    manifest_path = Path(objs[0]["file"]).with_suffix(".json")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["extractor"] == "unknown"
    assert manifest["source_kind"] == "url"


# ---------- v2.4.0: safe_fetch.py ----------

SAFE_FETCH = REPO / "scripts" / "safe_fetch.py"


def test_safe_fetch_rejects_file_scheme(tmp_path):
    """safe_fetch.py rejects file:// URLs."""
    r = subprocess.run(
        [sys.executable, str(SAFE_FETCH), "file:///etc/passwd", "--json"],
        capture_output=True, text=True)
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["status"] == "error"
    assert "scheme" in out["error"].lower()


def test_safe_fetch_rejects_ftp_scheme():
    """safe_fetch.py rejects ftp:// URLs."""
    r = subprocess.run(
        [sys.executable, str(SAFE_FETCH), "ftp://example.com/file", "--json"],
        capture_output=True, text=True)
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["status"] == "error"
    assert "scheme" in out["error"].lower()


def test_safe_fetch_rejects_empty_url():
    """safe_fetch.py rejects empty URLs."""
    r = subprocess.run(
        [sys.executable, str(SAFE_FETCH), "", "--json"],
        capture_output=True, text=True)
    assert r.returncode == 1


def test_safe_fetch_rejects_loopback():
    """safe_fetch.py rejects loopback addresses."""
    r = subprocess.run(
        [sys.executable, str(SAFE_FETCH),
         "http://127.0.0.1:8888/health", "--json"],
        capture_output=True, text=True)
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert "blocked" in out["error"].lower() or "error" in out["error"].lower()


# ---------- v2.4.0: init_wiki.py ----------

INIT_WIKI = REPO / "scripts" / "init_wiki.py"


def test_init_wiki_creates_structure(tmp_path):
    """init_wiki.py creates the full wiki directory structure."""
    wiki = tmp_path / "test-wiki"
    r = subprocess.run(
        [sys.executable, str(INIT_WIKI), "--wiki", str(wiki), "--json"],
        capture_output=True, text=True)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["status"] == "ok"
    # Check directories exist
    for d in ["raw/articles", "raw/papers", "raw/transcripts", "raw/assets",
              "entities", "concepts", "comparisons", "queries"]:
        assert (wiki / d).is_dir(), f"Missing dir: {d}"
    # Check template files exist
    for f in ["SCHEMA.md", "index.md", "log.md"]:
        assert (wiki / f).is_file(), f"Missing file: {f}"


def test_init_wiki_idempotent(tmp_path):
    """init_wiki.py is safe to re-run — won't overwrite existing files."""
    wiki = tmp_path / "test-wiki"
    # First run
    subprocess.run(
        [sys.executable, str(INIT_WIKI), "--wiki", str(wiki), "--json"],
        capture_output=True, text=True)
    # Modify a file
    schema = wiki / "SCHEMA.md"
    original = schema.read_text()
    schema.write_text("# Custom Schema\n")
    # Second run
    r = subprocess.run(
        [sys.executable, str(INIT_WIKI), "--wiki", str(wiki), "--json"],
        capture_output=True, text=True)
    assert r.returncode == 0
    # Custom content preserved
    assert schema.read_text() == "# Custom Schema\n"


# ---------- v2.4.0: doctor.py ----------

DOCTOR = REPO / "scripts" / "doctor.py"


def test_doctor_runs(tmp_path):
    """doctor.py runs and produces valid JSON output."""
    r = subprocess.run(
        [sys.executable, str(DOCTOR), "--wiki", str(tmp_path / "wiki"),
         "--json"],
        capture_output=True, text=True)
    assert r.returncode in (0, 1)  # may have warnings
    out = json.loads(r.stdout)
    assert "total" in out
    assert "passed" in out
    assert "failed" in out
    assert "checks" in out
    assert isinstance(out["checks"], list)
    assert len(out["checks"]) > 0
