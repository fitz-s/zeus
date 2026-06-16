# MASTER timing-semantics fix plan — the consolidated deliverable (2026-06-16)

```
Created: 2026-06-16
Authority basis: operator mandate — consolidate the whole session's audit; every defect, a correct/complete/
evidence-based fix, AND a prevention antibody so the class never recurs (彻底end不再犯). Root cause (operator-named):
timestamps written by GUESSING (瞎猜) instead of from a justified basis. SPINE of this plan = no timestamp without a reason.
Sources: this session's evidence docs (index in §5). Verified against CURRENT code + live DBs (read-only).
```

## §0 — Honest bottom line (de-inflated; this is the truth, not an alarm)

The engine mostly processes trades correctly TODAY. The timing defects sort into:

- **The money path is touched by SEVERAL guessed timestamps, NOT one class** (corrected by the provenance ledger —
  see `timestamp_provenance_ledger_2026-06-16.md`). The funnel traced fetch→evaluate→place→fill and found only C1;
  the **basis-lens** found money-path guesses in the **grading / exit / expiry / q-authority** paths the funnel never
  traversed. **79 of 115 timestamp sites carry a fabricated basis** (16 GUESS + 32 SYNTHETIC_NOW + 31 NAIVE). The
  money-path guesses (each needs the same verify-then-fix as C1):
  - **`harvester.py:1440` `settled_at = now()`** — stamps the cron clock as the settlement event time, feeds
    `dispatch_era_basis()` → **grades every position's P&L against a guessed time** while `obs_row['observation_time']`
    sits unused. Settlement = the only truth, graded on a guess. **(new top-tier; arguably worse than C1.)**
  - **`fill_tracker.py:1089` `entered_at = now()`** + **`monitor_refresh.py:1187` `48.0h` magic fallback** →
    `hours_since_open` → `compute_alpha` → **biases every live exit.**
  - **`expires_at = +3h/+24h` magic** (`ecmwf_open_data.py:936`, `entry_readiness_writer.py:186`) → prematurely
    expires valid fusion triggers → **suppresses real trades.**
  - **naive `recorded_at` on `forecast_posteriors` (q-authority, `v2_schema.py:382`) and `readiness/topology`
    (LIVE_ELIGIBLE gate, `db.py:2089/2132`)** → corrupts the comparison that sizes trades / gates tradeability on the Chicago host.
- **C1 AVAIL-CLOCK itself is currently MASKED** (q-divergence ~0 today: ECMWF fetch lag ≥497min > the 485min gate),
  so the C1 defect is the **missing guard** + the corrupted availability/lineage clock — latent-unsafe, not wrong-now.
  The ledger also shows the C1 "proof-of-possession" basis (`captured_at`/`fetch_time`) is ITSELF synthetic (the whole
  ingest collection plane collapses to one `computed_at=now()`), so the C1 fix must capture the **distinct real
  collection events** (pre-download / file-write-complete / post-commit), not just re-point `available_at`.
- **Remaining C2/C3/C4 sites that are genuinely telemetry-only** (venue_timestamp, latency, the audit-lineage naive
  columns) still mis-route 0 market events — but the line between telemetry and money-path is per-SITE (basis-lens),
  not per-CLASS (funnel-lens). The execution order (§2) is updated to add the money-path guesses above near C1.
- **Four things are ALREADY SHIPPED — do NOT re-fix** (see §4): 30s→180s window, DAY0 venue-close horizon,
  SOURCE_TRUTH over-gate, and `readiness_state.expires_at` (now 477/477 populated).
- **The real prize is the prevention layer (§3)** — one shared antibody set that ends the guessing structurally.

## §1 — The spine: every timestamp must carry a justified BASIS (no guessing)

