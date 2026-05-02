# State Module Authority Book

**Recommended repo path:** `docs/reference/modules/state.md`
**Current code path:** `src/state`
**Authority status:** Dense module reference for canonical runtime truth. It explains how append-first authority, projections, reconciliation, and truth-file boundaries work.

## 1. Module purpose
Own Zeus's canonical inner truth: DB schema, canonical writes, lifecycle append/projection discipline, reconciliation, portfolio truth, and derived truth-file boundaries.

## 2. What this module is not
- Not a report layer and not an operator dashboard.
- Not a place for market math, source routing, or contract semantics to be reinvented.
- Not a scratchpad for ad hoc JSON truth when canonical DB/event truth exists.

## 3. Domain model
- Append-only domain events and deterministic projections.
- Canonical world/trades databases and schema management.
- Portfolio and lifecycle truth, including decision chain and chain/CLOB reconciliation.
- Truth-file exports as derived compatibility surfaces rather than primary state.

## 4. Runtime role
Everything that matters economically or operationally eventually lands here: trade decisions, lifecycle events, projections, reconciliation state, ledger rows, chronicled evidence, and portfolio views.

## 5. Authority role
This is the strongest code truth surface after executable tests. Delivery law already ranks executable source, tests, and DB/event/projection truth above human docs. That means state changes are architecture/governance changes even when the diff is small.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `src/state/db.py` and schema helpers as the canonical write substrate
- `src/state/ledger.py`, `projection.py`, `decision_chain.py`, and `lifecycle_manager.py` for event/projection/lifecycle legality
- `docs/authority/zeus_current_architecture.md` runtime-truth and lifecycle law
- `docs/operations/current_data_state.md` for current posture, never as durable law

### Non-authority surfaces
- Derived JSON summaries, CSV exports, notebooks, or status files
- Archive traces that describe truth-surface failures but do not execute them
- Any local scratch DB or one-off migration result not adopted into canonical state

## 7. Public interfaces
- DB connection and write helpers in `db.py`
- Projection rebuild/read helpers
- Portfolio read models and lifecycle-manager transitions
- Truth-file emitters and readers where still needed for compatibility

## 8. Internal seams
- Append-first write path vs deterministic projection path
- Chain/CLOB reconciliation vs local projections
- Portfolio read models vs lifecycle manager legality
- Legacy truth files vs canonical DB state

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `db.py` | Canonical write API and high-blast-radius state anchor, including R3 M5 `exchange_reconcile_findings` and R3 R1 `settlement_commands` schema. |
| `ledger.py` | Append-only economic/lifecycle event substrate. |
| `projection.py` | Deterministic fold/rebuild layer. |
| `lifecycle_manager.py` | Legal transition enforcement. |
| `decision_chain.py` | Point-in-time decision lineage and auditability. |
| `chain_reconciliation.py / chain_state.py` | Bridge between on-chain/CLOB truth and local records. |
| `portfolio.py / portfolio_loader_policy.py` | Read model and load policy for live holdings. |
| `canonical_write.py / chronicler.py / truth_files.py` | Write discipline, chronicle, and derived compatibility surfaces. |
| `collateral_ledger.py` | Z4 pUSD/CTF collateral snapshots and reservations; fail-closed pre-submit truth. |
| `venue_command_repo.py` | Durable venue command/event journal plus R3 U2 raw provenance projections (`venue_submission_envelopes`, order facts, trade facts, position lots, provenance envelope events). R3 M1 keeps command-side transitions grammar-additive and leaves order/trade facts in U2. R3 M2 adds economic-intent duplicate lookup for unresolved `SUBMIT_UNKNOWN_SIDE_EFFECT` commands and persists acked `venue_order_id` from append-event payloads. |
| `job_run_repo.py / source_run_repo.py / readiness_repo.py / market_topology_repo.py` | PR45b data-daemon readiness substrate: durable job/source run provenance, scoped readiness verdicts, and current market-topology authority state. Missing or expired readiness defaults to `UNKNOWN_BLOCKED`. |

## 10. Relevant tests
- tests/test_db.py
- tests/test_dt1_commit_ordering.py
- tests/test_dt1_savepoint_integration.py
- tests/test_dt4_chain_three_state.py
- tests/test_b070_control_overrides_history_v2.py
- tests/test_chronicle_dedup.py
- tests/test_cross_module_invariants.py
- tests/test_collateral_ledger.py
- tests/test_executable_market_snapshot_v2.py
- tests/test_provenance_5_projections.py
- tests/test_command_grammar_amendment.py
- tests/test_riskguard_red_durable_cmd.py
- tests/test_unknown_side_effect.py
- tests/test_exchange_reconcile.py
- tests/test_settlement_commands.py
- tests/test_job_run_schema.py
- tests/test_source_run_schema.py
- tests/test_readiness_state_schema.py
- tests/test_market_topology_state_schema.py

