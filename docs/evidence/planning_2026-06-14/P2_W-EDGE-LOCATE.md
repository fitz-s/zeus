# P2 — WORKSTREAM W-EDGE-LOCATE: Operationalize Settlement-Backed Correct-Bin Edge Location

**Date:** 2026-06-14
**Mode:** PLAN-MAKING (file-level implementation plan; no production edits, no deploy, no live touch). DBs opened `?mode=ro`.
**Authority spine:** `P1_strategy_of_record.md` (Thrust 6 + S4's edge-location), `diagnosis_confirmation.md`, `b2_capital_efficiency_audit.md`, operator contract laws 1/4/5/8.
**Sibling workstreams (compose, do NOT duplicate):** `P2_W-QLCB.md` (the q_lcb-input causal fix, T3/T4), `P2_W-SUBMIT.md` (submit re-decision, T5). This document owns **the grading instrument + the edge-location query + the candidate-focus feedback** — the thing that decides, on an ongoing basis, *which* (city, ring-distance, lead) cells carry real correct-bin edge, and gates every promotion the sibling workstreams produce.

---

## 0. WHAT THIS WORKSTREAM IS (and is NOT)

S4's edge-location, operationalized. The operator's question is not "fix q_lcb" (that is W-QLCB) — it is: **"where, provably, does the model honestly disagree with the market AND settlement back it, and how do we keep candidate generation pointed there?"** Three deliverables:

1. **The edge-location query** (`E1`) — the standing read-only join that ranks (city × ring-distance × lead-bucket × direction) cells by *settlement-backed* realized-vs-price edge, event-level de-duplicated, walk-forward, vs-market. This IS the "which markets/bins carry real edge" instrument.
2. **The grading harness** (`E2`) — Thrust 6 of the strategy-of-record: the settlement-graded, event-level reliability monitor that converts E1's cells into LICENSE / NO-EDGE / INSUFFICIENT verdicts with the five INV-CAL contracts. It is the **gate on every promotion** W-QLCB and W-SUBMIT produce — not a new live gate.
3. **The candidate-focus feedback** (`E3`) — the narrow, settlement-fitted seam by which an E2-LICENSED cell *widens* the existing `direction_law` ring threshold `T` for that cell (restoring mid-band where settlement proves it), and an E2-NO-EDGE cell leaves it untouched. This is the "candidate generation focuses there" half — implemented as a **fitted per-cell boundary, never a hardcoded band**, composing with the existing `direction_law.py` geometry rather than adding a parallel gate.

**It is NOT** a new admission gate, a loosening of `capital_efficiency`, or a fills-realized tracker. The existing `src/state/edge_observation.py` is a fills-realized weekly tracker keyed on `strategy_key` — **wrong instrument** for a system that is not filling (it reads `query_authoritative_settlement_rows`, i.e. *executed* positions; we have ~zero). W-EDGE-LOCATE grades the **counterfactual** (what edge *would* have existed had the rejected candidate filled), which is the only signal available pre-fill.

---

## 1. THE EMPIRICAL GROUND TRUTH (re-derived this session, read-only)

I re-derived the problem from the evidence rather than inheriting S4's framing. The decisive substrate and its honest verdict:

### 1.1 The counterfactual substrate exists and is large

`no_trade_regret_events` lives in **`state/zeus-world.db`** (NOT zeus-forecasts.db, where it is an empty mirror — `SELECT COUNT(*)` = 0 there, 260,829 here; span 2026-05-29 → 2026-06-14). Every rejected candidate writes a row carrying `q_live`, `q_lcb_5pct`, `c_cost_95pct`, `direction`, `bin_label`, `city`, `target_date`, `would_have_won` (the counterfactual settlement outcome, populated on 20,009 of 260,829 rows — the ones whose target_date has since settled). This is the raw material; `forecast_posteriors ⨝ settlement_outcomes(VERIFIED)` (277 distinct city/date/metric cells, 7,009 VERIFIED settlements) is the cleaner parallel join for the calibration view.

### 1.2 The ring carries edge in MECHANISM — but the distinct-event sample is razor-thin

The raw counterfactual edge by direction (`would_have_won IS NOT NULL`):

| direction | raw rows | wins | realized WR | avg q_lcb | avg cost | realized edge (WR−cost) |
|---|---|---|---|---|---|---|
| buy_no | 18,872 | 17,851 | 0.946 | 0.963 | 0.953 | **−0.007** |
| buy_yes | 1,137 | 65 | 0.057 | 0.031 | 0.028 | **+0.029** |

buy_no's +0.95 WR is **base-rate favorite-buying** (law 4: in the price; net edge −0.007, slightly negative after the cost is paid) — NOT alpha. The buy_yes signal is the candidate. Bucketed by cost (proxy for ring distance) on raw rows, band 2 (cost 0.05–0.15) shows realized WR 0.58 vs cost 0.08 → apparent edge **+0.49 after fee**. That is the "suppressed ring alpha" headline.

**But it is a row-count illusion.** Event-level de-duplication (the INV-CAL-1 contract) collapses it:

| cost band | RAW rows | DISTINCT (city,date,bin) events | event wins | event WR | edge after fee |
|---|---|---|---|---|---|
| <0.05 (far tail) | 1,050 | 39 | 1 | 0.026 | +0.006 |
| 0.05–0.15 (ring) | **48** | **5** | **2** | **0.40** | **+0.31** |
| 0.15–0.35 | — | — | — | — | — |
| >0.35 | — | 6 | 2 | 0.33 | −0.19 |

The band-2 "ring edge" is **5 distinct events, 2 wins** — and 28 of the 48 raw rows are a *single* market (Taipei 2026-06-05 28°C) written once per reactor cycle. The distinct winning markets in the entire ring band are **three**: Warsaw-06-04-20C, NYC-06-04-66-67°F, Taipei-06-05-28C. The crush signature is real (in band 2, q_lcb 0.039–0.059 sits below cost 0.087 while q_live 0.090 ≈ cost; 38/48 rows have q_lcb < 0.5·q_live → TRADE_SCORE rejection), but **n=5 events is below any honest licensing threshold.**

### 1.3 The honest verdict that shapes this workstream

This is the operator's law-1 fork resolved with numbers: there *is* a near-center ring where the model is mildly under-confident and the LCB construction crushes it below price (W-QLCB's target), but **the settlement-backed distinct-event evidence for tradeable ring alpha is currently n≈5, far too thin to license anything.** Therefore W-EDGE-LOCATE's PRIMARY job is not to *declare* edge — it is to **grow this distinct-event cohort to N_min under honest dedup + walk-forward, and refuse to license until it clears.** The harness must be built so that if the cohort never clears after the 1¢ fee, it emits a dated, numeric "this cell is efficient — no edge" — a legitimate law-1 outcome, not a failure. The 40× row inflation is itself the proof that the instrument must be event-level or it will hallucinate alpha that is one lucky market.

