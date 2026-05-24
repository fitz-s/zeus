# Execution-Layer Staleness — Root Cause + Fix Spec (2026-05-24)

## Verdict (hard-number-anchored, advisor-validated)

The live system trades ~0 NEW entries because **real, above-floor edges die at order
construction on snapshot staleness**, NOT coverage and NOT forecast bias.

- Live-lead forecast is accurate (±2°F vs settlement; Chicago +0.3°F). Forecast EXONERATED.
- Coverage (7 vs ~50 cities) is real but secondary — fixed by PR #325 (discovery 619.7s→81.1s).
- **Primary break:** 194 above-floor (p_market≥5c) real edges (up to 66c) reached
  `EXECUTION_FAILED` in 7 days. Dominant cause = staleness:
  - `executable_snapshot_stale` — freshness window = **30s**
    (`src/contracts/executable_market_snapshot_v2.py:27` FRESHNESS_WINDOW_DEFAULT=30s)
  - `collateral_snapshot_stale: age_seconds=210–700`
  - Freshest live executable snapshot observed: **111s old** vs 30s gate → nothing tradeable.
- The 5 historical fills were `day0_window`/`settlement_capture` via the OBSERVATION path,
  which does not gate on the 30s CLOB-snapshot freshness. The CLOB-priced live strategies
  (`opening_inertia`, `imminent_open_capture`) ALL die on staleness.

## Mechanism (module boundary, Fitz #2: translation loss across discovery→execution)

`imminent_open_capture` mode runs every 5 min (`src/main.py:2570`). Within a run:
capture snapshots (discovery) → evaluate → reprice. The within-run discovery→reprice
latency exceeds the 30s freshness window, so by reprice the snapshot is already stale.
`_reprice_decision_from_executable_snapshot` (`src/engine/cycle_runtime.py:842`,
`get_snapshot` at :872) **re-reads the stale cycle snapshot and never re-captures**;
`is_fresh(...)` (30s) then raises `executable_snapshot_stale` (:877-878).

The 30s gate is CORRECT (do not submit on a stale book). The defect is that submit
relies on the cycle snapshot instead of re-capturing fresh.

## Fix (category-impossible, keeps the 30s safety gate)

**Fresh-at-submit re-capture.** In the reprice path, when the persisted snapshot fails
`is_fresh`, re-capture a fresh snapshot for the SINGLE candidate market and use it,
instead of raising stale. Reuse the validated primitive
`capture_executable_market_snapshot` (`src/data/market_scanner.py:2369`) — same pattern
as `refresh_executable_market_substrate_snapshots` (:3128-3153):
- build `market` dict (one outcome) from the stale snapshot's persisted token facts
  (condition_id, token_id, no_token_id, question_id — identity does NOT go stale, only prices),
- build `decision` SimpleNamespace with `tokens` + `edge.direction` (already have `d`),
- obtain a CLOB client (reprice has none today — instantiate a short-lived
  `PolymarketClient(public_http_timeout=...)` on-demand, as discovery does), single bounded fetch,
- re-read the fresh snapshot, proceed.

Collateral: ensure a fresh collateral read at submit (background refresh target is 30s,
`src/main.py:847`, but ages to 210–700s during slow cycles).

Alternatives rejected: widen 30s window (unsafe — submits on stale prices);
speed discovery <30s (insufficient — 81s>30s; refresh cadence is the issue).

## Relationship test (write FIRST — cross-module invariant)

`tests/test_exec_freshness_recapture.py`:
A decision built against an executable snapshot whose `captured_at` is >30s old at submit
MUST trigger a fresh re-capture and yield a valid repriced best-ask, NOT raise
`executable_snapshot_stale`. Prove RED before fix (stub the stale snapshot, assert the
raise pre-fix), GREEN after (assert re-capture called + no raise).

## Deploy ordering (advisor)

1. Land this exec-freshness fix (test GREEN, worktree, paper replay).
2. THEN merge PR #325 coverage (fc000a9dbb, 81.1s/49 cities) — more cities flow into the
   SAME execution gate, so coverage without this fix just expands the dead-end funnel.
3. Single small live order → watch full e2e → then open throttle.

## Parallel (orthogonal, in flight)
- Shadow-strategy bin-bias eval (subagent) → promotion verdict for day0_nowcast_entry
  (sees 66c observation edges; promotion is a separate lever from this execution fix).
