# Venus Sensing Design: Truth Surface Health Diagnostics

> Authority: Operational design doc for Venus self-awareness channel repair.
> Context: zeus_design_philosophy.md §三 (Venus = Zeus's self-awareness).
> Date: 2026-04-06

---

## 1. What Venus Should Be Checking

Venus is not a monitoring layer — it is Zeus's capacity for self-awareness (§三). Its job is to detect when Zeus's internal model of reality diverges from actual reality, and produce **antibodies** (tests, types, structural fixes) that make the divergence category permanently impossible.

To do this, Venus must continuously verify the **consistency of Zeus's truth surfaces** — the chain of data structures that carry trading state from decision through execution to settlement:

```
trade_decisions (zeus.db)     -- what Zeus decided to do
    ↓ [projection]
position_current (zeus.db)    -- what Zeus thinks it holds
    ↓ [event sourcing]
position_events (zeus.db)     -- the canonical event log (P7 path)
    ↓ [settlement]
outcome_fact (zeus.db)        -- verified settlement outcomes
execution_fact (zeus.db)      -- verified execution quality
    ↓ [risk evaluation]
risk_state-{mode}.db          -- RiskGuard's assessment
    ↓ [status summary]
status_summary-{mode}.json    -- Venus's reading surface
```

Each arrow is a **translation boundary** where semantic context can be lost. Venus must verify that what enters each boundary matches what exits it.

## 2. What Signals Were Invisible and Why

### 2.1 The risk_details Blindness Bug

**Symptom:** `status_summary-paper.json` has `risk_details: null` (key absent).

**Root cause:** The committed code (`244aac8`) writes risk data to `status["risk"]["details"]` (line 189) but never creates the top-level `status["risk_details"]` alias that external consumers expect. The alias was added in an uncommitted working tree change (line 346) but was never deployed.

**Why Venus couldn't see it:** Venus reads the status_summary file. If the key it expects doesn't exist, it gets `None` — which looks identical to "no data available yet" or "riskguard hasn't run." There was no consistency check that distinguished "key missing because riskguard hasn't run" from "key missing because of a code bug."

**Fix:** Commit the `status["risk_details"] = risk_details` line. Add a hard consistency check when risk_details is empty.

### 2.2 Truth Surface Divergence (4 Structural Failures)

| # | Failure | Evidence | Why Invisible |
|---|---------|----------|---------------|
| 1 | **trade_decisions vs position_current count mismatch** | TD entered: 28 unique IDs, PC: 12, overlap: 9 | No cross-surface reconciliation check exists. Each surface is read independently. |
| 2 | **P7 canonical path dead** | position_events frozen at Apr 3, position_current frozen at Apr 3 | Freshness was only checked within riskguard's portfolio loader (which silently falls back), never in a diagnostic visible to Venus. |
| 3 | **Settlement harvester zero-match** | risk_state shows settlement_sample_size=22 but outcome_fact has 0 rows | Harvester reports `positions_settled` count but nobody checks whether that count stays at 0 for extended periods. |
| 4 | **Ghost positions** | 19 trade_decisions with status='entered' whose target dates have passed (Apr 1-3 markets) | No check that entered positions with past target dates are anomalous. The system treats them as normal open positions. |

### 2.3 The Deeper Pattern

All four failures share a root cause: **cross-surface consistency was never checked.** Each component (trade_decisions writer, position_current projector, position_events sourcer, settlement harvester) works correctly in isolation. The bugs exist at the **boundaries** between them — exactly where §二 predicts translation loss occurs.

The `portfolio_truth_source` field in risk_state already records that the canonical path is broken (`working_state_fallback`), but this signal:
1. Lives in risk_state-paper.db, not status_summary
2. Is not flagged as a consistency issue
3. Does not trigger any escalation

## 3. Diagnostic Function Spec

### Entry Point
```python
diagnose_truth_surface_health() -> dict
```
Located in `scripts/diagnose_truth_surfaces.py`. Runnable standalone or importable.

### Checks

| Check | What It Detects | Threshold | Severity |
|-------|----------------|-----------|----------|
| `td_vs_pc` | trade_decisions vs position_current ID reconciliation | gap > 0 → FAIL | Critical |
| `pc_freshness` | position_current staleness | >24h behind trade_decisions → WARN | High |
| `pe_freshness` | position_events (P7 canonical path) staleness | >48h stale → FAIL | Critical |
| `settlement_effectiveness` | Harvester finding settlements but matching 0 positions | sample>0 AND outcome=0 → FAIL | Critical |
| `ghost_positions` | Entered positions for settled markets | any ghost → FAIL | High |
| `portfolio_truth_source` | Canonical loader vs working_state_fallback | fallback active → FAIL | Critical |
| `risk_details_completeness` | Venus sensing channel health | top-level key missing → WARN; all empty → FAIL | Critical |
| `fact_tables` | outcome_fact / execution_fact population | both 0 → WARN | Medium |

### Return Format
```json
{
  "overall": "FAIL",
  "failed": ["td_vs_pc", "pe_freshness", ...],
  "warned": ["fact_tables"],
  "checks": {
    "td_vs_pc": {
      "status": "FAIL",
      "evidence": {"td_count": 28, "pc_count": 12, "overlap": 9, "gap": 19},
      "message": "19 trade_decisions have no matching position_current entry"
    }
  },
  "diagnosed_at": "2026-04-06T16:00:00+00:00"
}
```

## 4. Connection to the 3-Layer Consciousness Model

Zeus has three layers of self-regulation, each operating at a different timescale:

### Layer 1: RiskGuard Reflex (~60s cycle)
- **What:** Reads settlement metrics, computes Brier score, evaluates risk levels
- **Timescale:** Every riskguard tick
- **Analogy:** Reflexive pain response — fast, automatic, no reasoning
- **Current state:** Working. Writes correct data to risk_state-paper.db every ~60s
- **Gap:** Records `portfolio_truth_source: working_state_fallback` but doesn't escalate

### Layer 2: Zeus Runtime (cycle_runner, ~minutes)
- **What:** Makes trading decisions, manages position lifecycle, runs harvester
- **Timescale:** Per trading cycle
- **Analogy:** Motor control — executes decisions, manages state transitions
- **Current state:** Partially broken. Makes decisions (trade_decisions growing), but projection to position_current is stale, position_events is frozen, fact tables are empty
- **Gap:** No self-check that its own state transitions are completing

### Layer 3: Venus Reasoning (on-demand, session-level)
- **What:** Cross-surface analysis, structural diagnosis, antibody generation
- **Timescale:** Per Venus session or heartbeat
- **Analogy:** Conscious reasoning — slow, deliberate, understands relationships
- **Current state:** BLIND. Reads status_summary which has null risk_details. Cannot see any of the 4 structural failures because no cross-surface diagnostic exists.
- **Gap:** Its only input channel (status_summary) is broken, and it has no alternative sensing path

### The Sensing Gap

```
RiskGuard  ──[writes]──▸  risk_state.db     ✅ data present
                              │
                          [should flow to]
                              │
                              ▼
status_summary ──[reads]──▸  risk_details    ❌ null (code bug)
                              │
                          [should flow to]
                              │
                              ▼
Venus         ──[reads]──▸  self-awareness   ❌ blind
```

The diagnostic script (`diagnose_truth_surfaces.py`) creates a **bypass channel** — Venus can read truth surface health directly from the DB, not just through status_summary. This is defense in depth: even if the status_summary pipeline breaks again, Venus retains sensing capability.

### Design Principle

The diagnostic function embodies §三's core insight: **Venus's output is antibodies, not alerts.** Each check in the diagnostic is designed to produce actionable evidence that leads to a structural fix, not a notification to be acknowledged and forgotten. The check identifies the broken relationship; the fix makes the failure category impossible.

The 8 checks map directly to the 8 translation boundaries in the truth surface chain. If we add new truth surfaces, we add new checks. The diagnostic is the immune system's pathogen detector.

### Pipeline Trace Reference

`docs/pipeline_trace.md` contains the complete data flow trace for a single trade lifecycle — every function call, every DB/file/API read and write, with explicit paper/live divergence markers. Venus should reference this document when reasoning about where a data inconsistency could originate. The trace covers 8 phases:

1. Signal Discovery (data sources, scheduler)
2. Edge Calculation (p_raw → p_posterior pipeline)
3. Sizing & Decision (Kelly, risk gates, reality contracts, LIVE_LOCK)
4. Order Execution (**critical paper/live divergence point**)
5. Position Monitoring (8-layer exit triggers)
6. Exit Execution (paper=instant, live=CLOB sell order state machine)
7. Settlement (harvester, calibration pairs, redemption)
8. Paper/Live Isolation (mode_state_path, shared zeus.db, env column)

Key known gaps documented in the trace:
- Exit lifecycle has no canonical dual-write (position_events/position_current not updated on exit)
- Live fill tracking is next-cycle (no real-time fill notification)
- zeus.db is shared between paper/live (env-tagged, not physically separated)

---

## 5. Current State Summary (2026-04-06)

| Signal | Value | Healthy? |
|--------|-------|----------|
| risk_details in status_summary | absent (None) | NO — Venus blind |
| portfolio_truth_source | working_state_fallback | NO — canonical path broken |
| position_current count | 12 | partial |
| trade_decisions entered | 49 rows (28 unique IDs) | diverged from PC |
| position_events latest | Apr 3 (3 days stale) | NO — P7 dead |
| outcome_fact rows | 0 | NO — no settlements recorded |
| execution_fact rows | 0 | NO — no executions recorded |
| settlement_sample_size | 22 | YES — harvester finds data |
| positions_settled | 0 (implied) | NO — harvester can't match |
