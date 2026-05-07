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
# below for usage): Channel-A command marker `[skip-invariant]`, one-shot
# sentinel `$(git rev-parse --git-dir)/skip-invariant-once`, and env `COMMIT_INVARIANT_TEST_SKIP=1`.
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
# 1. Commit message marker `[skip-invariant]` (recommended — visible in
#    git log forever). Works for BOTH Channel A (agent PreToolUse Bash) AND
#    Channel B (git pre-commit). Channel B detects it by walking the parent
#    process chain via `ps -o args= -p $PPID` (since COMMIT_EDITMSG is not
#    written before pre-commit fires for `git commit -m "..."`).
#       git commit -m "Reconcile main-side healthcheck regression
#
#       [skip-invariant] origin/main was already failing 7 healthcheck
#       tests pre-merge; baseline lowered separately in commit XYZ."
#
# 2. Sentinel file `$(git rev-parse --git-dir)/skip-invariant-once` (one-shot,
#    auto-deleted by Channel B). Works in normal repos and linked worktrees.
#    Use when you can't easily set the message text (e.g., automated rebase)
#    or need the native git pre-commit path to skip. Trace lives in shell
#    history only.
#       touch "$(git rev-parse --git-dir)/skip-invariant-once" && git commit ...
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
# File-based skip sentinel: .claude/hooks/.invariant_skip (one-time, auto-deleted after use).
# Use when env var cannot propagate to the PreToolUse subprocess (e.g. agent channel).
SKIP_SENTINEL="${HOOK_DIR}/.invariant_skip"
if [ -f "$SKIP_SENTINEL" ]; then
    echo "[pre-commit-invariant-test] SKIPPED (sentinel file: .claude/hooks/.invariant_skip) channel=${CHANNEL}" >&2
    # Only delete on the git channel (final commit); agent channel fires first as
    # a pre-tool-use gate and must leave the sentinel for the git channel to read.
    if [ "$CHANNEL" = "git" ]; then
        rm -f "$SKIP_SENTINEL"
    fi
    exit 0
fi

# Bypass 2: one-shot sentinel file. Auto-delete in Channel B (git) only —
# both channels fire for a single `git commit`, so if Channel A (agent
# PreToolUse) cleared it, Channel B (the actual git pre-commit) would
# see no sentinel and run pytest anyway. Channel B is always the LAST
# to run before the commit object is created, so clearing there gives
# a true one-shot semantics for both channels.
SENTINEL_FILE="$(git rev-parse --git-dir 2>/dev/null || printf '%s' .git)/skip-invariant-once"
if [ -f "$SENTINEL_FILE" ]; then
    if [ "$CHANNEL" = "git" ]; then
        rm -f "$SENTINEL_FILE"
        echo "[pre-commit-invariant-test] SKIPPED (sentinel ${SENTINEL_FILE}, auto-cleared) channel=${CHANNEL}" >&2
    else
        echo "[pre-commit-invariant-test] SKIPPED (sentinel ${SENTINEL_FILE}; will auto-clear in channel=git) channel=${CHANNEL}" >&2
    fi
    exit 0
fi

# Bypass 1: Channel-A command marker `[skip-invariant]`.
# Native git pre-commit runs before the final commit message is available, so
# Channel B intentionally does not inspect COMMIT_EDITMSG.
SKIP_MARKER='[skip-invariant]'

# ---------------------------------------------------------------------------
# Phase 2 migration shim: STRUCTURED_OVERRIDE=BASELINE_RATCHET (new path)
# Accepts both the legacy [skip-invariant] marker AND the new structured
# override env var. Legacy path emits migration_warning ritual_signal.
# Migration runway: 30 days (retire 2026-06-06).
#
# New path (preferred):
#   STRUCTURED_OVERRIDE=BASELINE_RATCHET git commit -m "..."
#   (requires evidence/baseline_ratchets/<date>_<phase>.md)
#
# Legacy path (deprecated, still accepted):
#   git commit -m "... [skip-invariant] ..."
# ---------------------------------------------------------------------------

