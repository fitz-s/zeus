# Zeus timing-semantics audit — the COMPLETE plan (full context, 2026-06-16)

> Self-contained synthesis of the full session. Reads top-to-bottom with no external context required.
> Supersedes the fragmented evidence notes (indexed in Part IX). Where a number or file:line appears, it
> was verified against the live DBs (read-only) or the current worktree code, not carried from memory.

---

## PART I — THE MANDATE AND THE METHOD THAT WORKED (context)

**The ask.** Audit the timing semantics across the whole engine — data download → fetch → evaluate → order →
fill → settle. Confirm every latency / freshness window / deadline / fallback is correct; find what was *not*
asked (the unknown-unknowns); and ultimately: **why does the system not work properly, and how to fix it
completely so the problems never recur.**

**What did NOT work (and why this plan exists).** The first instinct — a 97-agent / 10M-token code review
organized by *file* — produced 89 findings but missed every functional question the operator actually cared
about (fusion coherence, the Day0 lifecycle, provider release lags, the fast-lane). A file-indexed audit
*structurally cannot* answer a cross-file behavioral question. Three reframings fixed it:

1. **Evidence-first, not code-reading.** Stop reporting what code *says*; query what the live system *did* —
   the 100GB+ of live DBs and the 2GB live log. This is where every real finding came from.
2. **By behavior, then by basis.** Trace the market-event funnel (intent-vs-implementation), then trace every
   *timestamp's provenance*. The basis-lens caught money-path defects the funnel missed (Part IV).
3. **Adversarial verification + honest de-inflation.** Every confident claim was re-probed; several were
   walked back (Part VI). The system mostly works *today*; the defects are mostly latent/telemetry, with a
   bounded money-path set. Over-claiming was as much the enemy as under-finding.

**The overreach that was rejected.** Mid-session this drifted into "redefine the edge / regime-B near-arbitrage
on already-determined outcomes." The operator correctly killed it: that assumes the market is dumb; the residual
price on an "eliminated" bin is the market *correctly* pricing source/settlement/revision risk. The mandate is
**fix the timing semantics so the system correctly processes the market and data it already handles** — not
invent alpha. This plan contains zero strategy/edge content.

---

## PART II — THE ROOT CAUSE (the one disease under all six classes)

**Timestamps are written by GUESSING (瞎猜), not derived from a justified basis.** A wrong-looking-right value
is the lowest-effort thing to write and is invisible until a DB audit months later. The codebase literally
admits this: `BasisKind.GUESS` is a real enum value (`src/contracts/time_semantics.py:87`).

**Quantified (the provenance ledger, 115 persisted/compared timestamp sites):**

| basis | count | meaning |
|---|---|---|
| REAL_SOURCE | 15 | a genuine external event time (venue matchtime, station obs time) |
| DERIVED_JUSTIFIED | 21 | computed from real inputs by an explicit correct formula |
| **GUESS** | **16** | a constant/placeholder with no derivation (cycle-as-available, +24h expiry, 48h, 30s) |
| **SYNTHETIC_NOW** | **32** | `datetime.now()` stamped as an event time it is not (venue_ts, posted=filled, settled_at) |
| **NAIVE_CURRENT_TS** | **31** | SQLite `CURRENT_TIMESTAMP` / naive datetime → format-corrupt + ambiguous on the Chicago host |

**79 of 115 (69%) carry a fabricated basis.** That is the answer to "why nothing works properly."

**The fix standard, every replacement timestamp:** a real source OR an explicit correct derivation, *stated*;
if the genuine basis is unknown → **honest NULL** (+ authority QUARANTINED), never a guess. Worked example done
right (already shipped at `bayes_precision_fusion_download.py:909`): `available_at = min(captured_at, nominal)`
— captured = provable possession; nominal = a real publish estimate; `min` = honest earliest-usable.

---

## PART III — THE COMPLETE DEFECT INVENTORY (evidence → mechanism → fix → verify → prevention)

Each defect: what it is, the verified evidence, the mechanism, the justified-basis fix, how to verify, and the
antibody. Severity tiers: **MONEY-PATH** (feeds q / grade / exit / tradeability) vs **TELEMETRY** (0 market
events) vs **SHIPPED** (already correct — do not touch) vs **DOC-ROT**.

### C1 — Availability clock (MONEY-PATH; currently masked)
- **Evidence (live):** `ensemble_snapshots.available_at == issue_time` (cycle) in **5000/5000** rows, `==
  fetch_time` in **0**. Propagates a parent>self staleness regression into **315,470/1,265,824** decision_certificates.
