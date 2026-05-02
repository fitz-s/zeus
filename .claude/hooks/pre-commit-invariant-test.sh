#!/usr/bin/env bash
# Created: 2026-04-27
# Last reused/audited: 2026-05-02
# Authority basis: round2_verdict.md §4.1 #5 + judge_ledger.md §54 + ultrareview-25 F2 fix
#                  + ultrareview25_remediation 2026-05-01 P0-2 (dual-channel)
#                  + 2026-05-02 PR #40 follow-up: documented bypasses (marker /
#                    sentinel / env) so agents can land main-side regression
#                    reconciliations without --no-verify and without burning
#                    context on env-propagation workarounds.
#
# Dual-channel pre-commit invariant gate. Runs the pytest baseline check on
# every `git commit`, regardless of whether the commit was issued by an agent
# (Claude Code Bash tool) or directly by the operator. Single source of truth
# for the BASELINE_PASSED count.
#
# Three documented escape hatches (see "Documented escape hatches" section
# below for usage): commit-message marker `[skip-invariant]`, one-shot
# sentinel `.git/skip-invariant-once`, and env `COMMIT_INVARIANT_TEST_SKIP=1`.
# Use them when origin/main itself regresses or you are deliberately ratcheting
# the baseline; do not use --no-verify (which silently skips ALL hooks).
#
# Channel A (agent / PreToolUse Bash): wired in .claude/settings.json with
# matcher "Bash". The hook receives a JSON payload on stdin; we parse it,
# filter to `git commit` invocations, and run the test.
#
# Channel B (operator / git pre-commit hook): wired by symlinking this file to
# .claude/hooks/pre-commit and running `git config core.hooksPath .claude/hooks`
# (operator runs scripts/install_hooks.sh once per fresh clone). git invokes
# the hook with NO stdin; we detect this via basename($0) == "pre-commit" or
# the GIT_INDEX_FILE env var that git sets for every hook.
#
# Both channels share BASELINE_PASSED, the test file list, and the opt-out env
# var so coverage is identical regardless of who runs `git commit`.
#
# Exit 0 = allow; exit 2 = block (works for both channels).

set -euo pipefail

HOOK_DIR=$(cd "$(dirname "$0")" && pwd)
HOOK_COMMON="${HOOK_DIR}/hook_common.py"

# ---------------------------------------------------------------------------
# Channel detection
# ---------------------------------------------------------------------------
SCRIPT_BASENAME=$(basename "$0")
if [ "$SCRIPT_BASENAME" = "pre-commit" ] || [ -n "${GIT_INDEX_FILE:-}" ]; then
    CHANNEL=git
else
    CHANNEL=agent
fi

# ---------------------------------------------------------------------------
# Channel A (agent): parse JSON, filter to `git commit`
# ---------------------------------------------------------------------------
if [ "$CHANNEL" = "agent" ]; then
    INPUT=$(cat)
    if ! COMMAND=$(printf '%s' "$INPUT" | python3 "$HOOK_COMMON" extract-json-field command 2>/tmp/pre-commit-invariant-json.err); then
        echo "[pre-commit-invariant-test] BLOCKED: malformed Claude hook JSON ($(cat /tmp/pre-commit-invariant-json.err 2>/dev/null || echo parse failure))" >&2
        exit 2
    fi

    if [ -z "$COMMAND" ]; then
        exit 0
    fi
    # Detect `git commit` invocation (allow `git commit-tree`, `git commit-graph` plumbing).
    if HOOK_COMMAND="$COMMAND" python3 "$HOOK_COMMON" has-git-subcommand commit; then
        :
    else
        PARSE_STATUS=$?
        if [ "$PARSE_STATUS" -eq 64 ]; then
            echo "[pre-commit-invariant-test] BLOCKED: could not safely parse git commit command" >&2
            exit 2
        fi
        exit 0
    fi
fi
# Channel B (git): no JSON, no command filter — git already filtered to commit.

# ---------------------------------------------------------------------------
# Documented escape hatches (in priority order). Use the LIGHTEST one that
# applies — they each leave a different audit trail.
#
# 1. Commit message marker `[skip-invariant]` (recommended for one-off
#    bypasses, e.g., merging in main-side regressions, intentional baseline
#    drops). Visible in `git log` forever; an auditor can see why a commit
#    skipped the gate without reading shell history.
#       git commit -m "Reconcile main-side healthcheck regression
#
#       [skip-invariant] origin/main was already failing 7 healthcheck
#       tests pre-merge; baseline lowered separately in commit XYZ."
#
# 2. Sentinel file `.git/skip-invariant-once` (one-shot, auto-deleted).
#    Use when you can't easily set the message text (e.g., automated
#    rebase). Trace lives in shell history only.
#       touch .git/skip-invariant-once && git commit ...
#
# 3. Env var `COMMIT_INVARIANT_TEST_SKIP=1` (session-wide). Channel A
#    (agent / PreToolUse) generally CANNOT propagate inline env vars —
#    use marker (#1) or sentinel (#2) instead. Channel B (operator
#    shell) can use this directly.
#
# All three honor the same exit-0 path. Don't add more bypass mechanisms
# without removing one of these — the value is in being few and discoverable.
# ---------------------------------------------------------------------------

