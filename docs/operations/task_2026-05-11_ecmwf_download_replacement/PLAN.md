# ECMWF Open Data Download — One-Shot Structural Replacement PLAN (v3 — addressing critic R2)

- Created: 2026-05-11
- Last revised: 2026-05-11 (v3 — addressing critic R2 NEEDS_MINOR_REVISION; structural choice unchanged)
- Authority basis: user directive 2026-05-11; prior dossier `docs/operations/task_2026-05-08_phase_b_download_root_cause/DOSSIER.md`; SDK source `/Users/leofitz/miniconda3/lib/python3.14/site-packages/ecmwf/opendata/client.py`; multiurl source; critic R1 (resolved in v2); critic R2 (1 blocker + 1 critical nit + 3 minor — addressed below).
- Status: v3 — for third critic round (R2 confirmed structural choice intact; only edits required).
- Recommendation: **Candidate H — Parallel SDK invocations with per-step file boundaries + partial-cycle source_run contract**; mode SHORT.

## v3 diff summary

- **R2-Blocker (A6 function-name miscitation)** — §6 A6 now cites both `evaluate_horizon_coverage` (`forecast_target_contract.py:136-145`, gates near-horizon trading via `required_steps` only) AND `evaluate_producer_coverage` (`:148-194`, line 184 is the `missing_steps = expected - observed` consumer of `observed_steps_json`). §2 verified-facts retains its existing correct citation.
- **R2-Critical-Nit (extract-on-PARTIAL wiring)** — §5.1 control-flow restructured: `_emit_source_run` is invoked at end-of-cycle (after extract+ingest), not before. PARTIAL falls through to the existing extract block (`ecmwf_open_data.py:671-691`, fires unconditionally when `skip_extract=False`); only `FAILED` and pure `SKIPPED_NOT_RELEASED` early-return before extract. Existing code already extracts unconditionally — no extra edit needed beyond ordering the new control flow correctly.
- **R2-Minor-1 (REL-6 baseline)** — REL-6 baseline now points to a concrete on-disk historical GRIB at `/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/ecmwf_open_ens/ecmwf/20260504/open_ens_20260504_00z_steps_6-12-…-240_params_mx2t6.grib2` (grep-verified present 2026-05-11).
- **R2-Minor-2 (F2 SQL dead clause)** — `'%quota%'` removed from the NOT-IN allow-list; documented in §7 F2.
- **R2-Minor-3 (smoke probe date rot)** — §5.0 probe uses `(datetime.utcnow() - timedelta(days=1)).strftime('%Y%m%d')`.
- **R2-Minor-4 (probe assertion comment)** — §5.0 adds `# param=1 × member=51 (cf=1 + pf=50) × step=1 = 51 messages; if param-list expands, multiply`.

Items marked RESOLVED by R2 (manifest_sha256 hash domain; max_workers=5; atomic resume; F2 NOT-IN allow-list scaffold; smoke-probe presence; partial-cycle FSM mapping) preserved unchanged below.

---

## 1. Root cause (single structural decision) — unchanged

The 6 observed failure modes share one design choice in `src/data/ecmwf_open_data.py`: **"71 (step × param) sub-fetches → 1 monolithic target file, executed by a single serial subprocess with no failure boundary smaller than the whole job."** SDK `client.py:319-346` `get_parts` iterates `for url in data_urls` serially; no `nthreads` knob exists in SDK or `multiurl`. Adopt per-step file boundaries and modes (3),(4),(5),(6) collapse; (1),(2) follow as side-effects. K=1 structural decision masquerading as N=6 bugs (Fitz §1).

## 2. Verified facts (grep-anchored 2026-05-11) — unchanged

