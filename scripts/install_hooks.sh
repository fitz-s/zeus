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
# Run this script once after `git clone` to enable the dispatch-backed git
# hook entrypoints.
#
# Idempotent — safe to re-run. Verifies that all required hook entrypoints are
# present and executable.
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
    echo "[install_hooks]   ${SCRIPT_DIR}/install_hooks.sh" >&2
    echo "[install_hooks]   (or wherever the repo is checked out). If you cloned to a" >&2
    echo "[install_hooks]   different path, the script will still find the right root" >&2
    echo "[install_hooks]   automatically — but the .git directory must exist." >&2
    exit 1
fi

cd "$REPO_ROOT"

HOOK_DIR=".claude/hooks"
REQUIRED_HOOKS=(
    "pre-commit"
    "pre-merge-commit"
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

# Smoke-test the dispatch-backed entrypoints with their normal git-hook shape.
echo "[install_hooks] Smoke-testing pre-commit dispatch entrypoint..."
if COMMIT_INVARIANT_TEST_SKIP=1 SECRETS_SCAN_SKIP=1 "${HOOK_DIR}/pre-commit" </dev/null >/dev/null 2>&1; then
    echo "[install_hooks] pre-commit dispatch entrypoint reachable."
else
    echo "[install_hooks] WARN: pre-commit dispatch entrypoint returned non-zero on smoke-test; investigate." >&2
fi
echo "[install_hooks] Smoke-testing pre-merge-commit dispatch entrypoint..."
if "${HOOK_DIR}/pre-merge-commit" </dev/null >/dev/null 2>&1; then
    echo "[install_hooks] pre-merge-commit dispatch entrypoint reachable."
else
    echo "[install_hooks] WARN: pre-merge-commit dispatch entrypoint returned non-zero on smoke-test; investigate." >&2
fi

cat <<EOF

[install_hooks] DONE. Active hooks via core.hooksPath = ${HOOK_DIR}:
  pre-commit          → dispatch.py invariant-test + secrets-scan
  pre-merge-commit    → dispatch.py pre-merge contamination advisory

Companion config: .gitleaks.toml + SECURITY-FALSE-POSITIVES.md (root).

To verify on next commit:
  git commit --allow-empty -m "test: hooks active"
  # If pre-commit advisories fire, you'll see dispatch.py hook diagnostics.

To temporarily skip in an emergency:
  COMMIT_INVARIANT_TEST_SKIP=1 SECRETS_SCAN_SKIP=1 git commit ...
  # Both overrides write audit lines to .claude/logs/.

To uninstall:
  git config --unset core.hooksPath
EOF
