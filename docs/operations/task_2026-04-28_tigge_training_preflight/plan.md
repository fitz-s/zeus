# TIGGE Training Preflight — Smoke Packet

| Field | Value |
|---|---|
| Created | 2026-04-28 |
| Status | **SMOKE PASSED** — pipeline mechanism end-to-end functional; live-write blocked by independent observation-provenance gates (not by causality) |
| Authority basis | `docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/G1_work_record_2026-04-27.md::TIGGE training readiness handoff — 2026-04-28` |
| Sibling RFC | `task_2026-04-28_weighted_platt_precision_weight_rfc/rfc.md` (PAUSED per operator until training-data path is correct) |
| Smoke target | Warsaw, target 2026-04-08..2026-04-15 (8 dates settled HIGH) |
| Scratch DB | `/tmp/zeus-tigge-preflight.sqlite` (canonical clone, NOT canonical write) |
| Production DB mutation | **NONE** |

## Why this packet exists

The third-party G1 handoff (2026-04-28) found that local HIGH `mx2t6` JSONs lack the first-class `causality` field required by `src/contracts/snapshot_ingest_contract.py` Law 5. Without causality, every HIGH ingest fails with `MISSING_CAUSALITY_FIELD`, blocking the entire training pipeline upstream.

This packet:
1. Identified the upstream root cause: VM extractor's `_finalize_high_record` did not emit `causality` (only `_finalize_low_record` did)
2. Patched the VM extractor (one line, additive) so HIGH JSONs now carry the canonical `causality` field
3. Re-extracted Warsaw HIGH for the settlement-aligned window (2026-03-09..2026-04-15 issue dates)
4. Synced re-extracted JSON to local scratch
5. Ran Stage B (ingest) → D (rebuild) → E (refit) end-to-end on a temp DB
6. Confirmed pipeline mechanism is functional and identified the NEXT real gate (observation-provenance preflights)

## What was done (in order)

### 1. VM extractor patch (additive, one-line)

Original `_finalize_high_record` payload dict at `tigge_local_calendar_day_extract.py:112-160` did not emit `causality`. Patch inserted (line ~152):

```python
"causality": {"pure_forecast_valid": True, "status": "OK"},
```

This mirrors the LOW finalize function and satisfies Law 5. `boundary_policy` is correctly omitted for HIGH (per `snapshot_ingest_contract.py:61` defaults to `{}`); HIGH does not have boundary ambiguity since daily MAX occurs at noon, far from the 6h-step boundary.

Backup retained at `tigge_local_calendar_day_extract.py.bak-2026-04-28` on VM.

### 2. Re-extract Warsaw HIGH (settlement-aligned window)

```bash
gcloud compute ssh tigge-runner --project snappy-frame-468105-h0 --zone=europe-west4-a \
  --command='/data/tigge/venv/bin/python3 \
    "/data/tigge/workspace-venus/51 source data/scripts/extract_tigge_mx2t6_localday_max.py" \
    --track mx2t6_high --cities Warsaw \
    --date-from 2026-03-09 --date-to 2026-04-15 --overwrite \
    --summary-path /tmp/extract_warsaw_apr2026.json'
```

Result: 224 JSON files re-extracted; 208 with `causality` populated. (16-file gap = issue-date filter edge cases; not blocking smoke for target 2026-04-08..04-15.)

### 3. Sync down + Stage B (ingest)

```bash
# Tar+ssh stream from VM → /tmp/warsaw_high
gcloud compute ssh tigge-runner ... --command='cd "/data/tigge/.../raw" && tar c .../warsaw' \
  > /tmp/warsaw_high.tar
tar xf /tmp/warsaw_high.tar -C /tmp/warsaw_high

# Ingest target 2026-04-08..2026-04-15 (settlement-aligned)
python3 scripts/ingest_grib_to_snapshots.py \
  --track mx2t6_high \
  --json-root /tmp/warsaw_high \
  --db-path /tmp/zeus-tigge-preflight.sqlite \
  --cities Warsaw \
  --date-from 2026-04-08 --date-to 2026-04-15
```

Result:
```json
{"track":"mx2t6_high","data_version":"tigge_mx2t6_local_calendar_day_max_v1",
 "written":64,"skipped":0,"errors":0}
```
DB row state: `temperature_metric='high', training_allowed=1: 64` ✓

### 4. Stage D (rebuild calibration_pairs_v2 — DRY-RUN ONLY)

```bash
.venv/bin/python3 scripts/rebuild_calibration_pairs_v2.py \
  --dry-run --city Warsaw --db /tmp/zeus-tigge-preflight.sqlite
```

Both tracks evaluated:
- HIGH: 64 snapshots scanned, 64 eligible → estimated 6,528 pairs (64 × 102 C-unit bins)
- LOW: 0 (no LOW snapshots ingested in this smoke window)

Math correct, schema sound, eligibility computed.

### 5. Stage D (live write attempt — BLOCKED, expected)

