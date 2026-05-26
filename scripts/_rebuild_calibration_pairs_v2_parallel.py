# Lifecycle: created=2026-05-11; last_reviewed=2026-05-11; last_reused=2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: FT_SHIP_MASTER_SPEC_2026-05-25 Phase 3 (additive, non-destructive)
# P1 (2026-05-25): _delete_canonical_v2_slice call passes error_model_family_filter.
# P2 (2026-05-25): DataVersionQuarantinedError caught per-snapshot in parallel path.
# Purpose: Compute-in-workers + write-in-main parallel orchestrator for rebuild_calibration_pairs_v2.
# Authority basis: redesign after city-level multiprocessing failed under SQLite WAL writer-lock contention (2026-05-11).
# Reuse: imported lazily from scripts/rebuild_calibration_pairs_v2.py rebuild_v2() when --workers>1.

"""Compute-in-workers + write-in-main parallel orchestrator for rebuild_calibration_pairs_v2.

ARCHITECTURE
------------
SQLite WAL allows N readers but only ONE writer at a time. The previous
city-level multiprocessing design held a SAVEPOINT writer lock per worker
which serialized on the single writer slot and exhausted busy_timeout.

This module flips the split:

* Workers receive PURE serializable input (snapshot id, city name,
  member_maxes list, settlement_value, n_mc, seed) and return PURE
  serializable output (snapshot_id, p_raw_vec list, bin_labels,
  winning_bin_label, error). Workers never open a sqlite connection.
* The main process owns the only sqlite connection and performs all DB I/O:
  obs lookups, validation gates, SAVEPOINT, deletes, writes, commits.

Per-city SAVEPOINT semantics (T1E) are preserved. Within a city the MC
compute parallelizes across a persistent ProcessPoolExecutor (one pool for
all cities, amortizing spawn cost); writes to calibration_pairs_v2 happen
sequentially in main, in snapshot-order so RebuildStatsV2 counters match
the sequential path.

Validation gate ORDER mirrors ``_process_snapshot_v2`` exactly so
``RebuildStatsV2`` counters (``snapshots_no_observation``,
``snapshots_unit_rejected``, ``snapshots_contract_evidence_rejected``,
``contract_evidence_rejection_reasons``) end with the same values as
``--workers 1``.

SEED PROPAGATION
----------------
Sequential rebuild advances a single ``np.random.Generator`` across every
snapshot in city order. The parallel path cannot replay that exact stream
because work completes out of order. Instead, when ``seed_base`` is set we
derive a per-snapshot seed as ``seed_base ^ snapshot_id`` so each snapshot's
MC stream is reproducible across runs (independent of worker scheduling)
without being byte-identical to the sequential MC values. Pair *counts*
remain identical because ``len(bins)`` is independent of MC draws — the
parent's "IDENTICAL pair counts" requirement holds trivially.
"""

from __future__ import annotations

import sqlite3
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from src.contracts.ensemble_snapshot_provenance import DataVersionQuarantinedError


# ---------------------------------------------------------------------------
# Worker (top-level, picklable). NO database access.
# ---------------------------------------------------------------------------