## 11. Invariants
- Append event before projection; never let derived JSON outrank canonical DB/event truth.
- Unknown chain status is not the same as known-empty chain status.
- Lifecycle phases are enum-backed and bounded by law, not arbitrary strings.
- Void/quarantine/admin-close are not ordinary holding states.
- pUSD buy collateral and CTF sell inventory are distinct state truths; neither substitutes for the other.
- Terminal venue command states must release collateral reservations in the same append-event transaction.
- A post-side-effect submit exception is not semantic rejection; it blocks duplicate submits until recovery proves venue state or records safe-replay permission.
- RED force-exit durable CANCEL proxy commands are emitted only through `cycle_runner._execute_force_exit_sweep`; RiskGuard does not write venue commands directly.
- Settlement/redeem command failure is not lifecycle settlement; only canonical settlement paths may terminalize positions.

## 12. Negative constraints
- No signal, strategy, or UI code may write lifecycle state directly.
- No temporary migration or notebook may become canonical truth by accident.
- No state patch may bypass transaction boundaries or savepoint discipline.
- No current-fact document can waive state law.

## 13. Known failure modes
- Projection files diverge from DB truth and silently become operator reality.
- Decision/entry logs are mistaken for full lifecycle truth, recreating settlement-crisis blind spots.
- Chain reconciliation collapses three-valued truth into binary synced/not-synced and causes false closure claims.
- Shadow persistence lingers after migrations and forks the truth surface.

## 14. Historical failures and lessons
- [Archive evidence] `docs/archives/audits/legacy_audit_truth_surfaces.md` found canonical, derived, stale, and dead truth surfaces coexisting; state docs must make the hierarchy explicit.
- [Archive evidence] `docs/archives/traces/settlement_crisis_trace.md` showed that `trade_decisions` was treated as a lifecycle table when it was only a decision log.
- [Archive evidence] `docs/archives/investigations/agent_edit_loss_investigation.md` reinforced that uncommitted state/schema work can vanish unless packet discipline and commit hygiene are enforced.

## 15. Code graph high-impact nodes
- `src/state/db.py` — historically one of the largest blast-radius files and still the most likely central hub.
- `src/state/portfolio.py` and `projection.py` — bridge runtime, execution, and observability readers.
- `src/state/lifecycle_manager.py` and `chain_reconciliation.py` — likely chokepoints for legality and outer-truth convergence.

## 16. Likely modification routes
- Schema or truth-contract change: law + schema + tests + migration proof in one packet.
- R3 F1 forecast provenance columns (`source_id`, `raw_payload_hash`,
  `captured_at`, `authority_tier`) are additive legacy-safe columns on
  `forecasts`; new writes populate them, while legacy rows may remain nullable.
- Projection-only change: prove append/projection parity and rebuild determinism.
- Chain/CLOB reconciliation change: test unknown/known-empty/synced states explicitly.

## 17. Planning-lock triggers
- Any change under `src/state/**` touching schema, truth ownership, projection, lifecycle write paths, or reconciliation.
- Any addition of new durable state surface or derived truth file.
- Any migration/cutover that changes canonical read/write semantics.

## 18. Common false assumptions
- If status_summary looks right, state must be right.
- A projection table or JSON snapshot can temporarily stand in for canonical truth without risk.
- Unknown chain status can be treated as empty for convenience.
- Because a state bug is 'just storage', it cannot change economics.

## 19. Do-not-change-without-checking list
- `db.py` write APIs and transaction ordering
- `lifecycle_manager.py` phase grammar
- `chain_reconciliation.py` unknown-vs-empty semantics
- Projection rebuild logic without parity tests

## 20. Verification commands
```bash
pytest -q tests/test_db.py tests/test_dt1_commit_ordering.py tests/test_dt1_savepoint_integration.py tests/test_dt4_chain_three_state.py
pytest -q tests/test_chronicle_dedup.py tests/test_cross_module_invariants.py
python -m py_compile src/state/*.py src/state/schema/*.py
python scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence <packet-plan> --json
```

## 21. Rollback strategy
Rollback as one state packet. Revert schema/migration/projection changes together; if a migration wrote live data, record explicit rollback SQL or quarantine the new write family instead of partial file rollback.

## 22. Open questions
- How much of legacy truth-file compatibility still matters, and which readers remain active?
- Does repo reality now justify a dedicated `state/contracts` sublayer for schema/truth contracts?

## 23. Future expansion notes
- Create a machine-readable state sub-manifest inside `architecture/module_manifest.yaml` with write/read ownership tags.
- Add graph-derived affected-reader summaries to state context packs.

## 24. Rehydration judgement
This book is the dense reference layer for state. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
