# Timing Instrument Intent Verdicts (2026-06-15)

```
Created: 2026-06-15
Last reused or audited: 2026-06-15
Authority basis: timing-semantics audit; writer-intent confirmation pass.
Source: sonnet code-reader (Explore) + main-agent line-by-line confirmation of load-bearing sites.
```

## Q1 — `decision_events`: LIVE_BUT_UNWIRED_WRITER + READ_DEPENDS_ON_EMPTY  (the linchpin)

Writer IS wired and called after every live submit:
- `src/state/decision_events.py:266` `write_decision_event()` — full INSERT incl. all timing-chain fields.
- Called from `src/engine/cycle_runtime.py:6106`, inside a **fail-soft** block
  (`except Exception as _de_exc: logger.warning("...degraded..."); summary["degraded"]=True`, lines 6118-6124).
- NOT-NULL guard at `decision_events.py:229-242`: `_required_live = {first_member_observed_time,
  run_complete_time, zeus_submit_intent_time, venue_ack_time, polymarket_end_anchor_source(+observation_available_at)}`;
  `missing=[k for k,v if not v]; if missing: raise ValueError(...)`.

Why 0 rows: `first_member_observed_time` / `run_complete_time` are structurally empty (Q2) →
guard raises ValueError → fail-soft swallows it → INSERT skipped → table stays empty.

Reader: `src/analysis/evidence_report.py:203-223` — `n_decisions = COUNT(*) FROM decision_events
WHERE strategy_key=?` — the per-strategy decision count for calibration/learning analytics →
reads the empty table → **0**.

Superseded by: `decision_certificates` (1.26M rows, live) via `src/decision_kernel/ledger.py` —
that is the active provenance path. decision_events was the original timing-chain store.

## Q2 — `ensemble_snapshots` timing columns (root of the cascade)

Single writer `src/engine/evaluator.py:6705` INSERT column list writes `available_at` (nominal cycle)
but **NOT `source_available_at` per row**; it writes `first_member_observed_time`/`run_complete_time`
from `ens_result.get(...)` (evaluator.py:6700-6701).

`ens_result["first_member_observed_time"]` is derived at `src/data/ecmwf_open_data.py:880-882`:
```
_avail_times = [str(row["source_available_at"]) for row in rows if row.get("source_available_at")]
first_member_observed_time_iso = min(_avail_times, default="")
run_complete_time_iso = ("" if partial_run else max(_avail_times, default=""))
```
→ empty whenever the aggregated `rows` lack per-row `source_available_at`, and `run_complete_time`
is additionally forced `""` on any `partial_run` (observed_members < 51).

`source_run` (separate table) DOES get `source_available_at=source_release_time` (ecmwf_open_data.py:912)
— but that is the run-level row, not the per-member rows feeding the aggregation.

Per-column verdict: `source_cycle_time`/`source_release_time` = INTENTIONAL_PLACEHOLDER (run-level only);
`source_available_at` per-row, `first_member_observed_time`, `run_complete_time` = **UNWIRED_SHOULD_CAPTURE**.

## Q3 — `data_coverage.expected_at`: NEVER_WRITTEN_DEAD_READ

`record_written()` (`src/state/data_coverage.py:177`) accepts `expected_at` and upserts it via COALESCE,
but every caller passes `None` (record_legitimate_gap/record_failed/record_missing/bulk_record_written).
No `src/` code reads it to compute `fetched_at − expected_at` lateness. Unfinished column.

## Q4 — Latency authority

- `execution_fact.latency_seconds` (db.py:8279-8283): **real** `(filled_dt − posted_dt)` diff, NOT
  synthetic; 0.0 rows = same-second fills. **VESTIGIAL** — no calibration/risk/learning code reads it.
- `execution_feasibility_evidence.latency_ms`: declared AUTHORITATIVE (wired into the market-channel
  pipeline + feasibility reporting), but `feasibility_evidence_from_quote()` initializes it `None`
  pre-fill "to be updated when fill/outcome known" — and it is NULL across all 12.26M rows, so the
  post-fill update path does not fire. The authoritative latency is structurally never populated.

## Cascade (single severed wire → blind engine)

```
ensemble per-row source_available_at unwired  +  partial_run forces run_complete_time=""
  → first_member_observed_time / run_complete_time = ""  (100% NULL, confirmed 3000/3000)
    → decision_events NOT-NULL guard raises ValueError
      → cycle_runtime.py:6106 fail-soft swallows (logs "degraded", continues)
        → decision_events = 0 rows
          → evidence_report n_decisions = 0  (calibration/learning denominator)
```
**RUNTIME CORRECTION (2026-06-15):** the cascade above describes what WOULD happen if the writer
were reached, but the live log REFUTES the "raises on every submit + fail-soft swallows it"
mechanism: `grep "write_decision_event failed" logs/zeus-live.log` = **0** over the full 28-day
window (2026-05-18 → 06-15); the "requires non-empty fields" ValueError = 0. The writer is gated
inside the `stage="venue_ack"` block (cycle_runtime.py:6080) behind `if _dsc is not None`; the
`venue_ack` log token appears only **3×** in 28 days. So `decision_events` is empty **by OMISSION**
— the submit/ack path is rarely reached — not by caught exception. Consistent with the known
low-submit / no-trade posture (notepad GOAL "3 e2e real fills"; memory "dead submit plumbing").

The upstream finding stands independently: `first_member_observed_time`/`run_complete_time` are
100% NULL (3000/3000) and 45% of runs are PARTIAL (56/124), so even when the path IS reached the
guard would reject. Net: the timing ledger cannot fill for two stacked reasons — the submit event
rarely fires AND the upstream timing fields are structurally empty.
```