---

## 2. CURRENT-PATH MAP (file:line, what exists to reuse)

| Concern | Current site | Status |
|---|---|---|
| Counterfactual regret rows | `state/zeus-world.db:no_trade_regret_events` (260k rows, writer `src/events/reactor.py` + `src/events/no_submit_receipts.py`, schema `src/state/schema/no_trade_regret_events_schema.py`) | LIVE, populating; `would_have_won` backfilled on settled rows |
| Settlement truth | `state/zeus-forecasts.db:settlement_outcomes` (7,009 VERIFIED), graded via `src/contracts/graded_receipt.py:grade_receipt` (Direction Law lives there) | CURRENT — authority=VERIFIED only |
| Ring-distance primitive | `src/strategy/live_inference/direction_law.py:bin_forecast_distance` + `_SETTLEMENT_STEP_BY_UNIT` (already imported into reactor at `event_reactor_adapter.py:8038-8043`) | CURRENT_REUSABLE — pure, no I/O |
| Candidate ring scoping (live) | `direction_law.py:direction_law_rejection_reason` (T = max(1 step, k·σ), k=`DIRECTION_LAW_SIGMA_K=1.0`); invoked `event_reactor_adapter.py:7566` | CURRENT — this is the seam E3 widens per-cell |
| Isotonic realized-rate | `src/calibration/settlement_backward_coverage.py:_isotonic_realized_rate:97` | CURRENT_REUSABLE — E2 reuses for the band→realized map |
| Existing edge tracker (WRONG instrument) | `src/state/edge_observation.py` (fills-realized, `strategy_key`-keyed, weekly) + `scripts/edge_observation_weekly.py` | STALE_FOR_THIS_PURPOSE — reads executed positions; keep for its post-fill role, do NOT extend it for counterfactual edge |
| σ-shape calibration-at-fit (ring ratio) | `state/sigma_scale_fit.json` (`candidate=true`, ring dist-0..3 ratios 1.00–1.15, tail 0.30) | the W-QLCB / T4 lever; E2 consumes its bucketing scheme |

