# Run #15 Track 1 — F90 Deep Dive: cron/jobs.json vs crontab vs launchd

## Metadata
- **Run**: #15 Track 1
- **Date**: 2026-05-17
- **Worktree**: `.claude/worktrees/zeus-deep-alignment-audit-skill`
- **Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `7fb380c59d`
- **Pull**: `git fetch origin && git pull --ff-only` → "Already up to date"
- **Mandate**: F90 deep dive — jobs.json catalog vs live crontab, identify Karachi-supporting silent gaps, top-5 restore commands.
- **Findings delta**: F90 reframed (F90a/b/c sub-findings) + F93 (new) + F94 (new) + F95 (new).

---

## TL;DR — Run #14's F90 framing was wrong; the real problem is worse

Run #14 wrote: **"`cron/jobs.json` 82 KB vs `crontab -l` only 2 lines — 40-job catalog NOT scheduled"** (SEV-1).

**That premise is false in 3 ways:**

1. `crontab -l` is **71 lines / 24 active scheduled commands** (not 2). Run #14's auditor likely grepped wrong or sampled a stub.
2. `jobs.json` has **42 jobs** (11 enabled, 31 disabled), and is **executed by `ai.openclaw.node` daemon** (PID 17251) — not by system crontab. Run-file evidence proves this (10/11 enabled jobs have current `cron/runs/<jobid>.jsonl` entries with mtimes < 1 day old).
3. The two schedulers are **intentionally separate orchestration layers**:
   - `jobs.json` → openclaw-node → **LLM agent turns** (Discord-channel session pushes via `payload.kind: agentTurn`)
   - `crontab` → **pure-Python scripts** (no LLM in path)
   - `launchctl` plists → **long-running daemons** (zeus, 9router, gateway, ibkr)
   31 jobs.json entries are explicitly `"enabled": false` — they are **intentionally dormant**, not silently un-scheduled.

**But the audit uncovered worse latent failures the original F90 missed:**

- **F90a SEV-1**: 3 enabled jobs failing every tick with `cron payload.model 'openai-codex/gpt-5.4-mini' rejected by agents.defaults.model`. Stale model id in payload, hard-rejected by config. Production observability+ingestion impact.
- **F90b SEV-2**: `memory-dream-cycle` and `memory-reflector` returning `cron: job execution timed out` on most ticks.
- **F90c SEV-3**: No `cron_reconcile` tool exists across the 3 scheduler layers (jobs.json / crontab / launchd). No single inventory.
- **F93 SEV-3** (NEW): No Karachi-specific (or any city-specific) source-fetch job exists in jobs.json. Karachi feed depends entirely on crontab `oracle_snapshot_listener.py` (1 tick/day @ 10:00 UTC) + launchd `com.zeus.forecast-live` + `com.zeus.data-ingest`. Single-point-of-failure per layer.
- **F94 SEV-3** (NEW): `jobs-state.json` is structurally a NO-OP (all 42 entries are empty `{}`). Real run state lives in `cron/runs/<jobid>.jsonl`. F90 in Run #14 may have inspected jobs-state.json and concluded "nothing runs" → wrong artifact.
- **F95 SEV-2** (NEW): `zeus-antibody-scan` (regression detector), `zeus-daily-audit`, and `zeus-heartbeat` are all DISABLED in jobs.json. `zeus-heartbeat` is replaced by crontab `*/30 heartbeat_dispatcher.py` (intentional). `zeus-antibody-scan` and `zeus-daily-audit` are **dormant since 2026-04-14/15 with no replacement**. Karachi regression coverage gap.

---

## 1. cron/jobs.json — full inventory (42 jobs)

Schema: `{ version, jobs: [{ id, agentId, name, enabled, schedule, sessionTarget, payload, ... }] }`. Path `/Users/leofitz/.openclaw/cron/jobs.json`. Size 1190 lines / ~80 KB.

### 1.1 ENABLED jobs (11/42) — with actual run-file evidence