- **SDK fully serial.** `client.py:319-346`. No async / parallel knob.
- **SDK thread-safe per-Client.** `client.py:138` per-Client `requests.Session()`.
- **`step` in both URL_COMPONENTS and INDEX_COMPONENTS** (`client.py:453, 460`).
- **`manifest_sha256` hashes a JSON manifest file, not GRIB bytes.** `/Users/leofitz/.openclaw/workspace-venus/51 source data/scripts/tigge_local_calendar_day_common.py:42-43`. Concat-order independence is by construction.
- **5-status `source_run` FSM**: `src/state/source_run_repo.py:14` — `{RUNNING, SUCCESS, FAILED, PARTIAL, SKIPPED_NOT_RELEASED}`; `:15` completeness; `:93-94` `partial_run ⇔ completeness=PARTIAL`; columns `observed_steps_json`, `expected_steps_json`, `reason_code` at `:84-85, :148-149, :157`.
- **Per-step coverage consumer**: `src/data/forecast_target_contract.py:148-194` `evaluate_producer_coverage`; line 184: `missing_steps = set(expected_steps) - set(observed_steps)` → `MISSING_REQUIRED_STEPS`. Horizon gate at `:136-145` `evaluate_horizon_coverage` consumes `required_steps` + `live_max_step_hours` only (no observed_steps).
- **Extract block fires unconditionally** when `skip_extract=False`: `src/data/ecmwf_open_data.py:671-691`. Only failure paths above it (`download_failed`) early-return before extract; PARTIAL must fall through.
- **mx2t6_high vs mn2t6_low concurrency**: `src/ingest_main.py:1133-1142` — minute=30 vs minute=35, both default executor pool, different job IDs → cross-job overlap possible. Worst-case in-flight = 2 × `_DOWNLOAD_MAX_WORKERS`.
- **AWS / GCP mirrors uncapped.** Only `source='ecmwf'` origin has the 500-connection limit (`client.py:142-151`).
- **Open-Meteo entry_primary rejected by calibration contract** (unchanged).
- **Prior dossier step-availability lag** (DOSSIER §C) absorbed as `PARTIAL` + `SKIPPED_NOT_RELEASED` per-step path.

## 3. Candidate matrix — unchanged (see v1)

H selected; A runner-up; B/C/D/E/F/G rejected.

## 4. First-principles verdict — **Candidate H** (unchanged)

Net +20 LOC. Load-bearing invariant is `manifest_sha256` over the manifest JSON (concat-order does not enter) plus per-message content equality. Partial-cycle property emerges through `source_run.status=PARTIAL` + `observed_steps_json`.

## 5. Migration steps

### 5.0 — Pre-implementation smoke probe (BLOCKING gate)

```python
from datetime import datetime, timedelta
from ecmwf.opendata import Client
yday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y%m%d")
Client(source="aws").retrieve(
    date=int(yday), time=0, stream="enfo", type=["cf", "pf"],
    step=[3], param="mx2t3", target="/tmp/probe_step3.grib2",
)
# param=1 × member=51 (cf=1 + pf=50) × step=1 = 51 messages; if param-list expands, multiply.
import eccodes
with open("/tmp/probe_step3.grib2", "rb") as f:
    count = 0
    while (h := eccodes.codes_grib_new_from_file(f)) is not None:
        count += 1
        eccodes.codes_release(h)
    assert count == 51, count
```

If this fails, **stop and re-design**. Only proceed if probe passes.

### 5.1 — Replace `src/data/ecmwf_open_data.py:608-669` with parallel SDK fetch + restructured control flow

Module-level constants (antibody-style; no call-site kwargs):

```python
_DOWNLOAD_MAX_WORKERS = 5
_PER_STEP_TIMEOUT_SECONDS = 90
_PER_STEP_MAX_RETRIES = 3
_PER_STEP_RETRY_AFTER = 10
_RETRYABLE_HTTP = {500, 502, 503, 504, 408, 429}
```

Per-step task wrapper (own retry budget; SDK `maximum_retries=500` bypassed):

```python
def _fetch_one_step(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
    canonical = output_dir / f".step{step:03d}.grib2"
    partial   = canonical.with_suffix(".grib2.partial")
    if canonical.exists() and canonical.stat().st_size > 0:
        return ("OK", canonical)                                       # N1 resume
    last_err = None
    for mirror in mirrors:
        for attempt in range(_PER_STEP_MAX_RETRIES):
            try:
                Client(source=mirror).retrieve(
                    date=int(cycle_date.strftime("%Y%m%d")),
                    time=cycle_hour, stream="enfo", type=["cf", "pf"],
                    step=[step], param=[param], target=str(partial),
                )
                os.replace(partial, canonical)                          # N1 atomic
                return ("OK", canonical)
            except requests.HTTPError as e:
                code = getattr(e.response, "status_code", None)
                if code == 404:
                    return ("NOT_RELEASED", None)
                if code in _RETRYABLE_HTTP:
                    last_err = f"HTTP_{code}_mirror_{mirror}_attempt_{attempt}"
                    time.sleep(_PER_STEP_RETRY_AFTER); continue
                last_err = f"HTTP_{code}_mirror_{mirror}"; break
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = f"NET_{type(e).__name__}_mirror_{mirror}"
                time.sleep(_PER_STEP_RETRY_AFTER); continue
    return ("FAILED", last_err or "EXHAUSTED")
```

