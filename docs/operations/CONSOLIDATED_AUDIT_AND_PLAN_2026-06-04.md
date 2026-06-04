# Zeus Live — Consolidated Audit Synthesis & Implementation Plan
**Date:** 2026-06-04 · **Author:** orchestrator (Opus) · **Inputs:** 8 parallel deep-audit agents (1 debugger + 1 session-critic + 4 class-auditors + 2 open-mandate explorers), all read-only, all evidence-required against the live DBs / Polymarket API.

> This document is **more than the raw audits**: it dedups across agents, groups the ~20 findings into the **K structural roots** behind them (Fitz constraint #1: N symptoms = K decisions, K≪N), separates FIXED-this-session from OPEN, and gives a sequenced, arm-aware implementation plan to the first real FILL.

---

## 0. The one-paragraph truth

The system is **live, healthy, disarmed (`real_order_submit_enabled=false`), and has produced zero fills** — not because the calibration is wrong (the q-seam is unusually well-defended) but because of **four structural throttles + scopes** that each silently shrink what can trade, plus a handful of swallowed-error bugs that puncture the narrow surviving window. The operator's instinct ("more obvious bugs no one sees") is vindicated: the bugs run fine and pass tests; they are semantic/structural, not crashes. The biggest single cause of "no fill" is **deliberate-but-unfinished scopes** (day0 settlement-day trading is `RuntimeError`-gated out; orderbook ingestor was config-off; arm is off), not a calibration defect.

---

## 1. The K structural roots (synthesis)

Every finding below collapses into **6 structural decisions**:

| # | Structural root | Symptoms it explains | Status |
|---|---|---|---|
| **R1** | **Coverage emission is order-biased** (FSR `ORDER BY snapshot_id DESC LIMIT N` under uniform `computed_at`) | "3–13 city universe", A–L cities never trade | ✅ FIXED (fairness rotation → 49 cities) |
| **R2** | **The evaluable universe is sized off a 30s price-freshness TTL, not market identity** (8/16-family refresh-cap × 30s TTL) | even with 49 emitting, only ~8–16 are *simultaneously* tradeable; raising the cap overruns the 60s cycle | ⚠️ PARTIAL (cap 8→16; proper decouple = R2 task #178) |
| **R3** | **Settlement-day is an un-owned dead zone** — forecast-only admits only `PRE_SETTLEMENT_DAY`; the day0 scope that owns settlement-day is `RuntimeError`-gated out (`DAY0_OUT_OF_SCOPE_FOR_PR332`) | 64% of rejections = `EVENT_BOUND_MARKET_PHASE_CLOSED:settlement_day`; ~half of all evaluations can never trade | ❌ OPEN — **cannot be flag-flipped**, needs PR332 day0 build (task #179-pre) |
| **R4** | **Swallowed DB/schema errors become trade-rejections** (platt UNIQUE collision, `mainstream` column drift) puncturing the only tradeable window | 858× platt UNIQUE + 37× missing-column as no-trade reasons | ❌ OPEN (tasks #174, #175) |
| **R5** | **One belief computed on two domains** (normalization seam; lead-pooled bias; metric/season/unit keys) | lcb>point inversion (43%), lead-mismatch bias, the metric/season-crossing class | ◐ MOSTLY FIXED (metric+season+unit done; lcb-assert #176, lead-bias #177 open) |
| **R6** | **Detect-but-don't-enforce** (wrong-side buy_no, mainstream agreement are reference-only) | 396 SHORTING_LIKELY + 13 buy_no-on-modal reach trade_score>0 | ◐ direction-law modal-veto FIXED; mainstream enforce-on-arm = arm-coupling (task) |

**The single highest-leverage truth:** R3 (settlement-day dead zone) + R2 (freshness throttle) together explain "almost nothing trades." R1 was the visible 3-city symptom and is fixed. Calibration (the thing 3 days of work chased) was **not** the blocker.

---

## 2. Full findings (deduped, all agents, file:line, severity, status)

### CRITICAL / structural
- **R1 universe emission bias** — `src/events/triggers/forecast_snapshot_ready.py:472` (`ORDER BY …snapshot_id DESC LIMIT ?`), fairness round-robin `:142`/`:430` gated by `coverage_fairness_emit_enabled` (was OFF) + cycle source never wired at `src/main.py:3941`. **FIXED** (commit 2965c88: thread `cycle-N` source when fairness ON; flag ON; emit_limit 50; refresh_cap 16).
- **R3 settlement-day dead zone** — `src/strategy/market_phase.py:287` `FORECAST_ONLY_ADMIT_PHASES={PRE_SETTLEMENT_DAY}`; day0 owner `RuntimeError("DAY0_OUT_OF_SCOPE_FOR_PR332")` on enable. **OPEN — structural; do NOT flag-flip (crashes boot, verified 2026-06-04).**
- **R2 freshness-throttle** — `src/main.py:2704` `_FAMILY_REFRESH_CAP`; `src/contracts/executable_market_snapshot.py:27` 30s TTL; reader `event_reactor_adapter.py:5605`. **PARTIAL.**
- **R4a platt UNIQUE** — `src/calibration/store.py:540` save collides on unique index → swallowed → no-trade reason (858×, ongoing). **OPEN.**
- **R4b `mainstream` column drift** — `edli_no_submit_receipts` missing column referenced by writer (37×). **OPEN.**

### MAJOR
- **R5 lcb>point inversion** — 26,017/60,411 (43%) historical buy_no had `q_lcb_5pct>q_live`; clamp `market_analysis.py:1128` on un-normalized `p_posterior` vs recorded normalized `q_live` (`event_reactor_adapter.py:3463`). ~0 at HEAD (incidental), latent domain mismatch remains. **OPEN (#176).**
- **R5 lead-mismatch bias** — `read_bias_model` (`ens_bias_repo.py`) no lead param; `LEGACY_POOLED` 60–144h bias applied to 24h live forecast (~+0.4–0.6°C net warp). **OPEN (#177).**
- **R6 wrong-side buy_no / mainstream reference-only** — `mainstream_agreement.py` computes `direction_agrees_*` correctly but enforcement is double-gated OFF; 396 SHORTING_LIKELY + 13 buy_no-on-modal reached trade_score>0. **direction-law modal-veto FIXED** (commit: market_analysis veto); mainstream submit-enforce = couple to arm (below).
- **buy_no q degeneracy** — median q_live 0.999 / 84% >0.95 on OTM tail bins (manufactured edges); contained by FDR+Kelly+the modal veto, but it's the flood. **Calibration-limited; EMOS dispersion is the structural answer (deployed).**

### MINOR / latent / observability
- `_edli_q_source` dead write (`event_reactor_adapter.py:3752`) — no reader → can't audit which calibrator served a receipt (#120 observability gap).
- `bin_label`/`mainstream_bin_label` flat column ≠ `receipt_json.bin_label` (querying the column gives the wrong traded bin).
- bias-store (SH-flip+month) vs EMOS-store (NH-month) opposite season conventions one import apart — add a freeze-test.
- Qingdao `config.cities.json airport_name="Liuting"` stale (resolving ZSQD matches; label misleads next audit).
- `settlement_resolution.from_settlement_row` unit guard — **FIXED** (fail-closed on settlement_unit≠grid_unit).
- evaluator.py / replay.py boundary surfaces NOT yet traced (next-pass).
- M3/M4: user-channel (fills) websocket crash-loop; 244k stuck-pending book events from the (now re-enabled) ingestor need dead-lettering.

---

## 3. FIXED + deployed this session (commits on live `main`)
- Metric-crossing (mainstream gate + EMOS fail-closed) · season-crossing C1/C2 (canonical NH-month `emos_season` + 3-key readers) · LOW EMOS fitted+served · 2026-regime gate on LOW (HIGH untouched, golden-safe) · source-probe both extrema · settlement unit-guard · **R1 universe fairness (25→49 cities, live-confirmed)** · refresh_cap 8→16 (no cycle overrun) · **direction-law modal-veto** (buy_no on our forecast bin unconstructable) · PR #385 merged.

## 4. OPEN — sequenced implementation plan
**Phase A (unblock the surviving window — safe, mechanical, no arm):**
1. **#174 platt UNIQUE** → idempotent upsert (`INSERT … ON CONFLICT DO UPDATE`) + stop swallowing DB errors into rejection reasons.
2. **#175 `mainstream` column** → migration to add the column (or repoint writer); verify receipts write.
3. **#176 lcb≤q_live antibody** → fail-closed assert at adapter restore + align clamp domain.

**Phase B (correctness before any capital):**
4. **#177 lead-mismatch bias** → lead_bucket in bias key + fail-closed when snapshot lead ∉ band.
5. **#178 freshness-decouple (R2)** → size universe off identity (`require_fresh=False`), enforce 30s only at submit; batch/async price warm (no serial overrun).
6. Arm-coupling: `real_order_submit_enabled⟹mainstream_agreement_enforce_on_submit` (boot-guard refuses to arm with enforce OFF).

**Phase C (settlement-day scope — the big structural build, R3):**
7. **PR332 day0**: finish the observation-aware settlement-day path so day0 owns `SETTLEMENT_DAY` (it currently `RuntimeError`s). This is a build, not a flag. Until done, the system can only trade `PRE_SETTLEMENT_DAY` (D-1) markets — which is acceptable for the first fills.

**Phase D (the fill — #179):**
8. With A–B done + scopes that ARE safe online, find ONE candidate clearing EVERY real gate (phase, trade_score, FDR, Kelly, cert, mainstream internal+external, direction-law) with positive after-cost edge. Verify it matches our forecast AND external mainstream (the ARM condition). **Then arm → real fill → 120-min 守護.**

## 5. The arm line (rule 6 — do not cross blind)
Arming = `real_order_submit_enabled=true`. The ARM CONDITION (operator): system correct AND a shadow candidate matches internal AND external mainstream forecast, direction-law-correct. **Arming before Phase A–B = ruin** (a manufactured/lead-warped/inverted-lcb edge sized by Kelly is the most expensive mistake). The first fill must trade a `PRE_SETTLEMENT_DAY` market (day0 stays out until PR332).

## 6. My mistakes this session (corrections, so the next session doesn't repeat)
- **Wrong DB:** declared "market tables empty" by querying `zeus-world.db` for tables that live in `zeus_trades.db`/`zeus-forecasts.db` (K1 split). Market side was healthy.
- **refresh_cap=50** overran the 60s cycle → reverted to 16.
- **Re-enabled day0 by flag → crashed boot** (`DAY0_OUT_OF_SCOPE_FOR_PR332`). day0 is a build (PR332), not a toggle. Reverted; daemon recovered.
- Briefly mis-called the fairness fix "wrong path" right before the monitor confirmed 49 cities.
- Over-counted direction-law violations (used `|traded−our_point|≤1` instead of `==argmax`); the real veto is on the modal bin only.