| # | Job name | Schedule (expr / tz) | Agent | Last run (mtime) | Last status | Notes |
|---|---|---|---|---|---|---|
| 1 | `memory-observer` | `*/15 * * * *` CT | main | 2026-05-17 17:56 | **ERROR** | `payload.model openai-codex/gpt-5.4-mini rejected by agents.defaults.model` — failing every 15 min |
| 2 | `memory-reflector` | `0 * * * *` CT | main | 2026-05-17 18:03 | **ERROR** | `cron: job execution timed out` — failing hourly |
| 3 | `memory-dream-cycle` | `0 3 * * *` CT | main | 2026-05-17 03:10 | **ERROR** | `cron: job execution timed out` — failing nightly |
| 4 | `Memory Dreaming Promotion` | `0 3 * * *` CT | (unset) | 2026-05-17 03:10 | ok | Companion to dream-cycle |
| 5 | `academic-radar-scan` | `0 8 * * *` CT | jupiter | 2026-05-17 08:05 | ok | |
| 6 | `academic-radar-render` | `10 8 * * *` CT | jupiter | 2026-05-17 08:19 | ok | |
| 7 | `zeus-daily-review` | `30 0 * * *` CT | venus | 2026-05-17 00:33 | ok | **Karachi-relevant** (zeus venue) |
| 8 | `city-settlement-audit` | `0 9 * * 1` CT | venus | 2026-05-11 09:03 | ok | **Karachi-relevant**; next run Mon 5/18 |
| 9 | `google-workspace-chief-of-staff-morning-brief` | `30 9 * * 1-5` CT | main | 2026-05-15 11:33 | ? | last Fri OK; weekend skip expected |
| 10 | `finance-subagent-scanner` | `*/20 8-16 * * 1-5` ET | main | 2026-05-15 16:06 | **ERROR** | Same `payload.model` reject — failing every Mon-Fri tick |
| 11 | `finance-subagent-scanner-offhours` | `0 0,4,7,17,20 * * *` ET | main | 2026-05-17 16:00 | **ERROR** | Same `payload.model` reject — failing every tick |

**Net live (status=ok or expected-skip): 6/11. Production-failing: 4/11.**

### 1.2 DISABLED jobs (31/42)

| # | Job name | Schedule | Agent | Last historical run | Karachi-relevant? |
|---|---|---|---|---|---|
| 1 | `finance-premarket-brief` | 10 8 * * 1-5 ET | main | 2026-05-16 01:31 | no |
| 2 | `zeus-weekly-review` | 0 9 * * 1 CT | venus | 2026-04-13 18:02 | yes (low) |
| 3 | `expedition-pass` | 0 10,22 * * * CT | main | 2026-04-05 21:04 | no |
| 4 | `evolution-daily-report` | 30 22 * * * CT | main | 2026-05-07 22:35 | no |
| 5 | `evolution-expansion-tick` | 45 22 * * * CT | main | 2026-05-08 22:49 | no |
| 6 | `evolution-weekly-learning` | 0 21 * * 1 CT | main | 2026-05-04 21:15 | no |
| 7 | `evolution-skills-audit` | 0 21 1,15 * 1 CT | main | 2026-05-04 21:15 | no |
| 8 | `evolution-router-audit` | 0 21 8,22 * 1 CT | main | 2026-05-08 21:06 | no |
| 9 | `evolution-monthly-core-review` | 0 21 1 * * CT | main | 2026-05-01 21:10 | no |
| 10 | `codex-watchdog` | */15 * * * * CT | main | 2026-03-26 23:15 | no |
| 11 | `codex-consumption-report` | 0 9,21 * * * CT | main | 2026-04-07 09:02 | no |
| 12 | **`zeus-heartbeat`** | `*/30 * * * *` CT | venus | 2026-04-15 06:03 | **REPLACED** by crontab `*/30 heartbeat_dispatcher.py` — OK |
| 13 | **`zeus-daily-audit`** | `0 6 * * *` CT | venus | 2026-04-15 06:03 | **YES — NO REPLACEMENT** (F95) |
| 14 | `finance-report-renderer` | manual | main | 2026-04-13 14:55 | no |
| 15 | `finance-watcher-update` | manual | main | 2026-04-06 12:07 | no |
| 16 | `evolution-expansion-renderer` | manual | main | 2026-05-08 22:48 | no |
| 17 | **`zeus-antibody-scan`** | `0 7,19 * * *` CT | venus | 2026-04-14 19:05 | **YES — NO REPLACEMENT** (F95) |
| 18 | `finance-weekly-learning-review` | 20 22 * * 0 ET | main | 2026-04-26 22:23 | no |
| 19 | `finance-thesis-sidecar` | manual | main | — | no |
| 20 | `finance-tradingagents-sidecar` | 15,45 8-16 * * 1-5 ET | main | 2026-04-27 13:31 | no |
| 21 | `finance-tradingagents-primary-review` | 15 8 * * 1-5 ET | main | 2026-05-05 16:07 | no |
| 22 | `finance-immediate-alert` | */5 8-16 * * 1-5 ET | main | 2026-05-05 15:55 | no |
| 23 | `zeus-source-contract-auto-conversion-canary` | 0 0 1 1 * CT | venus | — | yes (low; one-shot canary) |
| 24 | `finance-premarket-delivery-watchdog` | 25 8 * * 1-5 ET | main | 2026-04-27 08:29 | no |
| 25 | `finance-midday-operator-review` | 15 13 * * 1-5 ET | main | 2026-05-05 12:22 | no |
| 26 | `dayloop-monitor` | 0 7-21 * * * CT | main | 2026-04-20 15:06 | no |
| 27 | `dayloop-fallback-wake` | */30 6-11 * * * CT | main | — | no |
| 28 | `dayloop-midday-replan` | 0 12-15 * * * CT | main | 2026-04-21 13:04 | no |
| 29 | `dayloop-nightly-reset` | 0 20-23 * * * CT | main | 2026-04-20 23:03 | no |
| 30 | `dayloop-eval` | 15 23 * * * CT | main | 2026-04-22 03:59 | no |
| 31 | `Write Dream Diary Entry` | one-shot at 2026-05-04 | main | 2026-05-04 04:35 | no |

