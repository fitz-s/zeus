# Promotion Pipeline Design — Operator-Approved "Both" Route

Created: 2026-05-22
Authority basis: 07_PHASE_6_EVIDENCE_LADDER.md + 05_PHASE_4_FDR_FAMILY_CANDIDATES.md + 04_PHASE_3_SHOULDER.md
Status: READ-ONLY architecture design. No code modified. Verified against live DBs @ 2026-05-22T12:09Z.

---

## Headline verdict (read this first)

The Phase-4 shadow candidates were *built* but their decision inputs were
**never plumbed into the historical record**. A faithful replay-rank track is
**infeasible for all 6 Phase-4 candidates** because the fields each `evaluate()`
consumes (uma_resolution_status, alert_source, fill_probability, neg_risk YES-ask
sum, regime correlation matrix, windowed spread/info-event proxy) do not exist at
decision-time granularity in any historical table.

The realistic "Both" route does **not** promote a `candidates/` strategy first.
It promotes a **shoulder/center vNext variant** first — `shoulder_sell` — because
that family rides the *forecast-edge* pipeline (`BinEdge`) that already produces
`opening_inertia`'s 1528 settled live trades, and that substrate
(`ensemble_snapshots_v2` + `historical_forecasts_v2` + `settlements_v2`) IS in the
historical record with deep history.

Phase-4 candidates are demoted to **Track-L-only** and gated on first
backfilling their input fields into `market_microstructure_snapshots` (currently
0 rows everywhere).

No schema bump is required. Phase-6 tables exist at world SV=26. The gap is
**producers, not DDL**.

---

## §0. Live data topology (K1 ghost-split — load-bearing)

The three DBs are split by the K1 migration (`src/state/db.py:4099`). Replay
producers and the live writer hit **different physical files**; conflating them
reproduces the F40/F41 silent-empty class.

| Table | WORLD (zeus-world.db, 39GB) | FCST (zeus-forecasts.db, 49GB) | TRADES (zeus_trades.db, 728MB ghost) |
|---|---|---|---|
| executable_market_snapshots | 0 | — | **4284** (2026-05-15→22) |
| market_price_history | 0 | 3974 | **622632** (2026-05-02→21, 750 mkts) |
| book_hash_transitions | 0 | — | **2355** (2026-05-20→22, **22 mkts, 2 days**) |
| token_price_log | 0 | — | **61817** (2026-05-02→22) |
| ensemble_snapshots_v2 | 0 | **1131925** (2024-01-01→2026-05-28) | 0 |
| historical_forecasts_v2 | **22644** | 0 | 0 |
| settlements_v2 | 0 | **4584** (2025-01-22→2026-05-21, 51 cities) | 0 |
| uma_resolution | **1333** (FROZEN 2026-01-01→02-21) | — | 0 |
| market_events_v2 | — | **13670** | 7964 |
| market_microstructure_snapshots | — | **0** | — |
| decision_events | **0** (live writer target) | — | — |
| no_trade_events | **843** | — | — |
| shadow_signals | 3058 | — | **27090** (forecast-edge log, no strategy tag, no outcome) |
| regret_decompositions / shadow_experiments | **0** | — | — |

Settlement-join feasibility (the realized-outcome backbone): of 87 distinct
`event_slug` in the 7-day microstructure window, **28** already resolve to a
`settlements_v2.market_slug` (more settle as future target_dates mature). Slug
format matches: `highest-temperature-in-{city}-on-{month}-{day}-{year}`.

**Producer-track DB-routing rule:** Track R reads FCST (ensembles/settlements) +
WORLD (historical_forecasts) and writes `shadow_experiments` /
`regret_decompositions` / `decision_events` to **WORLD**. Track L writes
`decision_events(source='shadow_decision')` to WORLD via the existing
`write_shadow_decision_event()` (which targets WORLD by `_is_world_db_conn`).
A post-build `COUNT(*)>0` smoke on the WORLD targets is mandatory.

---

