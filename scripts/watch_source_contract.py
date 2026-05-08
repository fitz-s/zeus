# Lifecycle: created=2026-04-29; last_reviewed=2026-04-29; last_reused=never
# Purpose: Polymarket Gamma settlement-source contract watch and city quarantine writer for configured weather cities.
# Reuse: Inspect src/data/market_scanner.py source-contract/quarantine helpers and architecture/script_manifest.yaml before relying on alerts.
"""Watch Polymarket resolution sources against configured city contracts.

This script is designed for Venus/cron-style monitoring. It fetches active
Gamma weather events, compares their resolutionSource metadata to Zeus city
source contracts, prints a machine-readable or text report, and exits non-zero
when configured source proof is missing or wrong. ALERT mismatches also persist
a city-level source-contract quarantine unless --report-only is supplied.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data import market_scanner as ms  # noqa: E402
from src.state import db as state_db  # noqa: E402


SEVERITY_RANK = {
    "OK": 0,
    "WARN": 1,
    "ALERT": 2,
    "DATA_UNAVAILABLE": 3,
}
COMPACT_SCHEMA_VERSION = 1

SOURCE_STATUS_SEVERITY = {
    "MATCH": "OK",
    "MISSING": "WARN",
    "AMBIGUOUS": "ALERT",
    "MISMATCH": "ALERT",
    "UNSUPPORTED": "ALERT",
}
QUARANTINE_STATUSES = ms.SOURCE_CONTRACT_ALERT_STATUSES


def _event_id(event: dict[str, Any]) -> str:
    return str(event.get("id") or event.get("slug") or "<unknown>")


def _event_text(event: dict[str, Any]) -> str:
    fields = [
        event.get("title", ""),
        event.get("slug", ""),
        event.get("description", ""),
        event.get("resolutionSource", ""),
        event.get("resolution_source", ""),
    ]
    for market in event.get("markets", []) or []:
        fields.extend(
            [
                market.get("question", ""),
                market.get("title", ""),
                market.get("description", ""),
                market.get("resolutionSource", ""),
                market.get("resolution_source", ""),
            ]
        )
    return " ".join(str(field) for field in fields if field)


def _is_temperature_event(event: dict[str, Any]) -> bool:
    text = _event_text(event).lower()
    return any(keyword in text for keyword in ms.TEMP_KEYWORDS)


def _metric_for_event(event: dict[str, Any]) -> str:
    fields = [
        event.get("title", ""),
        event.get("slug", ""),
        event.get("description", ""),
        event.get("groupItemTitle", ""),
        event.get("group_item_title", ""),
    ]
    for market in event.get("markets", []) or []:
        fields.extend(
            [
                market.get("question", ""),
                market.get("title", ""),
                market.get("description", ""),
                market.get("groupItemTitle", ""),
                market.get("group_item_title", ""),
            ]
        )
    return ms.infer_temperature_metric(*fields)


def evaluate_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return a source-contract verdict for one Gamma event."""
    if not _is_temperature_event(event):
        return None

    city = ms._match_city(str(event.get("title") or "").lower(), str(event.get("slug") or ""))
    if city is None:
        return {
            "severity": "WARN",
            "event_id": _event_id(event),
            "slug": event.get("slug", ""),
            "title": event.get("title", ""),
            "city": None,
            "target_date": None,
            "temperature_metric": None,
            "source_contract": {
                "status": "MISSING",
                "reason": "temperature event did not match a configured city",
                "resolution_sources": list(ms._collect_resolution_sources(event)),
                "source_family": None,
                "station_id": None,
                "configured_source_family": None,
                "configured_station_id": None,
            },
        }

    sanity_rejection = ms._market_city_sanity_rejection(event, city)
    if sanity_rejection is not None:
        return {
            "severity": "ALERT",
            "event_id": _event_id(event),
            "slug": event.get("slug", ""),
            "title": event.get("title", ""),
            "city": city.name,
            "target_date": ms._parse_target_date(event, city),
            "temperature_metric": _metric_for_event(event),
            "source_contract": {
                "status": "MISMATCH",
                "reason": sanity_rejection,
                "resolution_sources": list(ms._collect_resolution_sources(event)),
                "source_family": None,
                "station_id": None,
                "configured_source_family": city.settlement_source_type,
                "configured_station_id": city.wu_station,
            },
        }

    contract = ms._check_source_contract(event, city).as_dict()
    return {
        "severity": SOURCE_STATUS_SEVERITY.get(contract["status"], "ALERT"),
        "event_id": _event_id(event),
        "slug": event.get("slug", ""),
        "title": event.get("title", ""),
        "city": city.name,
        "target_date": ms._parse_target_date(event, city),
        "temperature_metric": _metric_for_event(event),
        "source_contract": contract,
    }