def _mc_compute_worker(payload: dict) -> dict:
    """Pure Monte-Carlo compute. Lazy-imports inside body for spawn safety.

    Input payload keys:
        snapshot_id (int)
        city_name (str)
        member_maxes (list[float])
        settlement_value (float)
        n_mc (int | None)
        seed (int | None)

    Optional error-model keys (present iff the rebuild ran with --error-model
    AND the bucket had a usable model):
        error_bias_native (float)        members - this is subtracted PRE-MC
        error_extra_sigma_native (float) widens the MC predictive draw
    When absent the MC path is byte-identical to the legacy rebuild.

    Output dict keys:
        snapshot_id (int)
        p_raw_vec (list[float] | None)
        bin_labels (list[str] | None)
        winning_bin_label (str | None)
        error (str | None)
    """
    snapshot_id = payload.get("snapshot_id")
    try:
        # Ensure project root on sys.path for spawn-mode children.
        project_root = Path(__file__).parent.parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))

        import numpy as np  # noqa: PLC0415

        from src.config import cities_by_name  # noqa: PLC0415
        from src.contracts.calibration_bins import grid_for_city  # noqa: PLC0415
        from src.contracts.settlement_semantics import SettlementSemantics  # noqa: PLC0415
        from src.signal.ensemble_signal import p_raw_vector_from_maxes  # noqa: PLC0415
        from src.types.market import validate_bin_topology  # noqa: PLC0415

        city = cities_by_name[payload["city_name"]]
        grid = grid_for_city(city)
        bins = grid.as_bins()
        validate_bin_topology(bins)
        sem = SettlementSemantics.for_city(city)

        seed = payload.get("seed")
        rng = np.random.default_rng(int(seed)) if seed is not None else np.random.default_rng()

        member_maxes = np.asarray(payload["member_maxes"], dtype=float)
        settlement_value = float(payload["settlement_value"])

        # Predictive-error correction (opt-in). The °C→native conversion + fit
        # happened in main (_pre_compute_snapshot_v2); the worker only applies the
        # already-native scalars. Absent keys => byte-identical legacy MC.
        error_bias = payload.get("error_bias_native")
        extra_sigma = payload.get("error_extra_sigma_native")
        if error_bias is not None:
            p_raw_vec = p_raw_vector_from_maxes(
                member_maxes - float(error_bias),
                city,
                sem,
                bins,
                n_mc=payload.get("n_mc"),
                rng=rng,
                extra_member_sigma=float(extra_sigma),
            )
        else:
            p_raw_vec = p_raw_vector_from_maxes(
                member_maxes,
                city,
                sem,
                bins,
                n_mc=payload.get("n_mc"),
                rng=rng,
            )
        winning_bin = grid.bin_for_value(settlement_value)
        return {
            "snapshot_id": snapshot_id,
            "p_raw_vec": [float(x) for x in p_raw_vec],
            "bin_labels": [b.label for b in bins],
            "winning_bin_label": winning_bin.label,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "snapshot_id": snapshot_id,
            "p_raw_vec": None,
            "bin_labels": None,
            "winning_bin_label": None,
            "error": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        }


# ---------------------------------------------------------------------------
# Main-process orchestrator. Owns the sqlite connection.
# ---------------------------------------------------------------------------

