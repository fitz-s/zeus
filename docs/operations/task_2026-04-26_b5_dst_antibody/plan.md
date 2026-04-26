# Slice K3.B5-antibody — DST is_missing_local_hour regression test

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: parent packet `docs/operations/task_2026-04-26_live_readiness_completion/plan.md` Wave 1 + source workbook (archived) row B5. Pre-recon 2026-04-26 found writer + backfill script already shipped in earlier packets — only the regression antibody is missing.
Status: planning evidence; implementation has NOT begun.
Branch: `claude/live-readiness-completion-2026-04-26`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-live-readiness-2026-04-26`

## 0. Scope statement

Add the workbook-required regression antibody (`tests/test_obs_v2_dst_missing_hour_flag.py`) to pin DST-gap detection contract end-to-end. Writer field, source-fetch row construction, and historical backfill script all already ship; only the antibody is absent.

## 1. Audit results (2026-04-26)

| B5 sub-deliverable per workbook | Pre-existing artifact | Disposition |
|---|---|---|
| Writer fix (`src/data/observation_instants_v2_writer.py` accepts the flag) | `is_missing_local_hour: int = 0` field already present at L138; writer round-trips through column at L413 | **NO CHANGE NEEDED** |
| All callers compute the flag via `_is_missing_local_hour` | Confirmed at `wu_hourly_client.py:331`, `ogimet_hourly_client.py:402`, `meteostat_bulk_client.py:298` (always 0 — Meteostat hourly-aligned), `hourly_instants_append.py:153`, `daily_obs_append.py:668`. Central helper at `src/signal/diurnal.py:19`. | **NO CHANGE NEEDED** |
| One-shot backfill script | `scripts/fill_obs_v2_dst_gaps.py` (created 2026-04-22, last_reused 2026-04-25). Addresses the WU-API partial-data-on-DST-day failure mode by Ogimet fallback. | **NO CHANGE NEEDED** |
| Regression antibody `tests/test_obs_v2_dst_missing_hour_flag.py` | **ABSENT** | **THIS SLICE** |

## 2. Files touched

| File | Change | Hunk size |
|---|---|---|
| `tests/test_obs_v2_dst_missing_hour_flag.py` (NEW) | 6 antibody tests pinning helper + ObsV2Row round-trip + caller integration | ~110 lines |
| `architecture/test_topology.yaml` | Register new test | 1 line added |

**No edits** to `src/data/observation_instants_v2_writer.py` (writer correct), `src/signal/diurnal.py` (helper correct), or `scripts/fill_obs_v2_dst_gaps.py` (backfill exists).

## 3. Worktree-collision check (re-verified 2026-04-26)

- `tests/test_obs_v2_dst_missing_hour_flag.py`: NEW file. SAFE.
- `architecture/test_topology.yaml`: touched by `zeus-fix-plan-20260426` (P3 plan + tooling registration) — companion file; mesh maintenance only; soft-warn acceptable.

## 4. Antibody test design

Empirical fixture verification (run 2026-04-26 in this worktree against `src/signal/diurnal._is_missing_local_hour`):
- `Europe/London`, 2025-03-30 01:30 → True (in DST gap; clocks jumped 01:00→02:00)
- `Europe/London`, 2025-03-30 03:00 → False
- `America/New_York`, 2025-03-09 02:30 → True (in DST gap; clocks jumped 02:00→03:00)
- `America/New_York`, 2025-03-09 04:00 → False
- `America/New_York`, 2025-03-09 01:00 → False

Tests:

| Test | Asserts |
|---|---|
| `test_is_missing_local_hour_london_spring_forward` | `_is_missing_local_hour(2025-03-30 01:30, Europe/London)` is True |
| `test_is_missing_local_hour_atlanta_spring_forward` | `_is_missing_local_hour(2025-03-09 02:30, America/New_York)` is True |
| `test_is_missing_local_hour_returns_false_outside_gap` | Same dates, but post-gap timestamps return False (control) |
| `test_obs_v2_row_accepts_is_missing_local_hour_flag` | `ObsV2Row` constructor accepts `is_missing_local_hour=1`; field round-trips through `to_db_tuple()` |
| `test_obs_v2_row_default_is_missing_local_hour_is_zero` | Default value is 0 — pin contract; future field-rename catches drift |
| `test_obs_v2_writer_persists_dst_gap_flag` | Construct ObsV2Row with `is_missing_local_hour=1`, write via `append_obs_v2_rows()` to `:memory:` DB, query back, confirm `is_missing_local_hour=1` survived the round-trip |

## 5. RED→GREEN sequence

This slice is antibody-only — no production code change. RED is meaningful only for tests that depend on file existence (none); the body of each test executes against existing helper + writer, so RED is "test file does not exist". Once the test file lands, GREEN is immediate (because the underlying production code is already correct per recon).

1. Write `tests/test_obs_v2_dst_missing_hour_flag.py` with all 6 tests.
2. Run `pytest -q tests/test_obs_v2_dst_missing_hour_flag.py` — expect GREEN out of the gate (writer + helper already correct).
3. Single commit (no separate RED commit since there's no impl change to flip).
4. Register test in `architecture/test_topology.yaml`.
5. Write `receipt.json` and `work_log.md`.

This is a deliberate departure from G6's RED→GREEN ritual because the production code is pre-correct; the slice's only contribution is regression coverage, not behavior change.

## 6. Acceptance criteria

- All 6 tests in `tests/test_obs_v2_dst_missing_hour_flag.py` green.
- Lifecycle headers present.
- `architecture/test_topology.yaml` lists the test.
- Regression panel shows no NEW failures (delta=0).
- `receipt.json` records: branch, commit hash, test counts, ABSORBED-ELSEWHERE evidence for writer/script (cite earlier packets so reviewers don't expect new code).

## 7. Out-of-scope for this slice

- Modifying `src/signal/diurnal._is_missing_local_hour` — already correct.
- Modifying `src/data/observation_instants_v2_writer.py` — already correct.
- Modifying `scripts/fill_obs_v2_dst_gaps.py` — already addresses the historical-backfill concern.
- Adding a strict-rejection rule (e.g., reject any obs_v2 row whose local_timestamp is in DST gap but is_missing_local_hour=0). The flag is annotative by design; promoting to a hard reject would be a separate slice + operator decision.

## 8. Provenance

Recon performed live 2026-04-26 in this worktree:
- Writer field: `src/data/observation_instants_v2_writer.py:138` (`is_missing_local_hour: int = 0`).
- Caller computation paths: `wu_hourly_client.py:331`, `ogimet_hourly_client.py:402`, `meteostat_bulk_client.py:298`, `hourly_instants_append.py:153`, `daily_obs_append.py:668`.
- Central helper: `src/signal/diurnal.py:19` (`_is_missing_local_hour`).
- Backfill script: `scripts/fill_obs_v2_dst_gaps.py` (Lifecycle: created=2026-04-22, last_reused=2026-04-25).
- Live fixture verification: 5 datetime cases confirmed against helper, all results match expected DST behavior.
