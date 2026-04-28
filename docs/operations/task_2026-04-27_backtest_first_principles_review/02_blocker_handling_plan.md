# 02 — Blocker Handling Plan

Created: 2026-04-27
Last reused/audited: 2026-04-27
Authority basis: `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md` §3 (39-finding registry), `docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/17_apply_order.md` (P0→P4 sequencing), live disk probes 2026-04-27
Status: planning evidence; not authority.

---

## 1. Purpose

The 4-decision design in `01_backtest_upgrade_design.md` proves that **D1+D3+D4 (purpose-split, sentinel sizing, decision-time provenance typing) are pure code work that can ship today, while D2 (PnL gating) and the entire ECONOMICS purpose are blocked by data-layer issues**.

This document enumerates every blocker, classifies it, and lays out who can unblock what, in what order, and which tracks can run in parallel. The aim is to convert the existing forensic P0→P4 register into a backtest-shaped action plan — not duplicate it, but project it onto the backtest axis.

---

## 2. Blocker classification axes

| Axis | Possible values | Why it matters |
|---|---|---|
| **Type** | `code`, `data`, `governance`, `external`, `operator-decision` | Determines who can act |
| **Owner** | `agent`, `operator`, `data-engineering`, `polymarket-side` | Who actually does the work |
| **Unblocks** | which backtest purpose (`SKILL` / `ECONOMICS` / `DIAGNOSTIC`) | Why the work matters here |
| **Parallelism** | `sequential` / `parallel-safe` | Can run alongside other tracks |
| **Reversibility** | `reversible` / `one-way` | Drives risk tolerance |

---

## 3. Blocker register

### 3.A — Empty WU observation provenance (39,431 of 39,437 rows = 99%)

**Type:** `data` + `operator-decision`
**Owner:** `data-engineering` + `operator`
**Unblocks:** `SKILL` (partially — provenance gates training data eligibility), `ECONOMICS` (fully)
**Reversibility:** mostly one-way (quarantine label is reversible; payload backfill is one-way once written)

**Disk truth (verified 2026-04-27):**

| Source | Total rows | Empty provenance | Rate |
|---|---|---|---|
| `wu_icao_history` | 39,437 | 39,431 | **99%** |
| `ogimet_metar_*` (5 stations) | 2,491 | 0 | 0% |
| `hko_daily_api` | 821 | 0 | 0% |

**The pattern is unambiguous.** WU writer historically did not stamp `provenance_metadata`; ogimet + HKO writers did. This is a **mono-source writer defect**, not a systemic data problem.

**Three handling options (Q5 from plan.md):**

| Option | Action | Cost | Risk |
|---|---|---|---|
| **A. Quarantine all 39,431** | `UPDATE observations SET authority='QUARANTINED', quarantine_reason='empty_provenance_wu_daily'` | Low | Moves 99% of WU obs out of training-eligible — large eligibility hit; HK/Taipei impacts disputed (separate issue) |
| **B. Partial backfill from oracle_shadow** | Use `raw/oracle_shadow_snapshots/{city}/{date}.json` to populate `provenance_metadata` for the 480 overlapping rows; quarantine the rest | Medium | Coverage: 480 / 39,431 = **1.2%** — most rows still need quarantine. Oracle shadow only covers 2026-04-15 to 2026-04-26 |
| **C. Log-replay reconstruction** | Walk historical fetcher logs (if any exist) to re-derive provenance deterministically | High; requires log persistence the operator hasn't confirmed | Highest authority; lowest probability of execution |

**Recommended sequence (operator confirm Q5):**

1. **Now**: option B — populate the 480 oracle_shadow-overlap rows; this gives a clean, audit-grade slice for SKILL backtest tests.
2. **Same packet**: option A for everything else — explicit quarantine with `quarantine_reason='empty_provenance_wu_daily_pre_2026-04-15'`. This is reversible if option C ever lands.
3. **Defer option C** until operator confirms historical fetcher logs exist.

**Antibody test:** `tests/test_observations_provenance_required.py::test_writer_rejects_empty_provenance` — applies to the writer going forward.

---

### 3.B — `market_events*` tables empty (Polymarket replay impossible)