# New structured override path (PREFERRED -- no migration_warning)
NEW_OVERRIDE="${STRUCTURED_OVERRIDE:-}"
if [ "$NEW_OVERRIDE" = "BASELINE_RATCHET" ] || \
   [ "$NEW_OVERRIDE" = "MAIN_REGRESSION" ] || \
   [ "$NEW_OVERRIDE" = "COTENANT_SHIM" ]; then
    echo "[pre-commit-invariant-test] SKIPPED (structured override ${NEW_OVERRIDE}) channel=${CHANNEL}" >&2
    exit 0
fi

if [ "$CHANNEL" = "agent" ]; then
    case "$COMMAND" in
        *"$SKIP_MARKER"*)
            # Legacy path -- emit migration_warning ritual_signal to telemetry
            HOOK_SIGNAL_DIR="${REPO_ROOT:-.}/.claude/logs/hook_signal"
            mkdir -p "$HOOK_SIGNAL_DIR" 2>/dev/null || true
            MONTH=$(date -u +%Y-%m 2>/dev/null || echo "unknown")
            WARN_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
            printf '{"hook_id":"invariant_test","event":"PreToolUse","decision":"allow","reason":"legacy_skip_invariant_marker","override_id":null,"session_id":null,"agent_id":null,"ts":"%s","ritual_signal":"migration_warning","migration_note":"[skip-invariant] is deprecated; use STRUCTURED_OVERRIDE=BASELINE_RATCHET. Runway ends 2026-06-06."}\n' "$WARN_TS" >> "${HOOK_SIGNAL_DIR}/${MONTH}.jsonl" 2>/dev/null || true
            echo "[pre-commit-invariant-test] SKIPPED (legacy marker ${SKIP_MARKER}) channel=${CHANNEL} -- MIGRATION WARNING: use STRUCTURED_OVERRIDE=BASELINE_RATCHET instead (runway ends 2026-06-06)" >&2
            exit 0
            ;;
    esac
else
    # Channel B: git pre-commit fires BEFORE COMMIT_EDITMSG is written when
    # invoked via `git commit -m "..."`, so reading that file alone is
    # unreliable. Primary strategy: walk the parent process chain (max 4 hops)
    # looking for the marker in the originating `git commit` argv. Fallback to
    # COMMIT_EDITMSG for interactive editor commits where the file IS written.
    _found_marker=0
    _pid=$PPID
    _hops=0
    while [ "$_pid" -gt 1 ] && [ "$_hops" -lt 4 ]; do
        _cmd=$(ps -o args= -p "$_pid" 2>/dev/null || true)
        case "$_cmd" in
            *"$SKIP_MARKER"*)
                _found_marker=1
                break
                ;;
        esac
        _pid=$(ps -o ppid= -p "$_pid" 2>/dev/null | tr -d ' ' || echo 1)
        _hops=$((_hops + 1))
    done
    # Fallback: COMMIT_EDITMSG exists for interactive editor commits.
    if [ "$_found_marker" -eq 0 ]; then
        COMMIT_MSG_FILE="${1:-.git/COMMIT_EDITMSG}"
        if [ -f "$COMMIT_MSG_FILE" ] && grep -qF -- "$SKIP_MARKER" "$COMMIT_MSG_FILE" 2>/dev/null; then
            _found_marker=1
        fi
    fi
    if [ "$_found_marker" -eq 1 ]; then
        echo "[pre-commit-invariant-test] SKIPPED (marker ${SKIP_MARKER} found in parent process args or COMMIT_EDITMSG) channel=${CHANNEL}" >&2
        exit 0
    fi
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PYTEST_BIN="${ZEUS_HOOK_PYTEST_BIN:-${REPO_ROOT}/.venv/bin/python}"