---

## 2. `crontab -l` — current live state (71 total lines / 24 active commands)

(Active lines only, paraphrased; full text on disk.)

| # | Schedule | Command (abbreviated) | Karachi-relevant? |
|---|---|---|---|
| 1 | `*/15 * * * *` | total-recall observer-agent.sh | no (memory infra) |
| 2 | `0 21 * * *` | knowledge/scripts/memory_indexer.py | no |
| 3 | `0 * * * *` | ops/scripts/cron_jobs_tracker.py | observability of jobs.json |
| 4 | `*/5 * * * *` | ops/scripts/codex_health.py check | no |
| 5 | `0 1 * * *` | ops/scripts/codex_keepalive.py | no |
| 6 | `*/15 * * * *` | ops/scripts/location_detector.py | no |
| 7 | `*/5 * * * *` | finance/scripts/ibkr_tickle.py | no |
| 8 | `*/20 8-16 * * 1-5` | finance/scripts/price_fetcher.py | no |
| 9 | `50 7 * * 1-5` | finance/portfolio_flex_fetcher + enrichers + resolver | no |
| 10 | `0 16 * * 1-5` | finance/portfolio_flex_fetcher + enrichers + resolver | no |
| 11 | `5 8,16 * * 1-5` | finance/portfolio_alerts.py | no |
| 12 | `55 7,16 * * 1-5` | finance/watchlist_sync.py | no |
| 13 | `0 22 * * 0` | finance/calibration_loop.py | no |
| 14 | `*/30 * * * *` | **zeus/scripts/heartbeat_dispatcher.py** | **YES** |
| 15 | `0 21 * * 0` | finance/signal_learner.py | no |
| 16 | `0 16 * * 1-5` | finance/hypothesis_tracker.py extract | no |
| 17 | `55 7 * * 1-5` | finance/hypothesis_tracker.py verify | no |
| 18 | `0 * * * 1-5` | finance/event_watcher.py tick | no |
| 19 | `30 22 * * *` | evolution/scripts/expansion_tick.py | no |
| 20 | `0 9 * * *` | ops/scripts/codex_watchdog.py | no |
| 21 | `15 8,16 * * 1-5` | finance/tools/sync_reviewer_snapshot.sh | no |
| 22 | `0 10 * * *` | **zeus/scripts/oracle_snapshot_listener.py** (WU+HKO) | **YES — primary Karachi feed** |
| 23 | `30 8 * * *` | runtime-digest/aggregator.py | no |
| 24 | `0 8 * * 1` | runtime-digest/memory_audit.py | no |

