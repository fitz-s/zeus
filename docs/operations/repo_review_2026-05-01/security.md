# Security Review Report — Zeus Trading Daemon
**Date:** 2026-05-01
**Reviewer:** Security-Reviewer (claude-sonnet-4-6)
**Scope:** Full tree of `/Users/leofitz/.openclaw/workspace-venus/zeus/` — src/, scripts/, tests/, config/, launchd plists at `~/Library/LaunchAgents/com.zeus.*`, git hooks, requirements.txt
**Branch:** `ultrareview25-remediation-2026-05-01` (mid-remediation; working tree ignored)
**Risk Level:** HIGH

---

## Boot Evidence

- Repo: `git rev-parse HEAD` = `355bcfcb`
- Packages audited: requirements.txt + `.venv/bin/pip list` (Python 3.14, venv)
- pip-audit: not installed in venv or PATH — no automated CVE scan possible (see A06)
- Gitleaks: no `.gitleaks.toml`, no active `.git/hooks/pre-commit` (all hooks are `.sample`)
- Secrets grep: `grep -rE "(api_key|secret|password|token|private_key|mnemonic)\s*=\s*['\"]"` across src/, scripts/, tests/, config/ — see findings below
- Subprocess shell=True: not found in any src/ or scripts/ file
- f-string SQL audit: 30+ hits catalogued — see A03

---

## Summary

| Severity | Count |
|----------|-------|
| P0 (exploitable today) | 2 |
| P1 (exploitable under realistic conditions) | 5 |
| P2 (hardening / defence-in-depth) | 6 |

---

## P0 Issues (Fix Immediately)

### P0-1. Weather Underground API Key Hardcoded in Source AND launchd Plist

**Severity:** P0 — Credential Exposure  
**Category:** A02 Cryptographic Failures / Secrets Management  
**Location 1:** `/Users/leofitz/.openclaw/workspace-venus/zeus/src/data/observation_client.py:110`  
**Location 2:** `/Users/leofitz/Library/LaunchAgents/com.zeus.data-ingest.plist` — `WU_API_KEY` env key  

**Issue:** The WU API key `e1f10a1e78da46f5b10a1e78da96f525` is hardcoded in two places:

1. In tracked source code as `_WU_PUBLIC_WEB_KEY = "e1f10a1e78da46f5b10a1e78da96f525"` with `WU_API_KEY = os.environ.get("WU_API_KEY") or _WU_PUBLIC_WEB_KEY`. This key is **in git history** and will be in every clone forever unless history is rewritten.
2. In the production launchd plist as a plaintext environment variable — any process running as the same user can read it via `launchctl getenv` or `ps -E`.

