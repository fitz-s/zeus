# Created: 2026-05-28
# Last reused or audited: 2026-05-28  (SD3: two-phase replay consumption + gate-aware full-reproduce)
# Lifecycle: created=2026-05-28; last_reviewed=2026-05-28; last_reused=never
# Purpose: Manifest-driven selective full_transport refit/MC-regen driver (dry-run default).
# Reuse: Requires isolated staging DB copy + frozen source + live pause for --execute. Never targets a prod DB.
# Authority basis: operator adjudication 2026-05-27/28 — selective (NOT full) rebuild
#   driven by the row-action manifest. p_raw is cohort-local: only cohorts whose
#   error-model params changed need MC regeneration. See
#   docs/operations/FULL_TRANSPORT_DOMAIN_REVALIDATION_2026-05-27.md.
"""Selective full_transport refit/regenerate driver (manifest-driven).

Reads ``ROW_ACTION_MANIFEST_2026-05-27.csv`` (per-row action class A-E) and emits —
or executes — the MINIMAL set of fit + MC-regen + replay operations. This is the
mathematical opposite of a full rebuild: cohorts whose error-model parameters did
NOT change (function-locality of p_raw) are never regenerated.

Action classes (mutually exclusive, from the reproducibility audit):
  A_REUSE_PENDING_REPLAY   stored == current-code recompute. Run replay-equivalence;
                           reuse pairs if it passes, else fall into regen set.
  B_REFIT_AND_REGEN_COHORT HIGH bias-domain failure (ungated paired delta). Refit row
                           under current gates + regenerate p_raw pairs for the cohort.
  C_NO_LEARNED_CORRECTION  insufficient prior. Refit writes an identity row (producer
                           MIN_PRIOR_N handler); regenerate pairs only if served.
  D_MONTH_SCOPE            coverage-mislabeled. Refit stamps coverage_months; reader
                           month-scope guard does the rest. Regen only if scope changes
                           served applicability.
  E_LOW_SCALE_REGEN        LOW scale-domain failure (residual_sd changed). Refit +
                           regenerate (sigma change alters p_raw spread).

DEFAULT IS DRY-RUN: prints the ordered command plan + the minimal regen scope.
Pass ``--execute`` to run. Execution REQUIRES a frozen source (snapshot copy or
ingest-paused) and a live-pause window — the underlying rebuild_calibration_pairs_v2
BULK chunker aborts if it contends with a live daemon (watchdog_s=30).

USAGE
─────
    # plan only (default)
    python scripts/selective_refit_from_manifest.py \
        --manifest docs/operations/ROW_ACTION_MANIFEST_2026-05-27.csv \
        --db /tmp/scratch_refit.db

    # execute (frozen source + live paused)
    python scripts/selective_refit_from_manifest.py \
        --manifest ... --db /tmp/scratch_refit.db --execute --n-mc 10000 --workers 4
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

logger = logging.getLogger(__name__)

_SEASON_MONTHS = {"DJF": (12, 1, 2), "MAM": (3, 4, 5), "JJA": (6, 7, 8), "SON": (9, 10, 11)}
_PY = sys.executable

# Action classes that REQUIRE MC pair regeneration (Θ changed → p_raw changed).
_REGEN_ACTIONS = {"B_REFIT_AND_REGEN_COHORT", "E_LOW_SCALE_REGEN"}
# Action classes that need a fit row but only conditional regen.
# C/D not in regen scope until manifest carries explicit served/changed flags.
_FIT_ONLY_ACTIONS = {"C_NO_LEARNED_CORRECTION", "D_MONTH_SCOPE"}
# Reuse-pending-replay: replay first, regen only on failure.
_REPLAY_ACTIONS = {"A_REUSE_PENDING_REPLAY"}


def _load_manifest(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def _run(cmd: list[str], *, execute: bool) -> int:
    printable = " ".join(cmd)
    if not execute:
        logger.info("[plan] %s", printable)
        return 0
    logger.info("[exec] %s", printable)
    return subprocess.call(cmd, cwd=str(ZEUS_ROOT))


def _read_stored_gate_hash(db_path: Path) -> Optional[str]:
    """Return the gate_set_hash stamped on the most recent model_bias_ens_v2 row,
    or None if the DB has no rows or the column does not yet exist."""
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(model_bias_ens_v2)").fetchall()}
        if "gate_set_hash" not in existing_cols:
            conn.close()
            return None
        row = conn.execute(
            "SELECT gate_set_hash FROM model_bias_ens_v2 "
            "WHERE gate_set_hash IS NOT NULL ORDER BY recorded_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return row["gate_set_hash"] if row else None
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        return None


# ---------------------------------------------------------------------------
# SD3 — pure helpers (test-targetable)
# ---------------------------------------------------------------------------

def _run_replay_for_a_cohorts(
    a_rows: list[dict],
    db_path: Path,
    *,
    n_per_cohort: int = 5,
    n_mc: int = 1000,
    tol: float = 1e-3,
) -> dict[tuple[str, str, str], bool]:
    """Run replay-equivalence for every A-cohort in a_rows.

    Returns a dict mapping (city, season, metric) -> pass_verdict (True=PASS).

    Imports ``_evaluate_cohort`` from replay_equivalence_full_transport directly so
    we get per-cohort CohortResult.pass_verdict — subprocess approach only yields
    an overall exit code and cannot recover per-cohort PASS/FAIL granularity.

    The DB is opened read-only; no writes occur here.
    """
    import numpy as np

    # Import the replay harness inline to avoid circular-import issues at module load.
    from scripts.replay_equivalence_full_transport import _evaluate_cohort, _open_readonly  # type: ignore[import]

    results: dict[tuple[str, str, str], bool] = {}
    rng = np.random.default_rng(42)

    conn = _open_readonly(str(db_path))
    try:
        for r in a_rows:
            city = r["city"]
            season = r["season"]
            metric = r["metric"]
            key = (city, season, metric)
            if key in results:
                continue  # de-dup: same cohort may appear multiple times
            cr = _evaluate_cohort(
                backup_conn=conn,
                city_name=city,
                metric=metric,
                season=season,
                error_model_source="recompute",
                model_db_conn=None,
                n_per_cohort=n_per_cohort,
                n_mc=n_mc,
                tol=tol,
                rng=rng,
            )
            results[key] = cr.pass_verdict
            logger.info(
                "replay %s/%s/%s -> %s",
                city, season, metric,
                "PASS" if cr.pass_verdict else f"FAIL ({cr.fail_reason})",
            )
    finally:
        conn.close()

    return results


def compute_final_regen(
    manifest_rows: list[dict],
    replay_results: dict[tuple[str, str, str], bool],
    gate_changed: bool,
) -> set[tuple[str, str, str]]:
    """Compute the minimal set of cohorts requiring MC pair regeneration.

    Args:
        manifest_rows: list of manifest CSV row dicts (keys: city, season, metric, action).
        replay_results: map (city, season, metric) -> pass_verdict from _run_replay_for_a_cohorts.
            Ignored when gate_changed=True.
        gate_changed: if True, ALL cohorts must be regenerated (gate change invalidates
            every stored row regardless of action class).

    Returns:
        Set of (city, season, metric) tuples that need MC pair regeneration.
        final_regen = B ∪ E ∪ A_failed   (gate_changed=False)
        final_regen = ALL cohorts          (gate_changed=True)

        Note: C and D are not included unless they appear as B/E/A_failed.
        C/D need a fit row only (identity or month-scoped), not MC pair regen,
        until the manifest carries explicit served/changed flags.
    """
    all_cohorts = {
        (r["city"], r["season"], r["metric"])
        for r in manifest_rows
    }

    if gate_changed:
        logger.warning(
            "gate change -> full reproduce: all %d cohorts queued for MC regen", len(all_cohorts)
        )
        return set(all_cohorts)

    regen: set[tuple[str, str, str]] = set()

    for r in manifest_rows:
        key = (r["city"], r["season"], r["metric"])
        action = r.get("action", "UNKNOWN")
        if action in _REGEN_ACTIONS:
            # B and E: always regen
            regen.add(key)
        elif action in _REPLAY_ACTIONS:
            # A: regen only if replay FAILED (or no result available → fail-closed)
            passed = replay_results.get(key, False)
            if not passed:
                regen.add(key)

    return regen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path,
                    help="staging DB (copy of forecasts.db). MUST NOT be a prod DB.")
    ap.add_argument("--execute", action="store_true",
                    help="actually run (default: dry-run plan only). Needs frozen source + live pause.")
    ap.add_argument("--n-mc", type=int, default=10000)
    ap.add_argument("--workers", type=int, default=0,
                    help="MC compute workers. 0 (default) = auto = (CPU cores - 2). The rebuild is "
                         "compute-in-workers / write-in-main (single writer on the isolated staging "
                         "DB), so MC scales with cores WITHOUT WAL multi-writer contention — "
                         "maximize cores to minimize wall-clock. Explicit value caps at CPU count.")
    ap.add_argument("--metrics", default="high,low")
    ap.add_argument("--n-per-cohort-replay", type=int, default=5,
                    help="Snapshots sampled per A-cohort during replay equivalence check.")
    ap.add_argument("--replay-n-mc", type=int, default=0,
                    help="MC iterations for the replay equivalence check. 0 (default) = MATCH "
                         "production --n-mc (required for a valid FINAL reuse decision — a lower "
                         "n_mc lets Monte-Carlo sampling noise flip A-cohort pass/fail). Set a small "
                         "value explicitly ONLY for a non-authoritative smoke check.")
    ap.add_argument("--tol", type=float, default=1e-3,
                    help="max_abs_diff tolerance for replay PASS verdict.")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()),
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.db.name in {"zeus-world.db", "zeus-forecasts.db", "zeus_trades.db", "zeus-trades.db"}:
        raise SystemExit(f"SAFETY: --db must be a copy, not {args.db.name}")
    # Speed: scale MC workers to cores. Single-writer arch (write-in-main) means no WAL
    # multi-writer starvation here — the old >4 clamp was for multi-writer jobs, not this one.
    import os as _os  # noqa: PLC0415
    _cores = _os.cpu_count() or 4
    if args.workers <= 0:
        args.workers = max(1, _cores - 2)  # leave headroom for the main writer + OS
    elif args.workers > _cores:
        logger.warning("workers=%d > %d cores: oversubscription wastes context switches; "
                       "clamping to %d", args.workers, _cores, _cores)
        args.workers = _cores
    logger.info("MC workers=%d (cores=%d, single-writer compute-parallel)", args.workers, _cores)

    # BL-D / Blocker 5: the replay equivalence check that decides A-cohort REUSE must use the
    # SAME MC law as the production pairs it compares against, else sampling noise flips pass/fail.
    # Default (--replay-n-mc 0) -> match production --n-mc. An explicit lower value is smoke-only.
    if args.replay_n_mc <= 0:
        args.replay_n_mc = args.n_mc
    elif args.execute and args.replay_n_mc < args.n_mc:
        logger.warning(
            "replay_n_mc=%d < production n_mc=%d: this is a SMOKE check only — its A PASS/FAIL "
            "verdicts are NOT authoritative for final reuse (Monte-Carlo sampling noise). Re-run "
            "with --replay-n-mc 0 (=%d) before trusting A-cohort reuse.",
            args.replay_n_mc, args.n_mc, args.n_mc,
        )

    rows = _load_manifest(args.manifest)
    by_action: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_action[r.get("action", "UNKNOWN")].append(r)

    # ---- Gate-change detection: snapshot BEFORE Step 1 rewrites rows ----
    # Query the stored gate_set_hash from the DB NOW, before the fit pass stamps the new hash.
    from src.calibration.ens_error_model import current_gate_set_hash
    current_hash = current_gate_set_hash()
    stored_hash = _read_stored_gate_hash(args.db)
    gate_changed = (stored_hash is not None) and (stored_hash != current_hash)
    if stored_hash is None:
        logger.info("gate check: no stored rows in DB — treating as gate_changed=False")
    elif gate_changed:
        logger.warning(
            "gate change detected: stored_hash=%s  current_hash=%s → full reproduce",
            stored_hash, current_hash,
        )
    else:
        logger.info("gate check: stored_hash=%s matches current — selective regen allowed", stored_hash)

    logger.info("=" * 64)
    logger.info("SELECTIVE REFIT PLAN  (execute=%s)  manifest=%s", args.execute, args.manifest.name)
    for action in sorted(by_action):
        logger.info("  %-26s %d cohorts", action, len(by_action[action]))
    regen_cohorts_mandatory = [r for a in _REGEN_ACTIONS for r in by_action.get(a, [])]
    fit_only = [r for a in _FIT_ONLY_ACTIONS for r in by_action.get(a, [])]
    replay_rows = [r for a in _REPLAY_ACTIONS for r in by_action.get(a, [])]
    logger.info("MANDATORY MC regen: %d  |  fit-only (C/D): %d  |  replay-then-maybe (A): %d",
                len(regen_cohorts_mandatory), len(fit_only), len(replay_rows))
    logger.info("=" * 64)

    rc_total = 0

    # ---- Step 1: fit ALL affected rows under current gates (cheap; stamps gate_set_hash) ----
    # One producer pass per metric refits every bucket; the C-handler writes identity
    # rows and coverage_months is stamped automatically. This covers B, C, D, E fit needs.
    logger.info("STEP 1 — fit rows under current gates (gate_set_hash + coverage_months stamped)")
    for metric in args.metrics.split(","):
        metric = metric.strip()
        if not metric:
            continue
        cmd = [_PY, "scripts/fit_full_transport_error_models.py",
               "--db", str(args.db), "--metric", metric]
        if args.execute:
            cmd.append("--commit")
        rc_total |= _run(cmd, execute=args.execute)

    # ---- Phase 1: run replay for A cohorts; classify each PASS/FAIL ----
    # Skipped when gate_changed=True (gate change invalidates everything; no point).
    replay_results: dict[tuple[str, str, str], bool] = {}
    if gate_changed:
        logger.warning(
            "PHASE 1 (replay) — SKIPPED: gate change detected; all A cohorts treated as FAIL"
        )
    else:
        logger.info(
            "PHASE 1 — replay-equivalence for %d A cohorts (execute=%s)",
            len(replay_rows), args.execute,
        )
        if args.execute:
            replay_results = _run_replay_for_a_cohorts(
                replay_rows,
                args.db,
                n_per_cohort=args.n_per_cohort_replay,
                n_mc=args.replay_n_mc,
                tol=args.tol,
            )
            a_passed = sum(1 for v in replay_results.values() if v)
            a_failed = sum(1 for v in replay_results.values() if not v)
            logger.info("replay: %d PASS, %d FAIL out of %d A cohorts",
                        a_passed, a_failed, len(replay_results))
        else:
            # Dry-run: log intent; treat all A as PASS (conservative plan estimate).
            logger.info("[plan] would run replay on %d A cohorts (all shown as plan-PASS)",
                        len(replay_rows))
            for r in replay_rows:
                key = (r["city"], r["season"], r["metric"])
                replay_results[key] = True  # plan-only placeholder

    # ---- Compute final_regen_manifest via pure helper ----
    final_regen = compute_final_regen(rows, replay_results, gate_changed)
    logger.info(
        "final_regen_manifest: %d cohorts  (B∪E=%d, A_failed=%d, gate_changed=%s)",
        len(final_regen),
        len(regen_cohorts_mandatory),
        sum(1 for r in replay_rows
            if not replay_results.get((r["city"], r["season"], r["metric"]), True)),
        gate_changed,
    )

    # ---- Phase 2: MC regenerate ONLY final_regen_manifest cohorts ----
    logger.info("PHASE 2 — MC regenerate %d cohorts from final_regen_manifest", len(final_regen))
    for key in sorted(final_regen):
        city, season, metric = key
        months = _SEASON_MONTHS.get(season)
        if not months:
            logger.warning("skip cohort %s/%s/%s: unknown season", city, season, metric)
            continue
        # BL-B / Blocker 2: scope the regen to THIS cohort's season months only, so a
        # (city, season, metric) regen does not rebuild the city's other seasons (which may
        # be A-pass reusable). _SEASON_MONTHS maps the season label -> its calendar months.
        # --months is now safe at any worker count (BL-B: the parallel path month-scopes its
        # DELETE). The rebuild is compute-in-workers / write-in-main (single writer), so MC
        # compute scales with cores without WAL multi-writer contention — pass full --workers.
        cmd = [_PY, "scripts/rebuild_calibration_pairs_v2.py",
               "--db", str(args.db), "--city", city,
               "--temperature-metric", metric,
               "--months", ",".join(str(m) for m in months),
               "--error-model", "full_transport_v1",
               "--n-mc", str(args.n_mc), "--workers", str(args.workers)]
        if args.execute:
            cmd += ["--no-dry-run", "--force"]
        else:
            cmd.append("--dry-run")
        rc_total |= _run(cmd, execute=args.execute)

    # ---- Step 4: audit must be 100% servable-reproducible (same-source) ----
    logger.info("STEP 4 — verify reproducibility (same-source audit)")
    cmd = [_PY, "scripts/audit_error_model_row_reproducibility.py",
           "--world-db", str(args.db), "--forecasts-db", str(args.db),
           "--family", "full_transport_v1"]
    rc_total |= _run(cmd, execute=args.execute)

    logger.info("=" * 64)
    logger.info("DONE  execute=%s  aggregate_rc=%d", args.execute, rc_total)
    if not args.execute:
        logger.info("This was a PLAN. Re-run with --execute (frozen source + live paused) to apply.")
    return 0 if rc_total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
