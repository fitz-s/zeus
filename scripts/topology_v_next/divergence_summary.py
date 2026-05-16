# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md
#                  §1.2 (public API), §6 (P4 gate metrics), §6.3 (dual-metric gate),
#                  §6.4 (per-friction-pattern), §7 P3.2 (test list),
#                  §9.1 (datetime.now(UTC) — NOT utcnow(), deprecated since 3.12)
"""
Divergence log analyzer and P4 gate evaluator for topology v_next shadow runs.

Public API (SCAFFOLD §1.2):
    SummaryReport      -- frozen dataclass, all §6 summary fields
    aggregate(start_date, end_date, *, root, out_path, skip_honored_filter) -> dict
    load_window(evidence_dir, days_back=14) -> list[DivergenceRecord]
    write_summary(report, output_dir) -> None
    cli_main(argv=None) -> int

P4 gate (SCAFFOLD §6.3):
    p4_gate_ok = (
        all(v >= 0.95 for v in per_profile_agreement.values() if v is not None)
        AND skip_honored_rate < 0.20
        AND sample_size_tier == "sufficient"
    )

Sample size tiers (SCAFFOLD §0 INCONSISTENCY-2):
    insufficient  < 100
    marginal     100-499
    sufficient   >= 500

Codex-importable: stdlib + scripts.topology_v_next only. No anthropic SDK.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from scripts.topology_v_next.dataclasses import FrictionPattern
from scripts.topology_v_next.divergence_logger import (
    DivergenceRecord,
    classify_divergence,
    daily_path,
)


# ---------------------------------------------------------------------------
# SummaryReport — per-window aggregate schema (SCAFFOLD §6)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SummaryReport:
    """
    Frozen dataclass capturing a summary window's aggregate metrics.

    All fields per SCAFFOLD §6. Serialised to JSON by write_summary().
    """

    # Window bounds
    date_range: tuple[str, str]            # (start_date_iso, end_date_iso)

    # Volume
    total_records: int                     # all records in window (incl. SKIP + ERROR)

    # Agreement (SKIP_HONORED and ERROR excluded from denominator; SCAFFOLD §6.2)
    overall_agreement_pct_excluding_skips: float   # aggregate across all profiles
    per_profile_agreement: dict[str, float | None] # profile_id -> pct (None if no eligible records)

    # Skip
    skip_honored_rate: float               # SKIP_HONORED / total_records

    # Friction patterns
    per_friction_pattern_count: dict[str, int]  # FrictionPattern value -> count (SCAFFOLD §6.4)

    # Sample size label (SCAFFOLD §0 INCONSISTENCY-2)
    sample_size_tier: str                  # "insufficient" | "marginal" | "sufficient"

    # P4 gate (SCAFFOLD §6.3)
    p4_gate_ok: bool


# ---------------------------------------------------------------------------
# Internal: _sample_size_tier
# ---------------------------------------------------------------------------

def _sample_size_tier(n: int) -> str:
    """Map record count to sample size label per SCAFFOLD §0 INCONSISTENCY-2."""
    if n < 100:
        return "insufficient"
    if n < 500:
        return "marginal"
    return "sufficient"


# ---------------------------------------------------------------------------
# Internal: _load_window generator
# ---------------------------------------------------------------------------

def _load_window(
    start_date: date,
    end_date: date,
    root: Path | str,
) -> Iterator[DivergenceRecord]:
    """
    Yield DivergenceRecord objects from JSONL files covering [start_date, end_date].

    Malformed lines: warn to stderr + continue (SCAFFOLD §1.1 never-raises contract).
    Missing files: silently skip (no file = zero records for that day).
    """
    root = Path(root)
    delta = (end_date - start_date).days
    for i in range(delta + 1):
        day = start_date + timedelta(days=i)
        path = daily_path(root=root, today=day)
        if not path.exists():
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                for lineno, raw in enumerate(fh, 1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                        # Reconstruct DivergenceRecord — tuples serialise as lists in JSON
                        record = DivergenceRecord(
                            ts=data["ts"],
                            schema_version=data["schema_version"],
                            event_type=data["event_type"],
                            agreement_class=data["agreement_class"],
                            profile_resolved_old=data.get("profile_resolved_old"),
                            old_admit_status=data["old_admit_status"],
                            profile_resolved_new=data.get("profile_resolved_new"),
                            new_admit_severity=data.get("new_admit_severity"),
                            new_admit_ok=data.get("new_admit_ok"),
                            intent_typed=data["intent_typed"],
                            intent_supplied=data.get("intent_supplied"),
                            files=tuple(data.get("files", [])),
                            missing_companion=tuple(data.get("missing_companion", [])),
                            companion_skip_used=data["companion_skip_used"],
                            friction_pattern_hit=data.get("friction_pattern_hit"),
                            closest_rejected_profile=data.get("closest_rejected_profile"),
                            kernel_alert_count=data["kernel_alert_count"],
                            friction_budget_used=data["friction_budget_used"],
                            task_hash=data["task_hash"],
                            error=data.get("error"),
                        )
                        yield record
                    except (KeyError, TypeError, ValueError) as exc:
                        sys.stderr.write(
                            f"[divergence_summary] malformed record in {path}:{lineno}: "
                            f"{type(exc).__name__}: {exc}\n"
                        )
        except OSError as exc:
            sys.stderr.write(
                f"[divergence_summary] cannot read {path}: {type(exc).__name__}: {exc}\n"
            )


# ---------------------------------------------------------------------------
# Internal: _compute_per_profile_agreement
# ---------------------------------------------------------------------------

def _compute_per_profile_agreement(
    records: list[DivergenceRecord],
    *,
    exclude_skip_honored: bool = True,
) -> dict[str, float | None]:
    """
    Compute per-profile agreement percentage (SCAFFOLD §6.3).

    Groups records by profile_resolved_new. For each group, computes
    n_agree / n_eligible where eligible excludes SKIP_HONORED (and ERROR)
    from the denominator. Returns None for a profile with zero eligible records.
    """
    # profile_id -> (n_agree, n_eligible)
    buckets: dict[str, list[int]] = {}  # [n_agree, n_eligible]

    for record in records:
        profile = record.profile_resolved_new or "__no_profile__"
        if profile not in buckets:
            buckets[profile] = [0, 0]

        ac = record.agreement_class
        if exclude_skip_honored and ac in ("SKIP_HONORED", "ERROR"):
            continue

        buckets[profile][1] += 1  # eligible
        if ac == "AGREE":
            buckets[profile][0] += 1  # agree

    result: dict[str, float | None] = {}
    for profile, (n_agree, n_eligible) in buckets.items():
        result[profile] = (n_agree / n_eligible) if n_eligible > 0 else None
    return result


# ---------------------------------------------------------------------------
# Internal: _compute_per_friction_pattern
# ---------------------------------------------------------------------------

def _compute_per_friction_pattern(records: list[DivergenceRecord]) -> dict[str, int]:
    """
    Count occurrences of each FrictionPattern across all records (SCAFFOLD §6.4).

    Only records with a non-None friction_pattern_hit are counted.
    All FrictionPattern values are present in the output (zero if not observed).
    """
    counts: dict[str, int] = {fp.value: 0 for fp in FrictionPattern}
    for record in records:
        if record.friction_pattern_hit is not None:
            if record.friction_pattern_hit in counts:
                counts[record.friction_pattern_hit] += 1
    return counts


# ---------------------------------------------------------------------------
# Internal: _aggregate_records
# ---------------------------------------------------------------------------

def _aggregate_records(
    records: list[DivergenceRecord],
    *,
    start_date_iso: str,
    end_date_iso: str,
    skip_honored_filter: bool = True,
) -> SummaryReport:
    """
    Core aggregation over a pre-loaded record list (SCAFFOLD §6).

    Separated from I/O for testability (SCAFFOLD §7 P3.2 test list).
    """
    total = len(records)

    # Skip-honored rate over ALL records
    n_skip = sum(1 for r in records if r.agreement_class == "SKIP_HONORED")
    skip_rate = (n_skip / total) if total > 0 else 0.0

    # Overall agreement-% (excluding SKIP_HONORED + ERROR from denominator)
    eligible = [
        r for r in records
        if r.agreement_class not in ("SKIP_HONORED", "ERROR")
    ]
    n_agree_overall = sum(1 for r in eligible if r.agreement_class == "AGREE")
    overall_pct = (n_agree_overall / len(eligible)) if eligible else 0.0

    # Per-profile agreement
    per_profile = _compute_per_profile_agreement(
        records, exclude_skip_honored=skip_honored_filter
    )

    # Per-friction-pattern counts
    per_friction = _compute_per_friction_pattern(records)

    # Sample size tier
    tier = _sample_size_tier(total)

    # P4 gate (SCAFFOLD §6.3)
    profile_pcts = [v for v in per_profile.values() if v is not None]
    all_profiles_pass = all(v >= 0.95 for v in profile_pcts) if profile_pcts else False
    p4_ok = (
        all_profiles_pass
        and skip_rate < 0.20
        and tier == "sufficient"
    )

    return SummaryReport(
        date_range=(start_date_iso, end_date_iso),
        total_records=total,
        overall_agreement_pct_excluding_skips=overall_pct,
        per_profile_agreement=per_profile,
        skip_honored_rate=skip_rate,
        per_friction_pattern_count=per_friction,
        sample_size_tier=tier,
        p4_gate_ok=p4_ok,
    )


# ---------------------------------------------------------------------------
# Public: load_window
# ---------------------------------------------------------------------------

def load_window(
    evidence_dir: Path | str = "evidence/topology_v_next_shadow",
    days_back: int = 14,
) -> list[DivergenceRecord]:
    """
    Return records from the last *days_back* days (inclusive of today).

    Pure convenience wrapper; computes UTC date range then calls _load_window.
    """
    today = datetime.now(UTC).date()
    start = today - timedelta(days=days_back - 1)
    return list(_load_window(start, today, root=evidence_dir))


# ---------------------------------------------------------------------------
# Public: write_summary
# ---------------------------------------------------------------------------

def write_summary(
    report: SummaryReport,
    output_dir: Path | str,
) -> None:
    """
    Write *report* as JSON to {output_dir}/divergence_summary_{start}_{end}.json.

    Uses atomic tmp + os.replace() (SCAFFOLD §4.6). Never raises — errors go
    to stderr so the caller is not disrupted.
    """
    try:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        start_iso, end_iso = report.date_range
        filename = f"divergence_summary_{start_iso}_{end_iso}.json"
        dest = output_dir / filename

        payload = _render_summary(report)
        content = json.dumps(payload, indent=2, sort_keys=True)

        # Atomic write: tmp file in same directory, then os.replace()
        fd, tmp_path = tempfile.mkstemp(
            dir=output_dir, prefix=".divergence_summary_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp_path, dest)
        except Exception:
            # Clean up tmp on failure; re-raise for outer handler
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"[divergence_summary] write_summary failed: {type(exc).__name__}: {exc}\n"
        )


# ---------------------------------------------------------------------------
# Internal: _render_summary
# ---------------------------------------------------------------------------

def _render_summary(report: SummaryReport) -> dict[str, Any]:
    """
    Convert SummaryReport to a JSON-serialisable dict.

    Tuple date_range → list for JSON compatibility.
    """
    return {
        "date_range": list(report.date_range),
        "total_records": report.total_records,
        "overall_agreement_pct_excluding_skips": report.overall_agreement_pct_excluding_skips,
        "per_profile_agreement": report.per_profile_agreement,
        "skip_honored_rate": report.skip_honored_rate,
        "per_friction_pattern_count": report.per_friction_pattern_count,
        "sample_size_tier": report.sample_size_tier,
        "p4_gate_ok": report.p4_gate_ok,
    }


# ---------------------------------------------------------------------------
# Public: aggregate
# ---------------------------------------------------------------------------

def aggregate(
    start_date: date | str,
    end_date: date | str,
    *,
    root: Path | str = "evidence/topology_v_next_shadow",
    out_path: Path | str | None = None,
    skip_honored_filter: bool = True,
) -> dict[str, Any]:
    """
    Load JSONL records in [start_date, end_date], aggregate, optionally write.

    Returns the summary dict regardless of whether out_path is set.
    If out_path is provided, calls write_summary() (atomic, never raises).

    SCAFFOLD §1.2 signature with positional date args + keyword-only options.
    """
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)

    records = list(_load_window(start_date, end_date, root=root))

    report = _aggregate_records(
        records,
        start_date_iso=start_date.isoformat(),
        end_date_iso=end_date.isoformat(),
        skip_honored_filter=skip_honored_filter,
    )

    if out_path is not None:
        write_summary(report, output_dir=out_path)

    return _render_summary(report)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cli_main(argv: list[str] | None = None) -> int:
    """
    Standalone CLI subcommand for divergence summary analysis.

    Exit codes:
        0  success (summary written, p4_gate_ok may be True or False)
        1  insufficient sample size (p4_gate_ok=False due to tier)
        2  hard error (argument parse failure, unexpected exception)
    """
    parser = argparse.ArgumentParser(
        prog="divergence_summary",
        description="Aggregate divergence observations and evaluate P4 gate.",
    )
    parser.add_argument(
        "--start-date",
        required=True,
        metavar="YYYY-MM-DD",
        help="Inclusive start date (UTC) for the aggregation window.",
    )
    parser.add_argument(
        "--end-date",
        required=True,
        metavar="YYYY-MM-DD",
        help="Inclusive end date (UTC) for the aggregation window.",
    )
    parser.add_argument(
        "--root",
        default="evidence/topology_v_next_shadow",
        metavar="DIR",
        help="Evidence directory root containing divergence_YYYY-MM-DD.jsonl files.",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="DIR",
        help=(
            "Output directory for the summary JSON. "
            "Defaults to --root if not specified."
        ),
    )
    parser.add_argument(
        "--include-skip-honored",
        action="store_true",
        default=False,
        help="Include SKIP_HONORED records in per-profile agreement denominator.",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    try:
        start = date.fromisoformat(args.start_date)
        end = date.fromisoformat(args.end_date)
    except ValueError as exc:
        sys.stderr.write(f"[divergence_summary] invalid date: {exc}\n")
        return 2

    if start > end:
        sys.stderr.write(
            f"[divergence_summary] --start-date {start} is after --end-date {end}\n"
        )
        return 2

    out_dir = args.out if args.out is not None else args.root

    try:
        summary = aggregate(
            start,
            end,
            root=args.root,
            out_path=out_dir,
            skip_honored_filter=not args.include_skip_honored,
        )
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"[divergence_summary] aggregate failed: {type(exc).__name__}: {exc}\n"
        )
        return 2

    # Emit human-readable result to stdout
    tier = summary["sample_size_tier"]
    total = summary["total_records"]
    overall = summary["overall_agreement_pct_excluding_skips"]
    skip_rate = summary["skip_honored_rate"]
    p4_ok = summary["p4_gate_ok"]

    sys.stdout.write(
        f"divergence_summary: {start.isoformat()} → {end.isoformat()}\n"
        f"  records:            {total}\n"
        f"  sample_size_tier:   {tier}\n"
        f"  overall_agreement:  {overall:.1%} (excl. skip/error)\n"
        f"  skip_honored_rate:  {skip_rate:.1%}\n"
        f"  p4_gate_ok:         {p4_ok}\n"
    )

    per_profile = summary.get("per_profile_agreement", {})
    if per_profile:
        sys.stdout.write("  per_profile_agreement:\n")
        for profile, pct in sorted(per_profile.items()):
            pct_str = f"{pct:.1%}" if pct is not None else "N/A (no eligible records)"
            sys.stdout.write(f"    {profile}: {pct_str}\n")

    if tier == "insufficient":
        return 1

    return 0


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(cli_main())