**Provenance verdict on `edge_observation.py`:** `CURRENT` for its declared purpose (post-fill alpha-decay on executed trades) but `QUARANTINED` for W-EDGE-LOCATE — it cannot answer "where is suppressed edge" because it only sees fills, and the binding constraint is *zero fills*. Do not reuse its `compute_realized_edge_per_strategy`; build E1/E2 on the regret + settlement substrate.

---

## 3. DELIVERABLE E1 — THE EDGE-LOCATION QUERY (read-only standing report)

### 3.1 What it produces

A single read-only module + CLI that, for a given as-of date, emits one row per **(city, metric, ring_distance_bucket, lead_bucket, direction)** cell:

```
cell_key | n_events | wins | realized_wr | mean_cost | mean_q_lcb | mean_q_point |
edge_after_price | edge_after_fee | realized_minus_price_lo95 | market_brier | model_brier |
verdict
```

`ring_distance_bucket ∈ {0, 1, 2, 3, ≥4, tail}` computed via the **live** `bin_forecast_distance(bin_low, bin_high, mu)/step` — the SAME primitive the reactor uses, so the report's geometry is byte-identical to admission geometry (no second distance authority — law: one authority per domain). `mu` is read from each regret row's family posterior (`forecast_posteriors.provenance_json.anchor_value_c`) joined on (city, target_date, metric, captured cycle).

### 3.2 Current path → exact change

- **New file:** `src/analysis/edge_location.py` (module) + `scripts/edge_location_report.py` (CLI wrapper, mirrors `edge_observation_weekly.py` structure for operator familiarity). Header per provenance rule (Created 2026-06-14; Authority basis: P2_W-EDGE-LOCATE.md §3).
- **Read substrate:** `no_trade_regret_events` (zeus-world) for the counterfactual, ATTACH `settlement_outcomes` (zeus-forecasts) for the VERIFIED grade and `forecast_posteriors` for `mu`. **INV-37 discipline:** read-only cross-DB via ATTACH on a single connection (no independent connections), per `.claude/CLAUDE.md` K1 DB split.
- **The core query** (the event-level dedup CTE proven in §1.2):
  ```sql
  WITH dedup AS (
    SELECT city, target_date, metric, bin_label, direction,
           MAX(would_have_won) AS won,          -- a bin wins iff it won (idempotent across cycles)
           AVG(c_cost_95pct)    AS cost,
           AVG(q_lcb_5pct)      AS q_lcb,
           AVG(q_live)          AS q_point
    FROM no_trade_regret_events
    WHERE would_have_won IS NOT NULL
      AND target_date < :as_of_date           -- INV-CAL-3 walk-forward
    GROUP BY city, target_date, metric, bin_label, direction)
  SELECT <cell_key>, COUNT(*) n_events, SUM(won) wins, AVG(won) realized_wr, ...
  FROM dedup JOIN <ring_distance + lead_bucket> GROUP BY <cell_key>;
  ```

### 3.3 Weighed alternatives + pick

