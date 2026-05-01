# 17 Apply Order

## Preflight

1. Create a clean local checkout of `fitz-s/zeus` at branch `data-improve` and baseline commit `0206428b26bbb0dd48223e449553d1075de37c72`.
2. Copy `zeus-world.db` to a scratch location. Never mutate the uploaded DB.
3. Run the SQL files in this package against the copied DB and save outputs.
4. Record exact Python version, package lockfile, repo SHA, DB SHA256, and environment variables.
5. Confirm no live trading process reads the scratch DB.

## Packet order

### P0 — Data audit containment

- Add a read-only data-readiness command.
- Add fail-closed guards for empty forecast/ensemble/calibration/replay/market tables.
- Add evidence-only views for legacy tables.
- Add regression tests that prove unsafe rows are rejected.

### P1 — Provenance hardening

- Retrofit provenance requirements for daily observations and settlements.
- Add payload hash/source URL/parser/station registry fields.
- Quarantine WU daily rows with empty provenance until reconciled.
- Add canonical source-role registry and eligibility views.

### P2 — Backfill guardrails

- Make all repair/backfill scripts dry-run default.
- Require completeness manifests, expected counts, local-day hour counts, source quotas, and failed-window summaries.
- Replace `INSERT OR REPLACE` with hash-checked idempotence or revision history.

### P3 — Usage path hardening

- Make calibration/replay/live consumers read safe views only.
- Ban `hourly_observations` from canonical paths.
- Require `training_allowed=1`, `causality_status='OK'`, eligible source role, and market identity for training.

### P4 — Populate canonical v2 truth after review

- Backfill `settlements_v2` from verified market rule/source payloads.
- Populate forecast/ensemble v2 tables with true issue/available/fetch times.
- Rebuild calibration pairs only after P0-P3 pass.

## Verification order

1. Run schema and count SQL checks.
2. Run provenance coverage SQL checks.
3. Run source-tier/fallback contamination checks.
4. Run timezone/DST local-day checks.
5. Run settlement alignment checks.
6. Run causality checks.
7. Run backfill consistency checks.
8. Run unit tests and negative tests.
9. Run a small dry-run replay on a known market with fully populated v2 rule/source rows.

## Commit order

1. Commit P0 readiness gates and tests.
2. Commit P1 provenance/schema/source registry changes.
3. Commit P2 script guardrails.
4. Commit P3 consumer guards.
5. Commit v2 data migration tooling separately from data artifacts.
6. Commit data backfill outputs only after independent review and DB checks.

## When to re-run Pro review

Re-run this audit after P0-P3 pass and before any v2 data rebuild is accepted as canonical. Re-run again after settlements_v2 and ensemble_snapshots_v2 are populated and calibration pairs are rebuilt.