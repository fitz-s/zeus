# Run #16 Track D — F90a model-allowlist fix specification

- Date: 2026-05-17
- Branch: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17`
- Worktree: `.claude/worktrees/zeus-deep-alignment-audit-skill`
- Scope: read-only audit + operator-actionable JSON patch. **No production edits.**
- Mandate: F90a (SEV-1) — 3 enabled cron jobs failing every tick because `payload.model = "openai-codex/gpt-5.4-mini"` does not resolve.

## 1. The 3 enabled failing jobs

From `cron/jobs.json` (jq filter `select(.enabled and .payload.model | test("gpt-5.4-mini"))`):

| # | `name` | `id` (UUID prefix) | `agentId` | Schedule (cron) | Current `payload.model` |
|---|--------|--------------------|-----------|-----------------|-------------------------|
| 1 | `finance-subagent-scanner` | `c031f32c` | `main` | `*/20 8-16 * * 1-5` America/New_York | `openai-codex/gpt-5.4-mini` |
| 2 | `finance-subagent-scanner-offhours` | `f57c165f` | `main` | `0 0,4,7,17,20 * * *` America/New_York | `openai-codex/gpt-5.4-mini` |
| 3 | `memory-observer` | `09c19ea1` | `main` | `*/15 * * * *` America/Chicago | `openai-codex/gpt-5.4-mini` |

All three target `agentId="main"`, scheduled by `openclaw-node` (confirmed Run #15 T1). Combined firing rate ≈ 4 ticks / 15 min during US market hours.

## 2. Allowlist + provider model registry (current state)

### 2a. `agents.defaults.models` allowlist (`openclaw.json` L172–175)

```json
"models": {
  "openai/gpt-5.4": {},
  "openai/gpt-5.5": {}
}
```

Only TWO entries. `agents.defaults.model.primary = "openai/gpt-5.4"`, fallback = `["minimax-portal/MiniMax-M2.7"]`.

### 2b. `models.providers` registry (`openclaw.json` L68–161)

| Provider id | Registered model `id`s | Auth | Base URL |
|-------------|------------------------|------|----------|
| `claude-max` | `claude-sonnet-4`, `claude-opus-4`, `claude-haiku-4` | proxy | `http://127.0.0.1:3456/v1` |
| `minimax-portal` | `MiniMax-M2.7` | oauth | `https://api.minimaxi.com/anthropic` |
| `openai-codex` | **`gpt-5.4` ONLY** — no `-mini` variant registered | oauth | `https://api.openai.com` |

**No provider literally named `openai` exists in `models.providers`.** The `openai/...` prefix used in `agents.defaults` and in the four per-agent primaries is a virtual alias whose router target is implicit (presumably resolves to `openai-codex` via name normalization in node runtime).

### 2c. Per-agent primary models (`openclaw.json` L195–290)

All four list-agents (`main`, `venus`, `jupiter`, `neptune`) declare:

```
primary: "openai/gpt-5.4-mini"
fallbacks: ["minimax-portal/MiniMax-M2.7"]
```

`openai/gpt-5.4-mini` is **NOT in the allowlist and NOT in any provider's models[].id**. Empirically these agents do execute (operator-observed), implying per-agent `model` overrides allowlist OR silently falls through to the minimax fallback.

### 2d. Other 39 jobs (`payload.model` histogram across all 42 entries)

```
24 × minimax-portal/MiniMax-M2.7       (working; registered provider+model)
12 × openai-codex/gpt-5.4-mini         (3 enabled, 9 disabled — all broken)
 6 × (no payload.model)                (uses agent's primary)
```

## 3. Root cause (precise reframe of F90a)

User-supplied summary calls it an "allowlist" failure. The deeper truth is **two-layer resolution failure**:

