# subdir-dict monkey-patch process-global race surface

<!-- Created: 2026-05-13 -->
<!-- Last reused or audited: 2026-05-13 -->
<!-- Authority basis: PR #108 "Out of scope" note; Task #53 -->

## Symptom

During an Open Data ingest cycle, `ingest_track()` inside
`scripts/ingest_grib_to_snapshots.py` reads `_TRACK_CONFIGS[track]["json_subdir"]`
to build the filesystem path for JSON discovery.  If two `collect_open_ens_cycle`
calls mutate that dict entry concurrently, one call can read the wrong subdir
and either silently ingest zero rows (path doesn't exist) or ingest rows from
the wrong source family into the forecasts DB without raising an error.

## Root cause

**Writer** (monkey-patch):
`src/data/ecmwf_open_data.py:1094`
```
_ingest_grib_module._TRACK_CONFIGS[cfg["ingest_track"]]["json_subdir"] = cfg["extract_subdir"]
```
Restore in `finally`:
`src/data/ecmwf_open_data.py:1132`

**Reader** (consumes patched value):
`scripts/ingest_grib_to_snapshots.py:721-724`
```
cfg = _TRACK_CONFIGS[track]
subdir = json_root / cfg["json_subdir"]
```

**Dict definition** (module-global, mutable):
`scripts/ingest_grib_to_snapshots.py:74-85`
```
_TRACK_CONFIGS: dict[str, dict[str, Any]] = {
    "mx2t6_high": { "json_subdir": "tigge_ecmwf_ens_mx2t6_localday_max", ... },
    "mn2t6_low":  { "json_subdir": "tigge_ecmwf_ens_mn2t6_localday_min", ... },
}
```

**Race surface**: `_opendata_startup_catch_up` (registered on the "fast" APScheduler
executor, `max_workers=4`) loops over both tracks sequentially and calls
`collect_open_ens_cycle` for each.  It runs concurrently with the 07:30/07:35 UTC
cron ticks registered on the "default" executor (`max_workers=1`).  Each cron tick
calls `collect_open_ens_cycle` for one track.  If a startup catch-up call for
track A is in the mutation window (`lines 1094–1132`) at the same moment the cron
tick for track A fires, the cron tick's `ingest_track()` reads the already-patched
subdir — either the correct Open Data path (harmless) or, if the finally-restore
has already run, the reverted TIGGE path — depending on scheduler timing.

Because each track key (`mx2t6_high`, `mn2t6_low`) is mutated independently, a
cross-track race (mx2t6 writer corrupts mn2t6 reader) is structurally impossible;
the race is intra-track only.

## Mitigation in place

ECMWF Open Data posts 00Z runs by ~07:00 UTC; the cron ticks fire at 07:30/07:35 UTC.
The startup catch-up fires once at daemon boot.  In practice, daemon restarts do not
coincide with the 07:30/07:35 window — the scheduling separation means the startup
catch-up completes (or times out) well before the next daily cron tick arrives.
The race window is therefore empirically narrow and has not been observed in production
logs (2026-05-11 wedge was a different issue: TIGGE holding db_writer_lock).

## Proposed fix

### (a) Threading lock around the mutation

Add a per-track `threading.Lock` in `ecmwf_open_data.py`; acquire before
read-mutate-restore, release in `finally`.  Wrap the reader in `ingest_track()`
with the same lock (requires the lock to be importable from `ecmwf_open_data` or
a shared module).

**Trade-offs**:
- Pro: minimal diff (~10 LOC), zero API change to `ingest_track()`.
- Con: couples `ingest_grib_to_snapshots.py` (a standalone script) to a lock
  defined in the module that imports it — inverted dependency.  Scripts run as
  `__main__` would bypass the lock entirely.
- Con: does not eliminate the monkey-patch; leaves the conceptual debt.

### (b) Pass `json_subdir` explicitly instead of mutating global dict (preferred)

Add an optional `json_subdir_override: str | None = None` parameter to `ingest_track()`.
When provided, shadow the dict lookup locally:

```python
subdir_name = json_subdir_override if json_subdir_override is not None else cfg["json_subdir"]
subdir = json_root / subdir_name
```

At the call site in `ecmwf_open_data.py`, pass `cfg["extract_subdir"]` directly:

```python
summary = _ingest_grib_ingest_track(
    track=cfg["ingest_track"],
    json_subdir_override=cfg["extract_subdir"],
    ...
)
```

Remove the read-mutate-restore block entirely (`lines 1093–1132` reduced to 0 LOC
of mutation code).

**Trade-offs**:
- Pro: eliminates the race category structurally — no shared mutable state.
- Pro: `_TRACK_CONFIGS` becomes genuinely immutable at runtime.
- Pro: `ingest_track()` becomes re-entrant; safe for any future parallelism.
- Con: `ingest_track()` is also a CLI entry point; the new kwarg must be
  wired through the `argparse` path or documented as internal-only.
- Con: ~15 LOC change across 2 files, plus 1-2 test updates.

## Cost estimate

| Fix | Files | LOC delta | Test changes |
|-----|-------|-----------|--------------|
| (a) lock | `ecmwf_open_data.py`, `ingest_grib_to_snapshots.py` | +15 | None — existing tests pass through |
| (b) explicit param | `ecmwf_open_data.py`, `scripts/ingest_grib_to_snapshots.py` | +10 / -8 net | 1-2 tests that stub `ingest_track` may need `json_subdir_override=None` in call signature |

Recommended: fix (b). The monkey-patch existed because `ingest_track()` had no
override parameter; adding one is a backwards-compatible, structurally correct fix
with lower ongoing maintenance cost than a lock guarding a design that shouldn't exist.