- **Alt A — grade off `no_trade_regret_events.would_have_won` (the pre-computed counterfactual).** Cheapest; the field is already populated by the reactor's regret writer. **Risk:** the writer's bin-match grading may not go through `grade_receipt`'s Direction-Law/preimage spine (S2's INV-CAL-2 concern, law 8) — a boundary/rounding mismatch could silently mislabel `would_have_won`.
- **Alt B — re-grade every cell from raw `settlement_outcomes` through `grade_receipt` at report time.** Authoritative, immune to writer drift, reuses the canonical spine. **Cost:** must reconstruct the traded_bin and direction from the regret row and call `grade_receipt` per event (~7k calls; sub-second).
- **Alt C — both: read `would_have_won` AND re-grade through the spine, assert agreement, alarm on mismatch.** Most expensive; doubles as a continuous audit of the regret writer.

**PICK: Alt B as the grade-of-record, with Alt C's mismatch-assert as a one-time validation test (§7), not a per-run cost.** Law 8 makes the metadata foundation the precondition: a report that grades off a possibly-mis-preimaged `would_have_won` could confidently locate "edge" on a bin that the writer mislabeled. Re-grading through `grade_receipt` is the single settlement authority and is cheap. The Alt-C cross-check ships once as a regression test that the writer's `would_have_won` matches the spine on the historical population; if it passes, future runs trust Alt B alone. **This directly serves law 8: bin identity / preimage correctness is verified before any edge claim is made.**

### 3.4 vs-market benchmark (INV-CAL-4, non-negotiable)

For each cell, compute `market_brier` from the market-implied probability (`c_cost_95pct` is the fee-adjusted price ≈ market P; the raw mid is reconstructable from the snapshot the regret row references via `causal_snapshot_id`) and `model_brier` from `q_point`. A cell is "edge" ONLY if `model_brier < market_brier` on the SAME events AND `realized_minus_price_lo95 > 0`. A cell that merely has realized > price but does not beat the market on Brier is **no-edge** (the market priced it right; we got lucky on direction). This is the law-4 antibody against re-discovering base-rate buy_no as "alpha."

---

## 4. DELIVERABLE E2 — THE GRADING HARNESS (the promotion gate, Thrust 6)

### 4.1 What it is

E1 is the *query*; E2 is the *verdict authority* that wraps it with the five INV-CAL contracts and emits a per-cell `EdgeVerdict ∈ {LICENSED, NO_EDGE, INSUFFICIENT_DATA}` that **gates every promotion** in W-QLCB (shadow q_lcb → live) and W-SUBMIT. It is the literal implementation of strategy-of-record Thrust 6.

### 4.2 The five contracts → file:line enforcement

| Contract | Enforcement (file:line) | Test that goes RED on revert |
|---|---|---|
| INV-CAL-1 event-level dedup | the §3.2 CTE `GROUP BY city,target_date,metric,bin,direction`; a test asserting no (city,date,bin) contributes >1 unit | `tests/analysis/test_edge_location.py::test_no_event_double_counts` — feed the Taipei 28-row fixture, assert n_events=1 |
| INV-CAL-2 settlement-only grade | re-grade via `grade_receipt` (§3.3 Alt B); audit bin-match against `settlement_semantics` preimage | `::test_grade_matches_spine_not_value_equality` (the D1-keystone mis-grade fixture) |
| INV-CAL-3 walk-forward | `WHERE target_date < :as_of_date` | `::test_future_settlement_excluded` |
| INV-CAL-4 vs-market mandatory | the §3.4 Brier gate; LICENSED requires `model_brier < market_brier` | `::test_base_rate_buy_no_is_no_edge` — feed the +0.95-WR buy_no cohort, assert verdict=NO_EDGE |
| ring-distance bucketing | `bin_forecast_distance` live primitive | `::test_distance_bucket_matches_reactor` — same bin/mu through both paths, assert identical bucket |

### 4.3 The verdict thresholds (D5 from the strategy-of-record, resolved here)

Two distinct N thresholds, two distinct units (the strategy-of-record left this open as D5):

