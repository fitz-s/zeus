# Locate Audit: 2026-06-16

## 1. `scripts/migrations/normalize_observation_instants_z_suffix.py`

**File:** `scripts/migrations/normalize_observation_instants_z_suffix.py`

### Invocation interface (lines 129-151):

```python
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=_DEFAULT_DB,
        help=f"Path to zeus-world.db (default: {_DEFAULT_DB})",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Apply the migration (default: dry-run only)",
    )
    args = parser.parse_args(argv)
    run(args.db_path, execute=args.execute)


if __name__ == "__main__":
    main()
```

### How to invoke:

- **Dry-run (default):** `python scripts/migrations/normalize_observation_instants_z_suffix.py`
  - Prints count of Z-suffix rows without mutation
- **Apply:** `python scripts/migrations/normalize_observation_instants_z_suffix.py --execute`
  - Updates rows and asserts count == 0 post-migration
- **Custom DB path:** Pass `--db-path <path>` (default: `_DEFAULT_DB`)

### DB path constant (lines 32-36):

```python
# Default DB path (operator can override via --db-path).
# Resolved relative to this script's repo root so the script is portable across
# worktrees without hard-coding an absolute host path.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB = _REPO_ROOT / "zeus-world.db"
```

**Targets:** `zeus-world.db` (observation_instants table; world-class truth)

**Dry-run semantics:** Pass nothing (default) → dry-run only; pass `--execute` for real apply.

**Idempotent:** Yes; re-running after successful apply is a no-op (0 rows match WHERE … LIKE '%Z').

---

## 2. `scripts/persist_day0_horizon_identity_fit.py`

**File:** `scripts/persist_day0_horizon_identity_fit.py`

### Invocation interface (lines 112-124, 219-220):

```python
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Copy the LIVE forecasts DB to a temp file and write there; LIVE untouched.",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        help="Read-only: report whether an identity fit is already present; no write.",
    )
    args = ap.parse_args(argv)
    # ... [body omitted for brevity, see lines 126-216]

if __name__ == "__main__":
    raise SystemExit(main())
```

### How to invoke:

- **Write to LIVE:** `python3 scripts/persist_day0_horizon_identity_fit.py`
  - Calls `write_platt_fit(fit)` with conn=None (opens LIVE connection under db_writer_lock)
- **Dry-run (temp copy):** `python3 scripts/persist_day0_horizon_identity_fit.py --dry-run`
  - Copies LIVE forecasts DB to temp file, writes there, reports OK/MISSING
- **Verify (read-only):** `python3 scripts/persist_day0_horizon_identity_fit.py --verify`
  - Checks LIVE read-only; reports whether fit is already present; no write

### What it writes and to which DB:

- **Writes:** `HorizonPlattFit` (identity/conservative fit with alpha=1.0, beta=0.0, gamma_*=0.0, delta=0.0, epsilon=0.0, n_obs=0)
- **Target DB:** `ZEUS_FORECASTS_DB_PATH` (imported from `src.state.db`)
- **Table:** `day0_horizon_platt_fits` (via `write_platt_fit()`)
- **PK:** `fit_run_id` (deterministic `_IDENTITY_FIT_RUN_ID = "hpf_v1_identity_conservative_v1"`)
- **Idempotent:** Yes; uses INSERT OR IGNORE on PK

### Return codes:
- 0: success (fit persisted and read-back confirmed)
- 1: verify mode → fit not present (lane would short-circuit)
- 2: any error (non-fatal, reported; no raise into caller)

---

## 3. `get_source_run` call sites

**Definition:** `src/state/source_run_repo.py:162-164`

```python
def get_source_run(conn: sqlite3.Connection, source_run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM source_run WHERE source_run_id = ?", (source_run_id,)).fetchone()
    return dict(row) if row else None
```

### Call sites:

Refs:
- `src/data/replacement_forecast_materializer.py:64` — import statement
- `src/data/replacement_forecast_materializer.py:185` — call: `run = get_source_run(conn, source_run_id)`

### Caller context (line 185):

```python
    run = None
    if source_run_id:
        try:
            run = get_source_run(conn, source_run_id)
        except sqlite3.OperationalError:
            # source_run table absent on this connection (minimal/legacy schema
            # or unit-test conn) -> degrade to the request's EXISTING per-role
            # source_available_at. Not a new guess (it is the same input the
            # pre-possession code used); true possession resumes automatically
            # wherever the source_run table exists (live zeus-forecasts.db).
            # Regression fix 2026-06-16 (no such table: source_run).
            run = None
    fetch_finished_at = run.get("fetch_finished_at") if run else None
```

**Try/except wrapping:** YES — wrapped in `try/except sqlite3.OperationalError` (line 184-193). Handles missing table on legacy/test connections gracefully; sets `run = None` on exception.

**Does any caller rely on it raising:** No — exception is caught and suppressed; result degrades to None.

---

## 4. Two near-identical `hours_since_open` blocks in `src/engine/monitor_refresh.py`

### Block 1: Function `_refresh_ens_member_counting` (lines 1186–1239)

**Start line:** 1186  
**End line:** 1239  
**Enclosing function:** `_refresh_ens_member_counting` (line 753)