- **Mechanism (verified end-to-end):** `ecmwf_open_data_ingest.py:346` hard-falls-back `available_at =
  run_init_dt` (= cycle) because `provenance["available_at"]` is set `None` (:222/:275) → `evaluator.py:6626`
  preferred that cycle value over the real `fetch_time` → written to `ensemble_snapshots.available_at` → read by
  the FSR builder → `decision_certificates.source_available_at`. Separately `source_runs.source_available_at =
  source_release_time` (`ecmwf_open_data.py:912,1460,1522,1707,1740`), which itself falls back to raw cycle when
  the safe-fetch metadata is absent (`:1355-1357`). The q-impact (`bayes_precision_fusion` weights on residual
  covariance only — no arrival-recency term, `src/forecast/bayes_precision_fusion.py`) is **masked today**:
  ECMWF real fetch lag (≥497 min) already exceeds the 485 min gate, so no provider fuses before its honest
  availability. **The defect that matters is the MISSING GUARD** — a faster/replacement source would bias q undetected.
- **Fix (justified basis = proof of possession):**
  - `evaluator.py:6626` → `available_at = proof_of_possession_available_at(fetch_time_value)` **[DONE in worktree]**
  - `ecmwf_open_data.py` ×5 → `source_available_at = proof_of_possession_available_at(computed_at)` (possession;
    do NOT credit the safe-fetch *gate* as a publish — it is a guess per the ledger; `computed_at` is the real
    authority-write wall-clock ≥ true possession, the conservative-honest direction).
  - `ecmwf_open_data_ingest.py:346` → drop the `run_init_dt` (cycle) fallback for `available_at`.
  - Fusion guard (`src/data/bayes_precision_fusion_capture.py`): exclude any instrument whose honest
    `source_available_at > decision_utc` (fail-open on NULL). **NB the audit's `src/data/bayes_precision_fusion.py`
    path does not exist** — the real modules are `src/forecast/bayes_precision_fusion.py` + `…_capture.py`.
- **Verify:** after the stamp fixes, `available_at == fetch_time` (not cycle) on new rows; the readiness gate
  `available_at > decision_utc` is unchanged in practice (decisions happen after fetch → still admits). The
  fusion guard is **shadow-q-staged** (expected excluded-count ~0 today; must prove the q-delta is null before promote).
- **Prevention:** ANTIBODY 1 — `src/contracts/availability_time.py::proof_of_possession_available_at` is the
  ONLY producer; CI `test_availability_time_law.py` fails any `available_at` write that bypasses it.

### M-ledger money-path guesses (MONEY-PATH; found by the basis-lens, missed by the funnel)
These live in the **grading / exit / expiry / q-authority** paths the fetch→fill funnel never traversed. Each
needs downstream-consumption verification before the fix.
- **M1 (worst) — `harvester.py:1440` `settled_at = datetime.now()`.** Stamps the cron clock as the settlement
  event time, fed to `dispatch_era_basis()` → **grades every position's P&L against a guessed time** while the
  real `obs_row['observation_time']` (in scope at :1448) sits unused. Fix → `observation_time`; absent → NULL +
  `authority='QUARANTINED'`; keep `recorded_at=now()` as a separate var. Verify it feeds grading not display;
  shadow-compare the ERA basis selection if any settled grade changes.
- **M2 — `fill_tracker.py:1089` `entered_at = now()`** + **`monitor_refresh.py:1187,1643` `48.0h` magic
  fallback** → `hours_since_open` → `compute_alpha` → **biases every live exit.** Fix → venue matchtime from the
  WS fill payload (absent → NULL); 48h → NULL/NaN so `compute_alpha` refuses. Shadow-compare alpha.
- **M3 — `expires_at = +3h/+24h` magic** (`ecmwf_open_data.py:936`, `entry_readiness_writer.py:186`) →
  prematurely expires valid fusion triggers → **suppresses real trades.** Fix → `source_cycle_time +
  source_cycle_max_age_hours()` (calendar `max_source_lag_seconds` ~30h; matches `replacement_forecast_materializer.py:2181`).
- **M4 — naive `recorded_at`** on `forecast_posteriors` (the q-authority, `v2_schema.py:382`) and
  `readiness_state`/`market_topology_state` (the LIVE_ELIGIBLE gate, `db.py:2089/2132`). Naive vs tz-aware
  comparison on the Chicago host corrupts the q-freshness ranking and the tradeability gate. Do these four with C1, not the C3 batch.
