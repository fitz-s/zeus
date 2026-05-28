# Created: 2026-05-28
# Last reused or audited: 2026-05-28
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
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

logger = logging.getLogger(__name__)

_SEASON_MONTHS = {"DJF": (12, 1, 2), "MAM": (3, 4, 5), "JJA": (6, 7, 8), "SON": (9, 10, 11)}
_PY = sys.executable

# Action classes that REQUIRE MC pair regeneration (Θ changed → p_raw changed).
_REGEN_ACTIONS = {"B_REFIT_AND_REGEN_COHORT", "E_LOW_SCALE_REGEN"}
# Action classes that need a fit row but only conditional regen.
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path,
                    help="staging DB (copy of forecasts.db). MUST NOT be a prod DB.")
    ap.add_argument("--execute", action="store_true",
                    help="actually run (default: dry-run plan only). Needs frozen source + live pause.")
    ap.add_argument("--n-mc", type=int, default=10000)
    ap.add_argument("--workers", type=int, default=4,
                    help="MC workers (<=4: WAL multi-writer starvation above that).")
    ap.add_argument("--metrics", default="high,low")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()),
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.db.name in {"zeus-world.db", "zeus-forecasts.db", "zeus_trades.db", "zeus-trades.db"}:
        raise SystemExit(f"SAFETY: --db must be a copy, not {args.db.name}")
    if args.workers > 4:
        logger.warning("workers=%d > 4 risks WAL multi-writer starvation; clamping to 4", args.workers)
        args.workers = 4

    rows = _load_manifest(args.manifest)
    by_action: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_action[r.get("action", "UNKNOWN")].append(r)

    logger.info("=" * 64)
    logger.info("SELECTIVE REFIT PLAN  (execute=%s)  manifest=%s", args.execute, args.manifest.name)
    for action in sorted(by_action):
        logger.info("  %-26s %d cohorts", action, len(by_action[action]))
    regen_cohorts = [r for a in _REGEN_ACTIONS for r in by_action.get(a, [])]
    fit_only = [r for a in _FIT_ONLY_ACTIONS for r in by_action.get(a, [])]
    replay = [r for a in _REPLAY_ACTIONS for r in by_action.get(a, [])]
    logger.info("MANDATORY MC regen: %d  |  fit-only (C/D): %d  |  replay-then-maybe (A): %d",
                len(regen_cohorts), len(fit_only), len(replay))
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

    # ---- Step 2: replay-equivalence for A rows (decide reuse vs regen) ----
    logger.info("STEP 2 — replay-equivalence for %d A cohorts (reuse if pass)", len(replay))
    # NOTE: replay is scoped by (city, metric); the replay harness filters per
    # (city, season, metric) bucket internally. A cohort here is one
    # (city, season, metric) row, so the same city+metric may appear for multiple
    # seasons — de-dup the replay invocation per (city, metric) to avoid redundant
    # runs while still covering every A-row's season inside the harness.
    seen_replay: set[tuple[str, str]] = set()
    for r in replay:
        key = (r["city"], r["metric"])
        if key in seen_replay:
            continue
        seen_replay.add(key)
        cmd = [_PY, "scripts/replay_equivalence_full_transport.py",
               "--db", str(args.db), "--recompute",
               "--city", r["city"], "--metric", r["metric"]]
        rc_total |= _run(cmd, execute=args.execute)

    # ---- Step 3: MC regenerate ONLY the mandatory cohorts (B + E) ----
    logger.info("STEP 3 — MC regenerate %d mandatory cohorts (B+E)", len(regen_cohorts))
    for r in regen_cohorts:
        season = r["season"]
        months = _SEASON_MONTHS.get(season)
        if not months:
            logger.warning("skip cohort %s/%s/%s: unknown season", r["city"], season, r["metric"])
            continue
        cmd = [_PY, "scripts/rebuild_calibration_pairs_v2.py",
               "--db", str(args.db), "--city", r["city"],
               "--temperature-metric", r["metric"],
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
