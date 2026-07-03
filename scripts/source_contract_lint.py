#!/usr/bin/env python3
# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Inter-registry coherence lint (calendar/data_sources_registry/forecast_source_registry/code).
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md
"""Inter-registry coherence lint — PLAN §PR1.

Checks 5 assertions across 3 registries:
  1. calendar.source_id ⊆ data_sources_registry.sources[].id
  2. forecast_source_registry entry_primary ROLE ⇒ calendar live_authorization=true
     (and diagnostic-only / experimental-backfill ⇒ NOT live; keyed on allowed_roles, not tier)
  3. calendar backfill_only=true ⇒ live_authorization=false
  4. code data_version param (snapshot_ingest_contract) matches SDK param (ecmwf_open_data.py);
     calendar parameter field drift is advisory only
  5. HKO source never carries WU/VHHH station mapping

Advisory by default (exit 0 with warnings).
--strict: exit 1 when any violation or drift finding is detected.

Note: Assertion 5 WILL flag drift today (calendar says mx2t6, code uses mx2t3).
This is expected and correct; a later calendar-fix PR resolves it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Bootstrap: ensure repo root is on sys.path so `src.*` imports work regardless
# of how this script is invoked (python3 scripts/source_contract_lint.py, cron, etc.)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CALENDAR_PATH = REPO_ROOT / "config" / "source_release_calendar.yaml"
REGISTRY_PATH = REPO_ROOT / "architecture" / "data_sources_registry_2026_05_08.yaml"
FORECAST_REGISTRY_PATH = REPO_ROOT / "src" / "data" / "forecast_source_registry.py"
SNAPSHOT_CONTRACT_PATH = REPO_ROOT / "src" / "contracts" / "snapshot_ingest_contract.py"
ECMWF_OPEN_DATA_PATH = REPO_ROOT / "src" / "data" / "ecmwf_open_data.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_calendar() -> list[dict[str, Any]]:
    with CALENDAR_PATH.open() as f:
        data = yaml.safe_load(f)
    entries: list[dict[str, Any]] = data.get("entries", [])
    return entries


def _load_registry_ids() -> set[str]:
    with REGISTRY_PATH.open() as f:
        data = yaml.safe_load(f)
    return {src["id"] for src in data.get("sources", [])}


def _make_finding(
    assertion: int,
    level: str,
    message: str,
    **extra: Any,
) -> dict[str, Any]:
    """Construct a finding dict."""
    return {"assertion": assertion, "level": level, "message": message, **extra}


# ---------------------------------------------------------------------------
# Assertion 1: calendar.source_id ⊆ data_sources_registry.sources[].id
# ---------------------------------------------------------------------------

def run_assertion_1_calendar_source_in_registry() -> list[dict[str, Any]]:
    """Return findings for calendar source_ids not present in data_sources_registry.

    KNOWN: calendar carries source_id='tigge'; registry has id='tigge_mars'.
    This is surfaced as a drift warning (not an error), since the alias is documented
    in forecast_source_registry.py (_CALIBRATION_LOOKUP_SOURCE_ID_BY_FORECAST_SOURCE_ID).
    Advisory by design — promoting to blocking after a registry aliasing PR.
    """
    entries = _load_calendar()
    registry_ids = _load_registry_ids()
    findings = []

    for entry in entries:
        source_id = entry["source_id"]
        if source_id not in registry_ids:
            findings.append(_make_finding(
                assertion=1,
                level="drift",
                message=(
                    f"calendar source_id={source_id!r} "
                    f"(calendar_id={entry['calendar_id']!r}) "
                    f"not found in data_sources_registry — "
                    f"possible alias (e.g. 'tigge' vs 'tigge_mars')"
                ),
                source_id=source_id,
                calendar_id=entry["calendar_id"],
            ))

    return findings


# ---------------------------------------------------------------------------
# Assertion 2: forecast_source_registry entry_primary role ⇒ calendar live_authorization=true
# ---------------------------------------------------------------------------

def run_assertion_2_primary_tier_implies_live_authorized() -> list[dict[str, Any]]:
    """Coherence: a forecast source whose ROLE includes 'entry_primary' must have calendar
    live_authorization=true; a diagnostic-only source must NOT (PR review #329 F7).

    The prior version keyed on ``tier == "primary"``, which is WRONG: tier='primary' means
    "primary forecast TABLE", not live-trading authority. openmeteo_previous_runs is
    tier=primary but allowed_roles=('diagnostic',) with calendar live_authorization=false —
    a CONSISTENT blocked/diagnostic source, not drift. Keying on allowed_roles removes the
    permanent false-positive and prevents a future --strict from forcing a diagnostic source
    into live authority.
    """
    from src.data.forecast_source_registry import SOURCES

    entries = _load_calendar()
    calendar_live: dict[str, bool] = {}
    for entry in entries:
        sid = entry["source_id"]
        calendar_live[sid] = calendar_live.get(sid, False) or bool(entry.get("live_authorization", False))

    # Calendar backfill_only per source (a backfill-only source is never expected live).
    calendar_backfill: dict[str, bool] = {}
    for entry in entries:
        sid = entry["source_id"]
        calendar_backfill[sid] = calendar_backfill.get(sid, False) or bool(entry.get("backfill_only", False))

    findings = []
    for source_id, spec in SOURCES.items():
        roles = tuple(getattr(spec, "allowed_roles", ()) or ())
        live_auth = calendar_live.get(source_id)
        # PR review #329 G: entry_primary alone does NOT imply live. An experimental /
        # operator-gated / disabled-by-default / backfill-only source legitimately carries
        # entry_primary in its role menu while being live_authorization=false. Only a source
        # that is a genuine live candidate must be live-authorized.
        is_live_candidate = (
            "entry_primary" in roles
            and getattr(spec, "tier", "") != "experimental"
            and not getattr(spec, "requires_operator_decision", False)
            and getattr(spec, "enabled_by_default", True)
            and not calendar_backfill.get(source_id, False)
        )
        if is_live_candidate and live_auth is False:
            findings.append(_make_finding(
                assertion=2, level="drift",
                message=(
                    f"forecast source={source_id!r} is a live entry-primary candidate but calendar "
                    f"live_authorization=false — must be live-authorized"
                ),
                source_id=source_id,
            ))
        elif roles == ("diagnostic",) and live_auth is True:
            findings.append(_make_finding(
                assertion=2, level="violation",
                message=(
                    f"forecast source={source_id!r} is diagnostic-only but calendar "
                    f"live_authorization=true — diagnostic sources must NOT be live-authorized"
                ),
                source_id=source_id,
            ))

    return findings


# ---------------------------------------------------------------------------
# Assertion 4: backfill_only=true ⇒ live_authorization=false
# ---------------------------------------------------------------------------

def run_assertion_4_backfill_implies_not_live() -> list[dict[str, Any]]:
    """Return violation findings for backfill_only=true entries with live_authorization=true."""
    entries = _load_calendar()
    findings = []

    for entry in entries:
        if entry.get("backfill_only") is True and entry.get("live_authorization") is True:
            findings.append(_make_finding(
                assertion=4,
                level="violation",
                message=(
                    f"calendar_id={entry['calendar_id']!r}: "
                    f"backfill_only=true implies live_authorization must be false"
                ),
                calendar_id=entry["calendar_id"],
            ))

    return findings


# ---------------------------------------------------------------------------
# Assertion 5: code data_version param matches SDK param; calendar drift is advisory
# ---------------------------------------------------------------------------

# Calendar metric -> ECMWF SDK TRACKS key (and the code data_version metric word).
# The HIGH track fetches mx2t*, the LOW track fetches mn2t*; comparing a LOW calendar
# entry against the HIGH code param (the original bug) produced a nonsense finding that
# the calendar-fix PR could never clear. Each entry is now keyed by its own metric.
_ECMWF_METRIC_TO_SDK_TRACK = {"high": "mx2t6_high", "low": "mn2t6_low"}
_ECMWF_METRIC_TO_CODE = {
    "high": ("_ECMWF_OPENDATA_HIGH_DATA_VERSION", "max"),
    "low": ("_ECMWF_OPENDATA_LOW_DATA_VERSION", "min"),
}


def _extract_sdk_param(track_key: str) -> str | None:
    """Read ecmwf_open_data.py TRACKS[track_key]['open_data_param'] via file scan.

    Text scan (not import) to avoid pulling the full ecmwf ingest module's external deps.
    """
    import re
    text = ECMWF_OPEN_DATA_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf'"{re.escape(track_key)}"\s*:\s*\{{[^}}]*"open_data_param"\s*:\s*"([^"]+)"',
        text,
        re.DOTALL,
    )
    return match.group(1) if match else None


def _extract_code_data_version_param(metric: str) -> str | None:
    """Extract the param substring from the metric-matched data_version constant in
    snapshot_ingest_contract.py.

        metric 'high' -> _ECMWF_OPENDATA_HIGH_DATA_VERSION = "ecmwf_opendata_<p>_local_calendar_day_max_v1"
        metric 'low'  -> _ECMWF_OPENDATA_LOW_DATA_VERSION  = "ecmwf_opendata_<p>_local_calendar_day_min_v1"
    """
    import re
    const, suffix = _ECMWF_METRIC_TO_CODE[metric]
    text = SNAPSHOT_CONTRACT_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf'{const}\s*=\s*"ecmwf_opendata_([^_]+)_local_calendar_day_{suffix}_v1"',
        text,
    )
    return match.group(1) if match else None


def run_assertion_5_code_param_vs_calendar() -> list[dict[str, Any]]:
    """Param-drift findings between code/SDK and calendar, keyed PER METRIC.

    Per ecmwf_open_data calendar entry (high/low):
      1. SDK param (TRACKS[track].open_data_param) must equal the code data_version param
         for the SAME metric — both should be mx2t3 (high) / mn2t3 (low). Mismatch = violation.
      2. Calendar ``parameter`` vs code param — expected drift today (calendar mx2t6/mn2t6 vs
         code mx2t3/mn2t3). Reported as advisory drift; clears only when the calendar PR
         updates BOTH entries, at which point the matching xfail test honestly flips to PASS.
    """
    findings: list[dict[str, Any]] = []

    for entry in _load_calendar():
        if entry.get("source_id") != "ecmwf_open_data":
            continue
        metric = str(entry.get("metric", ""))
        track_key = _ECMWF_METRIC_TO_SDK_TRACK.get(metric)
        if track_key is None:
            findings.append(_make_finding(
                assertion=5, level="warning",
                message=f"calendar_id={entry.get('calendar_id')!r}: unknown ecmwf metric {metric!r}",
                calendar_id=entry.get("calendar_id"),
            ))
            continue

        sdk_param = _extract_sdk_param(track_key)
        code_param = _extract_code_data_version_param(metric)
        calendar_param = entry.get("parameter", "")

        if sdk_param is None or code_param is None:
            findings.append(_make_finding(
                assertion=5, level="warning",
                message=(
                    f"calendar_id={entry.get('calendar_id')!r}: could not extract "
                    f"sdk_param({track_key})={sdk_param!r} / code_param({metric})={code_param!r}"
                ),
                calendar_id=entry.get("calendar_id"),
            ))
            continue

        if sdk_param != code_param:
            findings.append(_make_finding(
                assertion=5, level="violation",
                message=(
                    f"calendar_id={entry['calendar_id']!r}: SDK param ({sdk_param!r}) != "
                    f"code data_version param ({code_param!r}) for metric={metric!r} — must align"
                ),
                calendar_id=entry["calendar_id"], sdk_param=sdk_param, code_param=code_param,
            ))

        if calendar_param != code_param:
            findings.append(_make_finding(
                assertion=5, level="drift",
                message=(
                    f"calendar_id={entry['calendar_id']!r}: calendar parameter={calendar_param!r} "
                    f"!= code param={code_param!r} (metric={metric!r}) — calendar drift "
                    f"(expected; fix in later calendar PR)"
                ),
                calendar_id=entry["calendar_id"], calendar_param=calendar_param,
                code_param=code_param, sdk_param=sdk_param,
            ))

    return findings


# ---------------------------------------------------------------------------
# Assertion 6: HKO source never carries WU/VHHH station mapping
# ---------------------------------------------------------------------------

def run_assertion_6_hko_no_wu_vhhh() -> list[dict[str, Any]]:
    """Return violation findings for any calendar/registry entry where an HKO source
    carries a WU/VHHH station mapping.

    Today this is a no-op pass (no such binding exists). This is an antibody for
    future config drift: if anyone adds an HKO→WU/VHHH fallback, this fires.
    """
    findings = []

    entries = _load_calendar()
    for entry in entries:
        if not str(entry.get("source_id", "")).startswith("hko"):
            continue
        # Check all string values in the entry for WU/VHHH markers
        entry_str = str(entry)
        if "VHHH" in entry_str or "wu_icao" in entry_str:
            findings.append(_make_finding(
                assertion=6,
                level="violation",
                message=(
                    f"calendar_id={entry['calendar_id']!r}: "
                    f"HKO source carries WU/VHHH station mapping — forbidden"
                ),
                calendar_id=entry["calendar_id"],
                source_id=entry["source_id"],
            ))

    # Also check data_sources_registry for HKO entries with VHHH/wu_icao
    # in structured fields only (station_id, fallback_source, etc.), NOT in
    # documentation text (which legitimately describes the anti-pattern to avoid).
    with REGISTRY_PATH.open() as f:
        registry_data = yaml.safe_load(f)
    for source in registry_data.get("sources", []):
        if not str(source.get("id", "")).startswith("hko"):
            continue
        # Check structured fields that would indicate an actual routing to VHHH/WU
        # station_id, fallback, etc. — NOT narrative documentation text.
        for field in ("station_id", "fallback_source", "fallback_station", "wu_station"):
            val = str(source.get(field, ""))
            if "VHHH" in val or "wu_icao" in val:
                findings.append(_make_finding(
                    assertion=6,
                    level="violation",
                    message=(
                        f"data_sources_registry source={source['id']!r}: "
                        f"field {field!r}={val!r} carries WU/VHHH station mapping — forbidden"
                    ),
                    source_id=source["id"],
                    field=field,
                ))

    return findings


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------

def run_all_assertions() -> list[dict[str, Any]]:
    """Run all active assertions and return combined findings list."""
    all_findings: list[dict[str, Any]] = []
    all_findings.extend(run_assertion_1_calendar_source_in_registry())
    all_findings.extend(run_assertion_2_primary_tier_implies_live_authorized())
    all_findings.extend(run_assertion_4_backfill_implies_not_live())
    all_findings.extend(run_assertion_5_code_param_vs_calendar())
    all_findings.extend(run_assertion_6_hko_no_wu_vhhh())
    return all_findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inter-registry coherence lint (advisory by default, --strict exits 1)."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any violation or drift finding is detected.",
    )
    args = parser.parse_args(argv)

    findings = run_all_assertions()

    if not findings:
        print("source_contract_lint: OK — all 6 assertions passed, no findings.")
        return 0

    violations = [f for f in findings if f["level"] == "violation"]
    drifts = [f for f in findings if f["level"] == "drift"]
    warnings = [f for f in findings if f["level"] == "warning"]

    print(f"source_contract_lint: {len(findings)} finding(s) — "
          f"{len(violations)} violation(s), {len(drifts)} drift(s), {len(warnings)} warning(s)")
    print()

    for f in findings:
        prefix = {
            "violation": "[VIOLATION]",
            "drift": "[DRIFT]",
            "warning": "[WARNING]",
        }.get(f["level"], "[INFO]")
        print(f"  A{f['assertion']} {prefix} {f['message']}")

    print()
    if violations:
        print(f"  {len(violations)} violation(s) require fix (invariant broken).")
    if drifts:
        print(f"  {len(drifts)} drift(s) are advisory today; promote to violation after calendar-fix PR.")
    if warnings:
        print(f"  {len(warnings)} warning(s) are informational.")

    if args.strict and (violations or drifts):
        print("\nstrict mode: exiting 1 (violations or drift found)")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