**Restructured top-level flow (R2-critical-nit: extract MUST fire on PARTIAL).** Replace the current 608-669 block. `_emit_source_run` moves to end-of-cycle so extract+ingest run on `{SUCCESS, PARTIAL}`; only `{FAILED, pure-SKIPPED_NOT_RELEASED}` short-circuit before extract:

```python
tasks = [(s, cfg["open_data_param"]) for s in STEP_HOURS]
results: dict[int, tuple[str, str|None]] = {}
output_dir = output_path.parent
with ThreadPoolExecutor(max_workers=_DOWNLOAD_MAX_WORKERS) as ex:
    fut2step = {ex.submit(_fetch_one_step,
                          cycle_date=cycle_date, cycle_hour=cycle_hour,
                          param=p, step=s, output_dir=output_dir,
                          mirrors=_DOWNLOAD_SOURCES): s for s, p in tasks}
    for fut in as_completed(fut2step):
        results[fut2step[fut]] = fut.result()[:2]

ok_steps     = sorted(s for s, (st, _) in results.items() if st == "OK")
released_404 = sorted(s for s, (st, _) in results.items() if st == "NOT_RELEASED")
failed       = sorted(s for s, (st, _) in results.items() if st == "FAILED")

# Early-return branches: NOT entered for SUCCESS or PARTIAL — those fall through.
if failed:
    _emit_source_run(status="FAILED", completeness="MISSING",
                     partial_run=False, observed_steps=ok_steps,
                     reason_code=";".join(f"step{s}:{results[s][1]}" for s in failed[:5]))
    return {"status": "download_failed", "track": track,
            "data_version": cfg["data_version"], "stages": stages,
            "snapshots_inserted": 0}
if not ok_steps and released_404:
    _emit_source_run(status="SKIPPED_NOT_RELEASED", completeness="NOT_RELEASED",
                     partial_run=False, observed_steps=(),
                     reason_code=f"NOT_RELEASED_STEPS={released_404}")
    return {"status": "skipped_not_released", "track": track,
            "data_version": cfg["data_version"], "stages": stages,
            "snapshots_inserted": 0}

# SUCCESS (no released_404, no failed) OR PARTIAL (some OK + some released_404).
# Both paths concat the OK steps, then fall through to the existing
# extract block at ecmwf_open_data.py:671-691 and ingest block at :693+.
_concat_steps(ok_steps, output_path)
_partial_cycle = bool(released_404)   # branch flag for end-of-cycle _emit_source_run
```

After the existing extract+ingest blocks run (lines 671 onward, unchanged), the existing return path is augmented to emit the source_run row with status `PARTIAL` or `SUCCESS` plus `observed_steps_json=ok_steps`:

```python
# End-of-cycle, after the existing ingest stage closes its db_writer_lock:
_emit_source_run(
    status="PARTIAL" if _partial_cycle else "SUCCESS",
    completeness="PARTIAL" if _partial_cycle else "COMPLETE",
    partial_run=_partial_cycle,
    observed_steps=ok_steps,
    reason_code=(f"NOT_RELEASED_STEPS={released_404}" if _partial_cycle else None),
)
```

`_concat_steps` writes per-step `.step{NNN}.grib2` files in ascending-step binary append into `output_path` (GRIB2 self-delimiting; extractor is order-invariant by key — REL-1, REL-6).

### 5.2 — Delete `/Users/leofitz/.openclaw/workspace-venus/51 source data/scripts/download_ecmwf_open_ens.py` after 5.1 lands green.

### 5.3 — File-provenance headers per CLAUDE.md.

### 5.4 — Tests (`tests/test_ecmwf_open_data_parallel_fetch.py`)