(Also disabled/commented: TIGGE pipelines paused for re-extract; native finance scanners superseded.)

---

## 3. Per-job diff table — jobs.json vs crontab vs launchd

Reframing: there is no 1:1 mapping. The 3 layers serve different roles. The audit-relevant question is "for each layer, what's failing or missing?"

### 3.1 jobs.json (openclaw-node scheduler)

| Job | Catalog status | Real status (from run-file evidence) |
|---|---|---|
| `memory-observer` | enabled | **FAILING every 15 min (payload.model reject)** |
| `memory-reflector` | enabled | **FAILING hourly (timeout)** |
| `memory-dream-cycle` | enabled | **FAILING nightly (timeout)** |
| `Memory Dreaming Promotion` | enabled | ok |
| `academic-radar-scan` | enabled | ok |
| `academic-radar-render` | enabled | ok |
| `zeus-daily-review` | enabled | ok (Karachi-relevant) |
| `city-settlement-audit` | enabled | ok (Karachi-relevant; next 5/18) |
| `google-workspace-chief-of-staff-morning-brief` | enabled | ok-with-skip |
| `finance-subagent-scanner` | enabled | **FAILING every Mon-Fri tick (payload.model reject)** |
| `finance-subagent-scanner-offhours` | enabled | **FAILING every tick (payload.model reject)** |
| (31 others) | **disabled** | dormant (intentional or stale-debt — triage table in §1.2) |

### 3.2 crontab (pure-python scheduler)

| Command | Status |
|---|---|
| All 24 active lines | live; no smoke evidence of failure in tracker log |
| Commented TIGGE pipelines | PAUSED_FOR_TIGGE_REEXTRACT |
| Commented native finance scanners | Disabled 2026-04-13 in favor of jobs.json `finance-subagent-scanner*` (which are now FAILING — see §3.1) → **finance has a coverage GAP** |

### 3.3 launchd (long-running daemons; Karachi-runtime layer)