- **M5 — the ingest COLLECTION plane is one `computed_at=now()`** (`fetch_started_at = fetch_finished_at =
  captured_at = imported_at`). So C1's "proof-of-possession" basis is itself synthetic. Capture distinct real
  events (pre-download / file-write-complete / post-commit) so the C1 basis is real; `retrieved_at` (forecasts)
  move after the HTTP response; `run_init_dt` fallback → `source_cycle_time` not `now()`.

### C2 — Dead decision lanes (TELEMETRY; wire-or-remove)
- **Evidence:** `decision_events` = 0 rows; `day0_metric_fact`/`day0_nowcast_runs` = 0.
- **Mechanism:** `decision_events` writer (`cycle_runtime.py:6106`) is gated behind a `venue_ack` stage that
  fires ~once/28d AND requires `first_member_observed_time`/`run_complete_time` (100% NULL); empty **by
  omission**, not by a raised exception (0 "write_decision_event failed" in 28d log). Its only reader is the
  `n_decisions` denominator (`evidence_report.py:203`), NOT any ARM gate; the active provenance path is
  `decision_certificates` (1.27M rows). The Day0 nowcast lane is gated on an empty `day0_horizon_platt_fits`
  (`monitor_refresh.py:1827`) because a one-command Platt bootstrap was never run.
- **Fix (no-shadow wire-or-remove):** `evidence_report.py:203-223` → count `decision_certificates` for
  `n_decisions`; remove the dead `cycle_runtime.py:6084-6124` write block. Run
  `scripts/persist_day0_horizon_identity_fit.py` (one command) to unblock Day0; drop the 0-row `day0_metric_fact`.
- **Prevention:** ANTIBODY 3 — fail-LOUD lane-liveness counters: replace `except: summary['degraded']=True`
  with a named `_increment_summary_counter` + a per-cycle `decision_lane_writes` map; CI bans the bare pattern.

### C3 — Timestamp format/tz corruption (mostly TELEMETRY; M4 subset is money-path)
- **Evidence:** naive `recorded_at`/`ingested_at` (SQLite `CURRENT_TIMESTAMP`) vs ISO event-time →
  string-comparison corrupt (`'T'`>`' '`). `observation_revisions` 134,250 rows; `venue_order_facts`,
  `source_run_coverage`, `readiness_state`, `daily_observation_revisions`, `platt_models`,
  `provenance_envelope_events`. `observation_instants` 498/2.77M mix `Z` vs `+00:00` (+1 real inversion).
- **Fix:** `scripts/migrations/normalize_observation_instants_z_suffix.py` (498 rows, idempotent, on a copy
  first); add `db.py::utc_iso_now()`; flip the schema `CURRENT_TIMESTAMP` defaults → caller-supplied tz-aware
  ISO at `recorded_at`/`ingested_at` (new rows only; append-only history stays).
- **Prevention:** ANTIBODY 2 (`utc_iso_now()` helper) + ANTIBODY 5b (`test_timestamp_format_invariant.py`
  fails any `DEFAULT CURRENT_TIMESTAMP` on a timing column).

### C4 — Dead/placeholder/synthetic instrumentation (TELEMETRY, 0 market events)
- **Evidence:** `execution_feasibility_evidence.latency_ms`/`order_intent_time`/`submit_time` NULL across
  12.27M; `venue_timestamp = datetime.now()` (server matchtime discarded, `executor.py:3063/4146/4171`;
  `polymarket_user_channel.py:858/1046` alias to delivery timestamp not `matchtime`); `execution_fact` latency=0
  for ~28 bridge fills (`edli_position_bridge.py:978`).
- **Honest split (do not conflate):** "latency always 0" is THREE facts — venue_timestamp=wallclock on REST ACK
  (WRONG → NULL); `command_recovery` same-second fills (LEGITIMATE 0); `edli_position_bridge` synthetic (FIX,
  ~28 rows). `readiness_state.expires_at` is **already correct** (477/477 populated) — the evidence-doc "all
  NULL" was stale; do NOT re-fix.
- **Fix:** WS path prefer `matchtime`; REST ack `venue_timestamp = None` (honest absence, keep `observed_at` =
  ack-receipt LABELLED); `edli_position_bridge` `posted_at = venue_commands.created_at` (→ latency honest-NULL
  not synthetic-0); declare `latency_ms`/`submit_time`/`order_intent_time` permanently write-NULL with a schema comment.
- **Prevention:** ANTIBODY 5c — `test_timing_column_liveness.py`: every declared timing column populated >50% or
  in `HONEST_NULL_COLUMNS`; AST-ban `posted_at=filled_at`, `latency_ms=0` default, `venue_timestamp=ack_time`.