```python
    # M2b (timing-semantics fix 2026-06-16): hours_since_open MUST be derived
    # from a real entered_at; when entered_at is missing or malformed the basis
    # is UNKNOWN, so use an honest NaN sentinel rather than fabricating a 48h
    # hold age. NaN is checked below and routes to an explicit refuse (the gate
    # must REFUSE on missing authority, not silently grade exits against a
    # 2-day fiction). Never hardcode 48.0.
    hours_since_open = float("nan")
    if position.entered_at:
        try:
            entered = datetime.fromisoformat(position.entered_at)
            if entered.tzinfo is None:
                entered = entered.replace(tzinfo=timezone.utc)
            hours_since_open = (datetime.now(timezone.utc) - entered).total_seconds() / 3600.0
        except Exception:
            pass  # Malformed timestamp → leave NaN → alpha gate refuses below

    # K1/#68: verify calibration authority before computing alpha.
    # Same gate as evaluator.py — check for UNVERIFIED calibration rows.
    # Slice P2-A2 (PR #19 phase 2, 2026-04-26): scope to active metric so
    # cross-metric noise doesn't trigger false-positive stale-probability
    # warnings. Resolver from P2-C1 already determined position metric
    # for this monitor cycle (post-P2-C2 routing); reuse it here.
    _authority_verified = _monitor_q_source is not None
    if _monitor_q_source is None and conn is not None and hasattr(conn, 'execute'):
        from src.calibration.store import get_pairs_for_bucket as _get_pairs
        _cal_season = season_from_date(target_d.isoformat(), lat=city.lat)
        _gate_metric = "high" if _position_metric_str == "high" else None  # hoisted (P2-fix5)
        try:
            _unverified_pairs = _get_pairs(
                conn, city.cluster, _cal_season,
                authority_filter='UNVERIFIED',
                metric=_gate_metric,
            )
        except Exception:
            _unverified_pairs = []
        if _unverified_pairs:
            logger.warning(
                "Monitor authority gate: %d UNVERIFIED calibration rows for %s/%s — using stale probability",
                len(_unverified_pairs), city.name, _cal_season,
            )
            _set_monitor_probability_fresh(position, False)
            applied.append("authority_gate_blocked")
            return position.p_posterior, applied
        _authority_verified = True

    # M2b: missing/malformed entered_at -> hours_since_open is NaN -> REFUSE.
    # compute_alpha does not itself reject NaN (NaN < threshold is False, so it
    # would silently skip the freshness adjustment and return base alpha — the
    # same fabrication this fix removes). Refuse explicitly so the exit gate
    # treats missing hold-age authority as missing, not as "old enough to exit".
    if not np.isfinite(hours_since_open):
        _set_monitor_probability_fresh(position, False)
        applied.append("entered_at_missing_alpha_refused")
        return position.p_posterior, applied
```

---

### Block 2: Function `_refresh_day0_observation` (lines 1658–1703)

**Start line:** 1658  
**End line:** 1703  
**Enclosing function:** `_refresh_day0_observation` (line 1420)

```python
    # M2b (timing-semantics fix 2026-06-16): honest NaN sentinel — never the
    # fabricated 48h. NaN routes to the explicit refuse guard before
    # compute_alpha below (twin of the ENS-member-counting path).
    hours_since_open = float("nan")
    if position.entered_at:
        try:
            entered = datetime.fromisoformat(position.entered_at)
            if entered.tzinfo is None:
                entered = entered.replace(tzinfo=timezone.utc)
            hours_since_open = (datetime.now(timezone.utc) - entered).total_seconds() / 3600.0
        except Exception:
            pass  # Malformed timestamp → leave NaN → alpha gate refuses below

    # K1/#68: verify calibration authority before computing alpha.
    # Slice P2-A2 (PR #19 phase 2, 2026-04-26): twin of the gate above —
    # scope to active metric for the same false-positive-suppression reason.
    _authority_verified = False
    if conn is not None and hasattr(conn, 'execute'):
        from src.calibration.store import get_pairs_for_bucket as _get_pairs
        _cal_season = season_from_date(target_d.isoformat(), lat=city.lat)
        _gate_metric = "high" if _position_metric_str == "high" else None  # hoisted (P2-fix5)
        try:
            _unverified_pairs = _get_pairs(
                conn, city.cluster, _cal_season,
                authority_filter='UNVERIFIED',
                metric=_gate_metric,
            )
        except Exception:
            _unverified_pairs = []
        if _unverified_pairs:
            logger.warning(
                "Monitor authority gate: %d UNVERIFIED calibration rows for %s/%s — using stale probability",
                len(_unverified_pairs), city.name, _cal_season,
            )
            _set_monitor_probability_fresh(position, False)
            applied.append("authority_gate_blocked")
            return position.p_posterior, applied
        _authority_verified = True

    # M2b: missing/malformed entered_at -> hours_since_open is NaN -> REFUSE
    # (twin of the ENS-member-counting guard; compute_alpha silently tolerates
    # NaN, so the refusal must be explicit here).
    if not np.isfinite(hours_since_open):
        _set_monitor_probability_fresh(position, False)
        applied.append("entered_at_missing_alpha_refused")
        return position.p_posterior, applied
```

---

## Similarity Analysis

Both blocks are structurally identical in the core `hours_since_open` computation (lines 1192–1200 vs 1661–1669):

1. Initialize `hours_since_open = float("nan")`
2. If `position.entered_at` present:
   - Parse ISO datetime, add UTC tzinfo if missing
   - Compute `(datetime.now(UTC) - entered).total_seconds() / 3600.0`
   - On exception, leave NaN

**Key difference:** Line 1208 initializes `_authority_verified = _monitor_q_source is not None` (ENS path), while line 1674 initializes `_authority_verified = False` (Day0 path). The authority-gate logic is otherwise identical.

Both blocks then refuse with `entered_at_missing_alpha_refused` if `not np.isfinite(hours_since_open)`.

---

## Notes

- Blocks are explicitly marked as "twins" and "same gate" in comments (PR #19 phase 2, 2026-04-26).
- Both follow same refusal semantics: NaN routes to explicit refuse so missing hold-age authority is not silently treated as "old enough to exit".
- Both were authored under timing-semantics fix 2026-06-16 (per M2b comments).