Root cause of all six classes: a correct-looking guess is the lowest-effort thing to write and is invisible until a
DB audit months later — `available_at = cycle_time`, `venue_timestamp = now()`, `posted_at = filled_at = now`,
`latency = 0`, `CURRENT_TIMESTAMP` (naive), `30s`/`14h`/`expires=computed+24h` magic numbers. The code already has
`BasisKind` (`time_semantics.py:87`) — it admits some values are `GUESS`. The fix standard, every replacement:
**a real source OR an explicit correct derivation, stated; if the basis is genuinely unknown → honest NULL, never a guess.**
Example done right: `available_at = min(captured_at, cycle+release_lag)` — captured_at = provable possession;
cycle+release_lag = earliest possible dissemination; `min` = honest lower bound. *That has a reason.*

## §2 — Execution order (worktree + verifier; q-changes shadow-staged)

| # | class | what (exact file:line) | basis / why | staging |
|---|---|---|---|---|
| 1 | C1 | `evaluator.py:6626` → `available_at = fetch_time`; `ecmwf_open_data.py:912/1460/1522/1707` → `source_available_at = min(computed_at, source_release_time)`; `forecast_snapshot_ready.py:368/:250-254` → prefer `fetch_time`/`captured_at` over cycle placeholder | proof-of-possession (mirrors shipped `bayes_precision_fusion_download.py:908-909`). **ZERO q-change** | worktree+verifier, no shadow. **START HERE** |
| 2 | C1 | Fix-5 pre-fusion availability gate in `src/data/bayes_precision_fusion_capture.py` (gain `decision_utc`, exclude any instrument whose `source_available_at > decision_utc`, fail-open on NULL). NB real modules are `src/forecast/bayes_precision_fusion.py` + `src/data/bayes_precision_fusion_capture.py` | the missing guard; expected excluded-count ~0 today (belt-and-suspenders vs a future faster source) | **shadow-q-compare; must prove q-delta null before promote**. blocks on #1 |
| 3 | C6 | the ONE money-path `date.today()`: `shoulder_strategy_vnext.py:118/:134` → `datetime.now(timezone.utc).date()` | host=Chicago → fallback mis-tags non-US family near UTC-midnight | worktree+verifier; parallel with #1 |
| 4 | C4 | `executor.py:3063/4146/4171` venue_timestamp→None on REST ACK (honest absence); `polymarket_user_channel.py:848` split `observed_at`=last_update vs `venue_timestamp`=matchtime; `edli_position_bridge.py:978` `posted_at`=`cmd_created_at` (→ latency honest-NULL not synthetic-0); declare `latency_ms/submit_time/order_intent_time` write-NULL w/ schema comment | telemetry truth (0 market events); honest-NULL not synthetic | worktree+verifier (probe cmd_created_at first) |
| 5 | C2 | REMOVE path: `evidence_report.py:203-223` count `decision_certificates` for n_decisions; remove dead `cycle_runtime.py:6084-6124` write block. C2b: run `scripts/persist_day0_horizon_identity_fit.py` (1 cmd) to unblock Day0 nowcast; drop 0-row `day0_metric_fact` | no-shadow law (wire-or-remove); n_decisions telemetry-only | worktree+verifier |
| 6 | C3 | run `scripts/migrations/normalize_observation_instants_z_suffix.py` (498 HK Z→+00:00); add `db.py::utc_iso_now()`; flip schema `CURRENT_TIMESTAMP` defaults → ISO at recorded_at/ingested_at (`v2_schema.py:1183` + db.py list) | canonical ISO-UTC; new rows only (append-only history stays) | worktree+verifier; 498-row UPDATE on a copy first |
| 7 | C5 | prevention-ONLY (D1/D2 shipped): add per-warm-cycle `C5_CADENCE_COVERAGE` WARNING in `main.py:~3659` when effective_sweep_period > selection window | metric-only, no behavior change | worktree+verifier |
| 8 | C6 | doc-rot last: `time_semantics.py:620-634` 30s→180.0 (source_ref to executable_market_snapshot.py:47); main.py comments; split `ZEUS_DISCOVERY_CLOB_TIMEOUT` (Gamma 10s vs CLOB 5s); remaining 6 non-money-path `date.today()` | fix UP to live 180s, never revert down | worktree+verifier |

### §2b — Money-path additions from the provenance ledger (verify-then-fix, slot alongside C1)
These were missed by the funnel (fetch→fill lens) and surfaced by the basis-lens. Each needs the same verify-then-fix
discipline as C1 — confirm the downstream consumption on the live path before editing, then fix the basis:

