#!/usr/bin/env bash
# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: ultrareview25_remediation 2026-05-01 P0-2 + repo_review_2026-05-01 SYNTHESIS K-A/K-E
#
# install_hooks.sh — one-time per-clone hook setup.
#
# Why this exists: git hook directories live under .git/hooks/ which is NOT
# version-controlled. To make hooks travel with the repo, we keep them under
# .claude/hooks/ (tracked) and point git at that directory via core.hooksPath.
# Run this script once after `git clone` to enable both invariant-test and
# secrets-scan gates on operator-direct `git commit`.
#
# Idempotent — safe to re-run. Verifies that all required hooks are present
# and executable, and that the symlinks resolve correctly.
#
# Usage:
#   bash scripts/install_hooks.sh
#
# Verifying installation:
#   git config --get core.hooksPath          # should print: .claude/hooks
#   git -C . hook list 2>/dev/null || ls .claude/hooks
#
# Uninstall (operator must do this manually):
#   git config --unset core.hooksPath

set -euo pipefail

# Resolve the repo root from the script's own location so it works whether the
# operator runs it from inside the repo, from $HOME, or from anywhere else.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

if [ ! -d "${REPO_ROOT}/.git" ]; then
    echo "[install_hooks] ERROR: ${REPO_ROOT} is not a git repository." >&2
    echo "[install_hooks]   This script must live inside the Zeus repo at" >&2
    echo "[install_hooks]   /Users/leofitz/.openclaw/workspace-venus/zeus/scripts/install_hooks.sh" >&2
    echo "[install_hooks]   (or wherever the repo is checked out). If you cloned to a" >&2
    echo "[install_hooks]   different path, the script will still find the right root" >&2
    echo "[install_hooks]   automatically — but the .git directory must exist." >&2
    exit 1
fi

cd "$REPO_ROOT"

HOOK_DIR=".claude/hooks"
REQUIRED_HOOKS=(
    "pre-commit"
    "pre-commit-invariant-test.sh"
    "pre-commit-secrets.sh"
    "pre-merge-commit"
    "pre-merge-contamination-check.sh"
)

echo "[install_hooks] Repo root: $REPO_ROOT"

# Verify all required hooks exist.
MISSING=()
for hook in "${REQUIRED_HOOKS[@]}"; do
    if [ ! -e "${HOOK_DIR}/${hook}" ]; then
        MISSING+=("${hook}")
    elif [ ! -x "${HOOK_DIR}/${hook}" ] && [ ! -L "${HOOK_DIR}/${hook}" ]; then
        echo "[install_hooks] WARN: ${HOOK_DIR}/${hook} is not executable; fixing"
        chmod +x "${HOOK_DIR}/${hook}"
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "[install_hooks] ERROR: missing hooks: ${MISSING[*]}" >&2
    echo "[install_hooks] Did the .claude/hooks/ directory get truncated? Restore from git." >&2
    exit 1
fi

# Set core.hooksPath if not already set to .claude/hooks.
CURRENT_HOOKSPATH=$(git config --get core.hooksPath 2>/dev/null || echo "")
if [ "$CURRENT_HOOKSPATH" = "$HOOK_DIR" ]; then
    echo "[install_hooks] core.hooksPath already set to ${HOOK_DIR}; nothing to do."
else
    if [ -n "$CURRENT_HOOKSPATH" ]; then
        echo "[install_hooks] core.hooksPath currently set to '${CURRENT_HOOKSPATH}' — overwriting to '${HOOK_DIR}'."
    fi
    git config core.hooksPath "$HOOK_DIR"
    echo "[install_hooks] Set core.hooksPath = ${HOOK_DIR}"
fi

# Smoke-test the hooks by invoking them with empty stdin and a fake GIT_INDEX_FILE.
# Both should exit 0 (no failures) on a clean tree, or non-zero with diagnostic.
echo "[install_hooks] Smoke-testing pre-commit-secrets.sh (gitleaks may be absent — that's OK)..."
GIT_INDEX_FILE=fake "${HOOK_DIR}/pre-commit-secrets.sh" </dev/null >/dev/null 2>&1 || true
echo "[install_hooks] Smoke-testing pre-commit-invariant-test.sh (this may run pytest; ~5-30s)..."
if GIT_INDEX_FILE=fake COMMIT_INVARIANT_TEST_SKIP=1 "${HOOK_DIR}/pre-commit-invariant-test.sh" </dev/null >/dev/null 2>&1; then
    echo "[install_hooks] Invariant test hook reachable (skipped via env var for smoke-test)."
else
    echo "[install_hooks] WARN: invariant test hook returned non-zero on smoke-test; investigate." >&2
fi

cat <<EOF

[install_hooks] DONE. Active hooks via core.hooksPath = ${HOOK_DIR}:
  pre-commit          → invariant-test + secrets-scan (both required to pass)
  pre-merge-commit    → contamination-check (advisory on protected branches)

Companion config: .gitleaks.toml + SECURITY-FALSE-POSITIVES.md (root).

To verify on next commit:
  git commit --allow-empty -m "test: hooks active"
  # If pre-commit gates fire, you'll see [pre-commit-invariant-test] and
  # [pre-commit-secrets] diagnostics on stderr.

To temporarily skip in an emergency:
  COMMIT_INVARIANT_TEST_SKIP=1 SECRETS_SCAN_SKIP=1 git commit ...
  # Both overrides write audit lines to .claude/logs/.

To uninstall:
  git config --unset core.hooksPath
EOF