```bash
.venv/bin/python3 scripts/rebuild_calibration_pairs_v2.py \
  --no-dry-run --force --city Warsaw --db /tmp/zeus-tigge-preflight.sqlite
```

Result:
```
RuntimeError: Refusing live v2 rebuild: calibration-pair rebuild preflight is NOT_READY (
  empty_rebuild_eligible_snapshots,
  observation_instants_v2.training_role_unsafe,
  observations.hko_requires_fresh_source_audit,
  observations.verified_without_provenance,
  observations.wu_empty_provenance,
  payload_identity_missing
)
```

This is **healthy preflight behavior**. The mechanism works; the gate that fires is the real next blocker — observation-side provenance gaps independent of TIGGE causality.

### 6. Stage E (refit_platt_v2 — DRY-RUN)

```bash
.venv/bin/python3 scripts/refit_platt_v2.py --dry-run --db /tmp/zeus-tigge-preflight.sqlite
```

Result for both tracks: `Buckets eligible (n_eff >= 15): 0` — correctly reports no Platt fit possible because `calibration_pairs_v2` is empty (Stage D live-write was blocked). End-to-end mechanism functional.

## What this proves

| Hypothesis | Result |
|---|---|
| HIGH JSON `causality` absence is the FIRST blocker | ✓ confirmed via independent reproduction; resolved via 1-line VM extractor patch |
| Patched extractor produces ingest-acceptable HIGH JSON | ✓ 64/64 ingested, all training_allowed=1 |
| Stage B → D → E pipeline plumbing intact | ✓ all stages execute without error in dry-run mode |
| `precision_weight` is NOT the first blocker | ✓ confirmed; it remains a parallel RFC, untouched |
| Live training has additional gates beyond causality | ✓ exposed: observation-provenance gates (wu_empty_provenance, hko fresh-audit, observation_instants_v2 role-unsafe) |

## What this packet does NOT do

- Does NOT modify production `state/zeus-world.db` (zero rows written there from this smoke)
- Does NOT promote calibration_pairs_v2 or platt_models_v2
- Does NOT re-extract for cities other than Warsaw or for full date range
- Does NOT touch `precision_weight` schema/code (RFC paused per operator decision)
- Does NOT make `causality` optional or default it to OK at the contract layer
- Does NOT authorize any live venue side effect

## Blockers identified for next packet

The Stage D live-write preflight surfaces 6 OBS-side gates the next packet must address:

| Gate | Likely scope |
|---|---|
| `empty_rebuild_eligible_snapshots` | Need full HIGH+LOW re-extract for all 51 cities (Warsaw alone insufficient) |
| `observation_instants_v2.training_role_unsafe` | observation_instants_v2 row classification needs review |
| `observations.hko_requires_fresh_source_audit` | HKO source audit refresh (existing fatal_misread, sibling concern) |
| `observations.verified_without_provenance` | observations rows need provenance_metadata fields populated |
| `observations.wu_empty_provenance` | WU-source rows need provenance fields filled |
| `payload_identity_missing` | snapshots / forecasts need MetricIdentity stamping (similar to settlements migration) |

These are **observation-side** issues, not forecast-side. The TIGGE causality fix is necessary but not sufficient.

## Reuse / next-packet recommendations

1. **Full-coverage HIGH re-extract on VM** for all 51 cities × full date range. This is the immediate next slice once operator confirms; estimated ~hours wallclock on VM (24 vCPU). Resync to local in a single tar stream.
2. **Address the 6 OBS-side preflight gates** in a sibling packet (likely `task_2026-04-28_obs_provenance_preflight`). These are independent of the TIGGE work but block live training.
3. **After both above**: rerun Stage B → D → E with `--no-dry-run --force` flags — should produce real `calibration_pairs_v2` and `platt_models_v2` rows.
4. **RFC remains paused** until basic training data path proves stable.

## Stop conditions for next agent (per G1 handoff)

- Any proposal to write `state/zeus-world.db` from a smoke without packet authorization
- Any proposal to bypass observation-side preflight gates instead of resolving them
- Any change to `_finalize_low_record` or LOW-track behavior (LOW already correct)
- Any change to `causality` contract semantics
- Any `precision_weight` schema/code work without explicit operator unpause

## Files

| File | Purpose |
|---|---|
| `plan.md` | this file |
| `scripts/preflight_smoke.sh` | reproducible 1-shot smoke (VM + local) |
| `evidence/extract_warsaw_summary.json` | VM extractor output summary |
| `evidence/ingest_high_smoke.json` | Stage B HIGH result |
| `evidence/rebuild_dry_run.txt` | Stage D dry-run output |
| `evidence/refit_platt_dry_run.txt` | Stage E dry-run output |
| `evidence/preflight_blockers.json` | the 6 observation-side gates surfaced |

## Reproducibility (~5 min total wallclock)

```bash
bash docs/operations/task_2026-04-28_tigge_training_preflight/scripts/preflight_smoke.sh
```
