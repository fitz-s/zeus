#!/usr/bin/env python3
# Created: 2026-09-20
# Purpose: Compare causal source baskets against current-resolver settlement truth.
# Reuse: Read-only canonical inputs; stdout evidence never activates a live artifact.
"""Day-ahead, fixed-local-noon source-basket validation using production math.

One case per city/metric/date prevents busy cities and repeated recomputations
from manufacturing sample size. Historical labels retain their actual knowledge
times. Missing evidence is counted, never reconstructed as an earlier capture.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
import itertools
import json
import math
from pathlib import Path
import sqlite3
import sys
import time as clock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.calibration.emos import bin_probability_settlement  # noqa: E402
from src.calibration.scoring import validate_probability_group  # noqa: E402
from src.config import runtime_cities_by_name  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
from src.data.current_settlement_history import read_current_settlement_history  # noqa: E402
from src.data.bayes_precision_fusion_history_provider import raw_product_matches_live_source  # noqa: E402
from src.data.bayes_precision_fusion_download import OPENMETEO_MODEL_IDS  # noqa: E402
from src.data.replacement_current_value_serving import read_current_instrument_values  # noqa: E402
from src.data.replacement_forecast_cycle_policy import (  # noqa: E402
    replacement_source_cycle_max_age_hours,
)  # noqa: E402
from src.data.replacement_forecast_materializer import (  # noqa: E402
    _current_evidence_shape_from_values,
)  # noqa: E402
from src.forecast.center import raw_second_moment_weights  # noqa: E402
from src.state.db import _connect_read_only  # noqa: E402
from src.forecast.probability_validation import (  # noqa: E402
    MarketVector,
    ProbabilityValidationCase,
    ProbabilityVector,
    validate_probability_candidates,
)
from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT  # noqa: E402
from src.strategy.live_inference.source_clock_vnext import provider_family_for_source  # noqa: E402


def declared_baskets():
    """Fix the comparison universe from source policy, including absent products."""
    models = sorted({"ecmwf_ifs", *OPENMETEO_MODEL_IDS})
    return tuple(
        "+".join(basket)
        for size in range(2, 5)
        for basket in itertools.combinations(models, size)
        if len({provider_family_for_source(model) for model in basket}) == size
    )


def aware(value: object, *, sqlite_utc: bool = False) -> datetime:
    result = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if result.tzinfo is None:
        if not sqlite_utc:
            raise ValueError("missing timezone")
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def bounded(conn: sqlite3.Connection) -> None:
    deadline = clock.monotonic() + 3.0
    conn.set_progress_handler(lambda: clock.monotonic() > deadline, 10000)


def native_value(value_c: float, unit: str) -> float:
    return value_c * 1.8 + 32.0 if unit == "F" else value_c


def ordered_bins(provenance: dict, city, settlement: float):
    semantics = SettlementSemantics.for_city(city)
    semantics.assert_settlement_value(settlement)
    bins = sorted(
        provenance["bin_topology"],
        key=lambda b: -math.inf if b["lower_c"] is None else float(b["lower_c"]),
    )
    if (
        len(bins) < 2
        or bins[0]["lower_c"] is not None
        or bins[-1]["upper_c"] is not None
    ):
        raise ValueError("incomplete bin partition")
    last = None
    winners = []
    for i, b in enumerate(bins):
        if (
            b["settlement_unit"] != city.settlement_unit
            or b["rounding_rule"] != semantics.rounding_rule
        ):
            raise ValueError("bin settlement identity mismatch")
        lo = (
            None
            if b["lower_c"] is None
            else native_value(float(b["lower_c"]), city.settlement_unit)
        )
        hi = (
            None
            if b["upper_c"] is None
            else native_value(float(b["upper_c"]), city.settlement_unit)
        )
        for value in (lo, hi):
            if value is not None and (
                not math.isfinite(value) or abs(value - round(value)) > 1e-7
            ):
                raise ValueError("noninteger native bin label")
        if i and (lo is None or last is None or abs(lo - last - 1.0) > 1e-7):
            raise ValueError("overlapping or missing native bin")
        if (lo is None or settlement >= lo - 1e-7) and (
            hi is None or settlement <= hi + 1e-7
        ):
            winners.append(i)
        last = hi
    if len(winners) != 1:
        raise ValueError("ambiguous settlement winner")
    return bins, winners[0]


def make_candidates(conn, row, provenance, city, decision, history, bins):
    fusion = provenance["bayes_precision_fusion"]
    shape_identity = fusion["current_evidence_shape"]
    bounded(conn)
    snapshot = conn.execute(
        "SELECT * FROM ensemble_snapshots WHERE snapshot_id=?",
        (shape_identity["snapshot_id"],),
    ).fetchone()
    if snapshot is None:
        raise ValueError("ensemble snapshot missing")
    s = dict(snapshot)
    if (s["city"], s["target_date"], s["temperature_metric"]) != (
        row["city"],
        row["target_date"],
        row["temperature_metric"],
    ):
        raise ValueError("ensemble target mismatch")
    if s["authority"] != "VERIFIED" or s["boundary_ambiguous"]:
        raise ValueError("ensemble physical identity unavailable")
    ens_known = max(
        aware(s[k], sqlite_utc=k == "recorded_at")
        for k in ("source_available_at", "fetch_time", "recorded_at")
    )
    if ens_known > decision or aware(s["source_cycle_time"]) != aware(
        row["source_cycle_time"]
    ):
        raise ValueError("ensemble not same-cycle causal")
    members = tuple(
        float(value) for value in json.loads(s["members_json"]) if value is not None
    )
    members_unit = str(s["members_unit"] or "").strip().lower()
    if members_unit in {"degf", "f", "°f"}:
        members = tuple((value - 32.0) * 5.0 / 9.0 for value in members)
    elif members_unit not in {"degc", "c", "°c"}:
        raise ValueError("ensemble unit unavailable")
    bounded(conn)
    served = read_current_instrument_values(
        conn,
        city=row["city"],
        metric=row["temperature_metric"],
        target_date=row["target_date"],
        source_cycle_time_iso=row["source_cycle_time"],
        decision_time_iso=decision.isoformat(),
    )
    # Evaluate the global core plus the decision-time incumbent basket. Do not
    # use today's winning basket to define yesterday's candidate universe.
    universe = {"ecmwf_ifs", "icon_global", "ukmo_global_deterministic_10km"}
    universe.update(
        (fusion.get("source_clock_one_scheme") or {}).get("configured_sources", ())
    )
    values, cycles, known, raw_ids = {}, {}, {}, {}
    for model in sorted(universe.intersection(served)):
        item = served[model]
        age_hours = (decision - aware(item.served_cycle)).total_seconds() / 3600.0
        if not 0.0 <= age_hours <= replacement_source_cycle_max_age_hours():
            continue
        bounded(conn)
        raw = conn.execute(
            "SELECT * FROM raw_model_forecasts WHERE raw_model_forecast_id=?",
            (item.raw_model_forecast_id,),
        ).fetchone()
        if raw is None:
            continue
        r = dict(raw)
        try:
            stamp = max(
                aware(r[k], sqlite_utc=k == "recorded_at")
                for k in ("source_available_at", "captured_at", "recorded_at")
            )
            if (
                stamp > decision
                or r["endpoint"] != "single_runs"
                or r["coverage_status"] != "COVERED"
            ):
                continue
            if (
                (r["city"], r["metric"], r["target_date"], r["model"])
                != (row["city"], row["temperature_metric"], row["target_date"], model)
                or aware(r["source_cycle_time"]) != aware(item.served_cycle)
                or float(r["forecast_value_c"]) != item.value_c
            ):
                continue
            if not math.isfinite(item.value_c) or not raw_product_matches_live_source(
                r, city, lead_days=1,
            ):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        values[model], cycles[model], known[model], raw_ids[model] = (
            item.value_c,
            item.served_cycle,
            stamp,
            item.raw_model_forecast_id,
        )
    stats = {}
    for model in values:
        errors = [
            (h["values"][model] - h["settlement_c"]) ** 2
            for h in history
            if h["target_date"] < row["target_date"]
            and h["known_at"] < decision
            and model in h["values"]
        ]
        stats[model] = (sum(errors) / len(errors), len(errors)) if errors else (None, 0)
    ids = tuple(b["bin_id"] for b in bins)
    out = {}
    for size in range(2, min(4, len(values)) + 1):
        for basket in itertools.combinations(sorted(values), size):
            if len({provider_family_for_source(m) for m in basket}) != size:
                continue
            weights = raw_second_moment_weights({m: stats[m] for m in basket}, unit="C")
            center = sum(weights[m] * values[m] for m in basket)
            try:
                shape = _current_evidence_shape_from_values(
                    snapshot_id=s["snapshot_id"],
                    source_cycle_time=s["source_cycle_time"],
                    source_available_at=s["source_available_at"],
                    members_c=members,
                    provider_values_c={m: values[m] for m in basket},
                    provider_weights=weights,
                    center_c=center,
                    carrier_cycle_time=row["source_cycle_time"],
                    provider_cycles={m: cycles[m] for m in basket},
                )
                q = tuple(
                    bin_probability_settlement(
                        center,
                        shape.predictive_sigma_c,
                        b["lower_c"],
                        b["upper_c"],
                        half_step=float(b["settlement_step_c"]) / 2.0,
                        rounding_rule=b["rounding_rule"],
                    )
                    for b in bins
                )
                validate_probability_group(q)
            except ValueError:
                continue
            out["+".join(basket)] = ProbabilityVector(
                values=q,
                available_at=max(ens_known, *(known[m] for m in basket)),
                bin_ids=ids,
            )
    return out, values, raw_ids


def _native_interval(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("market_native_interval_invalid")
    number = float(value)
    if not math.isfinite(number) or abs(number - round(number)) > 1e-7:
        raise ValueError("market_native_interval_invalid")
    return int(round(number))


def _market_vector_for_case(
    evidence_conn: sqlite3.Connection,
    *,
    city: str,
    target_date: date,
    metric: str,
    unit: str,
    decision_at: datetime,
    baseline: ProbabilityVector,
    bins: list[dict],
) -> tuple[MarketVector | None, str]:
    """Return only a complete fresh family-book market vector for this exact baseline.

    The evidence store records token bin ids while forecast posteriors use venue
    question ids. Native integer interval equality is the only permitted bridge.
    """
    try:
        bounded(evidence_conn)
        selection_floor = max(baseline.available_at, decision_at - FRESHNESS_WINDOW_DEFAULT)
        rows = evidence_conn.execute(
            """
            SELECT o.*, s.family_id AS state_family_id, s.topology_hash,
                   s.complete_book AS state_complete_book, s.canonical_payload
              FROM family_book_observations AS o
              JOIN family_book_states AS s ON s.state_id = o.state_id
             WHERE o.city = ? AND o.target_date = ? AND o.temperature_metric = ?
               AND o.measurement_unit = ? AND o.complete_book = 1
               AND julianday(o.decision_time) >= julianday(?)
               AND julianday(o.decision_time) <= julianday(?)
             ORDER BY o.decision_time DESC, o.observation_id DESC
             LIMIT 256
            """,
            (
                city, target_date.isoformat(), metric, unit,
                selection_floor.isoformat(), decision_at.isoformat(),
            ),
        ).fetchall()
    except sqlite3.Error:
        return None, "market_evidence_schema_unavailable"

    expected_by_interval: dict[tuple[int | None, int | None], str] = {}
    try:
        for bin_row in bins:
            interval = (
                _native_interval(native_value(bin_row["lower_c"], unit))
                if bin_row["lower_c"] is not None
                else None,
                _native_interval(native_value(bin_row["upper_c"], unit))
                if bin_row["upper_c"] is not None
                else None,
            )
            if interval in expected_by_interval:
                raise ValueError("market_native_topology_ambiguous")
            expected_by_interval[interval] = str(bin_row["bin_id"])
    except (KeyError, TypeError, ValueError):
        return None, "market_baseline_topology_invalid"

    for raw in rows:
        try:
            row = dict(raw)
            selected_at = aware(row["decision_time"])
            if not baseline.available_at <= selected_at <= decision_at:
                raise ValueError("market_decision_clock_mismatch")
            if str(row["family_id"] or "") != str(row["state_family_id"] or ""):
                raise ValueError("market_state_family_mismatch")
            if not str(row["topology_hash"] or "").strip() or int(row["state_complete_book"]) != 1:
                raise ValueError("market_state_topology_unavailable")
            if not str(row["model_q_identity_hash"] or "").strip():
                raise ValueError("market_model_identity_missing")
            model_q = json.loads(str(row["model_q_json"] or ""))
            market_q = json.loads(str(row["market_q_json"] or ""))
            manifest = json.loads(str(row["source_manifest_json"] or ""))
            payload = json.loads(str(row["canonical_payload"] or ""))
            if not all(isinstance(value, dict) for value in (model_q, market_q, manifest, payload)):
                raise ValueError("market_vector_or_manifest_invalid")
            payload_bins = payload.get("bins")
            if (
                str(payload.get("family_id") or "") != str(row["family_id"])
                or str(payload.get("topology_hash") or "") != str(row["topology_hash"])
                or payload.get("complete_book") is not True
                or not isinstance(payload_bins, list)
            ):
                raise ValueError("market_state_payload_invalid")
            state_by_bin = {
                str(item.get("bin_id") or ""): item
                for item in payload_bins
                if isinstance(item, dict) and str(item.get("bin_id") or "")
            }
            keys = set(model_q)
            if (
                len(state_by_bin) != len(payload_bins)
                or not keys
                or keys != set(market_q)
                or keys != set(manifest)
                or keys != set(state_by_bin)
            ):
                raise ValueError("market_bin_set_incomplete")
            if any(not str(state_by_bin[key].get("raw_orderbook_hash") or "").strip() for key in keys):
                raise ValueError("market_yes_book_identity_missing")

            mapped: dict[str, str] = {}
            freshness_floor = decision_at - FRESHNESS_WINDOW_DEFAULT
            for market_bin_id in keys:
                source = manifest[market_bin_id]
                if not isinstance(source, dict):
                    raise ValueError("market_manifest_invalid")
                interval = (
                    _native_interval(source.get("lower_native")),
                    _native_interval(source.get("upper_native")),
                )
                baseline_bin_id = expected_by_interval.get(interval)
                if baseline_bin_id is None or baseline_bin_id in mapped.values():
                    raise ValueError("market_native_topology_mismatch")
                for field in (
                    "executable_snapshot_id", "source_captured_at",
                    "no_executable_snapshot_id", "no_raw_orderbook_hash",
                    "no_source_captured_at",
                ):
                    if not str(source.get(field) or "").strip():
                        raise ValueError("market_side_identity_missing")
                if str(source["no_raw_orderbook_hash"]) != str(
                    state_by_bin[market_bin_id].get("no_raw_orderbook_hash") or ""
                ):
                    raise ValueError("market_side_book_identity_mismatch")
                for field in ("source_captured_at", "no_source_captured_at"):
                    captured_at = aware(source[field])
                    if not freshness_floor <= captured_at <= selected_at:
                        raise ValueError("market_capture_not_fresh_for_case")
                mapped[market_bin_id] = baseline_bin_id
            if set(mapped.values()) != set(baseline.bin_ids):
                raise ValueError("market_native_topology_mismatch")
            model_values = tuple(model_q[market_id] for market_id, baseline_id in sorted(
                mapped.items(), key=lambda item: baseline.bin_ids.index(item[1])
            ))
            market_values = tuple(market_q[market_id] for market_id, baseline_id in sorted(
                mapped.items(), key=lambda item: baseline.bin_ids.index(item[1])
            ))
            if model_values != baseline.values:
                raise ValueError("market_model_vector_not_baseline")
            validate_probability_group(model_values)
            validate_probability_group(market_values)
            return MarketVector(
                bin_ids=baseline.bin_ids,
                values=market_values,
                observed_at=selected_at,
                quality_proven=True,
            ), "market_covered"
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None, "market_no_exact_complete_fresh_match"


def extract_cases(
    conn, *, cities, as_of, start_date, market_evidence_conn=None,
    market_evidence_unavailable_reason=None,
):
    bounded(conn)
    settlements = read_current_settlement_history(
        conn, cities_by_name=cities, as_of=as_of
    )
    excluded = Counter(settlements.excluded_reason_counts)
    cases, evidence, past = [], [], {}
    for truth in sorted(
        settlements.rows, key=lambda x: (str(x.target_date), x.city, x.metric)
    ):
        target = date.fromisoformat(str(truth.target_date))
        if target < start_date:
            continue
        city = cities[truth.city]
        decision = datetime.combine(
            target - timedelta(days=1), time(12), ZoneInfo(city.timezone)
        ).astimezone(timezone.utc)
        bounded(conn)
        row = conn.execute(
            "SELECT * FROM forecast_posteriors WHERE city=? AND target_date=? AND temperature_metric=? "
            "AND julianday(computed_at)<=julianday(?) AND julianday(recorded_at)<=julianday(?) "
            "AND julianday(computed_at)>=julianday(?) ORDER BY computed_at DESC,posterior_id DESC LIMIT 1",
            (
                truth.city,
                str(target),
                truth.metric,
                decision.isoformat(),
                decision.isoformat(),
                (decision - timedelta(hours=1)).isoformat(),
            ),
        ).fetchone()
        if row is None:
            excluded["no_causal_day_ahead_posterior"] += 1
            continue
        try:
            row = dict(row)
            provenance = json.loads(row["provenance_json"])
            bins, winner = ordered_bins(provenance, city, truth.settlement_value)
            ids = tuple(b["bin_id"] for b in bins)
            baseline_q = json.loads(row["q_json"])
            if set(baseline_q) != set(ids):
                raise ValueError("posterior bin identity mismatch")
            baseline = ProbabilityVector(
                values=tuple(baseline_q[k] for k in ids),
                available_at=max(
                    aware(row["computed_at"]),
                    aware(row["recorded_at"], sqlite_utc=True),
                ),
                bin_ids=ids,
            )
            key = (truth.city, truth.metric)
            history = past.setdefault(key, [])
            candidates, values, raw_ids = make_candidates(
                conn, row, provenance, city, decision, history, bins
            )
            known_at = aware(truth.label_known_at)
            settlement_c = (
                (truth.settlement_value - 32.0) / 1.8
                if truth.settlement_unit == "F"
                else truth.settlement_value
            )
            history.append(
                dict(
                    target_date=str(target),
                    known_at=known_at,
                    values=values,
                    settlement_c=settlement_c,
                )
            )
            if not candidates:
                raise ValueError("no_qualified_source_combination")
            market = None
            market_reason = market_evidence_unavailable_reason or "market_evidence_not_requested"
            if market_evidence_conn is not None:
                market, market_reason = _market_vector_for_case(
                    market_evidence_conn,
                    city=truth.city,
                    target_date=target,
                    metric=truth.metric,
                    unit=city.settlement_unit,
                    decision_at=decision,
                    baseline=baseline,
                    bins=bins,
                )
                if market is None:
                    excluded[market_reason] += 1
            elif market_evidence_unavailable_reason is not None:
                excluded[market_reason] += 1
            cases.append(
                ProbabilityValidationCase(
                    city=truth.city,
                    metric=truth.metric,
                    target_date=target,
                    decision_at=decision,
                    label_known_at=known_at,
                    bin_ids=ids,
                    winner_index=winner,
                    candidates=candidates,
                    baseline=baseline,
                    market=market,
                )
            )
            evidence.append(
                dict(
                    city=truth.city,
                    metric=truth.metric,
                    target_date=str(target),
                    decision_at=decision.isoformat(),
                    posterior_id=row["posterior_id"],
                    source_row_ids=raw_ids,
                    label_known_at=known_at.isoformat(),
                    market_coverage=market is not None,
                    market_reason=market_reason,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            excluded[str(exc)] += 1
    return cases, evidence, dict(excluded)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecasts", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--exclude-city", action="append", default=[])
    parser.add_argument("--market-evidence", type=Path)
    args = parser.parse_args(argv)
    predeclared = declared_baskets()
    conn = _connect_read_only(
        args.forecasts, deadline_monotonic=clock.monotonic() + 3.0
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=1")
    market_conn = None
    market_evidence_unavailable_reason = None
    if args.market_evidence is not None:
        if not args.market_evidence.is_file():
            market_evidence_unavailable_reason = "market_evidence_file_unavailable"
        else:
            try:
                market_conn = _connect_read_only(
                    args.market_evidence, deadline_monotonic=clock.monotonic() + 3.0
                )
                market_conn.row_factory = sqlite3.Row
                market_conn.execute("PRAGMA query_only=1")
            except (OSError, sqlite3.Error):
                market_evidence_unavailable_reason = "market_evidence_unreadable"
                if market_conn is not None:
                    market_conn.close()
                    market_conn = None
    try:
        cases, evidence, excluded = extract_cases(
            conn,
            cities={
                name: city
                for name, city in runtime_cities_by_name().items()
                if name not in args.exclude_city
            },
            as_of=aware(args.as_of),
            start_date=args.start_date,
            market_evidence_conn=market_conn,
            market_evidence_unavailable_reason=market_evidence_unavailable_reason,
        )
    finally:
        conn.close()
        if market_conn is not None:
            market_conn.close()
    result = (
        validate_probability_candidates(
            cases,
            predeclared_candidates=predeclared,
        )
        if cases
        else None
    )
    print(
        json.dumps(
            dict(
                validation=asdict(result)
                if result is not None
                else {"status": "INSUFFICIENT_CAUSAL_EVIDENCE"},
                cases=evidence,
                candidate_policy="distinct provider-family pairs/triples/quartets; absence retains zero coverage; distinct families do not imply independent errors",
                excluded=excluded,
                market_comparison=(
                    "UNAVAILABLE: --market-evidence not supplied"
                    if args.market_evidence is None
                    else f"UNAVAILABLE: {market_evidence_unavailable_reason}"
                    if market_evidence_unavailable_reason is not None
                    else "EXACT_COMPLETE_FRESH_MATCH_ONLY: no market superiority or latency claim"
                ),
                sampling="one fixed local-noon day-ahead case per city/metric/date; stored baseline versus current-law counterfactuals",
                latency_advantage_proven=False,
            ),
            default=str,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