**Type:** `data` + `external`
**Owner:** `data-engineering` (ingestion) + `polymarket-side` (data availability)
**Unblocks:** `ECONOMICS` only
**Reversibility:** reversible (historical archive can be re-ingested)

**Disk truth (verified 2026-04-27):**

```
market_events: 0 rows
market_events_v2: 0 rows
market_price_history: 0 rows
```

**External truth (verified 2026-04-27 against multiple primary sources — CORRECTED, see [04 §C3 + §C4](04_corrections_2026-04-27.md)):**

- Polymarket has many live temperature markets (count from search summary, low-criticality)
- CLOB has `GET /<clob>/data/trades` for trade history (auth-gated)
- **Polymarket DOES have public historical data infrastructure** — 4 layers verified:
  1. **Gamma API** (`gamma-api.polymarket.com`) — Zeus already uses for market discovery
  2. **Public Subgraph** at `github.com/Polymarket/polymarket-subgraph` (open-sourced) with 6 sub-subgraphs: `activity-subgraph` (trades/events), `fpmm-subgraph`, `oi-subgraph`, **`orderbook-subgraph` (order data)**, `pnl-subgraph`, `wallet-subgraph`. Free via The Graph network
  3. **Data API REST** `/trades` for historical trades (auth-gated)
  4. **WebSocket Market Channel** `wss://ws-subscriptions-clob.polymarket.com/ws/market` for forward-only orderbook snapshots
