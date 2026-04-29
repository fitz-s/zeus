#!/usr/bin/env bash
# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: round2_verdict.md §4.1 #5 + judge_ledger.md §54 (LIVE baseline 73/22/0)
#
# pre-commit-invariant-test.sh — runs pytest tests/test_architecture_contracts.py
# before any `git commit` is executed via Bash, and aborts on new failures vs the
# LIVE baseline. Baseline (per BATCH A close + judge_ledger): 73 passed / 22
# skipped / 0 new failures (+2 vs the 71-pass baseline cited in dispatch dispatch).
#
# Wired as a PreToolUse hook for Bash with command-pattern match on `git commit`
# in .claude/settings.json. Receives a JSON payload on stdin.
# Exit 0 = allow; exit 2 = block.

set -euo pipefail

INPUT=$(cat)

# Only run on `git commit` Bash invocations
COMMAND=$(printf '%s' "$INPUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
ti = d.get('tool_input', {}) or {}
print(ti.get('command') or '')
" 2>/dev/null || echo "")

if [ -z "$COMMAND" ]; then
    exit 0
fi

# Detect `git commit` invocation (allow `git commit-tree`, `git commit-graph` plumbing)
case "$COMMAND" in
    *"git commit"*)
        # Reject only top-level `git commit`, not `git commit-tree` etc.
        if ! printf '%s' "$COMMAND" | grep -qE 'git commit($| |-m|--)' ; then
            exit 0
        fi
        ;;
    *)
        exit 0
        ;;
esac

# Allow opt-out for trusted operator overrides
if [ "${COMMIT_INVARIANT_TEST_SKIP:-0}" = "1" ]; then
    echo "[pre-commit-invariant-test] SKIPPED (COMMIT_INVARIANT_TEST_SKIP=1)" >&2
    exit 0
fi

REPO_ROOT="/Users/leofitz/.openclaw/workspace-venus/zeus"
PYTEST_BIN="${REPO_ROOT}/.venv/bin/python"
# Baseline progression history:
#   BATCH C (settlement_semantics): 73 → 76 (+3 HKO/WMO type-encoded)
#   SIDECAR-3 (negative-half): 76 → 79
#   Tier 2 Phase 3 (digest_profiles equivalence): 79 → 83 (+4)
#   Tier 2 Phase 4 (@enforced_by prototype): 83 → 90 (+7)
#   EDGE_OBSERVATION BATCH 1 (realized-edge per strategy_key): 90 → 96 (+6)
#   EDGE_OBSERVATION BATCH 2 (detect_alpha_decay ratio test): 96 → 104 (+8)
#   BATCH 2 LOW-CAVEAT-EO-2-2 (critical-cutoff boundary test): 104 → 105 (+1)
#   EDGE_OBSERVATION BATCH 3 (weekly runner end-to-end): 105 → 109 (+4)
#   ATTRIBUTION_DRIFT BATCH 1 (per-position detector): 109 → 118 (+9)
#   ATTRIBUTION_DRIFT BATCH 2 (per-strategy drift_rate aggregator): 118 → 124 (+6)
#   ATTRIBUTION_DRIFT BATCH 3 (weekly runner end-to-end): 124 → 128 (+4)
#   WS_OR_POLL_TIGHTENING BATCH 1 (PATH A latency-only detector): 128 → 137 (+9)
#   BATCH 1 MED-REVISE-WP-1-1 (row-multiplication regression tests): 137 → 139 (+2)
#   WS_OR_POLL_TIGHTENING BATCH 2 (detect_reaction_gap ratio + 30s boundary): 139 → 149 (+10)
#   WS_OR_POLL_TIGHTENING BATCH 3 (weekly runner e2e + per-strategy threshold + negative_latency_count): 149 → 155 (+6)
#   LOW-OPERATIONAL-WP-3-1 fix (3 sibling sys.path bootstrap + 1 regression test covering all 3): 155 → 156 (+1)
#   CALIBRATION_HARDENING BATCH 1 (Platt parameter projection + store.py readers + tests): 156 → 170 (+14)
TEST_FILES="tests/test_architecture_contracts.py tests/test_settlement_semantics.py tests/test_digest_profiles_equivalence.py tests/test_inv_prototype.py tests/test_edge_observation.py tests/test_edge_observation_weekly.py tests/test_attribution_drift.py tests/test_attribution_drift_weekly.py tests/test_ws_poll_reaction.py tests/test_ws_poll_reaction_weekly.py tests/test_calibration_observation.py"
BASELINE_PASSED=170
BASELINE_SKIPPED=22

if [ ! -x "$PYTEST_BIN" ]; then
    echo "[pre-commit-invariant-test] WARN: ${PYTEST_BIN} not found; skipping check" >&2
    exit 0
fi

cd "$REPO_ROOT"

# Run, capture, parse (PYTEST_BIN is the venv python; invoke pytest as module).
# Multi-file: TEST_FILES is space-separated; let word-splitting expand it.
RESULT=$("$PYTEST_BIN" -m pytest $TEST_FILES -q --no-header 2>&1 || true)
# Note: `-m pytest` after the python interpreter is correct (python -m pytest <args>)
SUMMARY=$(printf '%s' "$RESULT" | tail -3 | tr '\n' ' ')

# Extract counts via grep
PASSED=$(printf '%s' "$SUMMARY" | grep -oE '[0-9]+ passed' | head -1 | grep -oE '[0-9]+' || echo "0")
FAILED=$(printf '%s' "$SUMMARY" | grep -oE '[0-9]+ failed' | head -1 | grep -oE '[0-9]+' || echo "0")
ERRORS=$(printf '%s' "$SUMMARY" | grep -oE '[0-9]+ error' | head -1 | grep -oE '[0-9]+' || echo "0")

if [ "$FAILED" -gt 0 ] || [ "$ERRORS" -gt 0 ]; then
    cat >&2 <<EOF
[pre-commit-invariant-test] BLOCKED: ${FAILED} failed + ${ERRORS} errors
in ${TEST_FILES} (baseline: ${BASELINE_PASSED} passed / ${BASELINE_SKIPPED} skipped / 0 failed).

Fix the failing tests OR explicitly opt out:
  export COMMIT_INVARIANT_TEST_SKIP=1

Last 3 lines of pytest output:
${SUMMARY}
EOF
    exit 2
fi

if [ "$PASSED" -lt "$BASELINE_PASSED" ]; then
    cat >&2 <<EOF
[pre-commit-invariant-test] BLOCKED: pass count regressed.
Observed ${PASSED} passed; baseline ${BASELINE_PASSED}. Some tests went from
PASS → SKIP/XFAIL/ERROR without explicit baseline update.

Last 3 lines:
${SUMMARY}
EOF
    exit 2
fi

# Allow
exit 0
