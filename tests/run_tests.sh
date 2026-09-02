#!/usr/bin/env bash
# Stdlib fallback test runner — no pytest required.
# Runs each test function in tests/test_capture_raw.py via pytest if present,
# else via a minimal unittest-style shim.
set -u
cd "$(dirname "$0")/.."
if python3 -m pytest --version >/dev/null 2>&1; then
    exec python3 -m pytest tests/ -q
fi
echo "pytest not found — running capture/verify smoke checks instead"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
BODY="Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore."
fail=0
check() {  # name, expected_substring, actual_output
    if echo "$3" | grep -q "$2"; then echo "OK   $1"; else echo "FAIL $1"; fail=1; fi
}
out=$(echo "$BODY" | python3 scripts/capture_raw.py --wiki "$TMP" --title "Smoke Doc" --source-url https://x.test)
check "first capture" '"status": "captured"' "$out"
out=$(echo "$BODY" | python3 scripts/capture_raw.py --wiki "$TMP" --title "Smoke Doc" --source-url https://x.test)
check "re-ingest unchanged" '"status": "unchanged"' "$out"
out=$(echo "$BODY v2" | python3 scripts/capture_raw.py --wiki "$TMP" --title "Smoke Doc" --source-url https://x.test)
check "drift single JSON" '"status": "drift"' "$out"
lines=$(echo "$out" | grep -c '"status"')
check "drift exactly one JSON line" '^1$' "$lines"
out=$(python3 scripts/verify_raw.py --wiki "$TMP" --json)
check "verify after drift (chain URLs valid)" '"status": "ok"' "$out"
if [[ "$fail" -eq 0 ]]; then echo "ALL SMOKE CHECKS PASSED"; fi
exit "$fail"