### C5 — Fetch cadence-vs-window throughput (LATENT, masked — prevention only)
- **Evidence:** 561s avg capture cadence vs 180s window → 49% stale at any instant; **BUT zero pre-close STALE
  drops** (all 2,066 post-close; the requeue loop absorbs live staleness). The 30s→180s widen + DAY0 horizon +
  SOURCE_TRUTH over-gate are ALREADY SHIPPED. ~1.32M old 30s-deadline rows aging out (do NOT backfill).
- **Fix:** prevention-ONLY. Do NOT widen the window (loosens the pricing snapshot). Add a `C5_CADENCE_COVERAGE`
  WARNING (`main.py:~3659`) when `effective_sweep_period` exceeds the freshness window for live-open families.
- **Prevention:** ANTIBODY 4 — the structural ratio becomes self-announcing on the first crossing.

### C6 — Doc-rot / config drift (DOC-ROT, last — except one money-path date.today)
- **Evidence:** `time_semantics.py:620-634` registry still says 30s (TEST_ONLY, never imported by a daemon —
  the live system reads 180s from `executable_market_snapshot.py:47`); `main.py` comments still "30s";
  `ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS` carries dual 10s/5s defaults (Gamma vs CLOB); 10 `date.today()` sites
  violate the `epistemic_context.py:12` ban.
- **Money-path exception (do FIRST, with C1):** `shoulder_strategy_vnext.py:118/:134` `date.today()` (host =
  Chicago) → mis-tags a non-US family near UTC-midnight. → `datetime.now(timezone.utc).date()`.
- **Fix (rest, last):** registry/comments UP to 180s (never revert live to 30s); split the env var; replace the
  6 non-money-path `date.today()`.
- **Prevention:** ANTIBODY 5d — `test_no_date_today_ban.py` (AST scan, enforces the existing prose ban) +
  the doc-rot closer: any `*_freshness_window`/`*_timeout` constant must appear in `time_semantics.py` with a
  `source=` callable reading the live value, not a literal.

---

## PART IV — THE HONEST STATE (de-inflated)

- **Genuinely money-path live-wrong:** C1 (masked today — fix the stamps + add the guard) + M1 settled_at
  (grading on a guess) + M2 entered_at/48h (exit-alpha) + M3 expires_at (trade-suppression) + M4 naive
  q-authority/gate columns. **This is the "NOT one" the operator insisted on.**
- **Telemetry-only (0 market events mis-routed):** the remaining C2/C3/C4 sites — real correctness work for
  honest instrumentation, but they do not change a live trade.
- **Already shipped — DO NOT re-fix:** 30s→180s window, DAY0 venue-close horizon, SOURCE_TRUTH over-gate,
  `readiness_state.expires_at`.
- **The real prize:** the prevention layer (Part VII) — it ends all six classes structurally.

---

## PART V — PREVENTION LAYER (the 不再犯, machine-enforced — extend the EXISTING BasisKind antibody)

`BasisKind` exists (`time_semantics.py:87`) but only annotates window/lag *constants*. Extend to EVERY persisted
timing value, four enforced layers (attach points verified in `prevention_scaffolding_2026-06-15.md`: hooks at
`.claude/hooks/pre-commit` + 9 CI workflows; the relations test exists but is advisory; no canonical helper —
410 raw sites; the `date.today()` ban is prose-only with 10 violators):

1. **BasisKind REQUIRED at the write boundary** — `log_settlement` / `append_order_fact` / `append_trade_fact` /
   `append_position_lot` / `log_execution_fact` / readiness writers take a mandatory `basis: BasisKind` beside
   each timestamp; persisting a time without one is a type error.
2. **Canonical producers** — ANTIBODY 1 `availability_time.py::proof_of_possession_available_at` (DONE);
   ANTIBODY 2 `db.py::utc_iso_now()`. Raw cycle/`CURRENT_TIMESTAMP`/wallclock-as-event assignment is forbidden.
3. **UNKNOWN → NULL, never back-fill** — a lint forbids `or datetime.now(` / `or _now_iso()` fallbacks on
   persisted event-time columns; writers pass NULL + QUARANTINED authority.
4. **Four CI bans, blocking merge** (the floor): (a) `test_availability_time_law.py`; (b)
   `test_timestamp_format_invariant.py`; (c) `test_timing_column_liveness.py`; (d) `test_no_date_today_ban.py`.
   Plus ANTIBODY 3 fail-loud lane counters + ANTIBODY 4 cadence-coverage warning.

