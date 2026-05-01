#!/usr/bin/env bash
# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: ultrareview25_remediation 2026-05-01 P0-2 + repo_review_2026-05-01 SYNTHESIS K-E
#
# Dual-channel pre-commit secrets scan.
#
# Runs gitleaks against the staged content (git channel) or the command-being-
# committed (agent channel — best-effort detection only). Honors the project
# allowlist at .gitleaks.toml + the [REVIEW-SAFE: <TAG>] convention documented
# in SECURITY-FALSE-POSITIVES.md.
#
# Channel A (agent / PreToolUse Bash): wired in .claude/settings.json. The
# agent path is best-effort only — we cannot scan staged content from the JSON
# payload alone. Instead, when the agent runs `git commit` we run gitleaks
# protect against the staged tree using `gitleaks protect --staged`.
#
# Channel B (operator / git pre-commit hook): symlinked as .claude/hooks/
# pre-commit-secrets-git (or invoked by the orchestrator pre-commit). git has
# already populated the index by the time pre-commit fires, so the same
# `gitleaks protect --staged` works.
#
# If gitleaks is not installed, this hook prints a one-time advisory and exits
# 0 (does NOT block commits). The .gitleaks.toml configuration is in place for
# whenever the binary is added to PATH.
#
# Override: `SECRETS_SCAN_SKIP=1 git commit ...` skips the scan with audit-trail.
# Exit 0 = allow; exit 2 = block.

set -euo pipefail

SCRIPT_BASENAME=$(basename "$0")
if [ "$SCRIPT_BASENAME" = "pre-commit-secrets-git" ] || [ -n "${GIT_INDEX_FILE:-}" ]; then
    CHANNEL=git
else
    CHANNEL=agent
fi

if [ "$CHANNEL" = "agent" ]; then
    INPUT=$(cat)
    COMMAND=$(printf '%s' "$INPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print('')
    sys.exit(0)
ti = d.get('tool_input', {}) or {}
print(ti.get('command') or '')
" 2>/dev/null || echo "")

    if [ -z "$COMMAND" ]; then
        exit 0
    fi
    if ! printf '%s' "$COMMAND" | grep -qE '(^|[;&|[:space:]])git[[:space:]]+commit([[:space:]]|$)'; then
        exit 0
    fi
fi

if [ "${SECRETS_SCAN_SKIP:-0}" = "1" ]; then
    REPO_ROOT_FOR_LOG=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
    OVERRIDE_LOG_PATH="${REPO_ROOT_FOR_LOG}/.claude/logs/secrets-overrides.log"
    OVERRIDE_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    mkdir -p "$(dirname "$OVERRIDE_LOG_PATH")" 2>/dev/null || true
    printf '%s\tchannel=%s\tcwd=%s\n' "$OVERRIDE_TS" "$CHANNEL" "$PWD" \
        >> "$OVERRIDE_LOG_PATH" 2>/dev/null || true
    echo "[pre-commit-secrets] SKIPPED (SECRETS_SCAN_SKIP=1) channel=${CHANNEL}; logged to ${OVERRIDE_LOG_PATH}" >&2
    exit 0
fi

if ! command -v gitleaks >/dev/null 2>&1; then
    echo "[pre-commit-secrets] ADVISORY: gitleaks not on PATH — skipping scan (channel=${CHANNEL})." >&2
    echo "[pre-commit-secrets] Install: brew install gitleaks  (.gitleaks.toml is already configured)." >&2
    exit 0
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT"

# `gitleaks protect --staged` scans the index. Honors .gitleaks.toml at repo root.
if ! gitleaks protect --staged --redact --no-banner --config "${REPO_ROOT}/.gitleaks.toml" 2>&1; then
    cat >&2 <<EOF
[pre-commit-secrets] BLOCKED: gitleaks found secrets in staged content (channel=${CHANNEL}).

If this is a documented operator-cleared false positive:
  1. Verify the constant carries [REVIEW-SAFE: <TAG>] inline.
  2. Confirm the tag is registered in SECURITY-FALSE-POSITIVES.md.
  3. Confirm .gitleaks.toml allowlist contains the regex.
  4. Re-stage and re-commit.

Emergency override (audit-logged):
  SECRETS_SCAN_SKIP=1 git commit ...
EOF
    exit 2
fi

# ---------------------------------------------------------------------------
# pip-audit — only fires if requirements.txt is in the staged diff.
# Catches a CVE-flagged version pin landing on a `git commit -- requirements.txt`.
# Same advisory-vs-block contract as gitleaks: if pip-audit is not installed,
# emit a one-time advisory and exit 0; otherwise scan and fail-closed on
# vulnerabilities. Audit-trail override via PIP_AUDIT_SKIP=1.
# (P3 follow-up to ultrareview25_remediation P1-4 pinning work.)
# ---------------------------------------------------------------------------

# Detect whether requirements.txt is in the staged diff. Cheap; only fires
# when the dependency surface actually changed.
if git diff --cached --name-only | grep -qx "requirements.txt"; then
    if [ "${PIP_AUDIT_SKIP:-0}" = "1" ]; then
        OVERRIDE_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
        OVERRIDE_LOG_PATH="${REPO_ROOT}/.claude/logs/pip-audit-overrides.log"
        mkdir -p "$(dirname "$OVERRIDE_LOG_PATH")" 2>/dev/null || true
        printf '%s\tchannel=%s\tcwd=%s\n' "$OVERRIDE_TS" "$CHANNEL" "$PWD" \
            >> "$OVERRIDE_LOG_PATH" 2>/dev/null || true
        echo "[pre-commit-secrets] pip-audit SKIPPED (PIP_AUDIT_SKIP=1) channel=${CHANNEL}; logged to ${OVERRIDE_LOG_PATH}" >&2
    elif command -v pip-audit >/dev/null 2>&1; then
        echo "[pre-commit-secrets] requirements.txt staged — running pip-audit (channel=${CHANNEL})..." >&2
        if ! pip-audit -r "${REPO_ROOT}/requirements.txt" --strict 2>&1; then
            cat >&2 <<EOF
[pre-commit-secrets] BLOCKED: pip-audit reported vulnerabilities in staged
requirements.txt. Bump the affected pin to a non-vulnerable version OR
explicitly opt out:
  PIP_AUDIT_SKIP=1 git commit ...
(audit-logged to .claude/logs/pip-audit-overrides.log)

Pin policy: every \`>=\` floor must be a \`==\` exact pin per the
2026-05-01 ultrareview25_remediation P1-4 audit. New pins must clear
pip-audit before merging.
EOF
            exit 2
        fi
    else
        echo "[pre-commit-secrets] ADVISORY: requirements.txt staged but pip-audit not on PATH — skipping (channel=${CHANNEL})." >&2
        echo "[pre-commit-secrets] Install: pip install pip-audit" >&2
    fi
fi

exit 0
