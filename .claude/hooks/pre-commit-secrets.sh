#!/usr/bin/env bash
# Created: 2026-05-01
# Last reused/audited: 2026-05-02
# Authority basis: ultrareview25_remediation 2026-05-01 P0-2 + docs/operations/task_2026-05-02_review_crash_remediation/PLAN.md Slice 5
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
# If gitleaks is not installed, this hook prints an advisory and exits 0
# (does NOT block commits). The .gitleaks.toml configuration is in place for
# whenever the binary is added to PATH.
#
# Override: `SECRETS_SCAN_SKIP=1 git commit ...` skips the scan with audit-trail.
# Exit 0 = allow; exit 2 = block.

set -euo pipefail

HOOK_DIR=$(cd "$(dirname "$0")" && pwd)
HOOK_COMMON="${HOOK_DIR}/hook_common.py"

SCRIPT_BASENAME=$(basename "$0")
if [ "$SCRIPT_BASENAME" = "pre-commit-secrets-git" ] || [ -n "${GIT_INDEX_FILE:-}" ]; then
    CHANNEL=git
else
    CHANNEL=agent
fi

if [ "$CHANNEL" = "agent" ]; then
    INPUT=$(cat)
    if ! COMMAND=$(printf '%s' "$INPUT" | python3 "$HOOK_COMMON" extract-json-field command 2>/tmp/pre-commit-secrets-json.err); then
        echo "[pre-commit-secrets] BLOCKED: malformed Claude hook JSON ($(cat /tmp/pre-commit-secrets-json.err 2>/dev/null || echo parse failure))" >&2
        exit 2
    fi

    if [ -z "$COMMAND" ]; then
        exit 0
    fi
    if HOOK_COMMAND="$COMMAND" python3 "$HOOK_COMMON" has-git-subcommand commit; then
        :
    else
        PARSE_STATUS=$?
        if [ "$PARSE_STATUS" -eq 64 ]; then
            echo "[pre-commit-secrets] BLOCKED: could not safely parse git commit command" >&2
            exit 2
        fi
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

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$REPO_ROOT"

set +e
python3 "$HOOK_COMMON" validate-review-safe-tags "$REPO_ROOT"
STATUS=$?
set -e
if [ "$STATUS" -ne 0 ]; then
    if [ "$STATUS" -eq 64 ]; then
        echo "[pre-commit-secrets] BLOCKED: could not validate staged REVIEW-SAFE registry" >&2
    else
        echo "[pre-commit-secrets] BLOCKED: staged REVIEW-SAFE tag is not registered in SECURITY-FALSE-POSITIVES.md" >&2
    fi
    exit 2
fi

if command -v gitleaks >/dev/null 2>&1; then
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
else
    echo "[pre-commit-secrets] ADVISORY: gitleaks not on PATH — skipping gitleaks scan only (channel=${CHANNEL})." >&2
    echo "[pre-commit-secrets] Install: brew install gitleaks  (.gitleaks.toml is already configured)." >&2
fi

# ---------------------------------------------------------------------------
# pip-audit — only fires if a requirements file is in the staged diff.
# Catches a CVE-flagged version pin landing on a `git commit -- requirements*.txt`.
# Same advisory-vs-block contract as gitleaks: if pip-audit is not installed,
# emit an advisory and exit 0; otherwise scan and fail-closed on
# vulnerabilities. Audit-trail override via PIP_AUDIT_SKIP=1.
# (P3 follow-up to ultrareview25_remediation P1-4 pinning work.)
# ---------------------------------------------------------------------------

# Detect whether a requirements file is in the staged diff. Cheap; only fires
# when the dependency surface actually changed. Include nested requirements
# files so package-local dependency surfaces are not missed.
STAGED_REQUIREMENTS=$(git diff --cached --name-only --diff-filter=ACMR | grep -E '(^|/)requirements([^/]*\.txt|/.*\.txt)$' || true)
if [ -n "$STAGED_REQUIREMENTS" ]; then
    if [ "${PIP_AUDIT_SKIP:-0}" = "1" ]; then
        OVERRIDE_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
        OVERRIDE_LOG_PATH="${REPO_ROOT}/.claude/logs/pip-audit-overrides.log"
        mkdir -p "$(dirname "$OVERRIDE_LOG_PATH")" 2>/dev/null || true
        printf '%s\tchannel=%s\tcwd=%s\n' "$OVERRIDE_TS" "$CHANNEL" "$PWD" \
            >> "$OVERRIDE_LOG_PATH" 2>/dev/null || true
        echo "[pre-commit-secrets] pip-audit SKIPPED (PIP_AUDIT_SKIP=1) channel=${CHANNEL}; logged to ${OVERRIDE_LOG_PATH}" >&2
    elif command -v pip-audit >/dev/null 2>&1; then
        echo "[pre-commit-secrets] requirements file(s) staged — running pip-audit on staged blobs (channel=${CHANNEL})..." >&2
        while IFS= read -r REQ_FILE; do
            [ -z "$REQ_FILE" ] && continue
            TMP_REQ=$(mktemp)
            if ! git show ":${REQ_FILE}" > "$TMP_REQ" 2>/dev/null; then
                rm -f "$TMP_REQ"
                echo "[pre-commit-secrets] BLOCKED: could not read staged blob for ${REQ_FILE}" >&2
                exit 2
            fi
            if ! pip-audit -r "$TMP_REQ" --strict 2>&1; then
                rm -f "$TMP_REQ"
                cat >&2 <<EOF
[pre-commit-secrets] BLOCKED: pip-audit reported vulnerabilities in staged
${REQ_FILE}. Bump the affected pin to a non-vulnerable version OR
explicitly opt out:
  PIP_AUDIT_SKIP=1 git commit ...
(audit-logged to .claude/logs/pip-audit-overrides.log)

Pin policy: every \`>=\` floor must be a \`==\` exact pin per the
2026-05-01 ultrareview25_remediation P1-4 audit. New pins must clear
pip-audit before merging.
EOF
                exit 2
            fi
            rm -f "$TMP_REQ"
        done <<EOF_REQS
$STAGED_REQUIREMENTS
EOF_REQS
    else
        echo "[pre-commit-secrets] ADVISORY: requirements file(s) staged but pip-audit not on PATH — skipping (channel=${CHANNEL})." >&2
        echo "[pre-commit-secrets] Install: pip install pip-audit" >&2
    fi
fi

exit 0