Why it ends recurrence: every class recurs because a wrong-looking-right pattern is the lowest-effort write and
invisible for months. The canonical producers + the CI bans shift every violation from "found in a
hundred-million-token audit" to "blocked at PR time on the author's own branch."

---

## PART VI — CORRECTIONS / REFUTATIONS (with reasoning — do NOT re-chase)

1. **Regime-B near-arbitrage "edge"** — out of scope and rejected; assumes the market is dumb. The residual
   price on an eliminated bin is the market correctly pricing source/settlement/revision risk.
2. **Fusion module path** — `src/data/bayes_precision_fusion.py` does not exist; real =
   `src/forecast/bayes_precision_fusion.py` + `src/data/bayes_precision_fusion_capture.py`.
3. **`readiness_state.expires_at` "all NULL / never expires"** — REFUTED live: 477/477 populated. Already shipped.
4. **"Only ONE money-path class"** — REFUTED by the basis-lens (Part III M-ledger). The funnel missed the
   grading/exit/expiry paths.
5. **C1 "wrong q right now"** — over-escalation; q-divergence is ~0 today (fetch lag ≥497min > 485min gate).
   Correct framing: the GUARD is absent → a faster source WOULD bias q undetected.
6. **"latency always 0"** — three distinct facts (Part III C4), not one bug.
7. **`opportunity_events` available-after-received (778k)** — BOOK_SNAPSHOT quote-cache (telemetry), not q.
8. **The 30s number** — fix UP to the live 180s; never revert the live code down to 30s.
9. **`decision_events`=0 / `edli_no_submit` frozen** — NOT regressions; `decision_certificates` 1.27M is the
   active path; empty by omission. Action = wire-or-remove, not debug-the-submit-path.
10. **Method correction** — a file-indexed audit cannot answer a behavioral question; evidence-from-live-DBs +
    the basis-lens is what found the real defects. The 97-agent file review missed all four operator questions.

---

## PART VII — EXECUTION PLAN (tiered; worktree `fix/timing-semantics-2026-06-16` off HEAD f237314fb6)

**Discipline:** verify-then-fix (read actual code + confirm downstream consumption before editing); q/grade/alpha
changers are shadow-staged; everything worktree + verifier; live daemons run from the main checkout (isolated).

- **Tier 1 — basis-only, zero behavior change (make stamps honest):**
  C1 stamps (evaluator [DONE] + ecmwf ×5 + ingest fallback) · M4 naive→ISO on q-authority/gate columns · M5
  collection-plane real events. Verify: new rows carry honest values; gates unchanged in practice.
- **Tier 2 — verify-then-shadow (can change grade/alpha/q):**
  M1 settled_at (shadow ERA basis) · M2 entered_at/48h (shadow alpha) · C1 fusion guard (shadow q) · M3 expires_at.
- **Tier 3 — telemetry truth:** C4 (venue_timestamp/latency honest-NULL) · C3 (format migration + helper) ·
  C2 (wire-or-remove dead lanes).
- **Tier 4 — prevention:** the canonical helpers + 4 CI bans + fail-loud counters + cadence warning.
- **Tier 5 — doc-rot:** C6 registry/comments/env-var/remaining date.today (the money-path date.today does in Tier 1).

**Status:** Tier-1 started — `src/contracts/availability_time.py` (antibody) created + verified; `evaluator.py:6626`
fixed through it + compiles. Next: ecmwf ×5, the ingest fallback, then verify, then Tier-2 shadows.

---

## PART VIII — THE STANDARD OF DONE (operator law)
Settlement = the only truth. A fix is DONE only when the corrected behavior is measured on live/replayed data
(grades unchanged or improved, q-delta null under shadow, gates admit the same live markets), not when "code
changed." Every replacement timestamp carries a documented, machine-checked basis.

## PART IX — EVIDENCE INDEX (this session)
`recon_timing_substrate_2026-06-15.md` · `FINDINGS_2026-06-15.md` · `instrument_intent_verdicts_2026-06-15.md` ·
`gating_unknowns_2026-06-15.md` · `deepdive_4q_verdicts_2026-06-15.md` · `code_map_extract_2026-06-15.md` ·
`FIX_PLAN_2026-06-15.md` · `unknown_unknown_sweep_2026-06-15.md` · `FIX_PRIORITY_funnel_2026-06-15.md` ·
`prevention_scaffolding_2026-06-15.md` · `MASTER_TIMING_FIX_PLAN_2026-06-16.md` ·
`timestamp_provenance_ledger_2026-06-16.md` · (this) `ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md`.