- **`N_MIN_EVENTS = 30`** distinct (city,date,bin) settled events per cell before E2 will emit anything other than INSUFFICIENT_DATA. **Why 30:** matches `settlement_backward_coverage.settlement_backward_coverage_check(min_n=30)` — the SAME settled-observation floor the existing coverage license already uses (reuse the constant, do not invent a second). Below 30 → `INSUFFICIENT_DATA`, cell stays dark, candidate-focus (E3) does NOT widen.
- **`LICENSE` predicate:** `n_events ≥ 30 AND realized_minus_price_lo95 > 0 AND model_brier < market_brier`. The lower-CI on realized−price uses a Wilson/Jeffreys interval (reuse the analytic bound W-QLCB's cold-start fallback defines — single authority).
- **`NO_EDGE`:** `n_events ≥ 30 AND (realized_minus_price_lo95 ≤ 0 OR model_brier ≥ market_brier)` — efficient cell, dated numeric verdict. This is the law-1-compliant "market is efficient here" output.

**Critical honesty wiring:** with today's data EVERY ring cell is `INSUFFICIENT_DATA` (n=5 < 30). E2 must therefore emit, on first run, "no cell is licensable yet — N_min not met on any ring cell; continue shadow accrual." This is the correct, non-failure first output. The harness's value is forward: as W-QLCB's shadow runs and more dates settle, the ring cell's distinct-event count grows toward 30.

### 4.4 Weighed alternatives for E2's home

- **Alt A — pure read-only report (no persisted verdict).** Re-computed each run. Simplest, no new table, no INV-37 surface. **Con:** every consumer (W-QLCB promotion check, W-SUBMIT) re-runs the join.
- **Alt B — persist `EdgeVerdict` rows to a new `edge_location_verdicts` table.** Consumers read the table. **Con:** a new persisted surface = gate-mass risk (operator law 3); the verdict can go stale vs settlement.
- **Alt C — read-only verdict object returned in-process, persisted ONLY as a dated JSON evidence artifact under `docs/evidence/` (like `edge_observation_weekly.py`), never as a DB authority.**

**PICK: Alt C.** The operator's no-gate-mass / no-shadow-table law (memory: `no-shadow-no-gate-accretion`) forbids a new persisted DB authority whose only job is to gate. The verdict is *derived context*, not canonical truth — settlement is the only truth (law 5), and the verdict is recomputable from it at any time. Persist a dated JSON for operator review and for the promotion-decision audit trail, recompute live for the gate. Zero new DB surface; zero staleness risk; matches the established `edge_observation_weekly` pattern.

---

## 5. DELIVERABLE E3 — CANDIDATE-FOCUS FEEDBACK (the "focus generation there" half)

### 5.1 The honest framing

S4's "candidate generation focuses there" is **already half-built**: `direction_law.py` scopes buy_yes to `distance(bin,μ*) ≤ T = max(1 step, k·σ)`. Candidate generation is ALREADY ring-focused. The question E3 answers is narrow: **may a cell that E2 has LICENSED widen its `T` to readmit the mid-band (dist 2–3) the current k=1.0 may exclude — and ONLY where settlement proves it?**

This is the law-1 "restore mid-band if real" instruction, made safe: the mid-band is restored **per-cell, fitted from settlement, gated on E2-LICENSED**, never as a global hardcode or a loosened constant.

### 5.2 Current path → exact change

- **Current:** `direction_law.py:DIRECTION_LAW_SIGMA_K = 1.0` is a module constant; `T` is uniform across all cells.
- **Change (before → after):**
  - **before:** `T = max(1*step, DIRECTION_LAW_SIGMA_K * predictive_sigma)` with `DIRECTION_LAW_SIGMA_K` a hardcoded 1.0.
  - **after:** `T = max(1*step, k_eff(cell) * predictive_sigma)` where `k_eff(cell) = edge_location_k_for_cell(city, metric, lead_bucket)` returns the fitted multiplier — **1.0 by default (byte-identical to today), raised toward the E2-fitted ring radius ONLY for cells E2 has LICENSED at dist 2–3.** The fit is the largest ring-distance bucket whose E2 verdict is LICENSED; `k_eff` is set so `T` just covers that bucket's outer edge. Cells with no LICENSED bucket beyond dist-1 keep k=1.0.
- **The seam:** `k_eff` is supplied to `_direction_law_reason_for_candidate` (`event_reactor_adapter.py:7566`) from a per-family lookup computed once per cycle (like `direction_law_mu` is), reading the E2 JSON artifact (derived context, fail-soft to 1.0 on any miss). **No new gate** — it MODULATES the existing one's single tunable, and only ever toward *admitting more*, gated on settlement evidence.

### 5.3 Weighed alternatives + pick

- **Alt A — widen `T` per-cell from E2 (above).** Restores mid-band where settlement licenses it; default-identical; one fitted boundary replacing one hardcoded constant (a SIMPLIFY in the operator's constant-elimination sense, task #64). **Risk:** widening `T` admits more candidates into FDR/sizing; if E2's license is wrong, more wrong-bin YES get through — but `capital_efficiency` + the corrected q_lcb (W-QLCB) still gate each one, so the floor holds.
- **Alt B — leave `T` global at k=1.0; rely solely on W-QLCB's corrected q_lcb to admit ring candidates.** Simplest; no E3 at all. **Con:** if the genuine ring edge lives at dist 2–3 (which the current k=1.0 may clip before q_lcb is ever evaluated), no q_lcb fix can rescue a candidate that direction_law already rejected. The geometry cut is BEFORE the belief gate (`event_reactor_adapter.py:7566` runs the direction-law reason and sets score=0). So a too-tight `T` is a silent upstream suppressor that q_lcb cannot reach.
- **Alt C — make `k` itself the fitted σ-multiplier from the settlement residual (the |μ*−settled| distribution), independent of E2 verdicts.** Most principled (k = the empirical coverage radius). **Con:** couples E3 to the σ-fit (T4/W-QLCB) and risks double-counting the same tail correction; harder to reason about in isolation.

**PICK: Alt A, with the explicit guard that E3 ships LAST (after E1/E2 are settlement-validated and W-QLCB is live), and defaults to byte-identical k=1.0.** Alt B's flaw is decisive: the direction-law geometry cut fires *before* the q_lcb gate, so a too-tight ring is an upstream suppressor invisible to W-QLCB. But E3 is only safe once E2 can be trusted, so it is sequenced after. Alt C is the right *long-term* form (and converges with W-QLCB's σ-fit) but is deferred to avoid coupling two fixes; D-EDGE-3 below flags the eventual merge.

### 5.4 RED-on-revert test

`tests/strategy/test_edge_focus.py::test_k_eff_defaults_to_one_without_license` (no E2 artifact → k=1.0, byte-identical) and `::test_k_eff_widens_only_on_licensed_cell` (a LICENSED dist-2 cell → k raised so dist-2 bin admits; a NO_EDGE dist-2 cell → k stays 1.0, bin still rejected). Antibody against E3 ever widening on an unlicensed cell.

---

## 6. DEPENDENCIES, SEQUENCING, MIGRATION

### 6.1 Dependency on other workstreams

```
E1 (query)  ──► E2 (harness/verdict)  ──► gates ──► W-QLCB shadow→live promotion
                      │                              W-QLCB σ-fit (T4) candidate→live
                      └────────────────► E3 (candidate-focus) ──► widens direction_law T
```

- **E1, E2 have NO code dependency on W-QLCB or W-SUBMIT** — they read existing substrate (regret + settlement) and can be built and validated FIRST, in parallel with the sibling workstreams. They are pure read-only analysis.
- **W-QLCB depends on E2** (not the reverse): W-QLCB's shadow q_lcb cannot be promoted to live without E2's LICENSED verdict on the ring cohort. So E2 must land before any W-QLCB live promotion (but W-QLCB's *shadow* build proceeds independently).
- **E3 depends on E2 + W-QLCB-live** — it ships last (§5.3).

### 6.2 Migration / data steps

- **No schema migration** (Alt C: no new table). The regret writer already populates `would_have_won`; confirm the backfill job that sets it on newly-settled dates is running (it populated 20,009/260,829 — the unsettled remainder is expected, fills in as dates settle).
- **One backfill audit (§7 test):** validate `would_have_won` against `grade_receipt` on the historical population once, to license Alt B's trust in the field's grading.

### 6.3 Build order within W-EDGE-LOCATE

1. **E1** — the read-only query + CLI (ships immediately, zero risk, gives the operator the dated edge map TODAY showing n=5 ring / INSUFFICIENT).
2. **E2** — wrap E1 with the five INV-CAL contracts + verdict + JSON artifact.
3. **(gate active)** — W-QLCB/W-SUBMIT promotions now consult E2.
4. **E3** — candidate-focus, last, after W-QLCB is live and E2 is trusted.

---

## 7. VERIFICATION GATE (settlement-graded, the operator's DONE-shaped bar)

The workstream is correct iff:

1. **E1 reproduces the §1.2 numbers exactly** on the frozen historical population (regression fixture: the 48-row Taipei/Warsaw/NYC band-2 cohort → n_events=5, wins=2, edge_after_fee≈+0.31). RED-on-revert: any change that re-inflates to row-count counting fails `test_no_event_double_counts`.
2. **The `would_have_won`-vs-`grade_receipt` cross-check passes** on the full settled population (Alt-C audit): the regret writer's counterfactual outcome matches the canonical spine on ≥99% of events, and the mismatches are catalogued (boundary/preimage cases → law-8 follow-up). If it fails materially, E1 must switch to re-grade-of-record (Alt B becomes mandatory per-run, not optional).
3. **E2 emits the honest first verdict:** on today's data, EVERY ring cell = INSUFFICIENT_DATA (n<30). The harness must NOT license anything yet. A test asserts no LICENSED verdict is reachable below n=30.
4. **The vs-market antibody fires:** feed the +0.95-WR buy_no base-rate cohort → E2 verdict = NO_EDGE (model_brier ≥ market_brier). RED-on-revert proves base-rate is never re-branded as alpha (law 4).
5. **Forward (the real gate):** the ring distinct-event cohort grows under shadow accrual; E2 transitions a cell INSUFFICIENT_DATA → LICENSED only when n≥30 AND realized−price lo95 > 0 AND model beats market on Brier. **DONE for this workstream = E2 correctly LICENSES or NO-EDGES the ring cell at n≥30 with settlement dates and numbers** — feeding the operator's global DONE (continuous >51% after-cost on traded markets). The proof is the verdict transition on real settlements, never the report running once.

**Prefer settlement-graded throughout:** every E2 verdict is derived solely from `settlement_outcomes(VERIFIED)` via `grade_receipt`. No in-sample promotion (law 5); walk-forward enforced by INV-CAL-3.

---

## 8. RISK + ROLLBACK

| Risk | Likelihood | Mitigation | Rollback |
|---|---|---|---|
| E1/E2 grade off a mis-preimaged `would_have_won` → locate phantom edge (law 8) | MED | §7.2 cross-check gates Alt-B trust; mismatch → forced re-grade | E1/E2 are read-only JSON artifacts — delete the artifact, no live impact |
| Thin sample → harness licenses on noise | HIGH if N_min ignored | N_MIN_EVENTS=30 hard floor reusing the coverage min_n; INSUFFICIENT_DATA default | verdict recomputed from settlement each run; no persisted authority to corrupt |
| E3 widens `T` on a wrongly-licensed cell → admits wrong-bin YES | MED | E3 ships last, defaults k=1.0, gated on E2-LICENSED; `capital_efficiency` + corrected q_lcb still gate each admitted candidate | revert `k_eff` to constant 1.0 — one-line, byte-identical to today |
| Confusion with the fills-realized `edge_observation.py` | LOW | explicit provenance verdict (§2) QUARANTINING it for this purpose; new module is separate | n/a |
| Regret writer stops populating `would_have_won` | LOW | §6.2 confirms backfill job; E2 falls to INSUFFICIENT_DATA (fail-safe, never false-license) | n/a |

**Rollback posture:** E1/E2 are pure read-only analysis emitting JSON evidence — there is NOTHING to roll back in the live path. E3 is the only live-touching piece and reverts to a one-line constant. This workstream cannot break live by construction (laws-aligned: it is a grading instrument, not a gate).

---

## 9. SELF-CHECK — systematically correct, or a 1-order hack?

**Systematically correct.** The test of law 2 (a fix just to fill one order = FAILURE): W-EDGE-LOCATE deliberately does NOT try to fill the Taipei/Warsaw/NYC ring markets — it does the opposite, refusing to license them because n=5 < 30. It builds the *instrument* that decides where edge is, event-level and walk-forward, so that *every future* promotion is settlement-gated rather than declared. It is the antibody against exactly the failure mode the row-count illusion (§1.2) would otherwise cause: a system that mistakes 28 cycles of one lucky market for +0.49 alpha and unblocks favorite-buying as "edge."

It composes with, rather than duplicates, the sibling workstreams: E2 is the *gate* on W-QLCB's q_lcb fix and W-SUBMIT's re-decision; E3 modulates the *one* existing `direction_law` tunable from settlement, eliminating a hardcoded constant (task #64) rather than adding a gate. Net gate count does not increase (E1/E2 add zero live gates; E3 replaces one constant with one fitted boundary). It honors law 8 (re-grades through the preimage-correct spine before any edge claim), law 4 (the Brier antibody rejects base-rate), law 5 (settlement-only, walk-forward), and law 1 (a dated "no edge here" is a first-class output).

**The one honest weakness, surfaced not hidden:** the entire workstream's forward value depends on the ring distinct-event cohort actually growing to n≥30 — which depends on the *market continuing to offer* near-center ring mispricings AND on W-QLCB unblocking the shadow admissions that accrue them. If neither happens, E2 will sit at INSUFFICIENT_DATA indefinitely and emit, correctly, "no licensable edge located." That is not a defect of the instrument — it is the instrument doing its job and telling the operator, with numbers, that the suppressed-alpha pool the system chased for 100 patches does not exist at tradeable size. The strategy-of-record's honest bottom line, made measurable.

---

## 10. OPEN DECISIONS FOR P3 (this workstream's forks)

- **D-EDGE-1 — Alt-B re-grade per-run vs trust `would_have_won`.** Resolved conditionally in §3.3: trust the field IF §7.2 cross-check passes ≥99%, else re-grade per run. P3 must run the cross-check to close it.
- **D-EDGE-2 — lead-bucket granularity.** E1 buckets by lead (forecast horizon: A_24h/B_48h matching `sigma_scale_fit.json`'s scheme). Open: whether to sub-bucket Day0 separately (the obs/nowcast lane has different coverage). Lean: reuse the σ-fit's A/B buckets for v1; add Day0 only if its cohort is large enough to grade independently.
- **D-EDGE-3 — E3's eventual merge with W-QLCB's σ-fit (Alt C of §5.3).** The fitted ring radius (E3) and the σ-fit tail correction (T4) are two views of the same settlement-residual distribution. Long-term they should be one authority (operator's 大一统). Lean: keep separate in v1 (E3 = verdict-gated T-widen; T4 = point-q shape), merge in a later K-cut once both are settlement-proven, to avoid coupling two unvalidated fixes.
- **D-EDGE-4 — the `would_have_filled` dimension.** Regret rows also carry `would_have_filled` (the counterfactual fill, not just win). v1 grades edge assuming a fill; a real cell needs BOTH would-have-filled AND would-have-won. Lean: report `would_have_filled` rate per cell as a SEPARATE column (executability), do not fold it into the edge verdict yet — W-SUBMIT owns fill-realism; surface it here for the operator, gate on it in P3 once W-SUBMIT lands.

*End of P2 W-EDGE-LOCATE. Read-only plan; no production code or daemon changed. Every empirical claim cited to file:line, table, or query+counts run this session.*
