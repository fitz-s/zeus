# Quarantine Excision — src/ Census Ledger (2026-07-11)

Consolidated ledger for the T8 extermination census (`docs/rebuild/quarantine_excision_2026-07-11.md`).
Scope: all files under `src/` matching `rg -l -i quarantin src/`. Every distinct mechanism classified
B1 (dies with a named T-target)/B2 (RESHAPE-AND-RENAME)/B3 (dead code, delete)/B4 (text-only,
comments/docs). This file is the header; full per-file findings live in the part files linked below.

**STATUS: COMPLETE. Coverage confirmed: 86 files matched by `rg -l -i quarantin src/`, 86 covered**
(verified by diffing the grep output's basenames against every basename mentioned across all 8 part
files — zero gaps remain, including the 3 same-named `AGENTS.md` files across control/engine/state,
each independently classified). The original TAIL-1 sub-agent never reported back (distinct from the
two KM-B/KM-C agents that errored visibly) — found via this coverage diff and completed inline.

## Part files

| Part | Files covered | Path |
|---|---|---|
| KM-A | decision_integrity_quarantine.py, chain_reconciliation.py, db.py, cycle_runtime.py | [quarantine_ledger_src_part_KMA.md](quarantine_ledger_src_part_KMA.md) |
| KM-B | portfolio.py, market_scanner.py, lifecycle_manager.py, fill_tracker.py | [quarantine_ledger_src_part_KMB.md](quarantine_ledger_src_part_KMB.md) |
| KM-C | command_recovery.py, ensemble_snapshot_provenance.py, canonical_projections.py, control_plane.py, edli_position_bridge.py, price_channel_ingest.py, edli_fill_bridge_dispositions_schema.py | [quarantine_ledger_src_part_KMC.md](quarantine_ledger_src_part_KMC.md) |
| KM-D | day0_fast_obs.py, day0_oracle_anomaly.py, harvester.py, replacement_forecast_calibration_quarantine.py, harvester_truth_writer.py | [quarantine_ledger_src_part_KMD.md](quarantine_ledger_src_part_KMD.md) |
| TAIL-1 | contracts/boundary_policy.py, canonical_lifecycle.py, forecast_target.py, position_truth.py, resolution_era.py, semantic_types.py, settlement_axes.py, snapshot_ingest_contract.py, tigge_snapshot_payload.py; types/observation_atom.py, truth_authority.py; supervisor_api/contracts.py; calibration/ens_bias_repo.py, ens_error_model.py, manager.py, store.py | [quarantine_ledger_src_part_TAIL1.md](quarantine_ledger_src_part_TAIL1.md) |
| TAIL-2 | data/anchor_cross_check.py, bayes_precision_fusion_history_provider.py, hourly_instants_append.py, ingest_status_writer.py, ingestion_guard.py, openmeteo_ecmwf_ifs9_bucket_transport.py, polymarket_client.py, replacement_cycle_advance_trigger.py, replacement_forecast_live_materialization_queue.py, replacement_forecast_refit_gate.py, source_time.py, substrate_observer.py; oracle/ddd_artifacts/v2_city_floors.json | [quarantine_ledger_src_part_TAIL2.md](quarantine_ledger_src_part_TAIL2.md) |
| TAIL-3 | engine/AGENTS.md, cycle_runner.py, event_reactor_adapter.py, lifecycle_events.py; events/reactor.py; execution/edli_presence_resolver.py, exchange_reconcile.py, executor.py, exit_lifecycle.py; ingest/polymarket_user_channel.py; control/AGENTS.md | [quarantine_ledger_src_part_TAIL3.md](quarantine_ledger_src_part_TAIL3.md) |
| TAIL-4 | state/AGENTS.md, attribution_drift.py, canonical_write.py, chain_mirror_reconciler.py, chain_state.py, collateral_ledger.py, db_writer_lock.py, domains.py, edge_observation.py, ledger.py, market_topology_repo.py, projection.py, schema/v2_schema.py, settlement_writers.py, venue_command_repo.py, ws_poll_reaction.py; strategy/family_exclusive_dedup.py, fill_up_wiring.py; main.py; observability/status_summary.py; ops/monitor_cadence.py; risk_allocator/governor.py; riskguard/riskguard.py | [quarantine_ledger_src_part_TAIL4.md](quarantine_ledger_src_part_TAIL4.md) |

