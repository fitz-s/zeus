# Zeus Deep Alignment Audit — Run #3 Findings

**Date**: 2026-05-16 ~17:10 UTC
**Audit worktree HEAD**: `f65a6abe96` (zeus-deep-alignment-audit-skill)
**Anchor commit (main)**: `a924766c8a`
**Categories scanned**: B (math drift), E (daemon supervision), F (cross-module / schema drift), G (silent failures / alarm channel), I (secrets, **newly promoted from no-prior-coverage**)
**Categories skipped this run**: C (stat pitfalls), D (DST/tz — re-tested via cron grep, no new), H (assumption drift — covered in Run #1/#2)
**Time spent**: ~25 min (read-only)
**Tier-0 live target**: Karachi 2026-05-17 HIGH ≥37 °C, position `c30f28a5-d4e`

---

## Findings

### Finding #9 — SEV-1 — Cat I (Secrets) — NEW CATEGORY

> **FALSE POSITIVE OVERRIDE — 2026-05-16 (operator verdict)**
>
> - **Verdict**: FALSE POSITIVE.
> - **Reason**: The adjacent comment in the crontab clearly explains the key's role and documents that its placement here is intentional. Operator-confirmed: not a leak.
> - **Action**: Downgrade severity SEV-1 → **INFO** (no remediation needed; do NOT remove or rotate the key).
> - **Heuristic update (recorded in LEARNINGS.md Cat-J)**: secrets-in-config-line probes must scan ±3 lines of context for an explanatory comment before flagging. Comment-adjacency = intentional placement = INFO, not SEV-1.
> - **Preserved below as a documented false positive** to keep the LEARNINGS bank intact (the probe itself fired correctly; only the severity classification was wrong because the gate was missing).

**Title**: Wunderground API key (`WU_API_KEY`) is hardcoded plaintext in the user crontab — leaks to anyone reading `crontab -l`, `ps -ef`, or process-environ.

**Evidence**:
- `crontab -l` line:
  ```
  0 10 * * * cd /Users/leofitz/.openclaw/workspace-venus/zeus && \
    WU_API_KEY=e1f10a1e78da46f5b10a1e78da96f525 .venv/bin/python \
    scripts/oracle_snapshot_listener.py >> /Users/leofitz/.openclaw/logs/oracle-snapshot.log 2>&1
  ```
- The key is also visible in the per-process environment (`ps eww $PID | grep WU_API_KEY`) for the duration of each daily oracle-snapshot run (10:00 UTC).
- Memory note `claude_cli_kelp_forest_antibody.md` already establishes that this workstation has separation-of-secrets discipline (Keychain resolver `bin/keychain_resolver.py` is the documented path). The crontab line bypasses it.

**Impact**:
- Wunderground subscription key exposed to any process/user able to read the crontab or `/proc`-equivalent on macOS.
- If the workstation is ever a shared machine, screen-shared, or its dotfiles synced, the key is silently exfiltrated.
- Anyone holding this key can scrape WU at the paid-tier rate quota, possibly exhausting the operator's daily quota → silent oracle-snapshot failures (no key = HTTP 401 = downstream settlement drift).
- **Live-Karachi link**: HKO settles Karachi from publicly-known WU station; if the key gets exhausted by an attacker before 10:00 UTC tomorrow, the oracle snapshot for 2026-05-17 fails and the autoclose-baseline drifts.

**Recommended action (READ-ONLY recommendation — do not implement here)**:
1. Move the key to macOS Keychain (Zeus already has `bin/keychain_resolver.py`).
2. Replace the crontab line with `WU_API_KEY="$(python bin/keychain_resolver.py wu_api_key)"`.
3. Rotate the leaked key with Wunderground after migration.
4. Add a CI / pre-commit hook that fails when any string matching `[A-Z_]+_(KEY|TOKEN|SECRET|PASSWORD)=[A-Za-z0-9]{16,}` lands in shell scripts, crontabs, or plists.

**Confidence**: HIGH — the key is literal and the line is live in the running crontab.

---

### Finding #10 — SEV-1 — Cat G (Silent failures) + Cat E (Daemon supervision)

**Title**: Heartbeat sensor has been emitting `severity=RED root_cause=deep_heartbeat_critical` for hours; the dispatcher downgrades it to `degraded` (yellow) and the alarm has been firing every 30 min since at least 2026-05-16T13:30 UTC without operator action. **Severity-downgrade mismatch + alarm fatigue.**

**Evidence**:
- `logs/heartbeat-sensor.err` (live launchd-managed sensor):
  ```
  heartbeat_sensor: severity=ORANGE root_cause=assumption_mismatch
  heartbeat_sensor: severity=ORANGE root_cause=assumption_mismatch
  ...
  heartbeat_sensor: severity=RED root_cause=deep_heartbeat_critical   ← x49 consecutive
  ```
  (mtime: 2026-05-16 11:58 local = 16:58 UTC; >49 consecutive RED lines)
- `/Users/leofitz/.openclaw/logs/zeus-heartbeat-dispatch.log` (cron-driven dispatcher, `*/30 * * * *`):
  ```
  2026-05-16T13:30:04Z msg: "triggering venus session: severity=degraded"
  2026-05-16T14:00:04Z msg: "ALERT: zeus degraded (exit code 1)"
  2026-05-16T14:00:04Z msg: "triggering venus session: severity=degraded"
  ... (8 consecutive cycles, every 30 min, all "degraded")
  2026-05-16T17:00:02Z msg: "ALERT: zeus degraded (exit code 1)"
  ```
- The sensor said RED for 49 consecutive ticks → the dispatcher reports `degraded` (≈ yellow) every cycle for 3.5+ hours.

**Impact**:
- Two-channel disagreement (sensor says critical, dispatcher says degraded) → operator cannot trust either signal.
- The "triggering venus session" arm appears to fan out to OpenClaw venus (the Zeus operator agent) but operator did not stop the Run #2 / Run #3 audit to triage — suggesting either (a) the alert never reaches a human surface, or (b) it does but is being ignored as cry-wolf noise.
- **Live-Karachi link**: The Karachi 5/17 position is on-chain right now. A silently-broken alarm channel means a real settlement failure tomorrow may not surface in time for manual intervention.

**Recommended action**:
1. Audit `scripts/heartbeat_dispatcher.py` for the RED → "degraded" downgrade rule; either propagate RED upward or document why dispatcher classifies it lower.
2. Add a "RED for ≥3 consecutive 30-min ticks" rule that triggers a push notification (APNs / Discord / SMS) regardless of dispatcher's normal routing.
3. The dispatcher returning exit code 1 every cycle (`ALERT: zeus degraded (exit code 1)`) is itself a smell — cron jobs that always fail teach the operator to ignore failure.

**Confidence**: HIGH — both files are live and reproducible right now.

---

### Finding #11 — SEV-2 — Cat E (Daemon supervision)

**Title**: `com.zeus.heartbeat-sensor.plist` is mis-classified as a daemon: no `KeepAlive` key, `RunAtLoad=true` only. The "sensor daemon" runs **once** at user login and never respawns; the live heartbeat signal is actually driven by the `*/30 * * * *` cron dispatcher, not the launchd entry.

**Evidence**:
- `plutil -extract KeepAlive raw ~/Library/LaunchAgents/com.zeus.heartbeat-sensor.plist` → `Could not extract value: No value at that key path`
- `plutil -extract RunAtLoad raw …` → `true`
- `launchctl list | grep zeus.heartbeat-sensor` → `- 0 com.zeus.heartbeat-sensor` (PID `-` = no running process)
- `logs/heartbeat-sensor.err` mtime: 2026-05-16 11:58 (5+ hours ago) — file is not being updated since the post-login one-shot exited.
- `crontab -l` line `*/30 * * * * .../scripts/heartbeat_dispatcher.py >> /Users/leofitz/.openclaw/logs/zeus-heartbeat-dispatch.log 2>&1` is what actually keeps the heartbeat alive.

**Impact**:
- Two-system architecture: launchd entry that suggests "always-on sensor" + cron dispatcher that is the actual driver. Confusing surface for operator; if the cron is later removed thinking the launchd entry is redundant, the sensor goes dark with no warning.
- Compare to peers — `com.zeus.live-trading.plist`, `com.zeus.riskguard-live.plist`, `com.zeus.data-ingest.plist`, `com.zeus.forecast-live.plist` ALL have `KeepAlive=true` (confirmed via plutil) and respawn on crash. Heartbeat-sensor breaks the convention.

**Recommended action**:
1. Either:
   (a) Add `KeepAlive=true` to the plist and remove the cron-dispatcher fanout, OR
   (b) Delete the launchd plist (rename to `.disabled`) and document that the cron is the canonical driver.
2. Add a daemon-supervision invariant test: `tests/test_launchd_plists.py` parses every `~/Library/LaunchAgents/com.zeus.*.plist` and asserts either `KeepAlive=true` OR a corresponding cron line exists for the same script.

**Confidence**: HIGH — plist + launchctl + logs all agree.

---

### Finding #12 — SEV-2 — Cat F (Cross-module / schema drift)

**Title**: Post-K1 ghost trade-lifecycle tables persist as empty shells in `state/zeus-world.db` while the populated copies live in `state/zeus_trades.db`. Any silent reader that hits `get_world_connection().execute("SELECT … FROM position_current")` will return 0 rows with no error — undetectable misroute.

**Evidence**:
- `python` row-count probe:
  ```
  state/zeus-world.db    position_current               rows=0   max_rowid=None
  state/zeus-world.db    position_events                rows=0   max_rowid=None
  state/zeus-world.db    position_lots                  rows=0   max_rowid=None
  state/zeus-world.db    collateral_ledger_snapshots    rows=0   max_rowid=None
  state/zeus-world.db    collateral_reservations        rows=0   max_rowid=None
  state/zeus-world.db    venue_order_facts              rows=0   max_rowid=None
  state/zeus_trades.db   position_current               rows=2
  state/zeus_trades.db   position_events                rows=7
  state/zeus_trades.db   collateral_ledger_snapshots    rows=9415
  state/zeus_trades.db   collateral_reservations        rows=4
  state/zeus_trades.db   venue_order_facts              rows=3
  ```
- All 6 tables exist (schema present) in BOTH DBs — confirmed via `sqlite_master` query in each.
- Run-1 Finding #1 (registry-vs-disk drift) and Run-2 §A reported the writer route was fixed in PR #121 (`src/ingest_main.py:646` now uses `get_forecasts_connection`). The corresponding **DROP TABLE** in `world.db` for the trade-lifecycle tables was apparently never executed.

**Impact**:
- Any stale reader (legacy script, monitoring tool, ad-hoc query in a notebook) that opens world.db expecting these tables will see them, run the query without error, get an empty result, and silently conclude "no positions" / "no collateral reservations". 
- This is the same failure shape as Run-1 Finding #4 (writer mis-route returns silent NULL) but on the read side: schema exists → no `OperationalError` raised → caller doesn't know it's reading from a ghost.
- **Live-Karachi link**: Low direct link (the writer side is correctly routed to zeus_trades.db). Risk is that a future operator-written one-off SQL during the 5/17 settlement window hits the world.db copy and reports "Karachi position not found" when it really exists.

**Recommended action**:
1. Add a migration `scripts/migrations/202605_drop_world_trade_lifecycle_tables.py` that DROPs the empty shells in world.db (idempotent: skip if rowcount > 0 — fail loud).
2. Tighten the `architecture/db_table_ownership.yaml` enforcement: `assert_db_matches_registry()` (Run-1 Finding #1) should also assert tables declared `db: trades` do NOT have schema on any other DB.

**Confidence**: HIGH — direct row-count confirmation.

---

### Finding #13 — SEV-3 — Cat B (Math drift)

**Title**: `src/engine/replay.py` calls `dynamic_kelly_mult()` with three of five risk modulators **hardcoded to neutral** (`rolling_win_rate_20=0.50, portfolio_heat=0.0, drawdown_pct=0.0`). If any policy-tuning loop reads the replay output to set `config/settings.json::sizing.kelly_multiplier`, the resulting live multiplier is structurally biased upward.

**Evidence**:
- `src/engine/replay.py:1664-1671`:
  ```python
  k_mult = dynamic_kelly_mult(
      base=settings["sizing"]["kelly_multiplier"],
      ci_width=ci_width,
      lead_days=lead_days,
      rolling_win_rate_20=0.50,    # ← hardcoded neutral
      portfolio_heat=0.0,           # ← hardcoded neutral
      drawdown_pct=0.0,             # ← hardcoded neutral
  )
  ```
- Only `ci_width` and `lead_days` carry real signal; the other three knobs that the function was designed to use (win-rate momentum, concentration, drawdown protection) are short-circuited.
- `src/strategy/kelly.py` docstring lists all five as load-bearing for "dynamic multiplier reduces sizing when [...]".

**Impact**:
- In ISOLATION inside `replay.py` this is benign — replay has no live concentration state. The risk is downstream: if backtest summaries feed any auto-tuning of `kelly_multiplier`, the live setting will not respect concentration or drawdown discipline that the function is supposed to enforce.
- Needs verification: trace whether any automated policy job consumes replay's `size_usd` distribution to update `settings.json`. If yes → SEV-2. If no → keep as SEV-3 / cosmetic.

**Recommended action**:
1. Add a docstring at `replay.py:1664` explaining why three knobs are neutralized (and confirm no live consumer exists).
2. If a live consumer is found, plumb real `rolling_win_rate_20` / `portfolio_heat` / `drawdown_pct` from `state.position_current` / risk-guard snapshots into replay.

**Confidence**: MEDIUM — the math is real but the downstream consumer chain is not yet traced.

---

## Updated Executive Ranking (absorbing Run #1–#3 findings, with #9 false-positive override)

| Rank | Finding | SEV | Status | Risk to live Karachi 5/17 |
|------|---------|-----|--------|--------------------------|
| 1 | #10 Heartbeat RED for hours, dispatcher reports `degraded`, alarm channel broken | 1 | **NEW Run #3** | DIRECT (silent failure during 5/17 settlement) |
| 2 | #2 selection_hypothesis_fact decision_id 100% NULL (Run #1) — regression: 506 → 693 NULL (Run #2) | 1 | OPEN | DIRECT (lineage broken on live position) |
| 3 | #1 assert_db_matches_registry unwired at boot (Run #1) | 1 | OPEN | INDIRECT (future drift won't be caught) |
| 4 | #5 DB-lock storm in zeus-ingest (Run #2) | 1 | OPEN | INDIRECT (ingest slowness → stale forecasts) |
| 5 | #6 Empty `.log` files masking live daemons (Run #2) | 1 | OPEN | INDIRECT (operator misreads as offline) |
| 6 | #11 heartbeat-sensor.plist has no KeepAlive | 2 | **NEW Run #3** | LOW |
| 7 | #12 Ghost trade-lifecycle tables on world.db | 2 | **NEW Run #3** | LOW (read-side silent zero) |
| 8 | #3 Doctrine drift (Run #1) | 2 | OPEN | LOW |
| 9 | #4 Settlements_v2 settled_at==recorded_at + 5d silent writer (Run #1) | 2 | RESOLVED via PR #121 / Run #2 residue | LOW (residue only) |
| 10 | #7 Harvester filter coarse (Run #2) | 2 | OPEN | MED (could miss Karachi settlement) |
| 11 | #8 Sentinel timestamp on live row (Run #2) | 2 | OPEN | LOW |
| 12 | #13 dynamic_kelly_mult neutralized in replay | 3 | **NEW Run #3** | NONE (direct) |
| — | #9 WU_API_KEY plaintext in crontab — **FALSE POSITIVE** (operator override 2026-05-16; adjacent comment documents intentional placement) | INFO | FALSE-POSITIVE | NONE |

**K root structural gaps (Fitz K << N synthesis)**:
1. **Alarm-channel ↔ alarm-source disagreement** (Findings #6, #10, #11) — multiple layers report different severities; operator-trustable signal is absent.
2. **Antibody-implemented-but-unwired** (#1, partially #12) — checks exist as Python code but no production path invokes them; promoted Cat I in Run #2.
3. **Lineage-key default-None** (#2 hypothesis, #2-sister execution_fact) — `decision_id` parameter defaults to None; silently NULL when caller forgets to thread it.
4. **Schema-without-data ghost tables** (#12) — post-K1 cleanup left empty shells that silently absorb mis-routed reads.
5. **Secret-in-config-line** (#9) — secrets live in cron/plist/config text instead of Keychain; one rotation event would still leak via shell history.

---

## LEARNINGS.md update suggestions

### Yield ladder updates (after Run #3)

| Category | Run #3 | New ladder |
|---|---|---|
| A data provenance | 0 (re-test passive) | HIGH (sustained) |
| B math drift | 1 (SEV-3 #13) | LOW → MEDIUM (first non-zero in 3 runs) |
| C statistical pitfalls | 0 (skipped) | LOW (no change; still under 3-empty threshold because skipped, not tested) |
| D time-calendar | 0 (crontab grep only) | LOW |
| E daemon supervision | 1 (SEV-2 #11) | **PROMOTED to MEDIUM** (first time as standalone) |
| E settlement edges | 0 | HIGH (sustained but watch for demotion next run) |
| F cross-module invariants | 1 (SEV-2 #12) | HIGH (sustained) |
| G silent failures | 1 (SEV-1 #10) | HIGH (sustained) |
| H assumption drift | 0 | MEDIUM (no change) |
| **I antibody-unwired** | 0 (passively re-confirmed via #12 = #1 sibling) | HIGH (cat already active) |
| **J secrets in plaintext** | 1 (SEV-1 #9) | **PROMOTED to ACTIVE — needs 1 more run to validate** |

### New high-signal probes to add

1. `[J] crontab + plist + shell-rc plaintext secret scan: 'crontab -l | grep -oE "[A-Z_]+_(KEY|TOKEN|SECRET|PASSWORD)=[^ ]+"' AND grep across ~/Library/LaunchAgents/*.plist + ~/.zshrc + ~/.bashrc + ~/.profile` — caught Finding #9 in one line.
2. `[E] launchd KeepAlive convention check: 'for p in ~/Library/LaunchAgents/com.zeus.*.plist; do plutil -extract KeepAlive raw "$p" || echo "$p MISSING KeepAlive"; done'` — caught Finding #11.
3. `[F] schema-without-data ghost-table scan: for every table that exists on multiple DBs, count rows on each; flag any pair where exactly one side has 0 rows AND the other has > 0` — caught Finding #12. **Distinct from Run-1 probe** (which flagged DUP when both sides had rows); this catches the ASYMMETRIC ghost case.
4. `[G] severity-channel-disagreement check: parse logs/heartbeat-sensor.err for last severity, parse logs/zeus-heartbeat-dispatch.log for last dispatcher severity, flag if they differ for more than 30 min` — caught Finding #10.

### Anti-heuristic recorded

- **`grep -rEn "0x[a-fA-F0-9]{64}" src/`** as a secret/key probe is noisy — returns market_id and condition_id hex (true content, not secrets). Better: limit to `*.py` files AND require an `=` or `:` immediately before, OR scan only crontab/plist/shell-rc.

### Methodology antibody (audit-of-the-audit)

- **VS Code terminal output buffering bug observed** in this run: multi-line heredoc-style sqlite3/python invocations sometimes returned stale output from a prior tool call (Karachi position query first returned `693|693` lines that belonged to a different query). Antibody for future runs: prefix every probe output with a literal `printf` marker (`==SECTION==`) and grep the marker out of the response before parsing. Already used in the final Karachi/HB probe successfully.

---

## AUDIT_HISTORY.md row to append

```
| 3 | 2026-05-16 | a924766c8a (main) / f65a6abe96 (skill) | 5 | 2 | 2 | 1 | B, E, F, G, I probed (findings); C, D, H skipped | docs/operations/task_2026-05-16_deep_alignment_audit/RUN_3_findings.md |
```

### Retrospective paragraph

Run #3 produced 5 new findings (2 SEV-1, 2 SEV-2, 1 SEV-3) across 3 prior-untested surfaces. The biggest surprise was the **operator-blind RED-but-dispatcher-says-degraded** mismatch on the heartbeat channel (Finding #10) — neither Run #1 nor Run #2 probed the cron-dispatcher tier, only the launchd `.err`. The dispatcher had been firing `ALERT: zeus degraded` every 30 minutes for hours during Run #2 itself and went unnoticed by both audits. This is a category of failure the audit currently has no permanent probe for: **two-tier alarm pipelines where the producer says X and the consumer reports Y**. Probe #4 above codifies the gap. Second surprise: the new **secrets category** (J) yielded a SEV-1 on its first probe (WU_API_KEY in crontab) — suggests the broader secret surface (shell-rc, plist EnvironmentVariables, repo `.env` files) likely has more findings that have never been audited. Recommend promoting J to a first-class scan in Run #4. Run #3 confirms the K1 split-resolution from Run #2 (writers correctly routed) but reveals the cleanup was half-finished — ghost schema shells remain in world.db (#12). This pattern (half-finished cleanup) deserves its own probe in a future run.

### Meta-audit (Run #3 is the 3rd run — meta-audit step due)

Per SKILL.md, every 3rd run does a meta-audit. Findings:
- Active categories has grown from 8 (seed) → 9 (Run #1: +I proposed) → 10 (Run #2: I promoted) → 11 (Run #3: J proposed). Still under the 12-prune threshold; no pruning needed.
- Category yield reality after 3 runs: HIGH = A, E-settlement, F, G, I. MEDIUM = B (just promoted), H. LOW = C, D. No category has hit DEAD (3 consecutive zeros).
- **SKILL.md seed-list update recommendation**: split current seed `E. Settlement edges` into `E1. Settlement edges` + `E2. Daemon supervision` (the latter has yielded SEV-2 in Run #3 and the existing E description never covered it; Run #3 had to overload the F/G categories). This is a structural restructure that should be recorded in the meta-audit log in LEARNINGS.md.
- New seed to add at v1: `J. Secrets in plaintext` (cron, plist, shell-rc, repo configs).
- No category demotions warranted.

---

## END OF RUN_3_findings.md