def analyze_events(
    events: list[dict[str, Any]],
    *,
    city: str | None = None,
    include_unconfigured: bool = False,
    checked_at_utc: datetime | None = None,
    authority: str = "FIXTURE",
) -> dict[str, Any]:
    """Analyze a Gamma event list and return a deterministic watch report."""
    checked_at = checked_at_utc or datetime.now(timezone.utc)
    requested_city = city.lower() if city else None
    results: list[dict[str, Any]] = []
    skipped_non_temperature = 0
    skipped_unconfigured = 0

    for event in events:
        verdict = evaluate_event(event)
        if verdict is None:
            skipped_non_temperature += 1
            continue
        if verdict["city"] is None and not include_unconfigured:
            skipped_unconfigured += 1
            continue
        if requested_city and str(verdict.get("city", "")).lower() != requested_city:
            continue
        results.append(verdict)

    summary = {key: 0 for key in SEVERITY_RANK}
    for verdict in results:
        summary[verdict["severity"]] += 1
    if not results:
        status = "WARN"
        summary["WARN"] += 1
    else:
        status = max(results, key=lambda row: SEVERITY_RANK[row["severity"]])["severity"]

    return {
        "status": status,
        "checked_at_utc": checked_at.isoformat(),
        "authority": authority,
        "event_count": len(events),
        "checked_event_count": len(results),
        "skipped_non_temperature": skipped_non_temperature,
        "skipped_unconfigured": skipped_unconfigured,
        "city_filter": city,
        "summary": summary,
        "events": results,
        "next_actions": _next_actions(status, results),
    }


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    contract = event.get("source_contract") or {}
    return {
        "city": event.get("city"),
        "target_date": event.get("target_date"),
        "temperature_metric": event.get("temperature_metric"),
        "severity": event.get("severity"),
        "event_id": event.get("event_id"),
        "slug": event.get("slug"),
        "title": event.get("title"),
        "source_contract": {
            "status": contract.get("status"),
            "reason": contract.get("reason"),
            "configured_source_family": contract.get("configured_source_family"),
            "configured_station_id": contract.get("configured_station_id"),
            "observed_source_family": contract.get("source_family"),
            "observed_station_id": contract.get("station_id"),
            "resolution_sources": list(contract.get("resolution_sources") or []),
        },
    }


def build_compact_alert_report(
    report: dict[str, Any],
    *,
    report_only: bool,
) -> dict[str, Any]:
    """Return a small, model-safe source audit report.

    Cron delivery hands this to small models so they never have to infer the
    affected cities from a long full event list or a truncated raw JSON tail.
    """
    alert_events = [
        _compact_event(event)
        for event in report.get("events", [])
        if event.get("severity") == "ALERT"
    ]
    warn_events = [
        _compact_event(event)
        for event in report.get("events", [])
        if event.get("severity") == "WARN"
    ]
    affected_cities = sorted(
        {
            str(event.get("city"))
            for event in alert_events
            if event.get("city")
        }
    )
    quarantine_actions = list(report.get("quarantine_actions") or [])
    return {
        "schema_version": COMPACT_SCHEMA_VERSION,
        "status": report.get("status"),
        "authority": report.get("authority"),
        "checked_at_utc": report.get("checked_at_utc"),
        "event_count": report.get("event_count"),
        "checked_event_count": report.get("checked_event_count"),
        "skipped_non_temperature": report.get("skipped_non_temperature"),
        "skipped_unconfigured": report.get("skipped_unconfigured"),
        "summary": report.get("summary"),
        "alert_event_count": len(alert_events),
        "warn_event_count": len(warn_events),
        "affected_cities": affected_cities,
        "alert_events": alert_events,
        "warn_events": warn_events,
        "quarantine": {
            "report_only": bool(report_only),
            "written": bool(quarantine_actions),
            "actions": quarantine_actions,
            "mode": "read_only_no_write" if report_only else "write_on_alert",
        },
        "audit_persistence": report.get("audit_persistence"),
        "model_reporting_contract": [
            "Only alert_events are ALERT-affected market subjects.",
            "Do not infer affected cities from summary counts, event ordering, or truncated tail text.",
            "Rows absent from alert_events must not be reported as source-change ALERTs.",
        ],
        "next_actions": report.get("next_actions") or [],
    }


