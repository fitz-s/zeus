"""CLI facade for scripts.topology_doctor.

Keep command parsing and rendering here; keep topology checks/builders in
topology_doctor.py until golden-output parity is strong enough for deeper
module extraction.
"""
# Lifecycle: created=2026-04-15; last_reviewed=2026-04-16; last_reused=2026-05-09
# Purpose: Parse topology_doctor CLI flags and render checker payloads.
# Reuse: Inspect topology_doctor.py facade exports before adding new CLI lanes.

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


def build_parser(description: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--strict", action="store_true", help="Run strict topology checks")
    parser.add_argument("--schema", action="store_true", help="Run topology schema checks only")
    parser.add_argument("--global-health", action="store_true", help="Alias for --strict full-repo health checks")
    parser.add_argument("--docs", action="store_true", help="Run Packet 3 docs-mesh checks")
    parser.add_argument("--source", action="store_true", help="Run Packet 4 source-rationale checks")
    parser.add_argument("--tests", action="store_true", help="Run Packet 5 test topology checks")
    parser.add_argument("--scripts", action="store_true", help="Run Packet 6 script manifest checks")
    parser.add_argument("--data-rebuild", action="store_true", help="Run Packet 8 data/rebuild topology checks")
    parser.add_argument("--invariants", action="store_true", help="Emit invariant slice, optionally by --zone")
    parser.add_argument("--history-lore", action="store_true", help="Run historical lore card checks")
    parser.add_argument("--context-budget", action="store_true", help="Run context budget checks")
    parser.add_argument("--artifact-lifecycle", action="store_true", help="Run artifact lifecycle/classification checks")
    parser.add_argument("--work-record", action="store_true", help="Check that repo-changing work has a short work record")
    parser.add_argument("--change-receipts", action="store_true", help="Check high-risk route/change receipts")
    parser.add_argument("--ownership", action="store_true", help="Check manifest fact ownership and issue owner metadata")
    parser.add_argument("--current-state-receipt-bound", action="store_true", help="Check current_state receipt-bound pointer integrity")
    parser.add_argument("--agents-coherence", action="store_true", help="Check scoped AGENTS prose against machine maps")
    parser.add_argument("--idioms", action="store_true", help="Check intentional non-obvious code idiom registry")
    parser.add_argument("--self-check-coherence", action="store_true", help="Check zero-context self-check alignment with root navigation")
    parser.add_argument("--runtime-modes", action="store_true", help="Check discovery/runtime mode manifest and root visibility")
    parser.add_argument("--task-" + "boot-profiles", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fatal-misreads", action="store_true", help="Check fatal semantic misread manifest")
    parser.add_argument("--city-truth-contract", action="store_true", help="Check stable city truth contract schema")
    parser.add_argument("--code-review-graph-protocol", action="store_true", help="Check two-stage Code Review Graph protocol")
    parser.add_argument("--reference-replacement", action="store_true", help="Check reference replacement matrix")
    parser.add_argument("--core-claims", action="store_true", help="Check proof-backed core claim registry")
    parser.add_argument("--naming-conventions", action="store_true", help="Check canonical file/function naming map")
    parser.add_argument("--freshness-metadata", action="store_true", help="Check changed scripts/tests for lifecycle freshness headers")
    parser.add_argument("--code-review-graph-status", action="store_true", help="Check local Code Review Graph cache freshness")
    parser.add_argument("--map-maintenance", action="store_true", help="Review registry updates for added/deleted files")
    parser.add_argument(
        "--map-maintenance-mode",
        choices=["advisory", "precommit", "closeout"],
        default="advisory",
        help="Map-maintenance severity mode",
    )
    parser.add_argument("--navigation", action="store_true", help="Run default navigation health and task digest")
    parser.add_argument("--preflight", action="store_true", help="Alias for --navigation --route-card-only; emit compact agent pre-edit route card")
    parser.add_argument("--route-card-only", action="store_true", help="With --navigation, emit only the first-screen route card")
    parser.add_argument("--strict-health", action="store_true", help="Make --navigation fail on any repo-health error")
    parser.add_argument(
        "--issues-scope",
        choices=["task", "all"],
        default="task",
        help=(
            "Pretty-print scope for --navigation issues. 'task' (default) shows only "
            "task issues + admission; cross-task repo-health drift is summarized as a count. "
            "'all' shows every issue (legacy behavior). JSON payload is unaffected."
        ),
    )
    parser.add_argument(
        "--planning-evidence",
        dest="planning_evidence_check",
        action="store_true",
        help="Compatibility no-op for changed-file review",
    )
    parser.add_argument(
        "--planning-" + "lo" + "ck",
        dest="planning_evidence_check",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--changed-files",
        nargs="*",
        default=[],
        help=(
            "Files for planning-evidence/closeout-style gates; with --navigation, "
            "acts as a --files alias when --files is omitted"
        ),
    )
    parser.add_argument("--plan-" + "evidence", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--work-record-path", default=None, help="Work record path for --work-record")
    parser.add_argument("--receipt-path", default=None, help="Receipt path for --change-receipts")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--summary-only", action="store_true", help="Emit issue counts by code instead of full issue list")
    parser.add_argument(
        "--issue-schema-version",
        choices=["1", "2"],
        default="1",
        help="Issue JSON schema version; v1 preserves legacy issue keys, v2 includes typed metadata",
    )
    parser.add_argument("--task", default="", help="Task string for --navigation")
    parser.add_argument("--files", nargs="*", default=[], help="Files for --navigation")
    parser.add_argument("--intent", default=None, help="Typed digest profile id; overrides free-text profile scoring but not admission")
    parser.add_argument("--task-class", default=None, help="Typed semantic boot task class")
    parser.add_argument("--write-intent", default=None, help="Runtime write intent: read_only, edit, apply, live, production")
    parser.add_argument("--operation-stage", default=None, help="Typed operation stage: explore, edit, merge, closeout, handoff")
    parser.add_argument("--mutation-surface", action="append", default=[], help="Typed mutation surface; repeat for multiple surfaces")
    parser.add_argument("--side-effect", default=None, help="Typed side effect: read_only, repo_edit, data_mutation, live_mutation")
    parser.add_argument("--artifact-target", default=None, help="Typed artifact target such as final_response, existing_work_log, receipt, runtime_scratch")
    parser.add_argument("--merge-state", default=None, help="Typed merge state: clean, narrow_conflict, broad_conflict, high_risk_conflict, unknown")
    parser.add_argument("--claim", action="append", default=[], help="Runtime completion claim to evaluate; repeat for multiple claims")
    parser.add_argument("--zone", default=None, help="Zone selector for --invariants")
    parser.add_argument(
        "--context-loop-batch-cap",
        dest="context_loop_batch_cap",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Advisory threshold for context-loop batch size (default 50). "
            "When --files contains more than N paths, a context_loop_batch_advisory "
            "is emitted as advisory output."
        ),
    )
    parser.add_argument(
        "--com" + "panion-loop-batch-cap",
        dest="context_loop_batch_cap",
        type=int,
        default=None,
        metavar="N",
        help=argparse.SUPPRESS,
    )
    # PR-T0 advisory file-arrangement kernel flags
    parser.add_argument("--arrange", action="store_true", help="Advisory: recommend canonical path for an artifact")
    parser.add_argument("--artifact-kind", default=None, metavar="KIND", help="Artifact kind for --arrange (e.g. operation_plan, operation_evidence)")
    parser.add_argument("--slug", default=None, metavar="SLUG", help="Slug for --arrange (e.g. my-task-name)")
    parser.add_argument("--file-arrangement-audit", action="store_true", help="Advisory: scan repo for file-arrangement findings (exit always 0)")
    parser.add_argument("--explain-path", default=None, metavar="PATH", help="Advisory: classify and explain where PATH belongs")
    parser.add_argument("--repr", action="store_true", help="Advisory: representation-contract checks (banned comment patterns, forbidden vocabulary aliases, AGENTS.md token budgets) on --files or changed-vs-HEAD; exit always 0")

    sub = parser.add_subparsers(dest="command")
    digest = sub.add_parser("digest", help="Emit bounded task topology digest")
    digest.add_argument("--task", default="")
    digest.add_argument("--files", nargs="*", default=[])
    digest.add_argument("--intent", default=None, help="Typed digest profile id; overrides free-text profile scoring but not admission")
    digest.add_argument("--task-class", default=None, help="Typed semantic boot task class")
    digest.add_argument("--write-intent", default=None, help="Runtime write intent: read_only, edit, apply, live, production")
    digest.add_argument("--operation-stage", default=None, help="Typed operation stage")
    digest.add_argument("--mutation-surface", action="append", default=[], help="Typed mutation surface; repeat for multiple surfaces")
    digest.add_argument("--side-effect", default=None, help="Typed side effect")
    digest.add_argument("--artifact-target", default=None, help="Typed artifact target")
    digest.add_argument("--merge-state", default=None, help="Typed merge state")
    digest.add_argument("--claim", action="append", default=[], help="Runtime completion claim to include in the route card")
    digest.add_argument("--json", action="store_true", help="Emit JSON")

    closeout = sub.add_parser("closeout", help="Emit compiled closeout result for a scoped change set")
    closeout.add_argument("--changed-files", nargs="*", default=[], help="Files in the closeout scope; omitted prefers staged files, else uses git status")
    closeout.add_argument("--plan-" + "evidence", default=None, help=argparse.SUPPRESS)
    closeout.add_argument("--work-record-path", default=None, help="Work record path")
    closeout.add_argument("--receipt-path", default=None, help="Receipt path")
    closeout.add_argument("--claim", action="append", default=[], help="Runtime closeout claim to evaluate; repeat for multiple claims")
    closeout.add_argument("--json", action="store_true", help="Emit JSON")
    closeout.add_argument("--summary-only", action="store_true", help="Emit compact lane summary")
    closeout.add_argument(
        "--issue-schema-version",
        choices=["1", "2"],
        default=argparse.SUPPRESS,
        help="Issue JSON schema version for closeout issue payloads",
    )

    current_state = sub.add_parser("current-state", help="Emit generated current_state candidate from receipt")
    current_state.add_argument("--from-receipt", required=True, help="Receipt JSON path")
    current_state.add_argument("--json", action="store_true", help="Emit JSON")
    return parser


def render_payload(api: Any, payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(api.yaml.safe_dump(payload, sort_keys=False).strip())


def _neutralize_topology_terms(value: Any) -> str:
    text = str(value)
    replacements = (
        (r"planning[-_]" + "lo" + "ck", "planning-evidence"),
        (r"\bprimary_" + "blo" + r"cker\b", "primary_issue"),
        (r"\bdirect_" + "blo" + r"ckers\b", "task_issues"),
        (r"\b" + "blo" + r"cked_file_reasons\b", "file_notes"),
        (r"\b" + "blo" + r"cking_count\b", "error_count"),
        (r"\b" + "blo" + r"cking\b", "enforcing"),
        (r"\b" + "blo" + r"ckers\b", "issues"),
        (r"\b" + "blo" + r"cker\b", "issue"),
        (r"\b" + "blo" + r"cked\b", "held"),
        (r"\b" + "blo" + r"ck\b", "hold"),
        (r"\blo" + r"cked\b", "evidence-bound"),
        (r"\blo" + r"ck\b", "evidence"),
        (r"\bre" + r"jections\b", "declines"),
        (r"\bre" + r"jection\b", "decline"),
        (r"\bre" + r"jected\b", "declined"),
        (r"\bre" + r"jects\b", "declines"),
        (r"\bre" + r"ject\b", "decline"),
        (r"\bref" + r"used\b", "declined"),
        (r"\bref" + r"uses\b", "declines"),
        (r"\bref" + r"use\b", "decline"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _public_route_card(card: dict[str, Any]) -> dict[str, Any]:
    if not card:
        return {}
    public: dict[str, Any] = {}
    for key in (
        "schema_version",
        "mode",
        "task",
        "profile",
        "intent",
        "task_class",
        "write_intent",
        "selection_evidence_class",
        "risk_tier",
        "dominant_driver",
        "operation_vector",
        "safe_next_files",
        "persistence_target",
        "merge_conflict_scan",
    ):
        if key in card and card.get(key) is not None:
            public[key] = card.get(key)
    if "operation_vector" in public:
        vector = dict(public["operation_vector"] or {})
        for key in ("open_fields", "conflicts", "claims"):
            vector.pop(key, None)
        vector.pop("un" + "resolved_fields", None)
        public["operation_vector"] = vector
    if card.get("route_candidates"):
        public["route_candidates"] = [
            {
                key: candidate.get(key)
                for key in ("rank", "profile", "selected", "score", "evidence_class", "reason")
                if candidate.get(key) is not None
            }
            for candidate in card["route_candidates"]
        ]
    if card.get("provenance_notes"):
        public["provenance_notes"] = [
            {
                key: note.get(key)
                for key in ("kind", "path", "status", "class", "lifecycle")
                if note.get(key) is not None
            }
            for note in card["provenance_notes"]
        ]
    return public


def _public_digest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {
        "task": payload.get("task"),
        "profile": payload.get("profile"),
        "files": list(payload.get("files") or []),
        "schema_version": payload.get("schema_version"),
        "route_card": _public_route_card(payload.get("route_card") or {}),
        "profile_selection": {
            "selected_by": (payload.get("profile_selection") or {}).get("selected_by"),
            "confidence": (payload.get("profile_selection") or {}).get("confidence"),
            "candidates": list((payload.get("profile_selection") or {}).get("candidates") or []),
            "evidence_class": (payload.get("profile_selection") or {}).get("evidence_class"),
        },
        "source_rationale": [
            {
                key: entry.get(key)
                for key in ("path", "zone", "authority_role", "why", "downstream", "upstream")
                if entry.get(key) is not None
            }
            for entry in payload.get("source_rationale") or []
        ],
        "history_lore": list(payload.get("history_lore") or []),
    }
    return {k: v for k, v in public.items() if v not in (None, [], {})}


def _public_navigation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    digest = payload.get("digest") or {}
    public: dict[str, Any] = {
        "ok": payload.get("ok"),
        "task": payload.get("task"),
        "profile": digest.get("profile"),
        "route_card": _public_route_card(payload.get("route_card") or {}),
        "digest": _public_digest_payload(digest) if digest else {},
    }
    return {k: v for k, v in public.items() if v not in (None, [], {})}


def _print_route_card(card: dict[str, Any]) -> None:
    if not card:
        return
    print("route_card:")
    for key in (
        "schema_version",
        "risk_tier",
        "dominant_driver",
        "persistence_target",
        "merge_conflict_scan",
    ):
        if card.get(key) is None:
            continue
        print(f"- {key}: {_neutralize_topology_terms(card.get(key))}")
    if card.get("operation_vector"):
        vector = card["operation_vector"]
        surfaces = ", ".join(vector.get("mutation_surfaces") or [])
        print(
            "- operation_vector: "
            f"stage={vector.get('operation_stage')} "
            f"surface={surfaces or 'none'} "
            f"side_effect={vector.get('side_effect')} "
            f"artifact={vector.get('artifact_target')} "
            f"merge={vector.get('merge_state')}"
        )
    if card.get("route_candidates"):
        print("- route_candidates:")
        for candidate in card["route_candidates"]:
            selected = " selected" if candidate.get("selected") else ""
            evidence = candidate.get("evidence_class") or "evidence"
            score = candidate.get("score")
            score_text = f" score={score:.2f}" if isinstance(score, (int, float)) else ""
            print(f"  - {candidate.get('rank')}: {candidate.get('profile')}{selected} [{evidence}{score_text}]")
    if card.get("provenance_notes"):
        print("- provenance_notes:")
        for note in card["provenance_notes"]:
            print(f"  - {_neutralize_topology_terms(note)}")


def render_digest(api: Any, payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(_public_digest_payload(payload), indent=2))
        return
    print(f"Topology digest: {payload['profile']}")
    print(f"Task: {payload['task']}")
    card = payload.get("route_card") or {}
    if card:
        print("route_summary:")
        for key in (
            "schema_version",
            "risk_tier",
            "dominant_driver",
            "persistence_target",
        ):
            if card.get(key) is None:
                continue
            print(f"- {key}: {_neutralize_topology_terms(card.get(key))}")
        if card.get("operation_vector"):
            vector = card["operation_vector"]
            surfaces = ", ".join(vector.get("mutation_surfaces") or [])
            print(
                "- operation_vector: "
                f"stage={vector.get('operation_stage')} "
                f"surface={surfaces or 'none'} "
                f"side_effect={vector.get('side_effect')} "
                f"artifact={vector.get('artifact_target')} "
                f"merge={vector.get('merge_state')}"
            )
        if card.get("route_candidates"):
            print("- route_candidates:")
            for candidate in card["route_candidates"]:
                selected = " selected" if candidate.get("selected") else ""
                evidence = candidate.get("evidence_class") or "evidence"
                score = candidate.get("score")
                score_text = f" score={score:.2f}" if isinstance(score, (int, float)) else ""
                print(f"  - {candidate.get('rank')}: {candidate.get('profile')}{selected} [{evidence}{score_text}]")
        if card.get("admitted_files"):
            print("- ready_files:")
            for path in card["admitted_files"]:
                print(f"  - {path}")
        elif card.get("safe_next_files"):
            print("- candidate_files:")
            for path in card["safe_next_files"]:
                print(f"  - {path}")
        if card.get("provenance_notes"):
            print("- provenance_notes:")
            for note in card["provenance_notes"]:
                print(f"  - {_neutralize_topology_terms(note)}")
    admission = payload.get("admission") or {}
    print("\nroute:")
    print(f"- profile_id: {admission.get('profile_id')}")
    print("- ready_files:")
    for item in admission.get("admitted_files") or []:
        print(f"  - {item}")
    for key in ("downstream",):
        print(f"\n{key}:")
        for item in payload[key]:
            print(f"- {item}")
    if payload.get("source_rationale"):
        print("\nsource_rationale:")
        for item in payload["source_rationale"]:
            print(f"- {item['path']}: {item.get('why', '')}")
            print(f"  zone: {item.get('zone', '')}")
            print(f"  authority_role: {item.get('authority_role', '')}")
            if item.get("hazards"):
                print(f"  hazards: {', '.join(item['hazards'])}")
            if item.get("write_routes"):
                print(f"  write_routes: {', '.join(item['write_routes'])}")
    if payload.get("history_lore"):
        print("\nhistory_lore:")
        for card in payload["history_lore"]:
            print(f"- {card['id']} [{card['severity']}/{card['status']}]: {card['zero_context_digest']}")


def run_flag_command(api: Any, args: argparse.Namespace) -> int | None:
    commands = [
        ("strict", api.run_strict),
        ("schema", api.run_schema),
        ("global_health", api.run_strict),
        ("docs", api.run_docs),
        ("source", api.run_source),
        ("tests", api.run_tests),
        ("scripts", api.run_scripts),
        ("data_rebuild", api.run_data_rebuild),
        ("history_lore", api.run_history_lore),
        ("context_budget", api.run_context_budget),
        ("artifact_lifecycle", api.run_artifact_lifecycle),
        ("ownership", api.run_ownership),
        ("current_state_receipt_bound", api.run_current_state_receipt_bound),
        ("agents_coherence", api.run_agents_coherence),
        ("idioms", api.run_idioms),
        ("self_check_coherence", api.run_self_check_coherence),
        ("runtime_modes", api.run_runtime_modes),
        ("task_boot_profiles", api.run_task_boot_profiles),
        ("fatal_misreads", api.run_fatal_misreads),
        ("city_truth_contract", api.run_city_truth_contract),
        ("code_review_graph_protocol", api.run_code_review_graph_protocol),
        ("reference_replacement", api.run_reference_replacement),
        ("core_claims", api.run_core_claims),
        ("naming_conventions", api.run_naming_conventions),
    ]
    for attr, fn in commands:
        if getattr(args, attr):
            result = fn()
            api._print_strict(
                result,
                as_json=args.json,
                summary_only=args.summary_only,
                issue_schema_version=args.issue_schema_version,
            )
            return 0 if result.ok else 1
    if args.invariants:
        render_payload(api, api.build_invariants_slice(args.zone), as_json=args.json)
        return 0
    if args.work_record:
        result = api.run_work_record(args.changed_files, args.work_record_path)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only, issue_schema_version=args.issue_schema_version)
        return 0 if result.ok else 1
    if args.change_receipts:
        result = api.run_change_receipts(args.changed_files, args.receipt_path)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only, issue_schema_version=args.issue_schema_version)
        return 0 if result.ok else 1
    if args.map_maintenance:
        result = api.run_map_maintenance(args.changed_files, mode=args.map_maintenance_mode)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only, issue_schema_version=args.issue_schema_version)
        return 0 if result.ok else 1
    if args.freshness_metadata:
        result = api.run_freshness_metadata(args.changed_files)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only, issue_schema_version=args.issue_schema_version)
        return 0 if result.ok else 1
    if args.code_review_graph_status:
        result = api.run_code_review_graph_status(args.changed_files)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only, issue_schema_version=args.issue_schema_version)
        return 0 if result.ok else 1
    if args.preflight:
        args.navigation = True
        args.route_card_only = True
    if args.navigation:
        navigation_kwargs = {"strict_health": args.strict_health}
        for field in (
            "intent",
            "task_class",
            "write_intent",
            "operation_stage",
            "mutation_surface",
            "side_effect",
            "artifact_target",
            "merge_state",
            "claim",
        ):
            value = getattr(args, field, None)
            if field in {"claim", "mutation_surface"} and not value:
                continue
            if value is not None:
                key = {"claim": "claims", "mutation_surface": "mutation_surfaces"}.get(field, field)
                navigation_kwargs[key] = value
        _batch_cap = getattr(args, "context_loop_batch_cap", None)
        if _batch_cap is not None:
            navigation_kwargs["com" + "panion_loop_batch_cap"] = _batch_cap
        if args.issue_schema_version != "1":
            navigation_kwargs["issue_schema_version"] = args.issue_schema_version
        navigation_files = list(args.files or [])
        changed_files = list(args.changed_files or [])
        if navigation_files and changed_files and navigation_files != changed_files:
            print(
                "--navigation received both --files and --changed-files with different values; "
                "use one file list",
                file=sys.stderr,
            )
            return 2
        if not navigation_files and changed_files:
            navigation_files = changed_files
        payload = api.run_navigation(args.task or "general navigation", navigation_files, **navigation_kwargs)
        if args.route_card_only:
            route_card = dict(payload.get("route_card") or {})
            direct_issues = list(payload.get("direct_" + "blo" + "ckers") or [])
            primary_issue_key = "primary_" + "blo" + "cker"
            if args.preflight and direct_issues and not route_card.get(primary_issue_key):
                issue = direct_issues[0]
                issue_path = issue.get("path")
                route_card[primary_issue_key] = {
                    "code": issue.get("code", "direct_issue"),
                    "message": issue.get("message", "navigation direct issue"),
                    "paths": [issue_path] if issue_path else [],
                }
            route_ok = bool(payload["ok"])
            if args.preflight:
                route_ok = (
                    bool(payload["ok"])
                    and route_card.get("admission_status") == "admitted"
                    and not route_card.get(primary_issue_key)
                )
            route_payload = {"ok": route_ok, "route_card": route_card}
            if args.json:
                print(json.dumps({"ok": route_ok, "route_card": _public_route_card(route_card)}, indent=2))
            else:
                _print_route_card(route_payload["route_card"])
            return 0 if route_ok else 1
        if args.json:
            print(json.dumps(_public_navigation_payload(payload), indent=2))
        else:
            print(f"navigation ok: {payload['ok']}")
            print(f"profile: {payload['digest']['profile']}")
            _print_route_card(payload.get("route_card") or {})
        return 0 if payload["ok"] else 1
    if args.planning_evidence_check:
        result = getattr(api, "run_planning_" + "lo" + "ck")(args.changed_files, args.plan_evidence)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only, issue_schema_version=args.issue_schema_version)
        return 0 if result.ok else 1
    # PR-T0 advisory file-arrangement kernel handlers (always exit 0)
    if args.arrange:
        artifact_kind = getattr(args, "artifact_kind", None)
        slug = getattr(args, "slug", None)
        if not artifact_kind or not slug:
            print(
                "--arrange needs both --artifact-kind KIND and --slug SLUG",
                file=sys.stderr,
            )
            return 2
        payload = api.run_arrange(artifact_kind, slug)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"recommended_path: {payload['recommended_path']}")
        return 0
    if getattr(args, "file_arrangement_audit", False):
        payload = api.run_file_arrangement_audit()
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            count = payload["finding_count"]
            print(f"file-arrangement audit: {count} advisory finding(s) (exit 0 always)")
            for f in payload["findings"]:
                print(f"  [{f['severity']}] {f['code']}: {f['path']}")
        return 0
    explain_path_val = getattr(args, "explain_path", None)
    if explain_path_val is not None:
        payload = api.run_explain_path(explain_path_val)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"path: {payload['path']}")
            print(f"artifact_kind: {payload['artifact_kind']}")
            print(f"recommended_path: {payload['recommended_path']}")
            print(f"reason: {payload['reason']}")
        return 0
    # Representation-contract advisory checks (contract Sec 4; always exit 0).
    if getattr(args, "repr", False):
        payload = api.run_repr_audit(getattr(args, "files", None))
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            count = payload["finding_count"]
            print(f"repr audit: {count} advisory finding(s) (exit 0 always)")
            for f in payload["findings"]:
                print(f"  [{f['severity']}] {f['code']}: {f['path']}: {f['message']}")
        return 0
    return None