## §1. Replay feasibility verdict (make-or-break)

`evaluate()` call path: candidates consume `CandidateContext.analysis`
(MarketAnalysisVNext) + its `.metrics` (MicrostructureMetrics) +
ad-hoc attributes (`alert_source`, `uma_resolution_status`,
`passive_maker_estimate`, `neg_risk_*`). MicrostructureMetrics carries only:
snapshot_id, depth_at_best_ask, spread_observed_window_ms (**documented "None
until windowed observer ships"**, market_analysis_vnext.py:50),
raw_orderbook_hash_transition_delta_ms, polymarket_end_anchor_source.

| Candidate | Verdict | Missing decision-time input |
|---|---|---|
| **shoulder_sell** (vNext, NOT in candidates/) | **REPLAYABLE** | Rides `BinEdge` from forecast pipeline; inputs in ensemble_snapshots_v2 + historical_forecasts_v2; settlement in settlements_v2. Classifier is a SCAFFOLD (shoulder_strategy_vnext.py:74, hardcodes SHOULDER_NO_TRADE_GATE) — needs production logic, not new data. |
| **shoulder_buy** (vNext) | **REPLAYABLE** | Same forecast-edge substrate as shoulder_sell. |
| **center_sell / center_buy** (vNext) | **PARTIAL→REPLAYABLE** | center_buy already emits live (1 settled row); buy_yes non-shoulder edge classified in `_strategy_key_for` evaluator.py:2207. Replayable on forecast edges; center_sell needs the symmetric classifier wired. |
| stale_quote_detector | **NOT_REPLAYABLE** | `spread_observed_window_ms` (info-event proxy) never captured — permanently None at market_analysis_vnext.py:135. book_hash_transitions only 2 days / 22 mkts. |
| weather_event_arbitrage | **NOT_REPLAYABLE** | `alert_source` / `active_weather_alert` absent from every historical table (no external alert feed wired). |
| resolution_window_maker | **NOT_REPLAYABLE** | `uma_resolution_status` not captured on snapshots; uma_resolution table FROZEN at 2026-02-21 and keyed by condition_id only (no per-decision status). |
| liquidity_provision_with_heartbeat | **PARTIAL (biased)** | `PassiveMakerExecutionEstimate` is computable from venue_commands history, but it is a function of *Zeus's own prior orders* → non-stationary, look-ahead/self-reference replay bias. Not trustworthy for ranking. |
| cross_market_correlation_hedge | **NOT_REPLAYABLE** | `regime_correlation_cache` = 0 rows (Phase-5 store unfed). regime_tag_for returns UNKNOWN. |
| neg_risk_basket | **NOT_REPLAYABLE** | `neg_risk_family_complete` / `_token_count` / `_yes_ask_sum` documented "not yet wired in MarketAnalysisVNext" (own docstring). All snapshots are neg_risk=1 but the family book sum was never computed/stored. |

**One-line summary:** Only the shoulder/center *forecast-edge* family is
replayable. 0 of 6 `candidates/` strategies are trustworthy-replayable; 5 are
NOT_REPLAYABLE, 1 (liqprov) PARTIAL-with-bias.

---

## §2. EvidenceReport input contract (the schema producers must match)

From `src/analysis/evidence_report.py:104-216`:

- **n_decisions** ← `decision_events` COUNT WHERE `strategy_key = ?`
  (evidence_report.py:135-140). Authoritative denominator.
- **n_no_trades** ← `no_trade_events` COUNT WHERE `strategy_key = ?` AND
  `schema_compatibility='current'` (evidence_report.py:162-194).
- **n_wins / n_settled / mean_regret_usd** ←
  `regret_decompositions rd JOIN shadow_experiments se ON rd.experiment_id =
  se.experiment_id WHERE se.strategy_id = ?` (evidence_report.py:145-156).
  - n_settled = COUNT(*) of regret rows.
  - n_wins = SUM(`rd.total_regret_usd > 0`). Sign convention POSITIVE=WIN
    (regret_decomposer.py:12-22).
- **ci_lower / ci_upper** ← Beta(2+n_wins, 2+n_settled-n_wins) 95% CI
  (evidence_report.py:83-101). None when n_settled=0.
- **breakeven_win_rate** ← caller-supplied (default 0.5).

**Producer obligation:** to make a strategy assessable, BOTH must be written:
1. a `decision_events` row per would-be decision (strategy_key tag) — feeds n_decisions; and
2. one `shadow_experiments` row (experiment_id) + one `regret_decompositions`
   row per *settled* would-be decision (experiment_id + decision_event_id +
   7 components + total_regret_usd) — feeds n_wins/n_settled/CI.

The promotion gate (`promotion_predicate`, live_readiness_tribunal.py:114) fires
iff `tier_current < tier_required AND ci_lower > breakeven + cost_of_capital`.

---

## §3. Track R design (replay → rank) — OFFLINE, no daemon coupling

**Module:** `src/backtest/shadow_replay_harness.py` (new). Standalone CLI
(`python -m src.backtest.shadow_replay_harness --strategy shoulder_sell
--from 2025-06-01 --to 2026-05-15`). Zero import of cycle_runtime/evaluator
live paths; pure historical reconstruction.

**Reads (read-only `immutable=1` connections):**
- FCST.ensemble_snapshots_v2 + WORLD.historical_forecasts_v2 → reconstruct the
  `BinEdge` set at each historical decision instant (same inputs opening_inertia
  used). This is the would-be-decision generator.
- FCST.settlements_v2 (join on market_slug/city/target_date) → realized winning_bin
  → realized outcome of the would-be position.
- TRADES.market_price_history / token_price_log → entry price actually quotable
  at decision_time (NOT a price you could not have filled — see Risks §8).

**Computes per would-be decision:**
1. Counterfactual win/loss: did the shoulder/center edge's chosen side win at
   settlement? `realized_pnl = (settled_payoff − entry_price) * size`;
   `counterfactual_pnl` = best-alternative-action PnL (default: no-trade = 0,
   so total_regret = realized advantage vs sitting out).
2. The 7 regret components (regret_decomposer.py is a SUM-VERIFIER): allocate
   forecast_error / observation_error / quote_error / non_fill / fee / timing /
   settlement_ambiguity so they sum to total_regret within 1e-9. Thin allocation
   for v1: put forecast_error_usd = total and residual 0; refine later. The
   verify_sum() invariant enforces consistency.

**Writes (WORLD, one batch per replay run):**
- 1 `shadow_experiments` row (register_shadow_experiment: strategy_id +
  config_hash + cohort_tag = replay run tag). Immutable; idempotent by SHA-256.
- N `decision_events` rows (source='shadow_decision' or a new
  source='replay_decision' — see §7) with strategy_key, decision_time, outcome
  settled to win/loss.
- N `regret_decompositions` rows (experiment_id + decision_event_id + components).

**Emits:** a ranked Validator report — runs EvidenceReport→Tribunal→
PromotionReadinessValidator for each replayed strategy, sorted by ci_lower
descending, printed to stdout + written to `.omc/research/`. Offline; no tier
is written (validator is read-only advisory).

**Executor track:** R-1.

---

## §4. Track L design (live capture → confirm) — minimal-footprint

**Honest premise correction:** the brief states "the daemon already evaluates
all strategies for no_trade_events." Verified FALSE for the 6 Phase-4
candidates — `grep` finds **zero runtime callers** of
`StaleQuoteDetector.evaluate()` et al. outside candidates/ and tests. The daemon
evaluates only the 4 mainline strategies. Track L therefore requires a **new
dispatch call**, not free-rider observation. It must be fail-open / zero
money-path risk.

**Exact hook point:** `src/engine/cycle_runtime.py:951-952`, where
`MarketAnalysisVNext(snapshot=snapshot, history=[]).compute()` is already built
(`_vnext_metrics`). This is the single existing site where the microstructure
surface candidates consume is already materialized. Add a guarded shadow-dispatch
block immediately after, behind a config flag (`shadow_candidate_capture_enabled`,
default off), wrapped in a broad `try/except` that logs and continues on ANY
error (the live decision must never be affected).

**What it does:** for each registered candidate, build a `CandidateContext`
(natural_key + observed_at + the existing `_vnext_metrics`-bearing analysis),
call `candidate.evaluate(context=, conn=, decision_time=)`. The candidate's own
`write_shadow_decision_event` / `write_candidate_no_trade_row` writers persist to
WORLD.decision_events / no_trade_events with the strategy_key tag. No second
evaluation pass of the mainline strategies; reuses the already-computed snapshot.

**Settlement-attribution join (separate offline job, Track L-2):** a cron job
matches WORLD.decision_events(source='shadow_decision', outcome='shadow_enter')
to FCST.settlements_v2 by (market_slug→event_slug, target_date), computes
realized win/loss, and writes the matching shadow_experiments +
regret_decompositions rows. This is what advances n_settled for the live track.

**Executor track:** L-1 (hook + dispatch, fail-open), L-2 (settlement-attribution cron).

**Track-L gating note:** the 5 NOT_REPLAYABLE candidates will emit only
no_trade rows under Track L until their input fields (alert_source, uma_status,
regime cache, neg_risk sum, windowed spread) are first backfilled into
market_microstructure_snapshots / MarketAnalysisVNext. Track L for those
candidates is a *data-plumbing* prerequisite, not an evidence accumulation.

---

## §5. Shared validator job (operator-reviewable readiness)

**Module:** `src/analysis/promotion_readiness_job.py` (new). Offline/cron.
For each strategy: build_evidence_report(conn=WORLD) → adjudicate (pure compute,
no DB write unless operator applies) → PromotionReadinessValidator.assess().
Writes a readiness verdict file to `.omc/research/promotion_readiness_<date>.md`.

**Operator-applied only:** the job NEVER calls `adjudicate()` with a live conn
and NEVER writes a tier. It emits the recommendation. The operator_ref guard is
preserved: a PROMOTE crossing into >= LIVE_PILOT_TINY raises ValueError unless
operator_ref supplied (promotion_readiness.py:222, live_readiness_tribunal.py:320).
Operator applies by re-running with `--operator-ref=<approval>` which is the only
path that writes `evidence_tier_assignments`.

**Executor track:** shared with R-1 (same validator composition).

---

## §6. Fastest-candidate determination + timeline

**First-promotable: `shoulder_sell`.** It is the only strategy whose decision
input is fully in the historical record and whose settlement outcome is
deterministically derivable from settlements_v2 (2025-01→2026-05, 51 cities).

- **Replay-rank (Track R):** the *harness* is buildable in **days** (1 executor
  track). BUT the SCAFFOLD `classify_shoulder_candidate` must first be given
  production logic (it currently hardcodes no-trade). Realistic: harness +
  production classifier + ranked report = **~1 week**.
- **Evidence sufficiency caveat (advisor-flagged):** the *microstructure* window
  is 7 days / 28 settled — far below tribunal N. But shoulder_sell does NOT
  depend on microstructure; it depends on forecast edges + settlements, which go
  back to 2025. Replay can therefore generate **hundreds-to-thousands** of
  settled would-be decisions immediately, so ci_lower CAN clear breakeven in the
  replay-rank pass. This is precisely why shoulder beats the candidates/ set.
- **Live-confirm (Track L):** weeks. Live shadow capture accumulates ~1
  decision per market per cycle; reaching tribunal-N with fresh live evidence is
  **3-6 weeks** depending on market coverage.

**Recommended sequence for the operator:** RANK shoulder_sell via replay
(~1 week) → if ci_lower clears, begin Track-L live-confirm (3-6 weeks) → operator
applies tier crossing with operator_ref. Real capital moves only after live
confirm, per the "Both" route.

---

## §7. Schema impact

**No schema bump required.** Phase-6 tables (shadow_experiments,
evidence_tier_assignments, regret_decompositions) exist at world SV=26
(phase6_evidence_schema.py). decision_events / no_trade_events carry strategy_key.

**One optional non-breaking refinement:** add `source='replay_decision'` as an
allowed value for decision_events.source so replay-generated would-be decisions
are distinguishable from live shadow capture in the same table (avoids
double-counting n_decisions across tracks). This is a CHECK-constraint widening,
not a version bump, and is optional — Track R could reuse `source='shadow_decision'`
with a distinct cohort_tag on the shadow_experiments side. **Decision for
executor:** prefer the distinct source value to keep n_decisions per track
auditable.

---

## §8. Risks + antibodies

| Risk | Antibody |
|---|---|
| **Replaying an entry price you could not have filled** (quote at best-ask that had zero depth, or a mid that no counterparty would hit). | Track R must read TRADES.market_price_history.best_ask AND require depth_at_best_ask>0 at decision_time; if absent, mark the would-be decision non_fill (regret quote_error/non_fill component) rather than crediting a fill. Antibody test: relationship test asserting no replayed fill exists without a contemporaneous depth>0 quote row. |
| **Look-ahead bias** (using a forecast/settlement value timestamped after decision_time). | Every Track R read filters `available_at <= decision_time` (ensemble_snapshots_v2.available_at, historical_forecasts_v2.available_at). Antibody: assert MAX(available_at of inputs) <= decision_time per replayed row; fail the run on violation. |
| **Self-reference / non-stationarity** (liqprov fill_probability derived from Zeus's own past orders). | Mark liqprov PARTIAL; exclude from replay-rank. Antibody: harness refuses any candidate whose input is venue_commands-derived. |
| **Overfit to the 7-day microstructure window.** | For shoulder, replay on the full 2025-2026 forecast-edge history (not the 7-day microstructure slice). For candidates, do NOT rank on 7 days — Track-L only. Antibody: harness asserts n_settled >= configurable min (e.g. 100) before emitting a non-HOLD verdict. |
| **DB-routing silent-empty** (producer writes to wrong DB file → EvidenceReport reads 0). | Mandatory post-build `COUNT(*)>0` smoke on WORLD.decision_events + WORLD.regret_decompositions filtered by the run's strategy_key/experiment_id. Anchor: F40/F41. |
| **Predicate divergence** between replay-rank and live tribunal. | Both already route through the single `promotion_predicate()` (promotion_readiness.py:288). Do not re-derive the inequality in the harness; import it. |
| **uma_resolution staleness masquerading as fresh** (resolution_window_maker reading a 3-month-frozen table). | Track-L gating: candidate stays no_trade until uma_resolution ingest is confirmed current; freshness check on MAX(resolved_at_utc). |

---

## Staged build order (3-4 discrete executor tracks)

- **Track R-1 (days→1wk):** `shadow_replay_harness.py` + production
  `classify_shoulder_candidate` logic + ranked Validator report. Reads FCST/WORLD
  forecast+settlement history; writes shadow_experiments + decision_events +
  regret_decompositions to WORLD. Offline. **First deliverable; unblocks
  shoulder_sell ranking.**
- **Track L-1 (days):** cycle_runtime.py:951 fail-open shadow-dispatch hook +
  config flag (default off) + CandidateContext build reusing `_vnext_metrics`.
  Zero money-path coupling.
- **Track L-2 (days):** settlement-attribution cron joining
  decision_events(shadow) → settlements_v2 → regret_decompositions.
- **Shared (folds into R-1):** `promotion_readiness_job.py` running
  EvidenceReport→Tribunal→Validator, operator-applied, operator_ref-guarded.

**Data-plumbing prerequisite for Phase-4 candidate confirmation (separate, later):**
backfill market_microstructure_snapshots + wire alert_source / uma_status /
regime cache / neg_risk sum into MarketAnalysisVNext. Until then, the 6
candidates are Track-L-no_trade only and NOT promotable by either route.