def _next_actions(status: str, events: list[dict[str, Any]]) -> list[str]:
    if status == "OK":
        return ["No source-contract drift detected for configured active markets."]
    actions = [
        "Do not open new entries for WARN/ALERT city-date-metric subjects.",
        "Re-audit Gamma resolutionSource against the live Polymarket market page.",
    ]
    if any(event["severity"] == "ALERT" for event in events):
        actions.extend(
            [
                "Keep the affected city in source-contract quarantine so new entries stay blocked while old positions can still monitor and exit.",
                "If the source change is real, update config/cities.json and current_source_validity.md from packet evidence.",
                "Backfill only the affected city-date-metric/source-role rows, then rebuild settlements, calibration pairs, and Platt calibration for that bucket before release.",
            ]
        )
    return actions


def _quarantine_evidence_for_city(report: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "checked_at_utc": report.get("checked_at_utc"),
        "authority": report.get("authority"),
        "status": report.get("status"),
        "alert_event_count": len(events),
        "events": [
            {
                "event_id": event.get("event_id"),
                "slug": event.get("slug"),
                "title": event.get("title"),
                "target_date": event.get("target_date"),
                "temperature_metric": event.get("temperature_metric"),
                "source_contract": event.get("source_contract"),
            }
            for event in events
        ],
    }


