# Work Log -- task_2026-04-25_p2_daily_observation_revision_history

## Machine Work Record

Date: 2026-04-25
Branch: midstream_remediation
Task: P2 4.4.A2 WU/HKO daily observations hash-checked revision history
Changed files: pending
Summary: Replace WU/HKO daily backfill replace-on-conflict behavior with revision-preserving idempotence.
Verification: pending
Next: Verify the daily-specific helper/schema + two script write-path updates, then run critic/review, repair, commit, and push.

## 2026-04-25 -- packet started
- A1 obs_v2 revision-history packet landed at `0837afc`.
- Opened A2 as a daily `observations` backfill packet only.
- Confirmed target overwrite seams:
  - `scripts/backfill_wu_daily_all.py`
  - `scripts/backfill_hko_daily.py`
- Architect review rejected the initial generic `observation_revisions`
  reuse idea; daily rows need a dedicated `daily_observation_revisions`
  surface because high and low live on one canonical row.
- Deferred `scripts/backfill_ogimet_metar.py`: current Ogimet daily writes do
  not expose stable daily payload identity, so adding it here would widen from
  write-conflict behavior into source-contract work.
- Shared `src/data/daily_obs_append.py` only for canonical daily row mapping;
  live coverage-coupled UPSERT semantics stay unchanged.
- Known closeout discipline: runtime heartbeat churn must be committed
  separately before change-receipts.

## 2026-04-25 -- implementation verification
- Added a dedicated `daily_observation_revisions` schema and a shared
  `src/data/daily_observation_writer.py` helper for canonical daily row mapping
  plus WU/HKO backfill revision-preserving writes.
- Updated WU/HKO daily scripts to count only real inserts as inserted; reruns
  with same payload hash become no-op and payload drift records revision
  evidence without overwriting `observations`.
- Kept live daily ingest coverage behavior unchanged by sharing only the
  canonical current-row UPSERT helper.
- Tightened schema constraints after scout feedback: `existing_row_id` is
  required and `reason` is constrained to the two packet-approved values.
- Verification passed:
  - `python3 -m py_compile src/data/daily_observation_writer.py src/data/daily_obs_append.py src/state/db.py scripts/backfill_wu_daily_all.py scripts/backfill_hko_daily.py tests/test_backfill_completeness_guardrails.py tests/test_k2_live_ingestion_relationships.py tests/test_db.py`
  - `pytest -q tests/test_backfill_completeness_guardrails.py tests/test_k2_live_ingestion_relationships.py tests/test_backfill_scripts_match_live_config.py` -> 77 passed
  - `pytest -q tests/test_db.py tests/test_architecture_contracts.py tests/test_truth_surface_health.py` -> 162 passed, 46 skipped
  - `python3 scripts/topology_doctor.py --tests --json` -> ok
  - `python3 scripts/topology_doctor.py --scripts --json` -> ok
  - `python3 scripts/topology_doctor.py --freshness-metadata --changed-files <A2 source/script/test files>` -> ok
  - `python3 scripts/topology_doctor.py --planning-lock --changed-files <A2 packet files> --plan-evidence docs/operations/task_2026-04-25_p2_daily_observation_revision_history/plan.md` -> ok
  - `python3 scripts/topology_doctor.py --current-state-receipt-bound` -> ok
  - `python3 scripts/topology_doctor.py --work-record --work-record-path docs/operations/task_2026-04-25_p2_daily_observation_revision_history/work_log.md` -> ok
  - `git diff --check -- architecture docs scripts src tests` -> ok
- `python scripts/semantic_linter.py --check` passed for A2 source/script and
  focused tests. The broader impacted-test run still reports pre-existing
  fixture/static-check debt in legacy tests (`US-Northeast` fixture literals
  and one direct calibration table query); A2 did not add those reported lines.
- Runtime projection churn was committed separately at `b7aff79` before A2
  receipt closeout.

## 2026-04-25 -- review fixes
- Code review requested two fixes:
  - same-payload reruns must no-op by payload identity and not fail because of
    parser/provenance/data-source metadata churn
  - HKO revision rows must keep distinct CLMMAXT/CLMMINT component hashes in
    the high/low revision columns
- Fixed both in `src/data/daily_observation_writer.py`; added regressions in
  `tests/test_backfill_completeness_guardrails.py`.
- Post-fix verification passed:
  - `python3 -m py_compile <A2 Python files>` -> ok
  - `pytest -q tests/test_backfill_completeness_guardrails.py tests/test_k2_live_ingestion_relationships.py tests/test_backfill_scripts_match_live_config.py` -> 77 passed
  - `pytest -q tests/test_db.py tests/test_architecture_contracts.py tests/test_truth_surface_health.py` -> 162 passed, 46 skipped
  - `python scripts/semantic_linter.py --check <A2 source/script/focused tests>` -> ok

## 2026-04-25 -- closeout
- Implementation landed and pushed at `91d2a35`.
- Post-push runtime projection churn was isolated and pushed separately at
  `9ea3c1f` and `edc6bb6` so the next package starts without mixed runtime
  evidence.
- Next packet selected: P3 4.5.B reader-gate closeout for
  `observation_instants_v2` consumers. The next packet must not resolve the
  separate metric-layer design question.
