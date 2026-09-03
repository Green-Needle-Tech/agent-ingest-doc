#!/usr/bin/env python3
"""hermes_paths.py — general-purpose path resolution for host-portable skills.

Skills must not assume a fixed install location (e.g. /root). On some hosts
Hermes is installed under /home/ubuntu; credentials and state live under the
OS user's real home, not the invoking process's HOME (which Hermes may scope
to a profile directory). This module mirrors Hermes' own resolution order
(hermes_constants.py) using stdlib only:

  real home:    HERMES_REAL_HOME -> HOME -> pwd database -> expanduser("~")
                (skips a Hermes profile-home when detected via HERMES_HOME)
  hermes home:  HERMES_HOME -> <real home>/.hermes
  default wiki: WIKI_PATH env -> <real home>/wiki
  default hindsight URL: HINDSIGHT_URL env -> http://localhost:8888

Usage as a module:
  from hermes_paths import real_home, hermes_home, default_wiki, default_hindsight_url
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_HINDSIGHT_URL = "http://localhost:8888"


def _norm(path: str | os.PathLike) -> str:
    """Normalize a path for comparison (expanduser + absolute)."""
    try:
        return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))
    except Exception:
        return os.path.normcase(str(path))


def _pwd_home() -> str | None:
    """Return the OS account's home from the password database, if any."""
    try:
        import pwd  # POSIX-only; platforms are linux/macos
        return pwd.getpwuid(os.getuid()).pw_dir.strip() or None
    except Exception:
        return None


def real_home() -> Path:
    """Return the OS user's real home directory.

    Trust order: HERMES_REAL_HOME (set by Hermes for subprocesses) -> HOME ->
    pwd database -> expanduser("~"). A HOME that points at a Hermes profile
    home ({HERMES_HOME}/home) is skipped in favor of the account home, since
    credentials live in the real home, not the profile sandbox.
    """
    hermes_home_env = os.environ.get("HERMES_HOME", "").strip()
    profile_home = (
        os.path.join(hermes_home_env, "home")
        if hermes_home_env and os.path.isdir(os.path.join(hermes_home_env, "home"))
        else None
    )

    candidates: list[str] = []
    explicit = os.environ.get("HERMES_REAL_HOME", "").strip()
    if explicit:
        candidates.append(explicit)
    home = os.environ.get("HOME", "").strip()
    if home:
        candidates.append(home)
    pw_home = _pwd_home()
    if pw_home:
        candidates.append(pw_home)
    expanded = os.path.expanduser("~")
    if expanded and expanded != "~":
        candidates.append(expanded)

    seen: set[str] = set()
    for cand in candidates:
        key = _norm(cand)
        if not key or key in seen:
            continue
        seen.add(key)
        if profile_home and key == _norm(profile_home):
            continue  # profile sandbox — keep looking for the account home
        return Path(cand)
    # No home directory could be resolved. Refuse rather than fall back to a
    # publicly writable directory such as /tmp, where another user could
    # pre-create or hijack paths this skill writes to (CWE-377).
    raise RuntimeError(
        "could not resolve the OS user home directory; set HERMES_REAL_HOME "
        "or HOME explicitly"
    )


def hermes_home() -> Path:
    """Return the Hermes home directory (where .hermes state lives).

    Resolution order mirrors Hermes: HERMES_HOME env -> <real home>/.hermes.
    """
    env_val = os.environ.get("HERMES_HOME", "").strip()
    if env_val:
        return Path(env_val).expanduser()
    return real_home() / ".hermes"


def default_wiki() -> Path:
    """Return the default wiki path: WIKI_PATH env -> <real home>/wiki."""
    env_val = os.environ.get("WIKI_PATH", "").strip()
    if env_val:
        return Path(env_val).expanduser()
    return real_home() / "wiki"


def default_hindsight_url() -> str:
    """Return the default Hindsight base URL (HINDSIGHT_URL env)."""
    env_val = os.environ.get("HINDSIGHT_URL", "").strip()
    return env_val or DEFAULT_HINDSIGHT_URL


if __name__ == "__main__":  # pragma: no cover — debug helper
    print(f"real home:     {real_home()}")
    print(f"hermes home:   {hermes_home()}")
    print(f"default wiki:  {default_wiki()}")
    print(f"hindsight url: {default_hindsight_url()}")