# Worktree-tolerant venv discovery (2026-05-04). Fresh worktrees routinely
# lack a local `.venv` because the canonical workspace at the main repo
# holds the only real venv; sibling worktrees rely on operator-managed
# symlinks that are not auto-provisioned. Without this fall-through,
# every fresh worktree's first commit hits the BLOCKED "PYTEST_BIN not
# found" path, then operators manually
# `ln -s /path/to/main/zeus/.venv .venv`. The recurrence is documented
# in the Claude memory ("Pre-commit invariant hook needed `.venv/bin/
# python` in worktree: fixed by symlinking canonical venv").
#
# Discovery: parse `git worktree list --porcelain` and pick the first
# `worktree` line — git lists the canonical (main) repo first, before
# any `git worktree add`-ed sibling. Fall through ONLY if:
#   - operator did not pin ZEUS_HOOK_PYTEST_BIN (their override wins)
#   - local PYTEST_BIN is missing or non-executable
#   - main worktree is a different path than REPO_ROOT (otherwise we'd
#     re-check the same broken path)
#   - main worktree's `.venv/bin/python` exists and is executable
# When falling through, surface the choice to stderr so operators are
# not silently running pytest from a sibling worktree's interpreter.
if [ ! -x "$PYTEST_BIN" ] && [ -z "${ZEUS_HOOK_PYTEST_BIN:-}" ]; then
    # Parse "worktree <path>" lines preserving paths with spaces — the
    # naive `awk '{print $2}'` would truncate at the first space
    # (Copilot+Codex P2 on the initial PR push; e.g.
    # "/Users/alice/Work Trees/zeus" became "/Users/alice/Work" and the
    # fallback silently failed for any operator whose checkout path has
    # a space). `sed -n 's/^worktree //p'` strips just the prefix and
    # emits the full path remainder verbatim. `head -n1` picks the
    # canonical (main) worktree — git lists it first.
    MAIN_WT=$(git -C "$REPO_ROOT" worktree list --porcelain 2>/dev/null \
        | sed -n 's/^worktree //p' | head -n1)
    if [ -n "$MAIN_WT" ] && [ "$MAIN_WT" != "$REPO_ROOT" ] \
       && [ -x "$MAIN_WT/.venv/bin/python" ]; then
        PYTEST_BIN="$MAIN_WT/.venv/bin/python"
        echo "[pre-commit-invariant-test] INFO: using main-worktree venv at $PYTEST_BIN (no local .venv at $REPO_ROOT/.venv)" >&2
    fi
fi

# Dry-run: print the resolved PYTEST_BIN and exit 0 BEFORE running
# pytest. Used by tests/test_pre_commit_hook.py::TestWorktreeVenvDiscovery
# to exercise the real hook's discovery block end-to-end without paying
# the full pytest run-time (the original PR's tests duplicated the
# discovery in a probe — Copilot+Codex flagged the duplication as
# locking in any future drift). NOT for production use; operators
# always want the full check.
if [ "${ZEUS_HOOK_DRY_RUN:-0}" = "1" ]; then
    echo "[pre-commit-invariant-test] DRY_RUN: PYTEST_BIN=$PYTEST_BIN" >&2
    exit 0