- **Resolution source for verified US markets** (NYC / Chicago / Miami / LA): **Wunderground / KLGA, KORD, KMIA, KLAX** — Zeus's `settlements.settlement_source` (1400+ wunderground.com URLs) **matches reality**. The earlier "NOAA" claim was retracted in 04 §C3.
- **Open verification** ([04 §3 U4](04_corrections_2026-04-27.md#3-still-unverified-claims-flagged-for-future-verification)): whether `orderbook-subgraph` retains snapshots at arbitrary historical timestamps (vs only events) is unverified. Reading the schema files in the github repo would resolve this.

**Three handling options (Q3 from plan.md, REWRITTEN):**

| Option | Action | What it gets you | Cost |
|---|---|---|---|
| **A. Subgraph + Data API ingestion (FREE/CHEAP)** | Run a scheduled GraphQL puller against Polymarket public subgraph + Data API; populate `market_events_v2` from `activity-subgraph` + `orderbook-subgraph` | Historical trade events for all markets; orderbook events depending on subgraph schema (U4) | Low — free up to The Graph free tier; minimal infrastructure |
| **B. Forward-only WebSocket capture** | Subscribe to Polymarket Market Channel; persist book/price_change/last_trade events into `market_price_history` going forward | Real-time orderbook truth from go-live; perfect for forward-grade ECONOMICS | Medium — daemon, sequence-gap handling |
| **C. Both A+B** | A for historical reconstruction, B for going-forward orderbook precision | Maximum coverage and precision | Sum of A+B |

Recommended: **C (both)**. Subgraph for backfill of historical trade events; WebSocket for forward orderbook precision and real-time decision-time truth.

**Real per-period exceptions to the WU resolution rule** (these are narrow and already in `known_gaps.md`, NOT structural):
- **Taipei** switched 03-16~03-22 (CWA) → 03-23~04-04 (NOAA Taoyuan) → 04-05+ (WU/RCSS Songshan) — the only city with multi-period source switching
- **HK 03-13/14**: WU/VHHH (HK Airport) used by Polymarket; Zeus has HKO Observatory
- **WU API vs WU website daily summary** divergence: ~19 mismatches on SZ/Seoul/SP/KL/Chengdu — Zeus reads WU API `max(hourly)`, Polymarket reads WU website daily summary. Both WU, different code paths.

These are the **real F9 footprint**: narrow per-city / per-period exceptions, not "Polymarket uses NOAA universally". My earlier framing was retracted in 04 §C3.

**Recommended sequence (operator confirm Q3):**

1. **Operator decision**: Q3.
2. If A or A+B: scope a separate data-engineering packet `task_2026-04-XX_polymarket_websocket_capture` — out of scope here.
3. Until that packet lands and `market_events_v2 > 0`, the `economics.py` tombstone in the upgrade design refuses to run. **No theatre.**

---

### 3.C — `forecast_issue_time` is NULL on every row (F11 hindsight leakage)

**Type:** `code` + `data`
**Owner:** `agent` (writer fix) + `data-engineering` (backfill)
**Unblocks:** `SKILL` (partially), `ECONOMICS` (fully), `DIAGNOSTIC` (partially)
**Reversibility:** reversible

**Disk truth (verified 2026-04-27):**

`forecasts` has 23,466 rows. `forecast_issue_time` is NULL on every single one. `raw_payload_hash` is NULL on every row. `captured_at` is NULL on every row. `authority_tier` is NULL on every row.

**External truth (verified 2026-04-27 — CORRECTED, see [04 §C1 + §C2](04_corrections_2026-04-27.md)):**

- ENS Day 0 dissemination = `base_time + 6h40m`; Day N = `base_time + 6h40m + N×4min`. Verified verbatim at confluence.ecmwf.int/display/DAC/Dissemination+schedule (Day 0 at 06:40 UTC for 00 UTC base; Day 1 at 06:44 UTC; Day 15 at 07:40 UTC; same +6h40m offset for 06/12/18 UTC bases).
- ENS member count: **51** (50 perturbed + 1 control). HRES is **separate**, not part of ENS. **Zeus's `primary_members: 51` is correct.** Earlier "52" claim retracted.
- `available_at` IS deterministically derivable per the wiki schedule above — it's law-grounded, not heuristic.

**This is the F11 antibody opportunity.** The new `decision_time_truth.AvailabilityProvenance` enum (designed in 01) explicitly distinguishes:
- `FETCH_TIME` (from raw fetcher response — ideal but `raw_response.imported_at` exists, fetch_time may need extraction)
- `RECORDED` (writer stamps `forecast_issue_time` from source headers)
- `DERIVED_FROM_DISSEMINATION` (`base_time + 6h40m + lead_day×4min` per ECMWF Confluence wiki, deterministic and citable)
- `RECONSTRUCTED` (heuristic; not training-grade)

The forecasts writer at `scripts/forecasts_append.py` (per F11) heuristically derives. The fix:

1. **Code slice (parallel-safe)**: extend writer to require `forecast_issue_time` from source headers when available, otherwise stamp `DERIVED_FROM_DISSEMINATION` with `base_time + 6h40m + lead_day×4min` (ECMWF) or the corresponding source-specific schedule (GFS / ICON / UKMO / OpenMeteo each need their own primary-source verification — flagged 04 §3 U5), and **never** silently fall back to `RECONSTRUCTED`.
2. **Backfill slice**: for the 23,466 existing rows, derive `forecast_issue_time` deterministically from `forecast_basis_date` if + only if a clear ECMWF run schedule applies. Stamp `provenance.availability_provenance = "DERIVED_FROM_DISSEMINATION"` so downstream training filters can choose to include or exclude.

**Acceptance gate:** training-eligibility view rejects rows with `availability_provenance IN ('reconstructed', NULL)`.

**Antibody test:** `tests/test_forecasts_writer_availability_provenance_required.py`.

---

### 3.D — All settlements are HIGH-track; LOW-track structurally absent

**Type:** `code` + `data` + `governance`
**Owner:** `agent` (writer) + `operator` (Q4 decision)
**Unblocks:** LOW-track `SKILL` and `ECONOMICS` (HIGH-track unaffected)
**Reversibility:** reversible

**Disk truth (verified 2026-04-27):**

```sql
SELECT temperature_metric, COUNT(*) FROM settlements GROUP BY temperature_metric;
-- ('high', 1561)
-- (no 'low' rows)
```

**Forensic finding C4** + **F3** confirmed at scale. The harvester at `src/execution/harvester.py:766` hardcodes `temperature_metric="high"`. There is no LOW writer.

**Operator question Q4:** does Zeus onboard LOW-track in production?

- If **yes**: a separate data-engineering packet is required (writer + reconstruction of historical LOW settlements from existing observations). Out of scope here. Backtest design accommodates either way (purpose-split is metric-agnostic).
- If **no**: backtest runs HIGH-only and explicitly says so in the SKILL contract output (`metric_scope: "high_only"`). LOW-track is removed from `BacktestPurpose` configurations as a runtime check.

**Backtest impact regardless of Q4 answer:** the design must NOT assume LOW data exists. The `decision_time_truth` loader requires explicit metric and refuses LOW until the writer lands.

---

### 3.E — `zeus_trades.db` is completely empty (no trade history to audit)

**Type:** `data` + `governance`
**Owner:** `operator` (decision: how to repopulate / when live restarts)
**Unblocks:** `DIAGNOSTIC` (and indirectly `ECONOMICS` once economics is unblocked)
**Reversibility:** one-way (live trades create canonical events)

**Disk truth (verified 2026-04-27):**

```
position_events: 0
position_current: 0
trade_decisions: 0
chronicle: 0
shadow_signals: 0
venue_commands: 0
strategy_health: 0
```

This is **post-canonical-DB-cutover state**. The auto_pause tombstone has been in place since 2026-04-16, so no trades have happened in 11+ days. Earlier history may exist in legacy `state/zeus.db` but per `current_data_state.md` that's "legacy and not the current canonical data store".

**Implications for DIAGNOSTIC purpose:**

- The current `run_trade_history_audit()` lane has nothing to audit until live trading resumes.
- `decision_log.artifact_json` MAY have residual records from before cutover; needs probing (separate slice).
- A useful interim DIAGNOSTIC tool is **forward-only**: instrument the live engine (when it un-pauses) to record decisions, then run DIAGNOSTIC against the rolling forward window.

**Recommended sequence:**

1. Operator decides on auto_pause tombstone (separate packet).
2. Once live resumes, instrument decision capture (already in design via `shadow_signals` + `decision_log`).
3. DIAGNOSTIC purpose has a meaningful corpus after ~2 weeks of live operation.

This is **not in this packet's scope**. Flagged for context.

---

### 3.F — `BACKTEST_AUTHORITY_SCOPE` is structural; cannot be lifted without ECONOMICS parity

**Type:** `governance`
**Owner:** `operator` (constitution)
**Unblocks:** nothing this packet would lift; the scope is an honest constraint
**Reversibility:** reversible by formal authority packet

**Boundary doc rule (verified 2026-04-27):** "No replay-derived promotion authority. Until replay achieves full market-price linkage across all subjects, active sizing parity, and selection-family parity, its output may inform but NOT authorize live math changes."

**This packet explicitly does NOT propose lifting `diagnostic_non_promotion`.** The redesign keeps the scope label as the typed `PurposeContract.promotion_authority: bool`, which is `False` for SKILL/DIAGNOSTIC and only becomes `True` for ECONOMICS at full parity. Even then, lifting the runtime constraint requires a separate authority packet.

---

## 4. Blocker dependency graph

```
                 [ Operator Q1: adopt purpose-split? ]
                         │
                         ▼
                 [ S1: purpose.py + decision_time_truth.py ]  ──── parallel ──── [ Q2 answer: F11 antibody behavior ]
                         │                                                                    │
                         ▼                                                                    ▼
                 [ S2: skill.py (live data sufficient) ]                          [ S3.C: forecasts writer fix + backfill ]
                         │                                                                    │
              ┌──────────┴──────────┐                                                         │
              ▼                     ▼                                                         │
    [ S3: diagnostic.py        [ S4: economics.py tombstone ]                                 │
      (probe decision_log)]            │                                                      │
              │                        │                                                      │
              │                        │ blocked by ───►  [ 3.B Polymarket capture            │
              │                        │                    (Q3 answer) ]                     │
              │                        │                                                      │
              │                        │                  [ 3.C forecast_issue_time backfill ]┘
              │                        │                                  │
              │                        ▼                                  │
              │              [ ECONOMICS unblock ]  ◄──── all data ───────┘
              │                  (still gated)
              ▼
    [ Live trades accumulate ]
              │
              ▼
    [ DIAGNOSTIC corpus matures ]
```

---

## 5. Parallel-track sequencing

### Track 1 — Code-only (agent-owned, ship now)

| # | Slice | Depends on | Blast radius |
|---|---|---|---|
| 1 | S1: `src/backtest/purpose.py` + `decision_time_truth.py` (additive, no replacement) | nothing | low |
| 2 | S2: `src/backtest/skill.py` (extract from `replay.py`, behavior preserved) | S1 | low-medium |
| 3 | F11 writer fix: `scripts/forecasts_append.py` requires `availability_provenance` | nothing | medium |
| 4 | Sentinel `Sizing.FLAT_DIAGNOSTIC` plumbing in DIAGNOSTIC lane | S1 | low |

### Track 2 — Data-engineering (operator + data-eng owned)

| # | Slice | Depends on | Owner |
|---|---|---|---|
| 5 | Empty-provenance triage: option B+A combined (3.A) | Q5 answer | data-eng |
| 6 | `forecast_issue_time` backfill on 23,466 rows | F11 writer fix landed | data-eng |
| 7 | Polymarket WebSocket capture (forward) | Q3 answer | data-eng |
| 8 | Polymarket historical archive ingestion (if Q3 = B or A+B) | Q3 answer + archive source identified | data-eng |

### Track 3 — Operator-only (governance)

| # | Item |
|---|---|
| 9 | Q1: adopt purpose-split? |
| 10 | Q2: F11 — hard reject RECONSTRUCTED, or annotate-and-allow? |
| 11 | Q3: Polymarket data source choice (A / B / A+B) |
| 12 | Q4: LOW-track production scope |
| 13 | Q5: empty-provenance handling option (A / B / C / mixed) |
| 14 | auto_pause tombstone (separate packet, not blocking design) |

### Track 4 — Cross-track gates

- ECONOMICS unblocks **only** when: T2.5 + T2.6 + T2.7 (or T2.8) + S4 all green AND PurposeContract for ECONOMICS satisfied at runtime.
- DIAGNOSTIC matures **only** when: live resumes (separate decision) + 2 weeks of trade decisions accumulated.
- SKILL unblocks **today** with empty-provenance gate via the `authority_tier='VERIFIED'` filter.

---

## 6. What this packet explicitly does NOT do

- Mutate any of the 39,431 empty-provenance rows. The triage above describes the work; the work belongs in `task_2026-04-XX_observations_empty_provenance_triage`.
- Subscribe to Polymarket WebSocket. Belongs in a separate data-engineering packet.
- Lift auto_pause tombstone.
- Lift `BACKTEST_AUTHORITY_SCOPE = diagnostic_non_promotion`.
- Build the LOW-track settlement writer.

---

## 7. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Operator picks Q5 option C (log replay) but no logs exist | Medium | Defer C; option A+B already covers the immediate need |
| Polymarket archive source cost (Q3 option B) is prohibitive | Medium | Q3 option A (forward-only) still gives a 60-90 day backtest window after a quarter |
| F11 writer fix breaks existing forecasts ingestion | Low | Slice ships behind a feature flag; gradual cutover with parity check |
| Purpose-split design (Q1) is rejected | Low | Fall back to `replay.py` mods that add purpose enum without splitting modules; less clean but compatible |
| ECMWF dissemination lag (Day 0 = base + 6h40m) changes | Very low | The cite is the Confluence wiki schedule; if it changes, the `derive_availability(ECMWF_ENS, base, lead)` function must be updated. Single test owns the formula. |
| Polymarket changes resolution oracle for weather markets | Low | Re-audit `current_source_validity.md`; backtest design unaffected (purpose-agnostic) |

---

## 8. Memory-rule applications

- **L20 grep-gate**: every disk-truth line was probed within 30 minutes of writing this doc; rows counts may shift on next ingest tick. Implementation packets re-verify.
- **L22 commit boundary**: implementation slices DO NOT autocommit before critic review.
- **L24 git scope**: stage only `task_2026-04-27_backtest_first_principles_review/**` files in this packet's commit.
- **L28 critic-baseline**: critic re-runs regression baseline; team-lead memory counts may drift ±2-3.
- **L30 SAVEPOINT audit**: any slice that touches `src/state/ledger.py` append paths must grep all callers first.