def run_parallel_rebuild(
    conn: sqlite3.Connection,
    city_buckets: dict,
    spec,
    *,
    workers: int,
    start_date: Optional[str],
    end_date: Optional[str],
    data_version_filter: Optional[str],
    cycle_filter: Optional[str],
    source_id_filter: Optional[str],
    horizon_profile_filter: Optional[str],
    n_mc: Optional[int],
    seed_base: Optional[int] = None,
    stats: Any = None,
    error_model_family: Optional[str] = None,
    error_cache: Optional[dict] = None,
) -> None:
    """Process every (city, snapshots) bucket using compute-in-workers + write-in-main.

    Mutates ``stats`` (a ``RebuildStatsV2``) in place so the caller observes the
    same counter shape as the sequential path. Per-city SAVEPOINT bounding is
    preserved: each city's writes land in a single ``v2_rebuild_bucket``
    SAVEPOINT that is RELEASE'd + commit'd before the next city begins.
    """
    if workers < 1:
        raise ValueError(f"run_parallel_rebuild: workers must be >=1, got {workers!r}")
    if stats is None:
        raise ValueError("run_parallel_rebuild: stats (RebuildStatsV2) is required")
    if not city_buckets:
        return
    if error_cache is None:
        error_cache = {}

    # Lazy import — avoids circular import at module load (parallel module is
    # imported from inside rebuild_v2() in the same script).
    from src.config import cities_by_name  # noqa: PLC0415
    from scripts.rebuild_calibration_pairs_v2 import (  # noqa: PLC0415
        _delete_canonical_v2_slice,
        _pre_compute_snapshot_v2,
        _write_snapshot_pairs_v2,
    )

    print(
        f"  parallel: pool workers={workers}, cities={len(city_buckets)}, "
        f"seed_base={seed_base}"
    )

    with ProcessPoolExecutor(max_workers=workers) as executor:
        for city_name, city_snaps in sorted(city_buckets.items()):
            city = cities_by_name.get(city_name)
            if city is None:
                continue
            if not city_snaps:
                continue

            conn.execute("SAVEPOINT v2_rebuild_bucket")
            try:
                # P1: scoped by error_model_family so only prior runs of the same
                # family are removed; legacy 'none' rows survive untouched.
                _delete_canonical_v2_slice(
                    conn,
                    spec=spec,
                    city_filter=city_name,
                    start_date=start_date,
                    end_date=end_date,
                    data_version_filter=data_version_filter,
                    cycle_filter=cycle_filter,
                    source_id_filter=source_id_filter,
                    horizon_profile_filter=horizon_profile_filter,
                    error_model_family_filter=error_model_family,
                )

                # ---- Step A: pre-process in main; build payloads for survivors.
                payloads: list[dict] = []
                # snap_index maps snapshot_id -> (snapshot_row, settlement_value,
                # applied_error_model_family) for the writer step. Holding
                # settlement_value here avoids an extra _fetch_verified_observation
                # round-trip during write; applied_family carries the per-snapshot
                # correction provenance (fail-open buckets => 'none').
                snap_index: dict[int, tuple[sqlite3.Row, float, str]] = {}
                for snap in city_snaps:
                    try:
                        survivor = _pre_compute_snapshot_v2(
                            conn, snap, city, spec=spec, stats=stats,
                            error_model_family=error_model_family,
                            error_cache=error_cache,
                        )
                    except DataVersionQuarantinedError as _dve:
                        # P2: spec-quarantined snapshot (passes is_quarantined() but
                        # outside this spec's positively-allowed data_version set).
                        # Skip + count; do not abort the city bucket.
                        stats.snapshots_quarantined += 1
                        print(
                            f"  SPEC-QUARANTINED (parallel skip) "
                            f"snapshot_id={snap['snapshot_id']} "
                            f"data_version={snap['data_version']!r}: {_dve}"
                        )
                        continue
                    if survivor is None:
                        continue
                    sid = int(snap["snapshot_id"])
                    # applied_family records whether THIS snapshot's bucket had a
                    # usable model — a fail-open bucket leaves the survivor
                    # unannotated and writes 'none'/bias_corrected=0.
                    _eb = survivor.get("error_bias_native")
                    applied_family = (
                        error_model_family
                        if (error_model_family and _eb is not None)
                        else "none"
                    )
                    snap_index[sid] = (
                        snap, float(survivor["settlement_value"]), applied_family,
                    )
                    seed = (
                        (int(seed_base) ^ sid) if seed_base is not None else None
                    )
                    payload = {
                        "snapshot_id": sid,
                        "city_name": city.name,
                        "member_maxes": survivor["member_maxes"],
                        "settlement_value": survivor["settlement_value"],
                        "n_mc": n_mc,
                        "seed": seed,
                    }
                    if _eb is not None:
                        payload["error_bias_native"] = _eb
                        payload["error_extra_sigma_native"] = survivor[
                            "error_extra_sigma_native"
                        ]
                    payloads.append(payload)

                # ---- Step B: parallel MC compute over the survivors.
                if payloads:
                    futures = [
                        executor.submit(_mc_compute_worker, p) for p in payloads
                    ]

                    # ---- Step C: collect results, write sequentially in main.
                    for fut in as_completed(futures):
                        result = fut.result()
                        if result.get("error"):
                            raise RuntimeError(
                                f"MC worker failed for snapshot_id="
                                f"{result.get('snapshot_id')}: {result['error']}"
                            )
                        sid = int(result["snapshot_id"])
                        snap, settlement_value, applied_family = snap_index[sid]
                        _write_snapshot_pairs_v2(
                            conn,
                            snap,
                            city,
                            spec=spec,
                            p_raw_vec=result["p_raw_vec"],
                            settlement_value=settlement_value,
                            bin_labels=result["bin_labels"],
                            winning_bin_label=result["winning_bin_label"],
                            stats=stats,
                            bias_corrected=(applied_family != "none"),
                            error_model_family=applied_family,
                        )

                conn.execute("RELEASE SAVEPOINT v2_rebuild_bucket")
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT v2_rebuild_bucket")
                conn.execute("RELEASE SAVEPOINT v2_rebuild_bucket")
                raise

            conn.commit()
