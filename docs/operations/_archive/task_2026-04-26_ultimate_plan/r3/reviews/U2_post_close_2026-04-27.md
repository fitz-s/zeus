# U2 post-close third-party review record — 2026-04-27

Phase: U2 `Raw provenance schema`
Branch: `plan-pre5`
Gate: Required post-close third-party critic + verifier before M1 unfreeze.

## Verdict

PASS — M1 may be unfrozen.

## Third-party critic

- Critic: Ampere — PASS.
- Finding: no blocking issues across schema, repo gates, executor ordering, tests, topology registration, and closeout artifacts.
- Non-blocking risks retained for downstream owners:
  - Legacy DBs can retain nullable `venue_commands.envelope_id`; new writes are Python-enforced.
  - Idempotency races may leave unreferenced pre-submit envelopes, but they remain append-only provenance and do not create confirmed exposure.
  - Topology closeout must continue to use the full receipt `changed_files` list.

## Third-party verifier

- Verifier: Beauvoir — PASS.
- Command evidence run by verifier:
  - `pytest -q -p no:cacheprovider tests/test_provenance_5_projections.py tests/test_executable_market_snapshot_v2.py tests/test_command_bus_types.py tests/test_command_recovery.py tests/test_venue_command_repo.py tests/test_executor_command_split.py tests/test_neg_risk_passthrough.py` -> `155 passed`.
- Evidence checked by verifier:
  - `src/state/db.py` U2 append-only provenance tables/triggers and `venue_commands.envelope_id`.
  - `src/state/venue_command_repo.py` snapshot+envelope gating, token/side/price/size mismatch rejection, command-to-provenance mirroring, append-only facts/lots, and CONFIRMED-only calibration reads.
  - `src/execution/executor.py` pre-submit envelope persistence before SDK contact on entry and exit paths.
  - `tests/test_provenance_5_projections.py` and `tests/test_executor_command_split.py` for schema, mismatch rejection, confirmed-only training, provenance mirroring, and entry/exit ordering.
  - U2 status, pre-close review, work record, and receipt consistency.

## Leader recheck after post-close start

- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase U2` -> GREEN.
- `git diff --check` -> clean.
- `pytest -q -p no:cacheprovider tests/test_provenance_5_projections.py` -> `13 passed`.

## Decision

U2 remains COMPLETE and its interface is frozen. M1 is unfrozen as the only ready-to-start phase after U2 post-close third-party review.