def apply_source_quarantines(
    report: dict[str, Any],
    *,
    quarantine_path: Path | None = None,
    observed_at: str | None = None,
) -> list[dict[str, Any]]:
    """Persist city quarantines for ALERT source-contract mismatches."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in report.get("events", []):
        city = event.get("city")
        contract = event.get("source_contract", {})
        if not city:
            continue
        if event.get("severity") != "ALERT":
            continue
        if contract.get("status") not in QUARANTINE_STATUSES:
            continue
        grouped.setdefault(str(city), []).append(event)

    actions: list[dict[str, Any]] = []
    observed = observed_at or str(report.get("checked_at_utc") or "")
    for city, events in sorted(grouped.items()):
        result = ms.upsert_source_contract_quarantine(
            city,
            reason="source_contract_mismatch",
            evidence=_quarantine_evidence_for_city(report, events),
            observed_at=observed or None,
            source="watch_source_contract",
            path=quarantine_path,
        )
        actions.append(
            {
                "action": "quarantine_city_source",
                "status": result["status"],
                "city": result["city"],
                "path": result["path"],
                "event_ids": [event.get("event_id") for event in events],
            }
        )
    return actions


def load_release_evidence(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("release evidence must be a JSON object")
    return payload


def render_release_result(result: dict[str, Any]) -> str:
    if result.get("status") == "blocked":
        return (
            f"source-contract-quarantine release blocked city={result.get('city')} "
            f"missing_evidence={result.get('missing_evidence')}"
        )
    transition = result.get("transition_record") or {}
    transition_suffix = ""
    if transition:
        transition_suffix = (
            f" branch={transition.get('transition_branch')} "
            f"from={transition.get('from_source_contract')} "
            f"to={transition.get('to_source_contract')}"
        )
    return (
        f"source-contract-quarantine release {result.get('status')} "
        f"city={result.get('city')} path={result.get('path')}{transition_suffix}"
    )


def build_conversion_plan(
    city: str,
    *,
    quarantine_path: Path | None = None,
) -> dict[str, Any]:
    active = ms.active_source_contract_quarantines(path=quarantine_path)
    entry = active.get(city)
    if entry is None:
        for candidate, candidate_entry in active.items():
            if candidate.lower() == city.lower():
                entry = candidate_entry
                city = candidate
                break
    branch = ms.source_contract_transition_branch(entry)
    return {
        "city": city,
        "status": "active_quarantine" if entry else "not_quarantined",
        "transition_branch": branch,
        "quarantine_entry": entry,
        "release_contract": {
            "required_evidence": list(ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE),
            "required_evidence_refs": {
                key: ms.SOURCE_CONVERSION_EVIDENCE_DESCRIPTIONS[key]
                for key in ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE
            },
        },
        "ordered_steps": [
            {
                "id": "attest_market_source",
                "why": "Confirm Polymarket page/source text and classify same-provider station change vs provider-family change vs unsupported source.",
                "hidden_branch": branch,
            },
            {
                "id": "update_config_and_current_source_validity",
                "why": "Change config only from packet evidence and refresh the audit-bound current source fact.",
                "release_evidence": ["config_updated", "source_validity_updated"],
            },
            {
                "id": "backfill_affected_source_rows",
                "why": "Cover only affected city/date/metric/source-role rows; if no backfill is required, record why.",
                "release_evidence": ["backfill_completed"],
            },
            {
                "id": "rebuild_settlement_and_calibration_surfaces",
                "why": "Settlement rows, calibration pairs, and Platt buckets must not mix old and new source identity.",
                "release_evidence": ["settlements_rebuilt", "calibration_rebuilt"],
            },
            {
                "id": "verify_and_release",
                "why": "Run source watch in report-only mode plus focused tests, then release with evidence_refs for every required field.",
                "release_evidence": ["verification_passed"],
            },
        ],
    }


def render_conversion_plan(plan: dict[str, Any]) -> str:
    lines = [
        (
            "source-contract-conversion-plan "
            f"city={plan['city']} status={plan['status']} "
            f"branch={plan['transition_branch']}"
        )
    ]
    for step in plan["ordered_steps"]:
        lines.append(f"- {step['id']}: {step['why']}")
    lines.append(
        "release requires evidence_refs for: "
        + ", ".join(plan["release_contract"]["required_evidence"])
    )
    return "\n".join(lines)


def build_history_report(
    city: str | None = None,
    *,
    quarantine_path: Path | None = None,
) -> dict[str, Any]:
    history = ms.source_contract_transition_history(city, path=quarantine_path)
    return {
        "status": "ok",
        "city_filter": city,
        "record_count": len(history),
        "history": history,
    }


def render_history_report(report: dict[str, Any]) -> str:
    lines = [
        (
            "source-contract-transition-history "
            f"city={report.get('city_filter') or '<all>'} "
            f"records={report.get('record_count')}"
        )
    ]
    for record in report.get("history", []):
        from_contract = record.get("from_source_contract") or {}
        to_contract = record.get("to_source_contract") or {}
        lines.append(
            (
                f"- {record.get('city')} branch={record.get('transition_branch')} "
                f"detected_at={record.get('detected_at')} "
                f"first_target_date={record.get('first_affected_target_date')} "
                f"released_at={record.get('released_at')} "
                f"from={from_contract.get('source_families')}/{from_contract.get('station_ids')} "
                f"to={to_contract.get('source_families')}/{to_contract.get('station_ids')}"
            )
        )
        evidence = record.get("completed_release_evidence") or {}
        for key in ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE:
            detail = evidence.get(key) or {}
            lines.append(f"  evidence {key}: {detail.get('evidence_ref')}")
    return "\n".join(lines)


def load_fixture(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        events = payload.get("events", [])
    else:
        events = payload
    if not isinstance(events, list):
        raise ValueError("fixture must be a list of events or an object with events")
    return events


def fetch_active_events() -> tuple[list[dict[str, Any]], str]:
    snapshot = ms._get_active_events_snapshot()
    if snapshot.authority in {"EMPTY_FALLBACK", "NEVER_FETCHED"}:
        return [], snapshot.authority
    return list(snapshot.events), snapshot.authority


def persist_audit_report(
    report: dict[str, Any], *, audit_db_path: Path | None
) -> dict[str, Any] | None:
    if audit_db_path is None:
        return None
    conn = state_db.get_connection(audit_db_path, write_class="bulk")
    try:
        state_db.init_schema(conn)
        result = state_db.append_source_contract_audit_events(conn, report=report)
        conn.commit()
        return {**result, "db_path": str(audit_db_path)}
    finally:
        conn.close()


def exit_code_for_report(report: dict[str, Any], *, fail_on: str) -> int:
    status = str(report.get("status") or "DATA_UNAVAILABLE")
    if SEVERITY_RANK[status] < SEVERITY_RANK[fail_on]:
        return 0
    if status == "DATA_UNAVAILABLE":
        return 3
    if status == "ALERT":
        return 2
    return 1


def render_text(report: dict[str, Any]) -> str:
    lines = [
        (
            "source-contract-watch "
            f"status={report['status']} authority={report['authority']} "
            f"checked={report['checked_event_count']}/{report['event_count']}"
        )
    ]
    for event in report["events"]:
        contract = event["source_contract"]
        if event["severity"] == "OK":
            continue
        lines.append(
            (
                f"- {event['severity']} {event.get('city') or '<unconfigured>'} "
                f"{event.get('target_date') or '<unknown-date>'} "
                f"{event.get('temperature_metric') or '<unknown-metric>'}: "
                f"{contract['status']} {contract['reason']} "
                f"sources={contract.get('resolution_sources')}"
            )
        )
    for action in report["next_actions"]:
        lines.append(f"next: {action}")
    for action in report.get("quarantine_actions", []):
        lines.append(
            f"quarantine: {action['status']} city={action['city']} "
            f"events={action.get('event_ids')} path={action.get('path')}"
        )
    return "\n".join(lines)


def render_compact_alert_text(report: dict[str, Any]) -> str:
    lines = [
        (
            "source-contract-alert-summary "
            f"status={report.get('status')} authority={report.get('authority')} "
            f"alerts={report.get('alert_event_count')} "
            f"affected_cities={report.get('affected_cities')}"
        )
    ]
    quarantine = report.get("quarantine") or {}
    lines.append(
        "quarantine: "
        f"mode={quarantine.get('mode')} written={quarantine.get('written')}"
    )
    audit_persistence = report.get("audit_persistence") or {}
    if audit_persistence:
        lines.append(
            "audit: "
            f"status={audit_persistence.get('status')} "
            f"inserted={audit_persistence.get('audit_rows_inserted')} "
            f"unchanged={audit_persistence.get('audit_rows_unchanged')} "
            f"db_path={audit_persistence.get('db_path')}"
        )
    for event in report.get("alert_events", []):
        contract = event.get("source_contract") or {}
        lines.append(
            (
                f"- ALERT {event.get('city')} "
                f"{event.get('target_date') or '<unknown-date>'} "
                f"{event.get('temperature_metric') or '<unknown-metric>'}: "
                f"{contract.get('status')} {contract.get('reason')} "
                f"configured={contract.get('configured_source_family')}/"
                f"{contract.get('configured_station_id')} observed="
                f"{contract.get('observed_source_family')}/"
                f"{contract.get('observed_station_id')} "
                f"sources={contract.get('resolution_sources')}"
            )
        )
    for rule in report.get("model_reporting_contract", []):
        lines.append(f"contract: {rule}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", help="Limit checks to one configured city name")
    parser.add_argument(
        "--include-unconfigured",
        action="store_true",
        help="Report temperature markets that do not match configured cities",
    )
    parser.add_argument("--fixture", type=Path, help="Read Gamma events from JSON fixture")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument(
        "--compact-alerts",
        action="store_true",
        help="Emit a small model-safe report containing only WARN/ALERT rows and audit counts",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Do not write source-contract quarantine state on ALERT",
    )
    parser.add_argument(
        "--quarantine-path",
        type=Path,
        help="Override source-contract quarantine state path",
    )
    parser.add_argument(
        "--audit-db-path",
        type=Path,
        help="Explicit SQLite DB path for append-only source-contract audit facts",
    )
    parser.add_argument(
        "--release-city",
        help="Release a city source-contract quarantine after conversion evidence is complete",
    )
    parser.add_argument(
        "--conversion-plan",
        help="Print the required conversion protocol for a quarantined city",
    )
    parser.add_argument(
        "--history",
        nargs="?",
        const="",
        metavar="CITY",
        help="Print recorded source-contract conversion history, optionally for CITY",
    )
    parser.add_argument(
        "--release-evidence",
        type=Path,
        help=(
            "JSON object with required true fields and evidence_refs for: "
            + ",".join(ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE)
        ),
    )
    parser.add_argument(
        "--fail-on",
        choices=("WARN", "ALERT", "DATA_UNAVAILABLE"),
        default="WARN",
        help="Smallest status that should produce a non-zero exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.history is not None:
        city_filter = args.history or None
        report = build_history_report(
            city_filter,
            quarantine_path=args.quarantine_path,
        )
        print(
            json.dumps(report, indent=2, sort_keys=True)
            if args.json
            else render_history_report(report)
        )
        return 0

    if args.conversion_plan:
        plan = build_conversion_plan(
            args.conversion_plan,
            quarantine_path=args.quarantine_path,
        )
        print(
            json.dumps(plan, indent=2, sort_keys=True)
            if args.json
            else render_conversion_plan(plan)
        )
        return 0

    if args.release_city:
        if args.release_evidence is None:
            parser.error("--release-city requires --release-evidence")
        result = ms.release_source_contract_quarantine(
            args.release_city,
            released_by="watch_source_contract",
            evidence=load_release_evidence(args.release_evidence),
            path=args.quarantine_path,
        )
        print(
            json.dumps(result, indent=2, sort_keys=True)
            if args.json
            else render_release_result(result)
        )
        return 0 if result.get("status") in {"released", "noop"} else 4

    if args.fixture:
        events = load_fixture(args.fixture)
        authority = "FIXTURE"
    else:
        events, authority = fetch_active_events()
        if authority in {"EMPTY_FALLBACK", "NEVER_FETCHED"}:
            report = {
                "status": "DATA_UNAVAILABLE",
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "authority": authority,
                "event_count": 0,
                "checked_event_count": 0,
                "skipped_non_temperature": 0,
                "skipped_unconfigured": 0,
                "city_filter": args.city,
                "summary": {"OK": 0, "WARN": 0, "ALERT": 0, "DATA_UNAVAILABLE": 1},
                "events": [],
                "next_actions": ["Do not rely on source monitor output until Gamma fetch recovers."],
                "quarantine_actions": [],
            }
            audit_result = persist_audit_report(report, audit_db_path=args.audit_db_path)
            if audit_result is not None:
                report["audit_persistence"] = audit_result
            output_report = (
                build_compact_alert_report(report, report_only=args.report_only)
                if args.compact_alerts
                else report
            )
            print(
                json.dumps(output_report, indent=2, sort_keys=True)
                if args.json
                else (
                    render_compact_alert_text(output_report)
                    if args.compact_alerts
                    else render_text(output_report)
                )
            )
            return exit_code_for_report(report, fail_on=args.fail_on)

    report = analyze_events(
        events,
        city=args.city,
        include_unconfigured=args.include_unconfigured,
        authority=authority,
    )
    report["quarantine_actions"] = (
        []
        if args.report_only
        else apply_source_quarantines(report, quarantine_path=args.quarantine_path)
    )
    audit_result = persist_audit_report(report, audit_db_path=args.audit_db_path)
    if audit_result is not None:
        report["audit_persistence"] = audit_result
    output_report = (
        build_compact_alert_report(report, report_only=args.report_only)
        if args.compact_alerts
        else report
    )
    print(
        json.dumps(output_report, indent=2, sort_keys=True)
        if args.json
        else (
            render_compact_alert_text(output_report)
            if args.compact_alerts
            else render_text(output_report)
        )
    )
    return exit_code_for_report(report, fail_on=args.fail_on)


if __name__ == "__main__":
    raise SystemExit(main())
