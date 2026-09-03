"""pytest fixtures for agent-ingest-doc tests.

The original test suite (tests/test_capture_raw.py) was written for the
legacy layout. Rather than rewriting every test, this fixture detects
when a test uses tmp_path as a bare wiki root (no raw/ or wiki/ subdir)
and initializes a legacy-layout wiki there, preserving the original
test semantics.

The karpathy-layout tests in tests/test_karpathy_compat.py explicitly
call init_karpathy() and are unaffected.
"""
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent  # repo root (this file lives in tests/)
sys.path.insert(0, str(REPO / "scripts"))


@pytest.fixture(autouse=True)
def legacy_wiki_for_bare_tmp_path(request, tmp_path):
    """If no other fixture initialized the wiki, create a legacy layout
    so the old tests work unchanged."""
    # Only apply to the old test file
    if "test_capture_raw" not in request.module.__name__:
        yield
        return
    # Check if tmp_path looks bare (no raw/ or wiki/ subdir)
    if not (tmp_path / "raw").exists() and not (tmp_path / "SCHEMA.md").exists():
        from init_wiki import init_legacy
        init_legacy(tmp_path)
    yield
