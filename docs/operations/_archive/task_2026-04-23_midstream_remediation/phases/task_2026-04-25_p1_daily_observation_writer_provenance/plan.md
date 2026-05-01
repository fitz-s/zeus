# P1 Daily Observation Writer Provenance Plan

Date: 2026-04-25
Branch: `midstream_remediation`

## Decision

Implement a narrow 4.3.B-lite packet: prevent daily observation backfill writers
from creating new `VERIFIED` rows with empty or weak provenance metadata.

This packet intentionally fixes the writer source of the WU empty-provenance
category before any row-level quarantine of the existing 39,431 legacy rows.
Existing unsafe rows are already fail-closed by `scripts/verify_truth_surfaces.py`;
mutating canonical DB truth remains a later repair packet.

## Scope

Allowed implementation files:

- `scripts/backfill_wu_daily_all.py`
- `scripts/backfill_hko_daily.py`
- `tests/test_k2_live_ingestion_relationships.py`

Control/evidence files:

- `architecture/test_topology.yaml`
- `architecture/topology.yaml`
- `architecture/docs_registry.yaml`
- `docs/AGENTS.md`
- `docs/README.md`
- `docs/operations/current_state.md`
- `docs/operations/AGENTS.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p1_daily_observation_writer_provenance/plan.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p1_daily_observation_writer_provenance/work_log.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p1_daily_observation_writer_provenance/receipt.json`

## Acceptance

- WU daily backfill writes non-empty per-row provenance identity for new
  `VERIFIED` observations, including payload hash, source URL, parser version,
  station/source identity, requested range, and target date.
- HKO daily backfill writes the same identity class for combined high/low
  observations, including high and low component payload hashes.
- No production DB rows are mutated.
- No schema/view migration, no `INSERT OR REPLACE` overhaul, and no P2/P3/P4
  migration work is included.

## Verification

- `python3 scripts/topology_doctor.py --task-boot-profiles --json`
- `python3 scripts/topology_doctor.py --fatal-misreads --json`
- `.venv/bin/python -m py_compile scripts/backfill_wu_daily_all.py scripts/backfill_hko_daily.py tests/test_k2_live_ingestion_relationships.py`
- `.venv/bin/python -m pytest tests/test_k2_live_ingestion_relationships.py -q`
- `.venv/bin/python -m pytest tests/test_backfill_scripts_match_live_config.py -q`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p1_daily_observation_writer_provenance/plan.md --json`
- `python3 scripts/topology_doctor.py --work-record --changed-files <files> --work-record-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p1_daily_observation_writer_provenance/work_log.md --json`
- `python3 scripts/topology_doctor.py --change-receipts --changed-files <files> --receipt-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p1_daily_observation_writer_provenance/receipt.json --json`
- Critic review before closeout.