1. **Provider-model-id mismatch**: `openai-codex` provider registers model id `gpt-5.4`; no `gpt-5.4-mini`. The fully-qualified `openai-codex/gpt-5.4-mini` cannot resolve to a registered `(provider, model_id)` tuple → hard error.
2. **Allowlist drift**: `agents.defaults.models` contains only `openai/gpt-5.4` and `openai/gpt-5.5`. The literal `openai-codex/...` namespace is not allowlisted at all, and `openai/gpt-5.4-mini` (which agents DO use as their own primary) is also absent — a latent inconsistency.

Layer (1) is why these 3 jobs fail every tick. Layer (2) is the META antibody surface (logged as F105 below) — the allowlist does not constrain per-agent primaries, so it is silently informational. Operators should be aware.

## 4. Per-job correct model (operator-actionable verdict)

Three substitution options, ranked:

| Option | Substitute value | Pro | Con |
|--------|------------------|-----|-----|
| **A (RECOMMENDED)** | `openai-codex/gpt-5.4` | Registered provider + registered model id; closest semantic match (drop the unregistered `-mini` suffix). | Slightly larger/slower than `-mini` would have been. |
| B | Remove `payload.model` field entirely | Agent uses its own primary `openai/gpt-5.4-mini`, which empirically works for these agents. | Magnifies layer-(2) drift — relies on undocumented per-agent override behavior. |
| C | `minimax-portal/MiniMax-M2.7` | Matches the 24 jobs already running on this provider; proven path. | Changes model class (OpenAI → MiniMax); behavioral regression risk for finance/memory deterministic scanners. |