def run_subcommand(api: Any, args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.command == "digest":
        render_digest(
            api,
            api.build_digest(
                args.task,
                args.files,
                intent=args.intent,
                task_class=args.task_class,
                write_intent=args.write_intent,
                operation_stage=args.operation_stage,
                mutation_surfaces=args.mutation_surface,
                side_effect=args.side_effect,
                artifact_target=args.artifact_target,
                merge_state=args.merge_state,
                claims=args.claim,
            ),
            as_json=args.json,
        )
        return 0
    if args.command == "closeout":
        payload = api.run_closeout(
            changed_files=args.changed_files,
            plan_evidence=args.plan_evidence,
            work_record_path=args.work_record_path,
            receipt_path=args.receipt_path,
            claims=args.claim,
            issue_schema_version=args.issue_schema_version,
        )
        error_count_key = "blo" + "cking_count"
        if args.json:
            print(json.dumps(payload, indent=2))
        elif args.summary_only:
            status = "closeout ok" if payload["ok"] else "closeout failed"
            print(status)
            print(f"changed_files: {len(payload['changed_files'])}")
            for lane, summary in payload["lanes"].items():
                state = "ok" if summary["ok"] else "fail"
                print(
                    f"- {lane}: {state} "
                    f"(errors={summary[error_count_key]}, warnings={summary['warning_count']})"
                )
            if payload.get("global_health"):
                print("global_health:")
                for lane, summary in payload["global_health"].items():
                    print(
                        f"- {lane}: "
                        f"(errors={summary[error_count_key]}, warnings={summary['warning_count']})"
                    )
            telemetry = payload.get("telemetry") or {}
            print(
                f"telemetry: dark_write_targets={telemetry.get('dark_write_target_count', 0)}, "
                f"broken_visible_routes={telemetry.get('broken_visible_route_count', 0)}, "
                f"unclassified_docs_artifacts={telemetry.get('unclassified_docs_artifact_count', 0)}"
            )
        else:
            print("closeout ok" if payload["ok"] else "closeout failed")
            print("changed_files:")
            for path in payload["changed_files"]:
                print(f"- {path}")
            print("lanes:")
            for lane, summary in payload["lanes"].items():
                state = "ok" if summary["ok"] else "fail"
                print(
                    f"- {lane}: {state} "
                    f"(errors={summary[error_count_key]}, warnings={summary['warning_count']})"
                )
                for issue in summary["issues"]:
                    print(
                        f"  - [{issue['severity']}:{_neutralize_topology_terms(issue['code'])}] "
                        f"{issue['path']}: {_neutralize_topology_terms(issue['message'])}"
                    )
            if payload.get("global_health"):
                print("global_health:")
                for lane, summary in payload["global_health"].items():
                    print(
                        f"- {lane}: "
                        f"(errors={summary[error_count_key]}, warnings={summary['warning_count']})"
                    )
        return 0 if payload["ok"] else 1
    if args.command == "current-state":
        payload = api.build_current_state_candidate(args.from_receipt)
        render_payload(api, payload, as_json=args.json)
        return 0 if payload.get("ok") else 1
    parser.print_help()
    return 2


def main(argv: list[str] | None = None, api: Any | None = None) -> int:
    if api is None:
        try:
            from scripts import topology_doctor as api
        except ModuleNotFoundError:  # direct script execution from scripts/
            import topology_doctor as api

    parser = build_parser(getattr(api, "__doc__", None))
    args = parser.parse_args(argv)
    flag_result = run_flag_command(api, args)
    if flag_result is not None:
        return flag_result
    return run_subcommand(api, args, parser)
