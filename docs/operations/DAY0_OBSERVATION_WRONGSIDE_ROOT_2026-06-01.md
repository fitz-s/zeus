# DAY0 Observation Wrong-Side Root + Structural Fix Design

- Created: 2026-06-01
- Last reused/audited: 2026-06-01
- Authority basis: operator live-shadow incident (Paris June-1 buy_no on observed low=14°C);
  this doc supersedes/guides the concurrent sonnet executor (ae5fe38…) market_end_at WIP.
- Status: READ-ONLY investigation. No code changed by this author.

---

## 12-LINE DECISION SUMMARY

1. **VERDICT: WRONG-SIDE (catastrophic-if-unshadowed), not settled-no-edge.** Paris "lowest temp be 14°C, June 1", buy_no, q_NO=0.9968. Forecast members (ECMWF snap 1152237) are 11.6–13.0°C, **0% at 14°C** → forecast confidently says low≠14. Operator states observed low IS 14°C → YES realized → buy_no is the losing side. The forecast is ~1.5–2°C cold-biased AND blind to the already-observed extremum.
2. **ROOT (category, not instance):** the EDLI live-decision path is **phase-blind**. It never consults the existing authoritative `MarketPhase` machinery (`src/strategy/market_phase.py`), so it admits markets that are already **POST_TRADING** (weather endDate = 12:00 UTC of target_date per F1 invariant). Paris decided at 16:06 UTC — **4 h past** its 12:00 UTC close.
3. The day0 observed-extremum absorbing mask EXISTS (`_day0_absorbing_mask`, reactor 3640) but fires **only** when `family.event_type == "DAY0_EXTREME_UPDATED"` (reactor 3205/3223/2884) — a path gated **OFF** in forecast_only (`day0_extreme_trigger_enabled=False`, `main.py:_assert_edli_live_scope` hard-raises if on).
4. `finalization_time` exists in `SettlementSemantics` but flows into a decision **only** via `_edli_day0_settlement_semantics` → `Day0ExtremeUpdatedTrigger` (`main.py:3789`), also gated off. The `FORECAST_SNAPSHOT_READY` trigger has **zero** finalization/same-day/phase checks.
5. Blast radius: **13 distinct same-day (target_date==today) candidates this cycle, ALL buy_no, all submit-ready** — ~1/3 of live candidates. Same defect class.
6. The daemon re-fires every minute on a **cached, already-ended** snapshot (`ems2-1d2aae35…`, market_end_at=12:00) via `redecision_continuous_enabled`.
7. **Predecessor solution already exists:** `market_phase_for_decision()` returns `POST_TRADING` the instant `decision_time_utc >= polymarket_end_utc`. This is the category-killer; EDLI just never calls it.
8. **STRUCTURAL FIX:** at EDLI family admission, compute `MarketPhaseEvidence` and **fail-closed reject any family not in TRADING/SETTLEMENT_DAY** (reject POST_TRADING, RESOLVED, and phase=None/unknown). Gate at the single admission chokepoint, not per-bin.
9. The executor's uncommitted `market_end_at > fresh_at` predicate is the **right placement, wrong authority**: it depends on an ephemeral daemon-truncated table where a retained NULL `market_end_at` silently passes the gate. Replace the raw column compare with the F1-fallback-bearing phase evidence so a missing endDate still resolves to 12:00 UTC (fail-closed), never NULL-passes.
10. Wiring day0 observation INTO forecast_only is the WRONG primary fix (more surface, needs fresh obs that aren't even in the decision DBs — Paris obs stop at 2026-05-28). Exclusion is correct: forecast_only must not trade a window it cannot observe.
11. RED relationship test: a same-day family past 12:00-UTC-of-target_date yields **NO** candidate; a future-date family still yields its candidate. Assert at the admission boundary, with distinct keys per family.
12. Antibody = the phase-gate test in CI + `MarketPhase` import made mandatory on the EDLI admission path; makes "trade a closed/observed market" unconstructable, not patched.

---

## 1. VERDICT — WRONG-SIDE, WITH SETTLEMENT PROOF

**Receipt (live shadow, `edli_no_submit_receipts`, zeus-world.db):**
- city=Paris, bin="Will the lowest temperature in Paris be 14°C on June 1?", metric=low
- direction=**buy_no**, q_live=0.9968350111860362, trade_score=+0.863, target_date=2026-06-01
- condition_id `0xba8fe243…dc18e6d`, causal snapshot 1152237, decided 2026-06-01T16:06–16:10 UTC (re-firing per minute).

**Forecast that produced q_NO=0.997** (ensemble_snapshots, snap 1152237, source ecmwf_open_data, n=51):
- members min/mean/max = **11.62 / 12.42 / 13.00 °C**; rounded distribution `{12:31, 13:20}`; **0% of members at 14°C**.
- So the model assigns ~0 probability to "low = 14°C" → q_NO ≈ 0.997. The model expects 12–13°C.

**Bin settlement semantics — exact-degree, not threshold.** Polymarket weather bins are either exact ("be X°C") or shoulder ("X°C or higher/lower"). The same live cycle contains both forms ("Tokyo … 29°C **or higher**", "Warsaw … 26°C **or higher**" vs "Paris … **be 14°C**"), confirming "be 14°C" settles YES iff rounded(low) == 14 (a single-degree bin: low==high==14). With operator-confirmed observed low = 14°C → **YES is the realized outcome**.

**Reconciliation:** q_NO=0.997 (forecast) vs realized YES (observation) are in direct contradiction. buy_no therefore buys the **losing** side at c_cost≈ (book-derived) for a ~0 true payoff. This is **wrong-side**, driven by (a) ~1.5–2°C cold bias and (b) the system never absorbing the already-observed extremum. It is NOT "technically correct but already settled" — the position would resolve to a loss, not a no-edge wash.

---

## 2. ROOT — WHY A forecast_only SAME-DAY MARKET TRADES BLIND

### 2.1 The day0 observed-extremum mask exists but is OFF-path
- `_day0_absorbing_mask` (reactor `event_reactor_adapter.py:3640`) zeroes bins ruled out by the observed rounded extreme; `_apply_day0_mask_to_probability_vector` (3665) renormalizes.
- It is applied **only** inside the `family.event_type == "DAY0_EXTREME_UPDATED"` branches: `_market_analysis_from_event_snapshot` lines **3205–3206, 3223**, and the generated-probability branch at **2884–2895**.
- The forecast_only candidate arrives as **`FORECAST_SNAPSHOT_READY`** (dispatch `event_reactor_adapter.py:143` / `2874`), which calls `_canonical_probability_and_fdr_proof` with **no mask** and no `allow_latest`. Observed truth never enters.

### 2.2 The DAY0 path itself is gated OFF in this scope
- `config/settings.json` edli_v1: `edli_live_scope="forecast_only"`, `day0_extreme_trigger_enabled=False`, `day0_hard_fact_live_enabled=False`, `day0_authority_catchup_scanner_enabled=False`.
- `src/main.py:494 _assert_edli_live_scope` **hard-raises `DAY0_OUT_OF_SCOPE_FOR_PR332`** if either day0 flag is set. So the only observation-aware path is structurally unreachable.

### 2.3 `finalization_time` never reaches the forecast_only decision
- `finalization_time` lives in `SettlementSemantics` (`src/contracts/settlement_semantics.py:113`) but its **only** decision-time consumer is `_edli_day0_settlement_semantics` (`src/main.py:3797`, hardcoded `"12:00:00Z"`), wired into `Day0ExtremeUpdatedTrigger.scan_authority_rows` (`main.py:3789`) — the gated-off day0 scanner.
- `src/events/triggers/forecast_snapshot_ready.py` has **zero** references to `finalization`, `same_day`, `target_date==today`, or phase. The forecast_only admission has no window-closing check. (The per-city #60 finalization is NOT honored here either — the live value is the hardcoded 12:00Z, only reachable on the off path.)

### 2.4 The admission deliberately disables market freshness ("market won't disappear")
- `_latest_snapshot_rows_for_event_family` (`event_reactor_adapter.py:3775`) is called by both entry gates (lines **195/507**) with `require_fresh=False`, under the operator design law *"freshness 针对价格不针对市场; 市场捕捉了不会突然消失"* (freshness is for price, not market; a captured market won't suddenly vanish).
- **This assumption is false for day0/same-day markets:** a weather market *does* "disappear" — it ends at 12:00 UTC of target_date (F1). The admission has no counter-rule, so an ended market is re-admitted indefinitely.

### 2.5 Continuous re-decision amplifies it
- `redecision_continuous_enabled=True`, `redecision_max_per_cycle=50`. Latest Paris receipts (16:06→16:10) all reuse cached snapshot `ems2-1d2aae35…` whose `market_end_at=2026-06-01T12:00:00+00:00`. The loop re-emits each minute on an **already-ended** market.

### 2.6 The predecessor solution the path bypasses
- `src/strategy/market_phase.py:128 market_phase_for_decision` returns **`POST_TRADING`** when `decision_time_utc >= polymarket_end_utc`; `_f1_fallback_end_utc` (203) derives 12:00 UTC of target_date when endDate is absent; `MarketPhaseEvidence` (`src/strategy/market_phase_evidence.py`, created 2026-05-04) carries phase + provenance and is explicitly designed for *"reject phase=None under flag ON for live entries."*
- **Grep proof:** `MarketPhase` / `market_phase_for_decision` / `POST_TRADING` / `MarketPhaseEvidence` appear **0 times** in `src/engine/event_reactor_adapter.py` and `src/events/`. The EDLI live path was built without the phase authority that already makes this category impossible.

---

## 3. BLAST RADIUS

Distinct candidates in the current cycle by target_date: **2026-06-01: 13, 06-02: 12, 06-03: 14.**

All 13 same-day (target_date == today, 2026-06-01) distinct candidates are **buy_no**, all submit-ready (`source_status=LIVE_ELIGIBLE`, `proof_accepted`, `trade_score_positive`):

| city | metric | dir | q_live | bin |
|---|---|---|---|---|
| Paris | low | buy_no | 0.997 | be 14°C |
| Seoul | low | buy_no | 0.996 | be 16°C |
| Seoul | high | buy_no | 0.509 | be 25°C |
| Sao Paulo | high | buy_no | 0.916 | be 22°C |
| Tel Aviv | high | buy_no | 0.917 | be 28°C |
| Shenzhen | high | buy_no | 0.837 | be 33°C |
| Tokyo | high | buy_no | 0.913 | 29°C or higher |
| Toronto | high | buy_no | 0.978 | be 20°C |
| London | low | buy_no | 0.217 | be 14°C |
| Wellington | high | buy_no | 1.000 | be 21°C |
| Warsaw | high | buy_no | 1.000 | 26°C or higher |
| Wuhan | high | buy_no | 1.000 | be 32°C |
| Shanghai | high | buy_no | 1.000 | be 30°C |

Paris's `market_end_at` was 12:00 UTC (captured 12:01); decisions ran at 16:06–16:10 UTC → every same-day exact-degree bin is decided in **POST_TRADING**, blind to its observed extremum. The systematic buy_no skew is the cold-bias signature: a cold forecast pushes mass below the bin, inflating q_NO everywhere. Same-day amplifies it to wrong-side because the truth is already knowable.

(Shadow only — `real_order_submit_enabled=False` — so no capital lost. But this is exactly the class of defect that must die before unshadow.)

---

## 4. STRUCTURAL FIX DESIGN (CATEGORY-KILLER)

### 4.1 The rule
> **forecast_only MUST NOT admit any market family whose trading/observation window has closed or cannot be confirmed open.** Concretely: reject any family whose `MarketPhase` at `decision_time` is `POST_TRADING`, `RESOLVED`, or `unknown`/`None`. Admit only `PRE_SETTLEMENT_DAY`, `SETTLEMENT_DAY` (and `PRE_TRADING` only if start is explicitly known-future, else fail-closed). Fail-closed on any phase-determination error.

This makes "trade a market whose settlement-relevant extremum window is closing/closed without the day0 observation" **unconstructable** in forecast_only, regardless of book contents, active/closed flags, or cached snapshots.

### 4.2 Where to gate (single chokepoint)
At EDLI **family admission**, inside the FORECAST_SNAPSHOT_READY entry path, co-located with `_latest_snapshot_rows_for_event_family` consumers:
- `event_reactor_adapter.py:195` (`source_eligibility` / topology proof), and
- `event_reactor_adapter.py:507` (`_canonical_probability_and_fdr_proof` family build).

Add a phase gate computed once per family from `family.city`, `family.target_date`, `city_timezone`, `decision_time`, and the snapshot's `market_end_at` (falling back to F1 12:00 UTC when absent):

```
evidence = market_phase_evidence_from_market_dict(   # existing module
    market=selected_snapshot_row,                    # carries market_end_at/endDate
    city_timezone=<from city config or snapshot.city_timezone>,
    target_date_str=family.target_date,
    decision_time_utc=decision_time,
    uma_resolved=<optional on-chain resolve flag>,
)
if evidence.phase in {POST_TRADING, RESOLVED} or evidence.phase is None:
    return EventSubmissionReceipt(False, ..., reason="EVENT_BOUND_MARKET_PHASE_CLOSED")
```

Place it **before** probability/FDR/Kelly so closed families never reach scoring (and never re-fire through continuous re-decision). Emit a `no_trade_regret_events` row with `reason=MARKET_PHASE_CLOSED` + the evidence provenance for observability.

### 4.3 Why this supersedes the executor's `market_end_at > fresh_at` predicate
The executor's uncommitted predicate (`event_reactor_adapter.py:3816`) is **correct in placement** (entry path, non-None `fresh_at` at lines 195/507) but **wrong in authority and robustness**:
- It reads `market_end_at` raw from the **ephemeral, daemon-truncated** `executable_market_snapshots` table (observed empty mid-cycle). A retained row with **NULL** `market_end_at` **silently passes** (`market_end_at IS NULL OR market_end_at > ?`) → the exact NULL-fail-OPEN hole that lets a closed market through.
- It encodes "market closed" as a raw timestamp compare, duplicating logic the typed `MarketPhase` already owns (start/end/settlement-day/UMA-resolved, with provenance and the F1 fallback). Divergence risk: a future endDate-shape change updates `market_phase.py` but not this predicate.

**Reconcile, don't compete:** keep the predicate as a cheap SQL pre-filter IF its NULL branch is flipped to **fail-closed** (`market_end_at IS NOT NULL AND market_end_at > ?`, OR substitute the F1 12:00-UTC fallback in SQL), and add the `MarketPhaseEvidence` gate as the authoritative admission check. The phase gate is the antibody; the SQL predicate is an optimization.

### 4.4 Why NOT "wire day0 observation into forecast_only" (the alternative)
| Option | Pros | Cons |
|---|---|---|
| **A. Phase-exclude closed/same-day families (chosen)** | Category-killing; small surface (one admission gate); reuses audited `MarketPhase`/`MarketPhaseEvidence`; fail-closed by construction; no new data dependency | Forgoes any legitimate same-day edge before window close (acceptable: forecast_only by definition shouldn't trade what it can't observe) |
| **B. Inject day0 observed extremum into forecast_only scoring** | Could capture pre-close same-day edge | Large surface; resurrects the gated-off day0 path that `_assert_edli_live_scope` forbids; **requires fresh observations that are not in the decision DBs** (Paris obs stop at 2026-05-28 in all DBs); reintroduces unit/DST/source-authority risk the day0 gate exists to manage; does nothing for **post-close** families (the actual Paris case is 4 h post-close — no forecast+obs blend is valid then) |

Option B is a future capability (day0 trading) that belongs behind its own scope flag and full observation-authority gating — **not** a patch to forecast_only. The correctness fix is exclusion (A).

### 4.5 Downstream trace (10-step consequence check)
1. Closed families rejected at admission → 2. no probability/FDR/Kelly computed → 3. no submit-ready receipt → 4. continuous re-decision finds nothing to re-fire (cached belief never enqueued for closed family) → 5. `no_trade_regret_events` gains `MARKET_PHASE_CLOSED` rows (observable) → 6. future-date families unaffected (phase=PRE_SETTLEMENT_DAY/SETTLEMENT_DAY) → 7. shoulder vs exact bins unaffected (gate is family-level, pre-bin) → 8. unshadow gate cleaner (no wrong-side same-day contamination in #24) → 9. when day0 scope later activates, it owns same-day; forecast_only stays excluded (scopes disjoint) → 10. antibody test in CI prevents regression if a future refactor drops the gate.

---

## 5. RED RELATIONSHIP TEST SPEC (relationship-first, before implementation)

**Invariant under test (cross-module: market-phase clock × EDLI admission):**
*When a family's target_date is same-day AND `decision_time` is at/after the market's end anchor (explicit `market_end_at`, else F1 12:00 UTC of target_date), the EDLI forecast_only admission MUST produce NO candidate. A future-date family with identical structure MUST still produce its candidate.*

```python
# tests/engine/test_edli_forecast_only_phase_exclusion.py
# Created: 2026-06-01
# Authority basis: DAY0_OBSERVATION_WRONGSIDE_ROOT_2026-06-01.md §5

def test_same_day_post_finalization_family_yields_no_candidate():
    # GIVEN a FORECAST_SNAPSHOT_READY family for city=Paris, metric=low,
    #       target_date = decision_date (same-day),
    #       executable snapshot market_end_at = 12:00 UTC of target_date,
    #       decision_time = 16:00 UTC of target_date  (4h post-close, POST_TRADING),
    #       forecast members all < bin (q_NO high), book present.
    # WHEN the forecast_only admission path runs.
    # THEN result is NO_SUBMIT-absent: receipt is None / reason == "EVENT_BOUND_MARKET_PHASE_CLOSED";
    #      a no_trade_regret_events row exists with reason MARKET_PHASE_CLOSED and phase_source provenance.
    assert receipt is None  # RED pre-fix: a buy_no receipt with q_live~0.997 is produced.

def test_future_date_family_still_yields_candidate():
    # GIVEN the SAME structure but target_date = decision_date + 2 (future),
    #       market_end_at = 12:00 UTC of that future target_date (phase=PRE_SETTLEMENT_DAY).
    # WHEN the forecast_only admission path runs.
    # THEN a candidate IS produced (gate must not over-fire on legitimate forward markets).
    assert receipt is not None and receipt.accepted

def test_missing_market_end_at_falls_back_to_f1_and_excludes_if_past():
    # GIVEN same-day family, market_end_at = NULL (absent), decision_time = 16:00 UTC.
    # THEN F1 fallback (12:00 UTC target_date) applies → POST_TRADING → NO candidate.
    #      (Guards the NULL-fail-OPEN hole in the raw SQL predicate.)
    assert receipt is None
```

**Distinct-keys requirement (per memory lesson):** the same-day and future-date families MUST use **distinct** condition_ids / family_ids / target_dates so the test proves per-family isolation and cannot pass via a shared-key artifact. The test must be RED on current HEAD (Paris buy_no receipt reproduced) and GREEN only after the phase gate lands.

---

## 6. REFERENCES (file:line)

- `config/settings.json` edli_v1 — `edli_live_scope=forecast_only`, `day0_*_enabled=False`, `redecision_continuous_enabled=True`.
- `src/main.py:494` `_assert_edli_live_scope` — hard-raises `DAY0_OUT_OF_SCOPE_FOR_PR332`.
- `src/main.py:3789` / `3797` `_edli_day0_settlement_semantics` — only decision-time consumer of `finalization_time`, wired to the gated-off day0 scanner; hardcoded `"12:00:00Z"`.
- `src/engine/event_reactor_adapter.py:143` / `2874` — FORECAST_SNAPSHOT_READY dispatch (no mask).
- `…:2884` / `3205-3206` / `3223` — day0 absorbing mask applied ONLY for `DAY0_EXTREME_UPDATED`.
- `…:3640` `_day0_absorbing_mask`, `…:3665` `_apply_day0_mask_to_probability_vector` — exist, off-path for forecast_only.
- `…:195` / `…:507` — entry gates call `_latest_snapshot_rows_for_event_family(require_fresh=False, fresh_at=…)`.
- `…:3775-3818` `_latest_snapshot_rows_for_event_family` — `require_fresh=False` design law; **executor's uncommitted** `market_end_at IS NULL OR market_end_at > ?` predicate (3812-3817), NULL branch fails OPEN.
- `src/contracts/settlement_semantics.py:113` — `finalization_time` field (default `"12:00:00Z"`).
- `src/strategy/market_phase.py:128` `market_phase_for_decision` — returns POST_TRADING when `decision_time_utc >= polymarket_end_utc`; `…:203` `_f1_fallback_end_utc` (12:00 UTC of target_date); `…:226` `market_phase_from_market_dict`.
- `src/strategy/market_phase_evidence.py` — `MarketPhaseEvidence` (phase + provenance; designed for live fail-closed). **Zero** EDLI/reactor references (grep-proven).
- Live data: `edli_no_submit_receipts` (zeus-world.db) Paris row, snap 1152237; `ensemble_snapshots` (zeus-forecasts.db) snap 1152237 members 11.6–13.0°C / 0% at 14°C; `executable_market_snapshots` Paris cid market_end_at=2026-06-01T12:00:00+00:00; Paris `observation_instants` stop at 2026-05-28 across all DBs.