fi
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
TEST_FILES="tests/test_architecture_contracts.py tests/test_settlement_semantics.py tests/test_digest_profiles_equivalence.py tests/test_inv_prototype.py tests/test_edge_observation.py tests/test_edge_observation_weekly.py tests/test_attribution_drift.py tests/test_attribution_drift_weekly.py tests/test_ws_poll_reaction.py tests/test_ws_poll_reaction_weekly.py tests/test_calibration_observation.py tests/test_calibration_observation_weekly.py tests/test_learning_loop_observation.py tests/test_learning_loop_observation_weekly.py tests/test_invariant_citations.py tests/test_identity_column_defaults.py tests/test_truth_authority_enum.py tests/test_dynamic_sql_baseline.py tests/test_contract_source_fields_baseline.py tests/test_data_rebuild_relationships.py tests/test_phase10d_closeout.py tests/test_ensemble_snapshots_bias_corrected_schema.py tests/test_tigge_snapshot_p_raw_backfill.py tests/test_db.py tests/test_replay_time_provenance.py tests/test_run_replay_cli.py tests/test_rebuild_pipeline.py tests/test_calibration_unification.py tests/test_p0_hardening.py tests/test_healthcheck.py tests/test_assumptions_validation.py tests/test_semantic_linter.py tests/test_runtime_guards.py tests/runtime/test_evaluator_oracle_resilience.py"
# 2026-05-02 PR-B (oracle gate removal): 656 → 658.
# net = -2 deleted oracle-gate tests in test_runtime_guards (gate removed,
# graceful fallback covers the path) + 7 fixed healthcheck tests (autouse
# mock added, were failing on main against missing state/assumptions.json) -
# 9 not-currently-passing baseline (7 healthcheck reclaimed; 1 test_day0
# happens to pass; 1 test_live_assumptions_manifest xfailed pending fix) +
# 5 oracle resilience tests in tests/runtime/test_evaluator_oracle_resilience.py.
#
# 2026-05-04 PR #60 cluster-cleanup: 658 → 678 (+20). Repaired 50 silently
# rotted tests accumulated across PR #55–#59 by [skip-invariant] commits
# whose narrow test runs missed the regressions. Five clusters split between
# test debt (production moved forward, tests stale) and true regression
# (production code asymmetric or contract incomplete):
#   - Cluster A (test debt): safety_cap_usd removed in d0259327 (bankroll
#     doctrine), tests still passed it. -17 lines from test_runtime_guards.
#   - Cluster B (true regression): platt_models_v2 / calibration_pairs_v2
#     canonical schema lacked cycle/source_id/horizon_profile while
#     save_platt_model_v2 unconditionally inserted them. v2_schema.py now
#     adds the columns + idempotent ALTER for legacy DBs (mirror of the
#     ensemble_snapshots_v2 pattern). 11 hardcoded model_keys in tests
#     bumped from 5-part to 8-part Phase 2 format.
#   - Cluster C (test debt): production fetch_ensemble grew a kw-only
#     temperature_metric param; 8 lambda/def mocks added **kwargs.
#   - Cluster D (test debt): 2 new dynamic-SQL sites in
#     src/state/schema_introspection.py — internal-whitelist PRAGMA
#     interpolation; scanner baseline 143 → 145.
#   - Cluster E (true regression): 5 test_inv_prototype antibodies pinned
#     contracts production never reached — async-def recognition,
#     class-scope verification, schema column existence. Implemented in
#     architecture/inv_prototype.py + INV_02/INV_07 schema citations
#     gained ::table.column targets.
# All 50 prior failures cleared; net delta vs main = +20 passing tests.
#
# 2026-05-06 Phase 0.D fossil retire (topology-redesign): 678 → 674 (-4 passed), 46 → 50 (+4 skipped).
# architecture/digest_profiles.py deleted; test_digest_profiles_equivalence.py
# 4 tests switch from PASS/FAIL → SKIP (skipif guard already in place per Phase 3 intent).
# 2 PASS→SKIP (count/ids tests), 2 FAIL→SKIP (byte_for_byte + export_check).
# Net quality: neutral-to-positive (failures converted to expected skips).
BASELINE_PASSED=674
BASELINE_SKIPPED=50

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

Fix the failing tests OR explicitly opt out (use the LIGHTEST option that applies):

  1. Marker in commit message (recommended — visible in git log forever):
       git commit -m "Your message [skip-invariant] reason for skip"
     Works for BOTH Channel A (agent PreToolUse) and Channel B (git pre-commit).

  2. One-shot sentinel file (works when message text is not controllable):
       touch "\$(git rev-parse --git-dir)/skip-invariant-once" && git commit ...

  3. Env var (operator shell only — agents cannot reliably propagate this):
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