- `test_all_ok_returns_SUCCESS_COMPLETE`
- `test_some_404_returns_PARTIAL_PARTIAL_and_extract_fires` — **new assertion**: extract subprocess IS invoked when some steps are OK and some are 404 (mock `runner` and verify call with `label="extract_…"`); `status='PARTIAL'`; `observed_steps_json` is near-horizon subset.
- `test_all_404_returns_SKIPPED_NOT_RELEASED_and_extract_skipped`
- `test_non_404_retry_exhaustion_returns_FAILED_and_extract_skipped`
- `test_resume_via_atomic_rename`
- `test_partial_file_does_not_count_as_resume`
- `test_concat_order_step_ascending`
- `test_thread_safety_max_workers_5`

### 5.5 — Topology mesh: `topology_doctor.py --map-maintenance --changed-files src/data/ecmwf_open_data.py tests/test_ecmwf_open_data_parallel_fetch.py`.

### 5.6 — CI green; `launchctl kickstart -k gui/$(id -u)/com.zeus.data-ingest`.

## 6. Test plan — relationship invariants (Fitz §3)

- **REL-1** Successful merged `output_path` yields exactly `set(observed_steps)` distinct `(stepRange, member)` keys per param.
- **REL-2** SIGTERM-resume idempotence: canonical files NOT re-fetched; `.partial` files ARE re-fetched.
- **REL-3** Per-step independence: step=147 → infinite 503 must not block steps 3..144.
- **REL-4** Mirror failover convergence: aws 30%-503 → merged result complete via google/ecmwf retry.
- **REL-5** `manifest_sha256` invariance (concat-order does not enter the hash).
- **REL-6 (R2-Minor-1 — concrete baseline)** Pre-deploy in CI: fetch one cycle via new parallel path; reference baseline is the on-disk historical successful cycle at `/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/ecmwf_open_ens/ecmwf/20260504/open_ens_20260504_00z_steps_6-12-…-240_params_mx2t6.grib2` (and the matching `..._params_mn2t6.grib2`, both grep-verified present 2026-05-11). Run `extract_open_ens_localday.py` on both; assert per-`(member, step, city, target_local_date)` payload **content hash** is bit-identical across (parallel-fetched, today's run-date) vs (historical, 2026-05-04). For paths where the run-date differs, equality is asserted only on the schema/contract fields (manifest keys + member count + step set + payload-encoding consistency); operator-confirmed before merge. Lives at `tests/test_ecmwf_parallel_vs_historic_parity.py`, gated by `ZEUS_NETWORK_TESTS=1`.

### Acceptance (post-deploy)

- **A1** Tests in §5.4 pass.
- **A2** REL-1..REL-6 pass.
- **A3** 48 h post-deploy: `SELECT MAX(DATE(recorded_at)) FROM source_run WHERE source_id='ecmwf_open_data' AND status IN ('SUCCESS','PARTIAL');` ≥ today-1.
- **A4** Wall-clock p95 cycle fetch < 300 s.
- **A5** Zero SIGKILL events in `tmp/ecmwf_open_data_*.stderr.txt` over 48 h.
- **A6 (R2-Blocker fix — partial-cycle binding, two-function decomposition)** Far-horizon-only 404 cycle: `SELECT status, completeness_status, observed_steps_json FROM source_run WHERE source_id='ecmwf_open_data' ORDER BY recorded_at DESC LIMIT 1;` returns `(PARTIAL, PARTIAL, [near-horizon subset])`. Downstream behavior splits across two functions in `src/data/forecast_target_contract.py`:
  - `evaluate_horizon_coverage` (lines **136-145**, signature `(required_steps, live_max_step_hours)` only) gates whether near-horizon trading is admissible at all — it does NOT consume `observed_steps`; a city whose required steps stay within `live_max_step_hours` remains `LIVE_ELIGIBLE` at the horizon-gate level regardless of partial-cycle status.
  - `evaluate_producer_coverage` (lines **148-194**, line **184** `missing_steps = set(expected_steps) - set(observed_steps)` → `BLOCKED("MISSING_REQUIRED_STEPS")`) is the per-step consumer of the populated `observed_steps_json`. Cities whose required steps fall entirely within `ok_steps` clear this function; cities needing steps in `released_404` remain BLOCKED on that specific reason code, not on the whole cycle.
  - **Net A6 outcome**: near-horizon trading proceeds for in-coverage cities; only the far-horizon market subset is held — which is exactly the DOSSIER §C latency-window finding absorbed as a structural property.
- **A7** `_DOWNLOAD_SOURCES` priority observable in logs: per-step debug line `mirror_first_try=aws`.
- **A8** Code Review Graph: `topology_doctor.py --code-review-graph-status --json` no orphan edges from deleted subprocess.

## 7. Falsification triggers

- **F1 (fast-fail, 48 h)** Any of A1-A8 fail → `git revert`; per-step cleanup `find "51 source data/raw/ecmwf_open_ens" -name '*.step*.grib2*' -delete`.
- **F2 (R2-Minor-2 — concrete failure-class detection, 7 d)** Run nightly (dead `'%quota%'` clause removed; current code paths produce no quota-tagged `reason_code`):
  ```sql
  SELECT source_cycle_time, reason_code FROM source_run
   WHERE source_id='ecmwf_open_data'
     AND status='FAILED'
     AND recorded_at >= datetime('now','-7 days')
     AND NOT (
       reason_code LIKE '%HTTP_404%'
       OR reason_code LIKE '%NOT_RELEASED%'
       OR reason_code LIKE '%HTTP_429%'
     );
  ```
  Any row returned = a non-data-class failure (post-refactor regression).
- **F3 (REL-5/6 regression, 14 d)** Manifest hash drift in production. Severity HIGH — revert + audit message-iteration assumption.
- **F4 (mirror throttling)** Any `HTTP_429` rows in F2 query. Reduce `_DOWNLOAD_MAX_WORKERS` 5→3 (single-constant edit).

## 8. Rollback

Single commit. `git revert <commit>` + `launchctl kickstart -k gui/$(id -u)/com.zeus.data-ingest`. < 60 s. Orphan `.step*.grib2(.partial)?` files cleaned by one-line `find`.

## 9. 不做 (negative scope) — unchanged

No new launchd daemon / state file / SLA monitor / entry_primary switch / SDK rewrite / contract-surface change / call-site kwargs / subprocess-script retention.

## 10. Pre-mortem (top 3, probability subjective) — unchanged

1. **(p≈10%)** Per-step concat order leaks into a downstream consumer we have not audited. Mitigation: REL-6 pre-deploy parity probe.
2. **(p≈10%)** Concurrent mx2t6 + mn2t6 cycles trip aws/gcp throttling. Mitigation: F4 drop-to-3.
3. **(p≈5%)** `_RETRYABLE_HTTP` set incomplete. Mitigation: F2 surfaces unexpected `reason_code` within 24 h.

## 11. ADR — unchanged

- **Decision**: replace subprocess-wrapped serial-SDK monolithic download with in-process parallel-SDK per-step fetches; bind partial-cycle path to existing `source_run` 5-status FSM + `forecast_target_contract.evaluate_producer_coverage:184` per-step consumer.
- **Drivers**: K=1 structural failure; "一次性彻底" + "no new system"; Platt calibration baseline preservation; SDK forward-compat ownership boundary.
- **Alternatives**: A/B/C/D/E/F/G (§3).
- **Why H**: +20 net LOC; SDK retains index parsing + byte-range + mirror auth; absorbs DOSSIER §C latency lag as a partial-cycle property without new schema/contract.
- **Consequences accepted**: manifest_sha256 invariance over manifest JSON (not GRIB bytes); per-Client `requests.Session` thread-safety; atomic `.partial → os.replace` resume; orphan per-step files on FAILED cycle (one-line cleanup); ≤10 in-flight under concurrent mx2t6/mn2t6 (F4 covers throttling).
- **Follow-ups**: evaluate raising `_DOWNLOAD_MAX_WORKERS` 5→8 after 14 d clean; optional per-step timing telemetry into `source_run.metadata_json`.

## 12. Trade-off summary (one line)

**Replace the monolithic subprocess-wrapped serial-SDK download with in-process `ThreadPoolExecutor(max_workers=5)` parallel SDK calls at per-step file granularity, atomic `.partial → os.replace` resume, concat on success, and a partial-cycle contract that maps per-step 404 vs retry-exhaustion vs all-failed onto `source_run` `{SUCCESS, PARTIAL, SKIPPED_NOT_RELEASED, FAILED}` × `observed_steps_json` (consumed by `forecast_target_contract.evaluate_producer_coverage:184` for per-step missing detection; `evaluate_horizon_coverage:136-145` gates near-horizon admissibility — see §6 A6); delete the subprocess script; preserve `data_version` / mirror priority / Platt route — making the unbounded-monolithic-fetch CATEGORY structurally impossible at +20 net LOC.**