- **M1 (top-tier, settlement-grading): `harvester.py:1440` `settled_at`** → use `obs_row['observation_time']`
  (already in scope at :1448); absent → NULL + `authority='QUARANTINED'`; keep `recorded_at=now()` as a separate var.
  **Verify first:** confirm `dispatch_era_basis()` consumes `settled_at` for grading (not just display) on settled rows.
  Staging: worktree+verifier; if it changes any settled grade, shadow-compare the ERA basis selection.
- **M2 (exit-alpha): `fill_tracker.py:1089` `entered_at`** → venue matchtime from the WS fill payload; absent→NULL.
  **+ `monitor_refresh.py:1187,1643` `48.0h` fallback** → NULL/NaN so `compute_alpha` refuses (no magic age).
  Verify the `hours_since_open → compute_alpha → exit` chain; shadow-compare alpha before promote.
- **M3 (trade-suppression): `expires_at` magic** (`ecmwf_open_data.py:936` +24h, `entry_readiness_writer.py:186` +3h)
  → `source_cycle_time + source_cycle_max_age_hours()` (matches the correct `replacement_forecast_materializer.py:2181`).
  Verify the LIVE_ELIGIBLE gate; a longer correct window ADMITS more (un-suppresses) — confirm it doesn't admit stale.
- **M4 (q-authority + gate format): drop `DEFAULT CURRENT_TIMESTAMP`** on `forecast_posteriors.recorded_at`
  (`v2_schema.py:382`) and `readiness_state/market_topology_state.recorded_at` (`db.py:2089/2132`); caller supplies
  tz-aware ISO. This is C3 but on money-path gates — do these specific four with C1, not in the C3 telemetry batch.
- **M5 (C1 deepening): the ingest COLLECTION plane** — `fetch_started_at/fetch_finished_at/captured_at/imported_at`
  all = one `computed_at=now()`. Capture distinct real events (pre-download / file-write-complete / post-commit) so
  the C1 `available_at` fix rests on a REAL possession basis, not a synthetic one. `retrieved_at` (forecasts) move
  after the HTTP response; `run_init_dt` fallback → `source_cycle_time` not now().

## §3 — Prevention layer (the 不再犯 — machine-enforced; this is the real deliverable)

Attach points verified in `prevention_scaffolding_2026-06-15.md` (BasisKind exists; relations test exists but advisory;
`.claude/hooks/pre-commit` + 9 CI workflows; no canonical timestamp helper — 410 raw sites; `date.today()` ban unenforced).

- **ANTIBODY 1 — canonical possession-time helper** (kills C1): `src/contracts/availability_time.py::proof_of_possession_available_at(captured_at, nominal=None)` → `min(captured_at, nominal) if nominal else captured_at`. EVERY `available_at`/`source_available_at` writer MUST call it; raw cycle/release-gate assignment forbidden. One named producer whose name states the law.
- **ANTIBODY 2 — canonical ISO-UTC write helper** (kills C3): `db.py::utc_iso_now()` replaces every `DEFAULT CURRENT_TIMESTAMP` on a timing column. Makes the naive form impossible to introduce.
- **ANTIBODY 3 — fail-LOUD lane-liveness counters** (kills C2): replace every `except Exception: summary['degraded']=True` on a decision/timing write path with a paired named `_increment_summary_counter(summary,'<writer>_failed')` + a per-cycle `decision_lane_writes` map in the structured log. A dead lane becomes visible on the FIRST cycle, not after weeks.
- **ANTIBODY 4 — cadence-coverage liveness assertion** (kills C5): the `C5_CADENCE_COVERAGE` WARNING + a CI test driving N>30 families / processed=5 asserting it fires. The structural ratio becomes self-announcing.
- **ANTIBODY 5 — four CI bans, blocking merge** (the structural floor; this is the guarantee):
  - (a) `test_availability_time_law.py` — every `available_at` write calls the helper or carries `# AVAIL-POSSESSION-EXEMPTED: <reason>`; + runtime assert `available_at <= received_at` in the FSR builder.
  - (b) `test_timestamp_format_invariant.py` — no `CREATE TABLE … DEFAULT CURRENT_TIMESTAMP` on a timing column; a live INSERT must yield ISO T-separator, never naive space.
  - (c) `test_timing_column_liveness.py` — every declared timing column populated >50% of recent rows OR registered in `HONEST_NULL_COLUMNS` with justification; AST-ban `posted_at=filled_at`, `latency_ms=0` default, `venue_timestamp=ack_time`.
  - (d) `test_no_date_today_ban.py` — AST-ban `date.today()` in `src/**` (excluding offline calibration), finally enforcing the `epistemic_context.py:12` law that 10 sites violate.
  - **+ doc-rot closer:** any `*_freshness_window`/`*_timeout` constant must appear in `time_semantics.py` with a `source=` callable reading the live value, not a literal — a future comment/registry drift fails CI too.