## Consolidated bucket totals (8 of 8 parts, complete)

| Part | Mechanisms | B1 (dies-with-target) | B2 (reshape) | B3 (dead code) | B4 (text-only) |
|---|---|---|---|---|---|
| KM-A | 21 | 12 | 9 | 0 | 0* |
| KM-B | 15 | 11 | 3 | 0 | 1 |
| KM-C | 15 | 8 | 5 | 0 | 2 |
| KM-D | 13 | 2 | 9 | 0 | 2 |
| TAIL-1 | 16 | 7 | 5** | 0*** | 8 |
| TAIL-2 | 13 | 3 | 4 | 1 | 5 |
| TAIL-3 | 32 | 25 | 0**** | 1 | 6 |
| TAIL-4 | 38 | 16 | 11 | 0 | 11 |
| **Total (8/8)** | **163** | **84** | **46** | **2** | **35** |

\* KM-A has no standalone B4 rows; 2 of its B1/B2 rows carry an inline text-only sub-fix (misleading
log wording) noted but not double-counted.
\*\* TAIL-1's B2 count includes 1 tentative row (`snapshot_ingest_contract.py`, docstring-only
verification — flagged for a follow-up read before finalizing).
\*\*\* TAIL-1 found 0 *standalone* B3 rows, but confirmed (via `CanonicalEventType.CHAIN_QUARANTINED`
in position_truth.py) the dead-code status TAIL-3 already flagged for `build_chain_quarantined_canonical_write` — counted once, in TAIL-3.
\*\*\*\* TAIL-3 found 0 *standalone* B2 mechanisms in its 11 files — every real function it found is
either a comment describing a B2 mechanism owned by an out-of-scope file (counted B4 here) or a B1
branch riding a B2 core classified elsewhere.

## Cross-validated findings (independently found by 2+ investigators — high confidence)

1. **`settlements.authority='QUARANTINED'` — not in T1-T7, found independently by KM-A (db.py CHECK +
   trigger), KM-D (harvester.py + harvester_truth_writer.py, duplicated writer copies), and TAIL-1
   (`types/truth_authority.py` — the canonical enum SOURCE all the others encode as raw strings).**
   Legitimate reject/evidence-release pattern (`reactivated_by` provenance key gates
   QUARANTINED→VERIFIED). 92 live rows per code-comment probe — not in the mission doc's Live Blast
   Radius section. **The target rename is no longer a proposal — TAIL-1 found it already live**:
   `contracts/settlement_axes.py:179-182` already maps `auth == "QUARANTINED"` to
   `SettlementResolutionState.DISPUTED`, meaning `QUARANTINED`→`DISPUTED` is the correct name because a
   downstream consumer already uses it. Needs a coordinated rename across db.py's CHECK/trigger,
   harvester.py/harvester_truth_writer.py writer copies, `truth_authority.py`'s enum (the root), and 2
   more readers found by TAIL-1 (`calibration/manager.py`, `calibration/store.py`) plus
   `types/observation_atom.py`'s own independent instance of the same 3-state pattern — 7 files total,
   one packet.
