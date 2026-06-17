# Timing-semantics fix priority — intent vs implementation across the market-event funnel (2026-06-15)

```
Created: 2026-06-15
Source: workflow w02sch21e (fetch->evaluate->place->fill, intent-from-code vs actual-from-live-data, drops
classified by-design vs timing-bug, fixes ranked by market-events-wrongly-processed). Full: tasks/w02sch21e.output.
```

## Bottom line
The funnel mostly works. The three loudest historical timing bugs are ALREADY SHIPPED in current code
(DAY0 venue-close horizon dead-loop — starved ~277 families 06-14; SOURCE_TRUTH PARTIAL_BLOCKED over-gate —
fixed 06-11; 30s→180s selection window — executable_market_snapshot.py:47). READINESS_EXPIRED now fires 0× in
3 days (serve-freshest-available converts it to a warn). The recent STALE regret rows are stragglers from those
pre-fix incidents, not new drops.

**Exactly ONE live, high-volume, money-path-correctness timing defect remains. Everything else is telemetry.**

## Rank 1 — FIX-AVAIL-CLOCK (P1, money-path q-correctness) — THE fix
- **Defect:** `FORECAST_SNAPSHOT_READY.available_at` derives from `source_available_at` / `ensemble_snapshots`,
  a cycle-anchored placeholder **~8.35h before true dissemination** (`observed_at − available_at = +30,075s`,
  **98,719 events / 3d**).
- **Mechanism:** the readiness gate is forward-looking — `available_at > decision_utc → PARTIAL_BLOCKED`
  (`forecast_snapshot_ready.py:256-267`). An 8h-early `available_at` → gate **passes not-yet-disseminated
  providers** → they enter `bayes_precision_fusion` at full precision-weight (Sigma is residual covariance, **no
  arrival-time decay**) → **biases posterior q for every TRADE_SCORE decision.** Stale-as-fresh, corrupting q.
- **Same root as the sweep findings:** `opportunity_events` available-after-received (11%, 778k) and
  `decision_certificates` max_parent-fresher-than-child (24.9%, 315k) are facets of the same wrong availability clock.
- **Market events wrongly processed:** ~98,719 FSR events carry the inverted clock; the genuinely-fused-stale
  subset is bounded above by all 98,719 (exact count needs a replay — the true dissemination time was never recorded;
  that IS the defect).
- **Fix:** (1) source `available_at` from honest proof-of-possession — apply the SAME bound already used for
  raw_model_forecasts (`bayes_precision_fusion_download.py:908-909`: `min(captured_at, cycle+release_lag)`) to
  ensemble_snapshots / FSR. Conservative interim w/o schema change: in `forecast_snapshot_ready.py:250-255` prefer
  `snapshot.captured_at` over cycle-anchored `source_run.source_available_at`. (2) add an arrival-recency guard to
  `bayes_precision_fusion` that excludes/down-weights any instrument whose honest available_at is future vs decision_utc.
- **Staging (mandatory — this changes q for every trade):** behind a **shadow q-compare**; verify serve-freshest
  keeps the system from going dark when the gate correctly tightens. Worktree + verifier. Settlement-graded.

## Rank 2 — EXECUTABLE_QUOTE 30s-row carryover (P2) — NO code change
180s window is correct in code; ~97% of live snapshots (1.32M/1.36M, 48h) still carry the OLD 30s deadline. SELECTION
reads existing rows → substrate expires at 30s vs 561s cadence until old rows age out. Manifestation = transient
EXECUTABLE_SNAPSHOT_BLOCKED requeue churn (4,039/7d), self-resolving, ≈0 terminal drops. Fix: let 30s rows age out
(time-bounded, append-only — do NOT backfill); verify fresh_executable_city_count recovers.

## Rank 3 — FETCH cadence vs window throughput (P1, latent) — throughput not clock
561s avg capture cadence vs 180s window → 49% stale at any instant. But drops MASKED: 0 pre-close STALE rejections
(all 2,066 post-close); live staleness absorbed by requeue. Fix: do NOT widen window further; raise CLOB capture
coverage / prioritize the rotating cursor to live-open families so open markets recapture inside 180s. Verify via
topology-open × snapshot-recency join for the TRUE live-but-unswept count (73% topology gap is mostly by-design closed/future).

## Rank 4 — venue_timestamp = wallclock (P1 telemetry, 0 market events)
`executor.py:3063/4146` set venue_timestamp=ack_time=now (REST ack has no server ts); `polymarket_user_channel.py:858/1046`
alias venue_timestamp to WS delivery 'timestamp' not 'matchtime'. 100% of non-null = observed_at, ingest-lag identically 0.
Corrupts latency analytics only (61/64 orders, 89/101 trades); 0 money-path mis-routing. Fix: WS path prefer
`matchtime` as primary venue_timestamp; REST ack leave NULL (honest absence), source match-time from WS CONFIRMED via command linkage.

## Rank 5 — execution_fact latency=0 (P2 telemetry, 0 market events)
EDLI bridge sets posted_at=filled_at=now (`edli_position_bridge.py:978-979`) → latency_seconds=0.0 for ~28 bridge fills;
command_recovery 0 when cmd_created_at null (~15). Fix: recover posted_at from `venue_commands.created_at`; else posted_at=NULL
(honest → latency NULL not false 0.0).

## By-design (DO NOT TOUCH)
Gamma empty-backoff (future not-yet-listed, 300s, re-enters); venue-closed skip (past 12:00Z F1 close); timebox_unattempted
(retryable, not a drop); channel BEST_BID_ASK/BOOK events marked ignored after quote-cache ingestion.

## Biggest unknown
Exact count of cycles where real dissemination fell between the placeholder available_at and decision_utc (genuinely
fused stale-as-fresh) — needs a replay reconstructing real provider availability from release schedules, not a query.