Why this is the recurrence-killer: every class recurs because a wrong-looking-right pattern is the lowest-effort write and is invisible for months. Antibodies 1–4 supply the canonical correct producers + runtime visibility; the CI bans shift every violation from "found in a multi-hundred-million-token audit" to "blocked at PR time on the author's own branch."

## §4 — Refuted / corrected ledger (do NOT re-chase — the session's self-corrections)

1. Fusion module path: `src/data/bayes_precision_fusion.py` does NOT exist. Real: `src/forecast/bayes_precision_fusion.py` (bayes_fuse, ModelInstrument) + `src/data/bayes_precision_fusion_capture.py`. Target those for Fix-5.
2. `readiness_state.expires_at` is NOT all-NULL — 477/477 LIVE_ELIGIBLE carry it (honest-NULL for BLOCKED by design). ALREADY_SHIPPED. (The sweep's "never expires" was stale.)
3. ALREADY-SHIPPED, do not re-fix: 30s→180s window, DAY0 venue-close horizon, SOURCE_TRUTH over-gate, expires_at.
4. C1 q-divergence is ~0 TODAY (fetch lag ≥497min > 485min gate). Escalate as "guard absent → faster source WOULD bias", NOT "wrong q now." Source binding ~99% correct.
5. `decision_events=0` and `edli_no_submit_receipts` frozen are NOT regressions: `decision_certificates`=1.27M is the active path; `no_trade_regret_events`=7,764 post-freeze proves the reactor is processing; decision_events empty by OMISSION (venue_ack ~1/28d). Q2 lifecycle ALIGNED. Action = wire-or-remove, not debug-the-submit-path.
6. "latency always 0" is THREE facts: venue_timestamp=wallclock on REST ACK (WRONG→NULL); execution_fact latency=0 for same-second command_recovery fills (LEGITIMATE); edli_position_bridge synthetic posted=filled (~28 rows, FIX). Not one bug.
7. The 30s number is doc-rot — fix UP to the live 180s (registry/comments), never revert the live code DOWN to 30s.
8. `decision_events` ValueError on empty `run_complete_time` (ecmwf_open_data.py:882 forces "" on ~45% PARTIAL) is LATENT — never executed (gated behind the never-firing venue_ack). Moot under the REMOVE path; only a concern if C2 chooses WIRE.
9. The regime-B near-arbitrage "edge" thesis was OUT OF SCOPE and rejected (assumes the market is dumb); the residual price on "eliminated" bins is the market correctly pricing source/settlement/revision risk. Do not revive.

## §5 — Evidence index (this session)
`FINDINGS_2026-06-15.md` · `instrument_intent_verdicts_2026-06-15.md` · `deepdive_4q_verdicts_2026-06-15.md` ·
`gating_unknowns_2026-06-15.md` · `unknown_unknown_sweep_2026-06-15.md` · `code_map_extract_2026-06-15.md` ·
`FIX_PLAN_2026-06-15.md` · `FIX_PRIORITY_funnel_2026-06-15.md` · `prevention_scaffolding_2026-06-15.md` ·
`recon_timing_substrate_2026-06-15.md` · (pending) `timestamp_provenance_ledger` — the exhaustive per-write basis audit.
```