2. **`_decision_certificate_is_quarantined` duplicated in 2 live money-path callers, not shared** —
   found independently by KM-A (executor.py), KM-C (command_recovery.py), and TAIL-3 (confirmed both,
   explicitly flagged as "doubles the known live-caller count from 1 to 2"). This is the mission doc's
   own named hardest case (R1-b erratum #8); three independent investigators converging on the same
   two call sites is a strong signal to consolidate into one shared checker in the same packet.
3. **`position_lots.state='QUARANTINED'` is a SEPARATE enum/table from `position_current.phase`,
   easy to miss in a T5 migration scoped only to position_current** — found independently by KM-A
   (db.py:5433 CHECK), KM-C (canonical_projections.py comment corroborating 0-live-rows), TAIL-1
   (canonical_lifecycle.py's `ExposureState` docstring, the 3rd independent corroboration of the
   0-live-rows claim), TAIL-3 (venue_command_repo.py minting site, consumed by polymarket_user_channel.py
   M5 gate), and TAIL-4 (venue_command_repo.py, 3 call sites: 216-226, 330-359, 3284-3334). Five
   investigators, same finding.
4. **The quarantined-position-redecision-eligibility predicate is reimplemented independently 4 times**:
   `cycle_runtime.py::_quarantined_position_can_redecision` (KM-A, the canonical/central one — doc cites
   it by name), `cycle_runtime.py::_canonical_monitor_position_rows` inline copy (KM-A),
   `portfolio.py::_is_runtime_open_position` (KM-B), `price_channel_ingest.py` exposure-clause query
   (KM-C). All four gate on the same shape (quarantined phase + current-money-risk chain_state + positive
   chain_shares). High drift risk during T5 — recommend consolidating to one shared predicate all four
   import, in the same packet as the T5 rename.
5. **T5 touches THREE parallel enum definitions, not one** (TAIL-1 finding): `PositionPhase`
   (`contracts/canonical_lifecycle.py`, the one `LifecyclePhase` in lifecycle_manager.py aliases directly
   and KM-B's ledger entry treats as ground zero), plus `LifecycleState` and `ChainState`
   (`contracts/semantic_types.py`) — two more independent enums with their own QUARANTINED/
   QUARANTINE_EXPIRED/ENTRY_AUTHORITY_QUARANTINED members that `PositionPhase` does not subsume. A
   migration scoped only to `PositionPhase` (the obvious target) would silently miss the other two.

## Dependency-ordered removal sequence (recommended, extends the mission doc's RQ-1/RQ-2/RQ-3 waves)

1. **RQ-1 (parallel, self-contained, no cross-file coordination needed):**
   - T1: edli_position_bridge.py + price_channel_ingest.py + edli_fill_bridge_dispositions_schema.py
     (KM-C confirmed exact match to doc's claimed line ranges) — drain 8 QUARANTINED rows, drop CHECK
     literal, delete threshold/mint function.
   - T3: riskguard.py (TAIL-4, confirmed T3 core at :352-454) — consistency_lock verdict fix.
   - ensemble_snapshot_provenance.py (KM-C, doc's own worked example) — mechanical rename, single file,
     no live-state side-table.
   - market_scanner.py source-contract-block subsystem (KM-B) — mechanical rename, JSON file not DB,
     only 2 external script consumers.
   - `CanonicalEventType.CHAIN_QUARANTINED` (position_truth.py) + its sole dead writer
     `build_chain_quarantined_canonical_write` (lifecycle_events.py, TAIL-3+TAIL-1 cross-confirmed B3) —
     straight delete, zero live callers, no coordination needed.
   - settlements.authority reshape (finding #1 above, now 3-way cross-validated with a confirmed target
     name) — coordinate `truth_authority.py` (root enum) + db.py CHECK/trigger + harvester.py +
     harvester_truth_writer.py + calibration/manager.py + calibration/store.py + observation_atom.py
     (7 files) in one packet; needs its own live-row count first (92 rows per comment, unverified
     against current DB).
2. **RQ-2 (needs T2 scoped-block seam + certificate-checker consolidation):**
   - T2 global gate (cycle_runner.py, TAIL-3 confirmed exact line numbers :129-155/:400-402) + the
     scoped chain-only-block mechanism already mostly built (db.py record_token_suppression /
     query_chain_only_quarantine_rows / chain_reconciliation.py Rule-3, KM-A) — this is RENAME work,
     not new-seam work; the doc's "net-new seam" framing may be stronger than needed since the scoped
     block already exists under the "chain_only_quarantined" name.
   - Certificate-revocation consolidation (finding #2 above): merge executor.py + command_recovery.py
     checkers into one shared `is_certificate_revoked()`, re-point both in the same commit.
   - fill_tracker.py T4 (KM-B, confirmed 7 doc-listed sites + found an 8th at line 1471 not in doc's
     count) — split by cause per doc's target form.
3. **RQ-3 (T5 lifecycle phase retirement — the big one, needs everything above to have stopped minting
   first):**
   - **3-enum coordination (finding #5)**: `contracts/canonical_lifecycle.py::PositionPhase` +
     `contracts/semantic_types.py::LifecycleState` + `contracts/semantic_types.py::ChainState`, plus the
     shared-vocabulary constants root `contracts/position_truth.py` — all four must move together, this
     is the true ground zero, ahead of lifecycle_manager.py which only aliases `PositionPhase`.
   - lifecycle_manager.py LEGAL_LIFECYCLE_FOLDS + 4 enter_*_runtime_state writers (KM-B) — the live
     transition-table surface built on top of the enums above.
   - canonical_projections.py `_derive_phase_and_authority` (KM-C) — the A5 reducer; its
     `chain_review_required` overlay pattern is ALREADY the correct target shape, just needs the
     boolean/enum inputs renamed, not restructured.
   - Consolidate the 4 duplicated redecision-eligibility predicates (finding #4 above) into one shared
     function BEFORE or DURING this wave — doing it after risks divergent behavior across the 4 call
     sites during the migration window.
   - position_current.phase CHECK + position_lots.state CHECK (TWO SEPARATE migrations, finding #3
     above, now 5-way cross-validated — do not let position_lots get missed).
   - Delete (not rename) `chain_reconciliation.py::_materialize_chain_only_position_if_resolvable`
     (KM-A) — contradicts the doc's own T5 target design.
   - T6 control_plane.py ack machinery + its API surface `supervisor_api/contracts.py`'s
     `"acknowledge_quarantine_clear"` command literal (KM-C + TAIL-1, 2-file coordination found) —
     SEQUENCING RISK: do not delete until T5's replacement state has its own operator release path; it
     is currently the only release valve for stuck quarantined positions.
   - decision_integrity_quarantine.py fact-invalidation reshape (KM-A) — the PR-E forecast-linked
     tagging half; separate concern from the certificate-revocation half (already sequenced in RQ-2).
4. **T7 + T8 residue sweep:** occurred_at CHECK 'QUARANTINE' literal (KM-A, db.py:5102, appears dead —
   verify with full-repo grep before dropping), doc/architecture text fixes, remaining B4 comment sweep
   across all 8 parts (35 B4 rows total — mostly loose-English "quarantine"-as-verb usage in
   docstrings/comments with no attached mechanism, cheap to batch-fix once the real mechanisms above are
   renamed and the correct replacement vocabulary is settled).

## Notes for whoever executes this

- Every part file's "Consumers" column is the removal-order source of truth for its own mechanisms —
  this header aggregates cross-file patterns, not every individual caller.
- Several findings across KM-A/KM-B/KM-C/TAIL-1 explicitly flag file:line contradictions or gaps versus
  the mission doc's own claims (undercounted call sites, a mechanism that violates the doc's own stated
  T5 target, a whole mechanism family — settlements.authority — absent from the doc entirely). Read
  those rows before assuming the doc's line numbers/counts are current — code has drifted since the
  2026-07-11 measurement in several places (though T1/T2/T3's claimed line ranges were all verified
  exact by the investigating parts).
- TAIL-1's `snapshot_ingest_contract.py` B2 classification is docstring-level only (module docstring
  claims "3-law quarantine gating") — the actual gate implementation in that file's body was not
  independently verified in this pass. Confirm before treating its rename as settled.