# Bypass 3: env var (session-wide).
if [ "${COMMIT_INVARIANT_TEST_SKIP:-0}" = "1" ]; then
    echo "[pre-commit-invariant-test] SKIPPED (env COMMIT_INVARIANT_TEST_SKIP=1) channel=${CHANNEL}" >&2
    exit 0
fi

# Bypass 2: one-shot sentinel file. Auto-delete so it doesn't accidentally
# stay armed for the next commit.
SENTINEL_FILE=".git/skip-invariant-once"
if [ -f "$SENTINEL_FILE" ]; then
    rm -f "$SENTINEL_FILE"
    echo "[pre-commit-invariant-test] SKIPPED (sentinel ${SENTINEL_FILE}, auto-cleared) channel=${CHANNEL}" >&2
    exit 0
fi

# Bypass 1: commit message marker `[skip-invariant]`.
# Channel A (agent): the JSON command contains the message; substring-match
#                    the literal marker in the raw command string. Heredoc
#                    and -m forms both contain the literal token.
# Channel B (git):   $1 is the path to .git/COMMIT_EDITMSG (set by git for
#                    every commit hook invocation). Grep the file directly.
SKIP_MARKER='[skip-invariant]'
if [ "$CHANNEL" = "agent" ]; then
    case "$COMMAND" in
        *"$SKIP_MARKER"*)
            echo "[pre-commit-invariant-test] SKIPPED (marker ${SKIP_MARKER} in commit message) channel=${CHANNEL}" >&2
            exit 0
            ;;
    esac
else
    # Channel B: git passes COMMIT_EDITMSG as $1 to pre-commit only on some
    # versions; read .git/COMMIT_EDITMSG directly which is always populated.
    COMMIT_MSG_FILE="${1:-.git/COMMIT_EDITMSG}"
    if [ -f "$COMMIT_MSG_FILE" ] && grep -qF -- "$SKIP_MARKER" "$COMMIT_MSG_FILE" 2>/dev/null; then
        echo "[pre-commit-invariant-test] SKIPPED (marker ${SKIP_MARKER} in ${COMMIT_MSG_FILE}) channel=${CHANNEL}" >&2
        exit 0
    fi
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PYTEST_BIN="${ZEUS_HOOK_PYTEST_BIN:-${REPO_ROOT}/.venv/bin/python}"
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
#   CALIBRATION_HARDENING BATCH 2 (detect_parameter_drift ratio test + per-coefficient evidence): 170 → 181 (+11)
#   CALIBRATION_HARDENING BATCH 3 (weekly runner e2e + per-bucket threshold + bootstrap_usable_count fix + sys.path bootstrap regression): 181 → 189 (+8: 7 e2e + 1 LOW-NUANCE-CALIBRATION-1-2 test)
#   LEARNING_LOOP BATCH 1 (settlement→pair→retrain pipeline state projection + retrain_trigger.py read fn + tests): 189 → 203 (+14: 3 reader + 8 projection + 3 helper tests)
#   LEARNING_LOOP BATCH 2 (detect_learning_loop_stall 3 composable stall_kinds + per-kind insufficient_data + severity boundaries): 203 → 210 (+7)
#   LEARNING_LOOP BATCH 3 (weekly runner e2e + cross-module orchestration + AGENTS.md + 2 LOW carry-forwards: LOW-DESIGN-LL-2-1 documentation + LOW-DOCSTRING-CALIBRATION-3-2 5-runner extension): 210 → 217 (+7 e2e)
#   ultrareview25_remediation P0-3 (INV-05 antibody test_risk_actions_exist_in_schema): 217 → 218 (+1)
#   ultrareview25_remediation P0-5 (for_city routing antibody test_settlement_semantics_construction_routes_through_for_city): 218 → 219 (+1)
#   ultrareview25_remediation P1-8 (invariant citation gate test_invariant_citations.py — 2 tests + co-tenant): 219 → 222 (+3)
#   ultrareview25_remediation P1-9 (INV-03 append-first + INV-07 lifecycle grammar + INV-10 LLM-not-authority antibodies — 7 tests): 222 → 229 (+7)
#   ultrareview25_remediation P1-2 (identity-column DEFAULT regression gate — 2 tests): 229 → 231 (+2)
#   ultrareview25_remediation P1-3 (TruthAuthority StrEnum closure — 5 tests): 231 → 236 (+5)
#   ultrareview25_remediation P1-3+ (is_authoritative + requires_human_review predicates — 5 tests): 236 → 241 (+5)
#   Cherry-pick 7743f692 (ultrareview-25 P3-B): F5/F10 inv_prototype idempotency antibodies — 2 tests: 241 → 243 (+2)
#   ultrareview25_remediation P2 (dynamic-SQL per-file baseline gate — 2 tests): 243 → 245 (+2)
#   ultrareview25_remediation P2-1 (contract source-field per-file baseline — 2 tests): 245 → 247 (+2)
#   B4 Phase 7 (2026-05-01): expand gate with 14 newly-fixed test files from Phases 3/4/5/6
#     (Cat A 8 fixture refreshes + Cat B3 P0 hardening + Cat F runtime_guards + Cat G calibration
#     + Cat H 7 healthcheck + assumptions_validation + semantic_linter). 247→656 (+409); 22→46 (+24).
#     Files added: test_data_rebuild_relationships, test_phase10d_closeout,
#     test_ensemble_snapshots_bias_corrected_schema, test_tigge_snapshot_p_raw_backfill, test_db,
#     test_replay_time_provenance, test_run_replay_cli, test_rebuild_pipeline,
#     test_calibration_unification, test_p0_hardening, test_healthcheck,
#     test_assumptions_validation, test_semantic_linter, test_runtime_guards.
TEST_FILES="tests/test_architecture_contracts.py tests/test_settlement_semantics.py tests/test_digest_profiles_equivalence.py tests/test_inv_prototype.py tests/test_edge_observation.py tests/test_edge_observation_weekly.py tests/test_attribution_drift.py tests/test_attribution_drift_weekly.py tests/test_ws_poll_reaction.py tests/test_ws_poll_reaction_weekly.py tests/test_calibration_observation.py tests/test_calibration_observation_weekly.py tests/test_learning_loop_observation.py tests/test_learning_loop_observation_weekly.py tests/test_invariant_citations.py tests/test_identity_column_defaults.py tests/test_truth_authority_enum.py tests/test_dynamic_sql_baseline.py tests/test_contract_source_fields_baseline.py tests/test_data_rebuild_relationships.py tests/test_phase10d_closeout.py tests/test_ensemble_snapshots_bias_corrected_schema.py tests/test_tigge_snapshot_p_raw_backfill.py tests/test_db.py tests/test_replay_time_provenance.py tests/test_run_replay_cli.py tests/test_rebuild_pipeline.py tests/test_calibration_unification.py tests/test_p0_hardening.py tests/test_healthcheck.py tests/test_assumptions_validation.py tests/test_semantic_linter.py tests/test_runtime_guards.py"
# 2026-05-02 PR-B (oracle gate removal): 656 → 653.
# net = -2 deleted oracle-gate tests in test_runtime_guards (gate removed,
# graceful fallback covers the path) + 7 fixed healthcheck tests (autouse
# mock added, were failing on main against missing state/assumptions.json) -
# 9 not-currently-passing baseline (7 healthcheck reclaimed; 1 test_day0
# happens to pass; 1 test_live_assumptions_manifest xfailed pending fix).
BASELINE_PASSED=653
BASELINE_SKIPPED=46