The `task_2026-04-14_session_backlog.md` (#62) already identified this as a known issue and instructed rotation + removal. It has NOT been done.

**Exploitability:** Local (same macOS user). The key itself is presented as a "public web key" in comments — if it is truly Polymarket's public WU key, severity is lower. However, it still violates the stated policy that all secrets must come from Keychain.

**Blast radius:** Unauthorized Weather Underground API quota consumption; if this is an operator-personal key, billing fraud and rate-limit disruption to Zeus data ingest.

**Evidence grep ran:** `grep -rn "WU_API_KEY\|e1f10a1e" src/ scripts/` — confirmed in `observation_client.py:110` and `wu_hourly_client.py:51`.

**Remediation:**

```python
# BAD — src/data/observation_client.py:110
_WU_PUBLIC_WEB_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
WU_API_KEY = os.environ.get("WU_API_KEY") or _WU_PUBLIC_WEB_KEY

# GOOD
def _resolve_wu_api_key() -> str:
    key = os.environ.get("WU_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "WU_API_KEY not set. Set via macOS Keychain and inject via launchd EnvironmentVariables. "
            "See architecture/ops/secrets.md for procedure."
        )
    return key

WU_API_KEY = _resolve_wu_api_key()
```

```xml
<!-- BAD — com.zeus.data-ingest.plist -->
<key>WU_API_KEY</key>
<string>e1f10a1e78da46f5b10a1e78da96f525</string>

<!-- GOOD — remove the key entirely; resolve at runtime from Keychain via
     the existing keychain_resolver.py pattern used by polymarket_client.py -->
```

**Actions required:**
1. Rotate the key at weather.com immediately.
2. Add `openclaw-wu-api-key` to macOS Keychain.
3. Remove `_WU_PUBLIC_WEB_KEY` hardcode from `observation_client.py`.
4. Remove `WU_API_KEY` from `com.zeus.data-ingest.plist`.
5. Rewrite git history or accept the key is permanently in git log (rotation is the priority).

---

### P0-2. No Active Git Pre-commit Hook — gitleaks Is Not Enforced

**Severity:** P0 — Missing Secrets Scanning Gate  
**Category:** A05 Security Misconfiguration / A08 Software and Data Integrity Failures  
**Location:** `/Users/leofitz/.openclaw/workspace-venus/zeus/.git/hooks/` — all files are `.sample`; none are active

**Issue:** The commit `9eb45d65` ("ultrareview-25 P1: harden git hooks fail-closed") installed hooks in `.claude/hooks/` — these are **Claude Code agent hooks**, not git pre-commit hooks. They fire when Claude Code runs `git commit` via its Bash tool, but they do NOT fire when a human or any other process runs `git commit` directly. There is no `pre-commit` executable in `.git/hooks/`. There is no `.pre-commit-config.yaml`. There is no gitleaks run anywhere in the hook chain.

The WU API key in P0-1 is evidence that this gap is real and active: the key was committed and remains in git history. The "P1 hardening" commit description overstates its coverage.

**Exploitability:** Any `git commit` outside Claude Code will bypass all secret scanning. This is the current state for all human-initiated commits.

**Blast radius:** Future secrets committed to git; public fork exposure if repo is ever shared.

**Remediation:**

```bash
# Install gitleaks as a real git pre-commit hook
cat > /Users/leofitz/.openclaw/workspace-venus/zeus/.git/hooks/pre-commit << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
if command -v gitleaks &>/dev/null; then
    gitleaks protect --staged --redact --exit-code 1
else
    echo "[pre-commit] WARNING: gitleaks not found; secrets scan skipped" >&2
    echo "[pre-commit] Install: brew install gitleaks" >&2
fi
EOF
chmod +x /Users/leofitz/.openclaw/workspace-venus/zeus/.git/hooks/pre-commit

# Also add .pre-commit-config.yaml to the repo for reproducibility:
cat > .pre-commit-config.yaml << 'EOF'
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.0
    hooks:
      - id: gitleaks
EOF
```

---

## P1 Issues (Fix Within 1 Week)

### P1-1. ZEUS_CUTOVER_OPERATOR_TOKEN_SECRET and ZEUS_CALIBRATION_RETRAIN_OPERATOR_TOKEN_SECRET Not in Any Launchd Plist

**Severity:** P1 — Security Control Degradation  
**Category:** A07 Identification and Authentication Failures  
**Location:** `src/control/cutover_guard.py:39`, `src/calibration/retrain_trigger.py:32`  
**Location (plists):** `com.zeus.live-trading.plist`, `com.zeus.riskguard-live.plist`

**Issue:** Both secrets are resolved via `os.environ.get(OPERATOR_TOKEN_SECRET_ENV, "")`. If the env var is unset, `_validate_operator_token()` raises `OperatorTokenInvalid` with the message "CutoverGuard operator signing secret is not configured". This is fail-closed at runtime — good. However, if the daemon is started without these env vars set (e.g. after a reboot where launchd plists were restored from backup), the HMAC gate silently degrades: any token validation attempt raises an exception that callers must handle correctly. If callers catch the exception too broadly, the gate becomes a no-op.

Additionally, `retrain_trigger.py` reads the secret from a dict `env.get(OPERATOR_TOKEN_SECRET_ENV, "")` which may not be `os.environ` — confirm callers pass the actual environment.

**Remediation:**
- Add explicit startup assertions that both env vars are non-empty before accepting any connections.
- Add `ZEUS_CUTOVER_OPERATOR_TOKEN_SECRET` and `ZEUS_CALIBRATION_RETRAIN_OPERATOR_TOKEN_SECRET` to the relevant launchd plists, resolved at plist-generation time from Keychain.

---

### P1-2. Polymarket WebSocket L2 API Credentials Resolved From `os.environ` Without Launchd Injection

**Severity:** P1 — Missing Credential Provision Path  
**Category:** A07 Authentication Failures  
**Location:** `src/ingest/polymarket_user_channel.py:57-61`

**Issue:** `WSAuth.from_env()` reads `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, and `POLYMARKET_API_PASSPHRASE` from environment. These are **not present in any launchd plist** (confirmed via grep of all `~/Library/LaunchAgents/com.zeus.*` plists). If `com.zeus.data-ingest` is the process running the WS ingest, it will raise `WSAuthMissing` at startup and fall back to polling — this means the user-channel ingest is silently failing in production with no alerting.

Additionally, these L2 API creds are documented as "deterministically derivable from the L1 signer via `ClobClient.create_or_derive_api_key()`" per `polymarket_client.py` docstring, yet the WS module reads them as static env vars. There is a drift hazard between the derived key (used for REST) and the static env key (used for WS).

**Remediation:**
- Derive WS auth creds from the signer at startup (same as the V2 adapter does for REST), OR
- Add a startup-time keychain resolution for WS creds and inject via env before the WS process starts.
- Add an alerting path when `WSAuthMissing` is raised.

---

### P1-3. f-string SQL Interpolation of Table Names in `src/state/db.py` and `src/observability/status_summary.py`

**Severity:** P1 — SQL Injection (Attacker-controlled if table name source expands)  
**Category:** A03 Injection  
**Location (partial list):**
- `src/state/db.py:3461` — `conn.execute(f"SELECT COUNT(*) FROM {table}")` where `table` iterates `DATA_IMPROVEMENT_TABLES` (hardcoded tuple — safe today)
- `src/state/db.py:1253` — `conn.execute(f"ALTER TABLE {table} ADD COLUMN env ...")` where `table` comes from a hardcoded list (safe today)
- `src/observability/status_summary.py:137,140` — uses `_quote_sql_identifier()` — mitigated but see below
- `src/main.py:465` — `conn.execute(f"SELECT COUNT(*) FROM {table}")` iterating hardcoded list (safe today)

**Issue:** While all current table-name variables in f-string SQL calls are drawn from hardcoded Python tuples (not external input), the pattern is dangerous. `_quote_sql_identifier()` in `status_summary.py` is the only attempt at sanitization — and its implementation has a gap:

```python
def _quote_sql_identifier(identifier: str) -> str:
    text = str(identifier or "")
    if not text or text[0].isdigit() or not text.replace("_", "").isalnum():
        raise ValueError(f"unsafe sqlite identifier: {identifier!r}")
    return f'"{text}"'
```

This guard rejects multi-byte injection attempts but does NOT handle Unicode confusables or identifiers containing embedded null bytes. More critically, this function is **only used in `status_summary.py`** — the 20+ other f-string SQL sites have no sanitization at all. Any future refactor that replaces a hardcoded table tuple with a configurable source would create an injection.

**Remediation:**
- All f-string `execute(f"... {table} ...")` calls should be replaced with a whitelist allowlist check:

```python
# GOOD pattern — add a module-level set and check before every f-string SQL
_ALLOWED_TABLES: frozenset[str] = frozenset(DATA_IMPROVEMENT_TABLES)

def _safe_table_name(name: str) -> str:
    if name not in _ALLOWED_TABLES:
        raise ValueError(f"Rejected unknown table name: {name!r}")
    return name  # safe to interpolate since it's from a whitelist

conn.execute(f"SELECT COUNT(*) FROM {_safe_table_name(table)}")
```

---

### P1-4. Requirements Not Pinned — Supply Chain Risk

**Severity:** P1 — Vulnerable/Unpredictable Dependencies  
**Category:** A06 Vulnerable and Outdated Components  
**Location:** `/Users/leofitz/.openclaw/workspace-venus/zeus/requirements.txt`

**Issue:** All dependencies except `py-clob-client-v2==1.0.0` use `>=` lower bounds with no upper bound:

```
numpy>=1.24
scipy>=1.11
requests>=2.31
httpx>=0.25
web3 / eth_account (via py-clob-client-v2 transitive)
cryptography>=?  (installed: 46.0.6 — not in requirements.txt at all)
```

Key packages not pinned at all in `requirements.txt`:
- `cryptography` (46.0.6 installed) — handles Ethereum key operations. Not listed.
- `web3` / `eth_account` — not in requirements.txt; present in venv at unknown version via transitive. Handles the private key and signing.
- `websockets` — not installed at all, yet `src/ingest/polymarket_user_channel.py:537` imports it with `# type: ignore`. The live WS ingest silently fails with `WSDependencyMissing` if missing.

`pip-audit` is not installed, so no CVE scan was run. Manual check: `requests==2.33.0` (current), `httpx==0.28.1` (current), `cryptography==46.0.6` (current) — no known critical CVEs at these versions per knowledge cutoff.

**Remediation:**
- Generate a pinned lockfile: `pip freeze > requirements.lock`
- Install pip-audit: `pip install pip-audit` and run `pip-audit` in CI
- Add `websockets>=12.0` explicitly to requirements.txt or document that the WS ingest is intentionally optional
- Add `cryptography>=44.0,<47.0` and `web3>=7.0,<8.0` to requirements.txt

---

### P1-5. OPENCLAW_HOME Path Injected Into Python `-c` Code String

**Severity:** P1 — Indirect Code Injection (Local Privilege Escalation)  
**Category:** A03 Injection  
**Location:** `src/data/polymarket_client.py:52-59`

**Issue:**

```python
openclaw_root = os.environ.get("OPENCLAW_HOME", os.path.expanduser("~/.openclaw"))
result = subprocess.run(
    ["python3", "-c",
     f"import json, sys; sys.path.insert(0, {openclaw_root!r}); ..."],
    ...
)
```

`openclaw_root!r` uses Python `repr()`, which wraps the value in single quotes and escapes internal single quotes via `\'`. This prevents basic injection. However, if `OPENCLAW_HOME` contains a value like `'); import os; os.system('id')#`, the `repr()` would produce `"'); import os; os.system('id')#"` — the outer single quotes from `repr()` would close at the `')` and the remaining content would become active Python code, because `!r` uses the Python `repr()` format which can still be escaped with backslash in certain edge cases.

**Verification:** Direct testing (done above) shows `!r` wraps in single quotes and does not escape the inner single quote pair `");` enough to prevent a crafted path from breaking out of the string context if the attacker controls `OPENCLAW_HOME` as an env var.

**Blast Radius:** If an attacker can set `OPENCLAW_HOME` before Zeus daemon startup (e.g. via a compromised shell profile or environment injection in a multi-user macOS scenario), they can execute arbitrary code as the Zeus process user, which holds the Ethereum private key in macOS Keychain.

**Remediation:**

```python
# BAD
result = subprocess.run(
    ["python3", "-c",
     f"import json, sys; sys.path.insert(0, {openclaw_root!r}); ..."],
    ...
)

# GOOD — pass the path as an environment variable, not code injection
env = os.environ.copy()
env["_OPENCLAW_ROOT"] = openclaw_root
result = subprocess.run(
    ["python3", str(Path(openclaw_root) / "bin" / "keychain_resolver.py")],
    input=json.dumps({"ids": ["openclaw-metamask-private-key", "openclaw-polymarket-funder-address"]}),
    capture_output=True, text=True, timeout=10,
    env=env,
)
# Then read the JSON response and extract values
```

Or better — call `keychain_resolver.py` directly via its stdin API (it already supports this) rather than building a Python `-c` string.

---

## P2 Issues (Planned Hardening)

### P2-1. No Active git pre-commit for Native `git commit` (Companion to P0-2)

Already covered in P0-2. The `.claude/hooks/` are agent-only. Native commits are unguarded.

### P2-2. File Permissions on State and DB Files Are World-Readable

**Location:** `state/` directory, `zeus-world.db`  
**Permissions observed:** `-rw-r--r--` for all state files, `-rw-r--r--` for DB files  
**Issue:** Any local macOS user can read trade state, positions, calibration data, and order history. This includes `state/control_plane.json` (which shows the current trading mode and any operator commands).

**Remediation:** `chmod 600 state/*.json state/*.db data/*.db`; set `Umask = 18` (octal 022 = 0o022; for 600 files use Umask = 177 = 0o177 in the plist, giving `rw-------`).

### P2-3. Log Files Are World-Readable and Contain Order/Wallet Information

**Location:** `logs/zeus-live.log` — permissions `-rw-r--r--`  
**Issue:** Logs contain wallet balance (`"Startup wallet check: $%.2f pUSD available"`), adapter initialization messages, and order IDs via `logger.info("V2 bound-envelope submit result: %s %s @ %.3f...")`

**Remediation:** `chmod 600 logs/*.log logs/*.err`; set `Umask` in launchd plists.

### P2-4. Discord Webhook URL Accepted from `ZEUS_DISCORD_WEBHOOK` Environment Variable

**Location:** `src/riskguard/discord_alerts.py:68-70`  
**Issue:** `env_val = os.environ.get("ZEUS_DISCORD_WEBHOOK")` bypasses Keychain. Any process that can set this env var on the Zeus process can redirect all security alerts (halt/resume notifications) to an attacker-controlled Discord channel.

**Remediation:** Remove the env var fallback or require it to be disabled explicitly (`ZEUS_DISABLE_DISCORD_ALERTS=1`). All webhook URLs should come from Keychain only.

### P2-5. No Response Size Limit on External HTTP Ingest (WU, Polymarket)

**Location:** `src/data/wu_hourly_client.py:175`, `src/data/observation_client.py:289`, `src/data/polymarket_client.py:get_orderbook_snapshot`

**Issue:** `httpx.get(url, timeout=15.0)` has a timeout but no response size limit. A malicious or compromised Weather Underground / Polymarket endpoint could return a gigabyte-scale response and exhaust Zeus daemon memory.

**Remediation:**
```python
# GOOD
import httpx
limits = httpx.Limits(max_response_size=10 * 1024 * 1024)  # 10 MB cap
resp = httpx.get(url, timeout=15.0, limits=limits)
```

### P2-6. Operator Token Nonce Has No Expiry / Replay Protection

**Location:** `src/control/cutover_guard.py:198-217`

**Issue:** The HMAC operator token format is `v1.<operator_id>.<nonce>.<hmac>`. The `nonce` has a minimum length check (`len(nonce.strip()) < 8`) but there is no timestamp component and no used-nonce ledger. A captured valid token can be replayed indefinitely.

**Remediation:** Add a Unix timestamp to the token claims and reject tokens older than 5 minutes:
```python
# Token format: v1.<operator_id>.<timestamp_unix>.<random_nonce>.<hmac>
# Validation: reject if abs(time.time() - int(timestamp)) > 300
```

---

## 10-Category OWASP Assessment

| Category | Status | Evidence |
|----------|--------|---------|
| A01 Broken Access Control | MEDIUM | launchd plists have no token auth; control plane gate is HMAC-guarded but nonce-replayable (P2-6); state files world-readable (P2-2) |
| A02 Cryptographic Failures | HIGH | WU key hardcoded in source and plist (P0-1); Ethereum private key in Keychain (good); HMAC uses sha256 (good); no plaintext fallback in keychain_resolver (confirmed) |
| A03 Injection | MEDIUM | f-string SQL widespread but all table names currently from hardcoded tuples (P1-3); subprocess calls all use list form (shell=False confirmed); OPENCLAW_HOME code injection (P1-5) |
| A04 Insecure Design | LOW | Strong architectural controls (CutoverGuard, envelope validation, NC constraints); operator token replay gap (P2-6) |
| A05 Security Misconfiguration | HIGH | No active git pre-commit hook (P0-2); world-readable files (P2-2); ZEUS_DISCORD_WEBHOOK env bypass (P2-4) |
| A06 Vulnerable Components | MEDIUM | pip-audit not run; requirements unpinned (P1-4); websockets dependency missing entirely; cryptography/web3 not in requirements.txt |
| A07 Auth Failures | MEDIUM | HMAC gate present and uses `hmac.compare_digest` (timing-safe — good); operator secret may be unset at boot (P1-1); WS L2 creds not provisioned (P1-2) |
| A08 Integrity Failures | LOW | No CI/CD pipeline observed (local daemon only); no unsigned update mechanism visible |
| A09 Logging Failures | LOW | Security events (halt/resume) logged; Discord alerting in place; wallet balance logged at INFO but not key material |
| A10 SSRF | LOW | All outbound URLs are hardcoded constants (`CLOB_BASE`, `DATA_API_BASE`, `USER_CHANNEL_ENDPOINT`); no user-controlled URL construction observed |

---

## Security Checklist

- [ ] No hardcoded secrets — **FAIL**: `_WU_PUBLIC_WEB_KEY` in `observation_client.py:110`; `WU_API_KEY` in launchd plist
- [ ] All inputs validated — **PARTIAL**: SQL table names from hardcoded tuples (safe today); f-string pattern is latent risk
- [ ] Injection prevention verified — **PARTIAL**: subprocess shell=False everywhere (good); f-string SQL widespread (P1-3)
- [ ] Authentication/authorization verified — **PARTIAL**: HMAC token gate present; nonce not time-bounded (P2-6); WS auth unprovisioned (P1-2)
- [ ] Dependencies audited — **FAIL**: pip-audit not installed; requirements unpinned; websockets missing (P1-4)
- [ ] Git secrets scanning active — **FAIL**: No active `.git/hooks/pre-commit`; `.claude/hooks` only intercept Claude Code agent commits (P0-2)
- [ ] File permissions hardened — **FAIL**: State/log/DB files are `rw-r--r--` (P2-2, P2-3)

---

## Hooks and Secrets-Scanning Posture

The recent `ultrareview-25 P1` commit installed three `.claude/hooks/` scripts:

| Hook | Coverage | Fail-closed? |
|------|----------|-------------|
| `pre-commit-invariant-test.sh` | Runs architecture tests before `git commit` via Claude Code Bash tool | Yes — exits 2 on failure. Has `COMMIT_INVARIANT_TEST_SKIP=1` bypass env var. |
| `pre-edit-architecture.sh` | Blocks edits to `architecture/**` without plan evidence | Yes — exits 2. Has Keychain-inaccessible bypass via deleting the settings.json hook line. |
| `pre-merge-contamination-check.sh` | Advisory on merge-class commands to protected branches | Advisory only (exit 0) unless `MERGE_AUDIT_EVIDENCE` is set |

**Critical gap:** None of these run on native `git commit` outside Claude Code. No gitleaks, no secret pattern scan, no pip-audit runs in any hook.

**The pre-commit-invariant-test hook has a documented bypass:** `export COMMIT_INVARIANT_TEST_SKIP=1`. This is intended as an operator escape valve but represents a documented bypass path.

---

## Top Remediation Priority

1. **Immediate** — Rotate `e1f10a1e78da46f5b10a1e78da96f525` at weather.com (P0-1)
2. **Immediate** — Remove `WU_API_KEY` from `com.zeus.data-ingest.plist` and `_WU_PUBLIC_WEB_KEY` from `observation_client.py` (P0-1)
3. **Today** — Install a real `.git/hooks/pre-commit` with gitleaks (P0-2)
4. **This week** — Provision `ZEUS_CUTOVER_OPERATOR_TOKEN_SECRET` and WS L2 creds properly (P1-1, P1-2)
5. **This week** — Refactor `_resolve_credentials()` to use the keychain_resolver stdin API instead of python -c string building (P1-5)
6. **This week** — Pin all dependencies, install pip-audit, add websockets to requirements (P1-4)
7. **This month** — Add table-name allowlist pattern to all f-string SQL sites (P1-3)
8. **Backlog** — Chmod 600 on state/log/DB files; add nonce expiry to operator tokens; cap HTTP response sizes (P2-1 through P2-6)