| Plist | launchctl status | Karachi-relevant? |
|---|---|---|
| `com.zeus.forecast-live` | PID 10397, exit=1 last cycle | **YES — see F87 (Run #14)** |
| `com.zeus.data-ingest` | PID 34316, exit=0 | **YES** |
| `com.zeus.heartbeat-sensor` | PID present, exit=0 | yes |
| `com.zeus.live-trading` | PID 80628, signal=-15 last | yes |
| `com.zeus.riskguard-live` | PID 54734, signal=-15 last | yes |
| `com.zeus.venue-heartbeat` | PID 70301, signal=-15 last | yes |
| `com.zeus.calibration-transfer-eval` | exit=0 | no |
| `ai.openclaw.node` | PID 17251, exit=1 last | runs jobs.json |
| Others (9router, ibkr, gateway) | running | no |

---

## 4. Karachi-supporting filter

**Karachi markets: 2026-05-17, 2026-05-19, 2026-05-22 (live).**

### 4.1 Active Karachi-supporting jobs (across all layers)

| Layer | Job | Cadence | Last evidence | Status |
|---|---|---|---|---|
| crontab | `oracle_snapshot_listener.py` (WU+HKO) | daily 10:00 UTC | live | **PRIMARY KARACHI FEED** |
| crontab | `heartbeat_dispatcher.py` | */30 min | live | OK |
| launchd | `com.zeus.forecast-live` | daemon | exit=1 (F87 carry-forward) | **HOT** |
| launchd | `com.zeus.data-ingest` | daemon | exit=0 | OK |
| launchd | `com.zeus.heartbeat-sensor` | daemon | exit=0 | OK |
| launchd | `com.zeus.live-trading` | daemon | running | OK |
| launchd | `com.zeus.riskguard-live` | daemon | running | OK |
| jobs.json | `zeus-daily-review` | daily 00:30 CT | 2026-05-17 ok | OK |
| jobs.json | `city-settlement-audit` | Mon 09:00 CT | 2026-05-11 ok; next 5/18 | OK |

### 4.2 Missing / dormant Karachi-supporting jobs (impact analysis)

| Job | Layer | Status | Why it matters for Karachi | Impact-if-missing |
|---|---|---|---|---|
| `zeus-antibody-scan` | jobs.json | **DISABLED since 2026-04-14** | Detects regression in zeus subsystems (incl. Karachi pipeline) | Karachi regressions go undetected until manual audit |
| `zeus-daily-audit` | jobs.json | **DISABLED since 2026-04-15** | Daily venue+settlement integrity audit | Karachi settlement drift undetected for ≥24h |
| `zeus-weekly-review` | jobs.json | DISABLED since 2026-04-13 | Weekly Karachi P&L review | Operator must run manually |
| (no entry exists) | — | — | No job specifically refreshes Karachi WU/HKO data MORE than once/day | If 10:00 UTC fetch fails, no retry until next day → Karachi 5/17 settlement could rely on stale data |
| `finance-subagent-scanner*` | jobs.json | **enabled but FAILING** | Not Karachi-direct, but indicates jobs.json model-config drift that may affect any zeus job runtime |
| `memory-observer` | jobs.json | **enabled but FAILING** | Observability infra; if memory pipeline broken, anomaly-detection on Karachi events is degraded |

**Net Karachi-criticality of Run #14 F90:** The original F90 ("40 jobs un-scheduled") overstates. The TRUE Karachi-impacting gaps are (a) `zeus-antibody-scan`+`zeus-daily-audit` dormant since April with no crontab replacement (F95), and (b) `oracle_snapshot_listener.py` is a daily-single-point-of-failure (F93).

---

## 5. Top-5 critical "restore" / fix actions

⚠️ **Constraint reminder**: READ-ONLY production. None of the below were executed. These are operator-pasteable.

### Top-1 (SEV-1) — Fix `payload.model` reject blocking 3 enabled jobs

Not a single restore line; requires editing jobs.json `payload.model` for IDs of `memory-observer`, `finance-subagent-scanner`, `finance-subagent-scanner-offhours` to a model accepted by `agents.defaults.model` in `openclaw.json`. Steps:

```bash
# 1) identify the rejected model id
jq -r '.jobs[] | select(.enabled==true) | {name, model: .payload.model}' /Users/leofitz/.openclaw/cron/jobs.json | grep -B1 'openai-codex/gpt-5.4-mini'

# 2) check the agents.defaults.model whitelist
jq -r '.agents.main.models, .agents.defaults' /Users/leofitz/.openclaw/openclaw.json | head -40

# 3) update jobs.json payload.model (operator decision: which model id is current?). Backup first:
cp /Users/leofitz/.openclaw/cron/jobs.json /Users/leofitz/.openclaw/cron/jobs.json.bak-pre-f90a-$(date +%s)

# 4) targeted patch (operator-driven; left intentionally manual since model id selection requires policy decision)
```

### Top-2 (SEV-2) — Fix `memory-reflector` / `memory-dream-cycle` timeouts

```bash
# inspect last 5 errors
tail -50 /Users/leofitz/.openclaw/cron/runs/$(jq -r '.jobs[] | select(.name=="memory-reflector").id' /Users/leofitz/.openclaw/cron/jobs.json).jsonl | jq -r 'select(.status=="error") | .error' | head -5

# adjust payload.timeoutMs or break dream-cycle into smaller chunks (jobs.json patch)
```

### Top-3 (SEV-3, Karachi-defensive) — Re-enable `zeus-antibody-scan` + `zeus-daily-audit`

These are jobs.json toggle flips (NOT crontab); operator-pasteable JSON patches:

```bash
cp /Users/leofitz/.openclaw/cron/jobs.json /Users/leofitz/.openclaw/cron/jobs.json.bak-pre-f95-$(date +%s)

python3 -c "
import json, pathlib
p = pathlib.Path('/Users/leofitz/.openclaw/cron/jobs.json')
d = json.loads(p.read_text())
for j in d['jobs']:
    if j['name'] in ('zeus-antibody-scan', 'zeus-daily-audit'):
        j['enabled'] = True
        print(f'Enabled: {j[\"name\"]} ({j[\"schedule\"].get(\"expr\")})')
p.write_text(json.dumps(d, indent=2))
"
# Reload openclaw-node so it re-reads jobs.json
launchctl kickstart -k gui/$UID/ai.openclaw.node
```

### Top-4 (SEV-3, Karachi-direct) — Add a 2nd intra-day oracle snapshot

Single 10:00 UTC fetch is fragile. Add a 16:00 UTC retry to crontab so if the morning fetch fails, the evening fetch still captures Karachi settlement-window data:

```bash
(crontab -l; echo "0 16 * * * cd /Users/leofitz/.openclaw/workspace-venus/zeus && WU_API_KEY=e1f10a1e78da46f5b10a1e78da96f525 .venv/bin/python scripts/oracle_snapshot_listener.py --retry-mode >> /Users/leofitz/.openclaw/logs/oracle-snapshot-retry.log 2>&1") | crontab -
```

⚠️ Note: WU_API_KEY is currently in plaintext in crontab — separate hygiene issue (recommend keychain_resolver shim).

### Top-5 (SEV-2, observability) — Wire jobs-status alert into Discord

`jobs-state.json` is structurally empty (F94). Run files exist but no one consumes them. Suggested addition to crontab:

```bash
(crontab -l; echo "*/30 * * * * /opt/homebrew/bin/python3 /Users/leofitz/.openclaw/workspace/ops/scripts/cron_jobs_tracker.py --alert-on-failure --discord-channel-id 1479709490629578752 >> /Users/leofitz/.openclaw/logs/cron-jobs-tracker.log 2>&1") | crontab -
```

(`cron_jobs_tracker.py` already runs hourly — needs `--alert-on-failure` flag added; existing tracker may already support this; verify before pasting.)

---

## 6. Antibody — CI gate sketch (`tools/ops/cron_reconcile.py`)

**Goal**: prevent the 3-layer scheduler drift that caused Run #14 F90's misreading.

### 6.1 Skeleton

```python
#!/usr/bin/env python3
"""tools/ops/cron_reconcile.py — unified scheduler manifest + drift check.

Enumerates all 3 OpenClaw scheduling layers and produces a single manifest:
  1. cron/jobs.json (openclaw-node, agent-turn jobs)
  2. crontab -l (pure-Python jobs)
  3. launchctl list + ~/Library/LaunchAgents/*.plist (long-running daemons)

Outputs:
  - Markdown manifest table → stdout (or --output FILE)
  - JSON manifest → --json FILE
  - Drift diff vs checked-in expected → exit code 1 if changed

Intended CI use: `tools/ops/cron_reconcile.py --check ops/cron_manifest.expected.json`
"""
import json, subprocess, plistlib, pathlib, sys

def from_jobs_json():
    d = json.loads(pathlib.Path('cron/jobs.json').read_text())
    return [
        {'layer':'jobs.json', 'name':j['name'], 'enabled':j.get('enabled',False),
         'schedule':j.get('schedule',{}).get('expr') or j.get('schedule',{}).get('kind'),
         'tz':j.get('schedule',{}).get('tz','-')}
        for j in d['jobs']
    ]

def from_crontab():
    out = subprocess.check_output(['crontab','-l'], text=True)
    rows = []
    for ln in out.splitlines():
        s = ln.strip()
        if not s or s.startswith('#'): continue
        parts = s.split(None, 5)
        if len(parts) >= 6:
            rows.append({'layer':'crontab', 'name':parts[5][:80],
                         'enabled':True, 'schedule':' '.join(parts[:5]), 'tz':'system'})
    return rows

def from_launchd():
    out = subprocess.check_output(['launchctl','list'], text=True)
    rows = []
    for ln in out.splitlines()[1:]:
        f = ln.split('\t')
        if len(f) < 3: continue
        pid, status, label = f
        if 'openclaw' in label or 'zeus' in label or '9router' in label:
            rows.append({'layer':'launchd', 'name':label,
                         'enabled': pid != '-', 'schedule':'daemon', 'tz':'-'})
    return rows

if __name__ == '__main__':
    all_rows = from_jobs_json() + from_crontab() + from_launchd()
    print(f"# Cron manifest ({len(all_rows)} rows)")
    print("| Layer | Name | Enabled | Schedule | TZ |")
    print("|---|---|---|---|---|")
    for r in all_rows:
        print(f"| {r['layer']} | {r['name']} | {r['enabled']} | `{r['schedule']}` | {r['tz']} |")
    # --check: diff against checked-in expected.json, exit 1 on drift
```

### 6.2 CI wiring

Add to `.github/workflows/ops-checks.yml`:

```yaml
  cron-reconcile:
    runs-on: macos-14
    steps:
      - uses: actions/checkout@v4
      - name: Cron manifest drift check
        run: python3 tools/ops/cron_reconcile.py --check ops/cron_manifest.expected.json
```

(Caveat: GitHub-hosted runner can't see operator's actual `crontab -l` / `launchctl list`. The CI gate is for jobs.json alone; the launchd+crontab snapshot is a **local pre-commit hook** that operator runs before pushing scheduler changes.)

### 6.3 Local pre-commit (operator-side)

```bash
# .git/hooks/pre-commit (or husky)
if git diff --cached --name-only | grep -qE 'cron/jobs.json|LaunchAgents/'; then
    python3 tools/ops/cron_reconcile.py --json > ops/cron_manifest.expected.json
    git add ops/cron_manifest.expected.json
fi
```

### 6.4 Runtime drift alarm

Beyond CI: extend `cron_jobs_tracker.py` (already running hourly in crontab) to:

1. Read `cron/runs/<jobid>.jsonl` last line for every enabled jobs.json job.
2. If `lastStatus == "error"` for ≥3 consecutive runs → post Discord alert via existing total-recall observer channel.
3. If `lastRunAt` > 2× expected cadence for any enabled job → alert.

This is the antibody that would have caught F90a (3 jobs failing every tick) ~30 days ago instead of waiting for Run #15 manual audit.

---

## 7. Findings appendix (for `FINDINGS_REFERENCE_v2.md`)

- **F90 (REFRAMED, Run #15)**: jobs.json IS executed by `ai.openclaw.node`; "40 jobs un-scheduled" framing was wrong. True severity downgraded SEV-1 → SEV-3 (architectural source-of-truth ambiguity remains). Replaced by F90a/F90b/F90c sub-findings:
  - **F90a SEV-1 NEW** — 3 enabled jobs failing every tick: `memory-observer`, `finance-subagent-scanner`, `finance-subagent-scanner-offhours`. Root cause: `payload.model 'openai-codex/gpt-5.4-mini'` not in `agents.defaults.model`. Operator action required: model id reconciliation.
  - **F90b SEV-2 NEW** — `memory-reflector` + `memory-dream-cycle` timing out on most invocations. Memory pipeline integrity at risk.
  - **F90c SEV-3 NEW** — No `cron_reconcile` tool across 3 scheduler layers. Antibody sketch in §6.
- **F93 SEV-3 NEW** — No job in any layer specifically fetches Karachi forecast/oracle data more than once/day. Single 10:00 UTC `oracle_snapshot_listener.py` tick is single-point-of-failure for Karachi WU data feeding settlements. Top-4 mitigation above.
- **F94 SEV-3 NEW** — `cron/jobs-state.json` is structurally empty (all 42 entries `{}`). Real per-job state lives in `cron/runs/<jobid>.jsonl`. Run #14 likely inspected wrong artifact when concluding "nothing runs."
- **F95 SEV-2 NEW** — `zeus-antibody-scan` (regression detector) + `zeus-daily-audit` (integrity audit) DISABLED in jobs.json since 2026-04-14/15 with no crontab/launchd replacement. Karachi regression coverage gap. Top-3 fix above.

---

## 8. Methodology + caveats

- Read-only. No crontab/launchctl mutations performed.
- Evidence: `cron/jobs.json` parse + `cron/jobs-state.json` parse + `cron/runs/*.jsonl` mtime + last-line inspection + `crontab -l` (71 lines) + `launchctl list` filtered.
- Pylance `pylanceRunCodeSnippet` used for stable Python execution (terminal had cross-session contamination from a sibling session running zeus inspections; switched to pylance MCP to avoid output mingling — antibody from session memory).
- Run #14 F90's "82KB jobs.json, 2-line crontab" was reproducible only as a **misread** of the file inventory; current crontab is 71 lines / 24 active commands. Original auditor may have grepped `'^[*\d]'` and gotten a small sample, or sampled before recent crontab additions.