if [ ! -x "$PYTEST_BIN" ]; then
    cat >&2 <<EOF
[pre-commit-invariant-test] BLOCKED: ${PYTEST_BIN} not found or not executable.
Cannot prove invariant-test baseline ${BASELINE_PASSED} passed / ${BASELINE_SKIPPED} skipped.

Fix the Python environment OR explicitly opt out:
  export COMMIT_INVARIANT_TEST_SKIP=1
EOF
    exit 2
fi

cd "$REPO_ROOT"

# Run, capture, parse (PYTEST_BIN is the venv python; invoke pytest as module).
# Multi-file: TEST_FILES is space-separated; let word-splitting expand it.
set +e
RESULT=$("$PYTEST_BIN" -m pytest $TEST_FILES -q --no-header 2>&1)
PYTEST_STATUS=$?
set -e
# Note: `-m pytest` after the python interpreter is correct (python -m pytest <args>)
SUMMARY=$(printf '%s' "$RESULT" | tail -3 | tr '\n' ' ')

# Extract counts from full pytest output, not only the tail. Use Python so
# `set -o pipefail` cannot turn no-match grep pipelines into parser drift.
COUNTS=$(printf '%s' "$RESULT" | "$PYTEST_BIN" -c '
import re, sys
text = sys.stdin.read()
def last_count(word_re: str) -> int:
    matches = re.findall(r"(\d+)\s+" + word_re + r"\b", text)
    return int(matches[-1]) if matches else 0
print(last_count("passed"), last_count("failed"), last_count("errors?"))
')
PASSED=$(printf '%s' "$COUNTS" | awk '{print $1}')
FAILED=$(printf '%s' "$COUNTS" | awk '{print $2}')
ERRORS=$(printf '%s' "$COUNTS" | awk '{print $3}')

if [ "$PYTEST_STATUS" -ne 0 ] && [ "$FAILED" -eq 0 ] && [ "$ERRORS" -eq 0 ]; then
    cat >&2 <<EOF
[pre-commit-invariant-test] BLOCKED: pytest exited with status ${PYTEST_STATUS}
but no failed/error count could be parsed. Treating as fail-closed.

Last 3 lines of pytest output:
${SUMMARY}
EOF
    exit 2
fi

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