**Recommended: Option A** for all 3 jobs. Rationale: minimal behavioral delta; restores OpenAI provider intent; only loses the `mini` size tier (which doesn't exist in the registered openai-codex namespace anyway).

| # | Job | Current | Substitute |
|---|-----|---------|------------|
| 1 | `finance-subagent-scanner` | `openai-codex/gpt-5.4-mini` | `openai-codex/gpt-5.4` |
| 2 | `finance-subagent-scanner-offhours` | `openai-codex/gpt-5.4-mini` | `openai-codex/gpt-5.4` |
| 3 | `memory-observer` | `openai-codex/gpt-5.4-mini` | `openai-codex/gpt-5.4` |

## 5. JSON patch (text block — operator applies manually)

Apply the same string substitution to all 3 enabled jobs (also covers the 9 disabled jobs; safe — they remain disabled).

### 5a. Atomic single-string substitution (preferred)

```bash
# READ-ONLY preview:
jq '[.jobs[] | select(.payload.model == "openai-codex/gpt-5.4-mini") | {name, enabled}]' \
  /Users/leofitz/.openclaw/cron/jobs.json

# WRITE (operator-approved; backs up first):
cp /Users/leofitz/.openclaw/cron/jobs.json \
   /Users/leofitz/.openclaw/backups/cron-jobs-before-f90a-model-fix-$(date -u +%Y%m%dT%H%M%SZ).json

jq '(.jobs[] | select(.payload.model == "openai-codex/gpt-5.4-mini") | .payload.model) |= "openai-codex/gpt-5.4"' \
   /Users/leofitz/.openclaw/cron/jobs.json > /tmp/jobs.json.new \
&& mv /tmp/jobs.json.new /Users/leofitz/.openclaw/cron/jobs.json
```

### 5b. Per-job diff (illustrative — shows the mutation shape)

```diff
 {
   "id": "c031f32c-0392-45bb-ae1a-ad7e7aec6938",
   "name": "finance-subagent-scanner",
   "payload": {
     "kind": "agentTurn",
     ...
-    "model": "openai-codex/gpt-5.4-mini",
+    "model": "openai-codex/gpt-5.4",
     "timeoutSeconds": 900,
     ...
   }
 }
```

Identical diff for `finance-subagent-scanner-offhours` and `memory-observer`.

### 5c. Verification post-mutation

```bash
jq '[.jobs[] | select(.enabled and (.payload.model // "" | test("gpt-5.4-mini")))] | length' \
   /Users/leofitz/.openclaw/cron/jobs.json
# expected: 0

jq '[.jobs[] | select(.enabled and (.payload.model // "") == "openai-codex/gpt-5.4")] | length' \
   /Users/leofitz/.openclaw/cron/jobs.json
# expected: >= 3
```

## 6. Kickstart command (post-fix)

`openclaw-node` reads `cron/jobs.json` at process start; the running daemon will not pick up the change without a reload.

```bash
launchctl kickstart -k gui/$UID/ai.openclaw.node
```

(If service label differs locally, inspect with `launchctl list | grep -i openclaw` first.)

Post-kickstart verification (Karachi-window window):

```bash
# Watch for first tick of memory-observer after kickstart:
tail -F /Users/leofitz/.openclaw/logs/cron-jobs-tracker.log | grep -E "memory-observer|finance-subagent-scanner"
```

Expected: status `ok` (or at least no `model … not allowed` / `model not registered` reject).

## 7. Bonus sweep — other latent model-allowlist / registry drifts

| # | Surface | Drift | Sev | Note |
|---|---------|-------|-----|------|
| D1 | Per-agent `model.primary = "openai/gpt-5.4-mini"` (main/venus/jupiter/neptune) | Not registered under any provider; not in allowlist | **MEDIUM** | Logged as **F105** below. Agents apparently survive via per-agent override or silent minimax fallback. |
| D2 | `agents.defaults.models` allowlist contains `openai/gpt-5.5` | No provider registers model id `gpt-5.5`; pure aspirational entry | LOW | Latent; harmless until something tries to use it. |
| D3 | 9 disabled jobs also use `openai-codex/gpt-5.4-mini` | Would re-explode if any of them is re-enabled | MEDIUM | F90a fix (sec 5a) sweeps these too; recommended belt-and-suspenders. |
| D4 | `minimax-portal/MiniMax-M2.7` works in 24 jobs but is NOT in `agents.defaults.models` allowlist | Confirms allowlist is informational, not enforced for cron payload.model resolution | LOW | Logged as part of F105. |
| D5 | `claude-max` provider registers 3 models, but zero cron jobs reference them; `agents.list[]` also does not bind to them | Dead surface OR reserved for `acp.defaultAgent="claude"` ACP path only | LOW | Out of scope; flag-only. |

No other "`<provider>/<unregistered-model-id>`" strings detected in active cron payloads. (Histogram in §2d is exhaustive.)

## 8. Karachi 5/17 blast radius

- `memory-observer` (`*/15 * * * *` America/Chicago): 4 ticks per hour silently failing → memory-pipeline observation gap during Karachi pre-discount window (the persistence-anomaly remediation in Run #15 T2 depends on memory-pipeline freshness).
- `finance-subagent-scanner` (`*/20 8-16 * * 1-5` America/New_York): silently dead during US market hours; finance signals unaffected for Zeus but the broader OpenClaw fleet loses scanner output.
- `finance-subagent-scanner-offhours` (`0 0,4,7,17,20 * * *` America/New_York): 5 ticks/day silently failing.

Per-fix impact: restores 4+5+5 = ≥14 successful tick cycles/day immediately on kickstart.

## 9. Constraint compliance

- READ-ONLY: confirmed — no edits to `cron/jobs.json` or `openclaw.json` in this commit.
- `git pull --ff-only`: confirmed clean (HEAD already at `d9094b1b8`, no remote drift).
- Patch text-only: all mutations expressed as bash one-liner + per-job diff; operator applies.

## 10. Files referenced

- `cron/jobs.json` (42 entries) — read only
- `openclaw.json` L68–290 (providers + agents) — read only
- `node.json` (9 lines; bootstrap config; contains no model references)
- Output dump: `/tmp/run16_jobs.json` (the 3 enabled-failing jobs in full)
- Output dump: `/tmp/run16_modelhist.txt` (model histogram across all 42 jobs)
