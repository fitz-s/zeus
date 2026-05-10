# Alignment Safe Implementation Tracker

Created: 2026-05-08
Last updated: 2026-05-08
Authority basis: `docs/operations/task_2026-05-08_deep_alignment_audit/PLAN.md` safe-to-implement cut, current `main` code check, and read-only DB probes from 2026-05-08.

## Purpose

Track implementation packets that are safe to open after the deep alignment audit. This file is intentionally a task list, not another audit narrative.

Rule: implementation packets here must preserve the audit boundary. Do provenance/reporting work first; do not change live trading behavior unless the task explicitly says the behavioral root cause is proven.

## Task List

| ID | Packet | Status | Scope | Acceptance | Do Not Include |
|---|---|---|---|---|---|
| S1 | Market source-proof persistence | READY | Persist scanner source-contract evidence from F24. | Every active market scan has durable raw/structured source proof plus parsed/configured family+station+status. | Station migration repair, Gamma refetch, settlement policy changes. |
| S2 | Lifecycle funnel report | READY_FOR_REPORTING | Add a stage-count report for F25. | A runtime window can certify `no trades submitted` separately from `trade tables empty`. | Order submission changes; mandatory `selection_hypothesis_fact.decision_id` until write-order design is settled. |
| S3 | Calibration serving status surface | READY_OBSERVABILITY | Add forecast/calibration serving-bucket status from F22/F23. | OpenData rows show forecast readiness, requested calibration domain, Platt availability, route, and terminal status together. | OpenData Platt refit/promotion; broad evaluator threading changes already present on current main. |
| S4 | Price/orderbook evidence report | READY_OBSERVABILITY | Add price-evidence mode reporting from F18/A14. | Reports distinguish price-only scanner/token evidence from executable-snapshot/venue evidence. | Executor/order-submission changes; no submitted-order pricing defect was proven. |
| N1 | Hourly observation contract | NEEDS_DESIGN | Decide whether hourly instants are sparse evidence or complete local-day table. | Product decision recorded before schema/backfill work. | Silent rebuilds or metric inference without an explicit table contract. |
| N2 | Source-truth promotion domain | NEEDS_DESIGN | Decide weather-source truth vs market-resolution truth for promotion reports. | Promotion-domain rule names how quarantined market-resolution rows interact with VERIFIED weather labels. | Blanket downgrade of verified weather-source calibration pairs. |
| N3 | Current-fact freshness refresh | READY_DOCS_ONLY | Refresh stale row-count/current-data authority docs. | Current fact docs reconcile the 1,609 baseline with current DB row counts. | Runtime/data mutation. |

## S1 Checklist - Market Source-Proof Persistence

- [ ] Design compact storage: table vs JSON artifact.
- [ ] Include raw Gamma structured source fields and description-derived source URLs.
- [ ] Include parsed source family/station and configured source family/station.
- [ ] Include status/reason for MISSING, AMBIGUOUS, MISMATCH, UNSUPPORTED, or OK.
- [ ] Add parser persistence tests for WU, HKO, NOAA/Ogimet, CWA, and alias folding.
- [ ] Add migration/backfill policy: new scans only unless explicitly authorized.

Implementation notes:
- Parser already exists in `src/data/market_scanner.py`.
- Current audit found no active WU station mismatch after alias normalization.
- This should not change market eligibility logic in the first packet.

## S2 Checklist - Lifecycle Funnel Report

- [ ] Define stage names: evaluated, selected_post_fdr, opportunity_rejected, should_trade, order_submitted, fill, position, settlement_outcome, calibration_learning.
- [ ] Report counts by DB path and runtime window.
- [ ] Include terminal rejection stages and availability status.
- [ ] Add fixture tests proving `should_trade=0` + empty venue tables means certified no-submission, not missing venue evidence.
- [ ] Add design note for selection lineage: `selection_hypothesis_fact` is written before final `EdgeDecision.decision_id` exists, so use stable cycle/snapshot/opportunity linkage unless write order changes.

Implementation notes:
- Current `state/zeus_trades.db` has decision/opportunity/selection facts but zero order/position/outcome rows.
- All current opportunity rows have `should_trade=0`.

## S3 Checklist - Calibration Serving Status Surface

- [ ] Query forecast source coverage by source/data_version/cycle/horizon.
- [ ] Query active VERIFIED Platt availability by requested calibration source/data_version/cycle/horizon.
- [ ] Count fail-closed candidates by CALIBRATION_IMMATURE/RAW_UNCALIBRATED/unsupported source.
- [ ] Include transfer authority route if TIGGE transfer remains policy.
- [ ] Add regression assertion that OpenData candidates cannot land on schema-default `00/tigge_mars/full` without explicit transfer authority.

Implementation notes:
- Current main already threads `derive_phase2_keys_from_ens_result()` into evaluator and monitor `get_calibrator()` calls.
- Active VERIFIED Platt rows are TIGGE-only in the current DB snapshot; OpenData-specific active rows are zero.

## S4 Checklist - Price/Orderbook Evidence Report

- [ ] Report counts for price-only scanner evidence, token refresh evidence, executable snapshots, venue commands, order facts, and trade facts.
- [ ] Require economics/replay reports to declare price-evidence mode.
- [ ] Add fixture tests for `price_only` vs executable-snapshot-backed evidence modes.
- [ ] Keep live executor logic out of scope unless a submitted-order defect is later proven.

Implementation notes:
- Current code captures executable snapshots and reprices before live submission.
- Current `state/zeus_trades.db` has `market_price_history` and `token_price_log` rows, but zero executable snapshots and zero venue/order/trade rows in the probed window.

## Parking Lot

- N1 hourly observation contract: needs product decision before implementation.
- N2 source-truth promotion domain: needs authority decision before changing calibration/promotion reports.
- N3 current-fact freshness: safe as docs-only, but lower priority than S1-S3 because it does not affect runtime behavior directly.

## Next Recommended Packet

Start with S1. It has the narrowest blast radius, a clear parser/persistence boundary, and no dependency on live trade activity.
