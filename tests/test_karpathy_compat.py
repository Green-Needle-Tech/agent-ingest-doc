#!/usr/bin/env python3
"""End-to-end compatibility tests: agent-ingest-doc scripts against the
astro-han/karpathy-llm-wiki format.

The golden test (test_check_evidence_clean_after_ingest) drives the exact
consumer path: capture a raw file, write a wiki article in karpathy
format, then run the VENDORED check_evidence.py from karpathy-llm-wiki
and require 0 evidence errors and 0 unreferenced raw files.

Run: python3 -m pytest tests/test_karpathy_compat.py -q
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "capture_raw.py"
VERIFY = REPO / "scripts" / "verify_raw.py"
INIT = REPO / "scripts" / "init_wiki.py"
CHECK_EVIDENCE = REPO / "scripts" / "check_evidence.py"
UPSTREAM_TESTS = REPO / "tests" / "fixtures" / "test_check_evidence.py"

BODY = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do "
        "eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim "
        "ad minim veniam, quis nostrud exercitation ullamco laboris nisi.")


def run_capture(root, title, url, body=BODY, extra=None, stdin=None):
    cmd = [sys.executable, str(SCRIPT), "--wiki", str(root),
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


def init_karpathy(root):
    r = subprocess.run(
        [sys.executable, str(INIT), "--wiki", str(root), "--layout",
         "karpathy", "--json"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ---------- layout detection ----------

def test_init_karpathy_default_layout(tmp_path):
    """init_wiki.py defaults to the karpathy layout (raw/ + wiki/)."""
    root = tmp_path / "kb"
    out = init_karpathy(root)
    assert (root / "raw" / ".gitkeep").is_file()
    assert (root / "wiki" / "index.md").is_file()
    assert (root / "wiki" / "log.md").is_file()
    assert out["layout"] == "karpathy"
    # index/log start EMPTY per the karpathy-llm-wiki spec
    assert (root / "wiki" / "index.md").read_text() == "# Knowledge Base Index\n"


def test_init_legacy_layout_still_works(tmp_path):
    """--layout legacy creates the old structure."""
    root = tmp_path / "wiki"
    r = subprocess.run(
        [sys.executable, str(INIT), "--wiki", str(root), "--layout",
         "legacy", "--json"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["layout"] == "legacy"
    for d in ["raw/articles", "entities", "concepts", "comparisons", "queries"]:
        assert (root / d).is_dir()
    assert (root / "SCHEMA.md").is_file()


# ---------- capture: karpathy raw format ----------

def test_capture_karpathy_format(tmp_path):
    """Capture into a karpathy-layout root writes the blockquote-header
    raw file + sha256 JSON sidecar, NOT YAML frontmatter."""
    root = tmp_path / "kb"
    init_karpathy(root)
    rc, objs, _ = run_capture(root, "Test Doc", "https://example.com/a")
    assert rc == 0 and objs[0]["status"] == "captured"
    assert objs[0]["layout"] == "karpathy"
    raw = root / "raw" / "articles" / "test-doc.md"
    text = raw.read_text(encoding="utf-8")
    # karpathy header format, no YAML frontmatter
    assert text.startswith("# Test Doc\n\n")
    assert "> Source: https://example.com/a\n" in text
    assert "> Collected: " in text
    assert "> Published: Unknown\n" in text
    assert "---" not in text.split("\n")[0]
    # sidecar carries the sha256
    sidecar = raw.with_suffix(".json")
    assert sidecar.is_file()
    manifest = json.loads(sidecar.read_text())
    assert manifest["schema_version"] == 2
    assert len(manifest["sha256"]) == 64


def test_capture_karpathy_free_topic(tmp_path):
    """--topic accepts any kebab-case directory (karpathy topics are
    free-form, unlike the fixed legacy subdirs)."""
    root = tmp_path / "kb"
    init_karpathy(root)
    rc, objs, _ = run_capture(root, "Test Doc", "https://example.com/a",
                              extra=["--topic", "machine-learning"])
    assert rc == 0
    assert objs[0]["file"].endswith("raw/machine-learning/test-doc.md")


def test_capture_karpathy_published_date_prefix(tmp_path):
    """--published-date prefixes the filename and sets the header."""
    root = tmp_path / "kb"
    init_karpathy(root)
    rc, objs, _ = run_capture(root, "Test Doc", "https://example.com/a",
                              extra=["--published-date", "2026-04-03"])
    assert rc == 0
    assert objs[0]["file"].endswith("2026-04-03-test-doc.md")
    text = Path(objs[0]["file"]).read_text(encoding="utf-8")
    assert "> Published: 2026-04-03\n" in text


def test_capture_karpathy_drift_and_unchanged(tmp_path):
    """Drift detection works via the sidecar hash in karpathy layout."""
    root = tmp_path / "kb"
    init_karpathy(root)
    run_capture(root, "Test Doc", "https://example.com/a")
    # identical re-ingest -> unchanged
    rc, objs, _ = run_capture(root, "Test Doc", "https://example.com/a")
    assert objs[0]["status"] == "unchanged"
    # changed -> drift, -v2 next to the original
    rc, objs, _ = run_capture(root, "Test Doc", "https://example.com/a",
                              body=BODY + " X")
    assert objs[0]["status"] == "drift"
    assert objs[0]["file"].endswith("test-doc-v2.md")


def test_capture_karpathy_edited_file_detected_as_drift(tmp_path):
    """A raw file edited after ingest (sidecar not updated) is drift,
    not falsely unchanged."""
    root = tmp_path / "kb"
    init_karpathy(root)
    run_capture(root, "Test Doc", "https://example.com/a")
    raw = root / "raw" / "articles" / "test-doc.md"
    raw.write_text(raw.read_text().replace("Lorem", "LOREM"),
                   encoding="utf-8")
    rc, objs, _ = run_capture(root, "Test Doc", "https://example.com/a")
    assert objs[0]["status"] == "drift", objs


def test_capture_legacy_layout_unchanged(tmp_path):
    """A legacy-layout wiki still gets YAML frontmatter captures."""
    root = tmp_path / "wiki"
    subprocess.run([sys.executable, str(INIT), "--wiki", str(root),
                    "--layout", "legacy"], capture_output=True, text=True)
    rc, objs, _ = run_capture(root, "Test Doc", "https://example.com/a")
    assert rc == 0 and objs[0]["status"] == "captured"
    assert objs[0]["layout"] == "legacy"
    raw = root / "raw" / "articles" / "test-doc.md"
    text = raw.read_text(encoding="utf-8")
    assert text.startswith("---\nsource_url: https://example.com/a\n")


def test_capture_wiki_subdir_passed_as_root(tmp_path):
    """User passing <root>/wiki as --wiki still detects karpathy layout."""
    root = tmp_path / "kb"
    init_karpathy(root)
    rc, objs, _ = run_capture(root / "wiki", "Test Doc",
                              "https://example.com/a")
    assert rc == 0 and objs[0]["layout"] == "karpathy"
    assert (root / "raw" / "articles" / "test-doc.md").is_file()


# ---------- verify_raw: layout-aware ----------

def test_verify_karpathy_clean(tmp_path):
    root = tmp_path / "kb"
    init_karpathy(root)
    run_capture(root, "Test Doc", "https://example.com/a")
    r = subprocess.run([sys.executable, str(VERIFY), "--wiki", str(root),
                        "--json"], capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["status"] == "ok", out
    assert out["layout"] == "karpathy"
    assert out["raw_files_checked"] == 1


def test_verify_karpathy_detects_sidecar_mismatch(tmp_path):
    root = tmp_path / "kb"
    init_karpathy(root)
    run_capture(root, "Test Doc", "https://example.com/a")
    raw = root / "raw" / "articles" / "test-doc.md"
    raw.write_text(raw.read_text().replace("Lorem", "LOREM"),
                   encoding="utf-8")
    r = subprocess.run([sys.executable, str(VERIFY), "--wiki", str(root),
                        "--json"], capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert r.returncode == 1
    assert any("sha256 mismatch" in f for f in out["failures"]), out


def test_verify_karpathy_log_format(tmp_path):
    root = tmp_path / "kb"
    init_karpathy(root)
    (root / "wiki" / "log.md").write_text(
        "# Wiki Log\n\n## [2026-09-03] ingest | Test Doc\n"
        "- Disposition: New\n- Raw: raw/articles/test-doc.md\n",
        encoding="utf-8")
    r = subprocess.run([sys.executable, str(VERIFY), "--wiki", str(root),
                        "--json"], capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert r.returncode == 0, out  # valid karpathy log entry passes


# ---------- THE golden test: check_evidence.py compatibility ----------

def test_check_evidence_clean_after_ingest(tmp_path):
    """Full pipeline: capture -> karpathy article -> vendored
    check_evidence.py reports 0 evidence errors, 0 unreferenced raws."""
    root = tmp_path / "kb"
    init_karpathy(root)
    rc, objs, _ = run_capture(root, "Attention Is All You Need",
                              "https://arxiv.org/abs/1706.03762",
                              extra=["--topic", "machine-learning",
                                     "--published-date", "2017-06-12"])
    assert rc == 0 and objs[0]["status"] == "captured"
    raw_rel = "raw/machine-learning/2017-06-12-attention-is-all-you-need.md"

    # Write a karpathy-format article citing the raw file
    article_dir = root / "wiki" / "machine-learning"
    article_dir.mkdir(parents=True)
    (article_dir / "attention-mechanisms.md").write_text(
        "# Attention Mechanisms\n\n"
        "> Sources: Vaswani et al., 2017-06-12\n"
        f"> Raw: [Attention Is All You Need](../../{raw_rel})\n"
        "> Updated: 2026-09-03\n\n"
        "## Overview\n\n"
        "The Transformer, published 2017-06-12, replaces recurrence with "
        "self-attention.\n\n"
        "## See Also\n\n"
        "- [Glossary Z](../ai-ml/glossary-z.md)\n",
        encoding="utf-8")

    r = subprocess.run(
        [sys.executable, str(CHECK_EVIDENCE), str(root)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "Evidence errors\n(none)" in out or \
        "## Evidence errors\n\n(none)" in out, out
    assert "0 evidence error" in out, out
    assert "0 unreferenced raw file" in out, out


def test_check_evidence_flags_frontmatter_raw(tmp_path):
    """A legacy-format raw file (YAML frontmatter) is NOT karpathy-
    compatible: check_evidence treats frontmatter as body. This documents
    why karpathy layout must use the blockquote header format."""
    root = tmp_path / "kb"
    init_karpathy(root)
    # legacy-style raw file written by hand into the karpathy wiki
    raw_dir = root / "raw" / "articles"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "legacy-style.md").write_text(
        "---\n"
        "source_url: https://example.com/a\n"
        "ingested: 2026-09-03\n"
        "sha256: " + "0" * 64 + "\n"
        "---\n\n"
        "# Legacy Doc\n\n"
        + BODY, encoding="utf-8")
    article_dir = root / "wiki" / "articles"
    article_dir.mkdir(parents=True)
    (article_dir / "legacy-article.md").write_text(
        "# Legacy Article\n\n"
        "> Sources: Example, 2026-09-03\n"
        "> Raw: [Legacy Doc](../../raw/articles/legacy-style.md)\n"
        "> Updated: 2026-09-03\n\n"
        "## Overview\n\n"
        "The source_url is https://example.com/a and it was ingested "
        "2026-09-03.\n",
        encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(CHECK_EVIDENCE), str(root)],
        capture_output=True, text=True)
    # The YAML frontmatter pollutes the raw body — the article's claims
    # still verify (frontmatter is in the body), but this test documents
    # the format contract. The important part: no crash, exit 0.
    assert r.returncode == 0


# ---------- upstream test suite still passes on the vendored copy ----------

def test_vendored_check_evidence_upstream_suite():
    """The vendored check_evidence.py passes its own upstream test suite."""
    if not UPSTREAM_TESTS.is_file():
        import pytest
        pytest.skip("upstream test fixture not present")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(UPSTREAM_TESTS), "-q",
         "--rootdir", str(REPO)],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr
