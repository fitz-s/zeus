"""Context-pack and impact builder family for topology_doctor."""
# Lifecycle: created=2026-04-15; last_reviewed=2026-04-20; last_reused=2026-04-20
# Purpose: Build task-shaped context packets and derived review/debug appendices.
# Reuse: Keep generated context provisional; do not promote graph or lore evidence to authority here.

from __future__ import annotations

import re
from datetime import date, datetime
from fnmatch import fnmatch
from typing import Any


CURRENT_FACT_MAX_STALENESS_DAYS = 14
MODULE_BOOK_REQUIRED_SECTIONS = [
    "## 1. Module purpose",
    "## 6. Read/write surfaces and canonical truth",
    "## 9. Source files and their roles",
    "## 10. Relevant tests",
    "## 17. Planning-lock triggers",
    "## 20. Verification commands",
    "## 24. Rehydration judgement",
]
MODULE_MANIFEST_REQUIRED_FIELDS = [
    "path",
    "scoped_agents",
    "module_book",
    "priority",
    "zone",
    "authority_role",
    "high_risk_files",
    "law_dependencies",
    "current_fact_dependencies",
    "required_tests",
    "graph_appendix_status",
    "archive_extraction_status",
]


def build_impact(api: Any, files: list[str]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for entry in api._source_rationale_for(files):
        upstream = entry.get("upstream") or []
        downstream = entry.get("downstream") or []
        relations_complete = bool(upstream or downstream)
        tests = api._tests_for_packet("", [entry["path"]])
        entries.append(
            {
                "path": entry["path"],
                "zone": entry.get("zone", "unknown"),
                "authority_role": entry.get("authority_role", ""),
                "hazards": entry.get("hazards", []),
                "write_routes": entry.get("write_routes", []),
                "upstream": upstream,
                "downstream": downstream,
                "gates": entry.get("gates", []),
                "tests": tests,
                "relations_complete": relations_complete,
                "confidence": "complete" if relations_complete else "provisional",
                "missing_relation_reason": None if relations_complete else "source_rationale has no upstream/downstream relation for this file",
            }
        )

    zones = sorted({entry.get("zone", "unknown") for entry in entries if entry.get("zone")})
    write_routes = sorted({route for entry in entries for route in entry.get("write_routes", [])})
    hazards = sorted({hazard for entry in entries for hazard in entry.get("hazards", [])})
    tests_required = sorted({test for entry in entries for test in entry.get("tests", [])})
    static_checks = [
        "python scripts/semantic_linter.py --check " + " ".join(files)
    ] if files else []
    payload = {
        "files": files,
        "entries": entries,
        "aggregate": {
            "zones_touched": zones,
            "cross_zone": len(set(zones)) > 1,
            "write_routes": write_routes,
            "hazards": hazards,
            "tests_required": tests_required,
            "static_checks": static_checks,
            "files_may_not_change": api._files_may_not_change(zones),
        },
    }
    payload["context_assumption"] = api.build_context_assumption(
        profile="impact",
        profile_kind="impact_profile",
        source_entries=entries,
        confidence_basis=["source_rationale"],
    )
    return payload


def context_pack_profiles(api: Any) -> dict[str, dict[str, Any]]:
    return {
        str(profile.get("id")): profile
        for profile in api.load_context_pack_profiles().get("profiles") or []
        if profile.get("id")
    }


def run_context_packs(api: Any) -> Any:
    if not api.CONTEXT_PACK_PROFILES_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "context_pack_profiles_manifest_missing",
                    "architecture/context_pack_profiles.yaml",
                    "context pack profiles manifest is missing",
                )
            ],
        )
    manifest = api.load_context_pack_profiles()
    issues: list[Any] = []
    required = manifest.get("required_profile_fields") or []
    seen: set[str] = set()
    for idx, profile in enumerate(manifest.get("profiles") or []):
        profile_id = str(profile.get("id") or f"profile[{idx}]")
        path = f"architecture/context_pack_profiles.yaml:{profile_id}"
        if profile_id in seen:
            issues.append(api._issue("context_pack_profile_duplicate_id", path, "duplicate context-pack profile id"))
        seen.add(profile_id)
        for field in required:
            if api._metadata_missing(profile.get(field)):
                issues.append(api._issue("context_pack_profile_required_field_missing", path, f"missing {field}"))
        authority_status = str(profile.get("authority_status") or "")
        if not authority_status.startswith("generated_") or not authority_status.endswith("_not_authority"):
            issues.append(
                api._issue(
                    "context_pack_profile_invalid_authority_status",
                    path,
                    "authority_status must mark generated output as not authority",
                )
            )
        lore_policy = profile.get("lore_policy") or {}
        for field in ("direct_evidence", "broad_relevant", "expanded_available"):
            if api._metadata_missing(lore_policy.get(field)):
                issues.append(api._issue("context_pack_profile_lore_policy_missing", path, f"missing lore_policy.{field}"))
    return api.StrictResult(ok=not issues, issues=issues)


def module_manifest_entries(api: Any) -> dict[str, dict[str, Any]]:
    return {
        str(name): entry
        for name, entry in (api.load_module_manifest().get("modules") or {}).items()
        if isinstance(entry, dict)
    }


def module_book_registered(api: Any, path: str) -> bool:
    entries = api.load_docs_registry().get("entries") or []
    return any(str(entry.get("path") or "") == path for entry in entries)


def module_entry_matches_file(path: str, entry: dict[str, Any]) -> bool:
    for key in ("path", "scoped_agents", "module_book"):
        target = str(entry.get(key) or "")
        if not target:
            continue
        if path == target or path.startswith(target.rstrip("/") + "/"):
            return True
    return False


def module_context_for_files(api: Any, files: list[str]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for module_name, entry in sorted(module_manifest_entries(api).items()):
        if not any(module_entry_matches_file(path, entry) for path in files):
            continue
        contexts.append(
            {
                "module": module_name,
                "path": entry.get("path"),
                "scoped_agents": entry.get("scoped_agents"),
                "module_book": entry.get("module_book"),
                "high_risk_files": entry.get("high_risk_files", []),
                "required_tests": entry.get("required_tests", []),
                "current_fact_dependencies": entry.get("current_fact_dependencies", []),
                "law_dependencies": entry.get("law_dependencies", []),
                "graph_appendix_status": entry.get("graph_appendix_status"),
                "archive_extraction_status": entry.get("archive_extraction_status"),
            }
        )
    return contexts


def run_module_manifest(api: Any) -> Any:
    if not api.MODULE_MANIFEST_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "module_manifest_missing",
                    "architecture/module_manifest.yaml",
                    "module manifest is missing",
                )
            ],
        )
    issues: list[Any] = []
    entries = module_manifest_entries(api)
    if not entries:
        issues.append(
            api._warning(
                "module_manifest_empty",
                "architecture/module_manifest.yaml",
                "module manifest has no module entries",
            )
        )
    for module_name, entry in sorted(entries.items()):
        path = f"architecture/module_manifest.yaml:{module_name}"
        for field in MODULE_MANIFEST_REQUIRED_FIELDS:
            if api._metadata_missing(entry.get(field)):
                issues.append(api._warning("module_manifest_required_field_missing", path, f"missing {field}"))
        for key in ("path", "scoped_agents", "module_book"):
            target = str(entry.get(key) or "")
            if target and not (api.ROOT / target).exists():
                issues.append(api._warning("module_manifest_path_missing", path, f"{key} target missing: {target}"))
        for target in entry.get("high_risk_files") or []:
            if not (api.ROOT / str(target)).exists():
                issues.append(api._warning("module_manifest_path_missing", path, f"high_risk_file missing: {target}"))
        for target in entry.get("required_tests") or []:
            if not (api.ROOT / str(target)).exists():
                issues.append(api._warning("module_manifest_test_missing", path, f"required_test missing: {target}"))
        book = str(entry.get("module_book") or "")
        if book and not module_book_registered(api, book):
            issues.append(
                api._warning(
                    "module_manifest_docs_registry_mismatch",
                    path,
                    f"module book {book} is not explicitly classified in docs registry",
                )
            )
    return api.StrictResult(ok=True, issues=issues)


def run_module_books(api: Any) -> Any:
    if not api.MODULE_MANIFEST_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "module_manifest_missing",
                    "architecture/module_manifest.yaml",
                    "module manifest is missing",
                )
            ],
        )
    issues: list[Any] = []
    entries = module_manifest_entries(api)
    books_by_path = {str(entry.get("module_book")): (module_name, entry) for module_name, entry in entries.items()}
    for book_path, (module_name, entry) in sorted(books_by_path.items()):
        path = api.ROOT / book_path
        scoped = api.ROOT / str(entry.get("scoped_agents") or "")
        if not path.exists():
            issues.append(api._warning("module_book_missing", book_path, f"module book missing for {module_name}"))
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for heading in MODULE_BOOK_REQUIRED_SECTIONS:
            if heading not in text:
                issues.append(
                    api._warning(
                        "module_book_missing_section",
                        book_path,
                        f"module book missing required section heading {heading!r}",
                    )
                )
        if scoped.exists():
            scoped_text = scoped.read_text(encoding="utf-8", errors="ignore")
            if book_path not in scoped_text:
                issues.append(
                    api._warning(
                        "module_book_scoped_agents_mismatch",
                        str(entry.get("scoped_agents")),
                        f"scoped AGENTS does not point to module book {book_path}",
                    )
                )
        else:
            issues.append(
                api._warning(
                    "module_book_scoped_agents_missing",
                    str(entry.get("scoped_agents") or book_path),
                    f"scoped AGENTS missing for module {module_name}",
                )
            )
    for rel in sorted(
        path.relative_to(api.ROOT).as_posix()
        for path in (api.ROOT / "docs" / "reference" / "modules").glob("*.md")
        if path.name != "AGENTS.md"
    ):
        if rel not in books_by_path:
            issues.append(
                api._warning(
                    "module_book_unregistered",
                    rel,
                    "module book exists on disk but has no module manifest entry",
                )
            )
    return api.StrictResult(ok=True, issues=issues)


def task_boot_profiles(api: Any) -> dict[str, dict[str, Any]]:
    return {
        str(profile.get("id")): profile
        for profile in api.load_task_boot_profiles().get("profiles") or []
        if profile.get("id")
    }


def fatal_misread_index(api: Any) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in api.load_fatal_misreads().get("misreads") or []
        if item.get("id")
    }


def infer_task_class(api: Any, task: str, files: list[str]) -> str | None:
    task_l = task.lower()
    file_text = " ".join(files).lower()
    manifest = api.load_task_boot_profiles()
    profile_by_id = task_boot_profiles(api)
    for profile_id in manifest.get("required_task_classes") or profile_by_id:
        profile = profile_by_id.get(str(profile_id))
        if not profile:
            continue
        terms = [str(term).lower() for term in profile.get("trigger_terms") or []]
        if any(term and (term in task_l or term in file_text) for term in terms):
            return str(profile_id)
    return None


def current_fact_audit_date(text: str) -> date | None:
    match = re.search(r"Last audited:\s*(\d{4}-\d{2}-\d{2})", text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def current_fact_status(api: Any, surfaces: list[str]) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    today = date.today()
    for surface in surfaces:
        path = api.ROOT / str(surface)
        status: dict[str, Any] = {
            "path": str(surface),
            "present": path.exists(),
            "freshness_status": "unknown",
            "last_audited": None,
            "max_staleness_days": CURRENT_FACT_MAX_STALENESS_DAYS,
            "warnings": [],
        }
        if not path.exists():
            status["freshness_status"] = "missing"
            status["warnings"].append(
                {
                    "code": "current_fact_surface_missing",
                    "severity": "warning",
                    "path": str(surface),
                    "message": "required current fact surface is missing",
                }
            )
            statuses.append(status)
            continue
        audited = current_fact_audit_date(path.read_text(encoding="utf-8", errors="ignore"))
        if not audited:
            status["freshness_status"] = "unknown"
            status["warnings"].append(
                {
                    "code": "current_fact_surface_audit_missing",
                    "severity": "warning",
                    "path": str(surface),
                    "message": "required current fact surface has no parseable Last audited date",
                }
            )
        else:
            age_days = (today - audited).days
            status["last_audited"] = audited.isoformat()
            status["age_days"] = age_days
            if age_days > CURRENT_FACT_MAX_STALENESS_DAYS:
                status["freshness_status"] = "stale"
                status["warnings"].append(
                    {
                        "code": "current_fact_surface_stale",
                        "severity": "warning",
                        "path": str(surface),
                        "message": f"required current fact surface is {age_days} days old",
                    }
                )
            else:
                status["freshness_status"] = "fresh"
        statuses.append(status)
    return statuses


def semantic_bootstrap_graph_status(api: Any, profile: dict[str, Any], files: list[str]) -> dict[str, Any]:
    graph_usage = dict(profile.get("graph_usage") or {})
    status: dict[str, Any] = {
        "stage": graph_usage.get("stage", "not_required"),
        "authority_status": graph_usage.get("authority_status", "derived_not_authority"),
        "use_for": graph_usage.get("use_for", []),
        "not_for": graph_usage.get("not_for", []),
        "availability": "not_required",
        "warnings": [],
    }
    if status["stage"] != "stage_2_after_semantic_boot":
        return status
    result = api.run_code_review_graph_status(files)
    status["availability"] = "available" if result.ok else "unavailable_or_stale"
    status["issues"] = [api.asdict(issue) for issue in result.issues]
    details = result.details or {}
    graph_meta = details.get("graph_meta") or {}
    status["details"] = {
        "authority_status": details.get("authority_status"),
        "graph_db": details.get("graph_db"),
        "schema_version": details.get("schema_version"),
        "graph_meta": {
            "present": graph_meta.get("present"),
            "tracked": graph_meta.get("tracked"),
            "parity_status": graph_meta.get("parity_status"),
            "mismatches": graph_meta.get("mismatches", []),
        },
    }
    if not result.ok:
        for issue in result.issues:
            status["warnings"].append(
                {
                    "code": "code_review_graph_unavailable_or_stale",
                    "severity": issue.severity,
                    "path": issue.path,
                    "message": issue.message,
                }
            )
    if graph_meta.get("parity_status") == "mismatch":
        status["warnings"].append(
            {
                "code": "code_review_graph_meta_mismatch",
                "severity": "warning",
                "path": str(graph_meta.get("path") or ".code-review-graph/graph_meta.json"),
                "message": "graph metadata parity mismatch; use graph as derived context only",
            }
        )
    return status


def semantic_bootstrap_claims(api: Any) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for claim in api.load_core_claims().get("claims") or []:
        if "semantic-boot" not in (claim.get("applicable_profiles") or []):
            continue
        claims.append(
            {
                "claim_id": claim.get("claim_id"),
                "assertion": claim.get("assertion"),
                "proof_targets": claim.get("proof_targets", []),
                "gates": claim.get("gates", []),
                "confidence": "verified_claim",
            }
        )
    return claims


def build_semantic_bootstrap(
    api: Any,
    task_class: str,
    *,
    task: str = "",
    files: list[str] | None = None,
) -> dict[str, Any]:
    files = files or []
    if not api.TASK_BOOT_PROFILES_PATH.exists():
        return {
            "ok": False,
            "authority_status": "generated_semantic_bootstrap_not_authority",
            "task_class": task_class,
            "task": task,
            "files": files,
            "issues": [
                {
                    "code": "task_boot_profiles_manifest_missing",
                    "path": "architecture/task_boot_profiles.yaml",
                    "message": "semantic task boot profile manifest is missing",
                    "severity": "error",
                }
            ],
        }
    profiles = task_boot_profiles(api)
    profile = profiles.get(task_class)
    if not profile:
        return {
            "ok": False,
            "authority_status": "generated_semantic_bootstrap_not_authority",
            "task_class": task_class,
            "task": task,
            "files": files,
            "issues": [
                {
                    "code": "semantic_bootstrap_unknown_task_class",
                    "path": "architecture/task_boot_profiles.yaml",
                    "message": f"unknown task class {task_class!r}",
                    "severity": "error",
                }
            ],
        }

    facts = current_fact_status(api, [str(item) for item in profile.get("current_fact_surfaces") or []])
    fatal_index = fatal_misread_index(api) if api.FATAL_MISREADS_PATH.exists() else {}
    fatal_misreads = [
        {
            "id": misread_id,
            "severity": fatal_index.get(str(misread_id), {}).get("severity"),
            "false_equivalence": fatal_index.get(str(misread_id), {}).get("false_equivalence"),
            "correction": fatal_index.get(str(misread_id), {}).get("correction"),
            "proof_files": fatal_index.get(str(misread_id), {}).get("proof_files", []),
        }
        for misread_id in profile.get("fatal_misreads") or []
    ]
    graph = semantic_bootstrap_graph_status(api, profile, files)
    warnings = [
        warning
        for status in facts
        for warning in status.get("warnings", [])
    ] + list(graph.get("warnings") or [])
    return {
        "ok": True,
        "authority_status": "generated_semantic_bootstrap_not_authority",
        "task_class": task_class,
        "task": task,
        "files": sorted(dict.fromkeys(files)),
        "purpose": profile.get("purpose", ""),
        "required_reads": profile.get("required_reads", []),
        "current_fact_surfaces": facts,
        "required_proof_questions": profile.get("required_proofs", []),
        "fatal_misreads": fatal_misreads,
        "forbidden_shortcuts": profile.get("forbidden_shortcuts", []),
        "current_core_claims": semantic_bootstrap_claims(api),
        "graph_usage": graph,
        "verification_gates": profile.get("verification_gates", []),
        "warnings": warnings,
        "issues": [],
    }


def strict_result_summary(api: Any, result: Any) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "issue_count": len(result.issues),
        "blocking_count": len([issue for issue in result.issues if issue.severity == "error"]),
        "warning_count": len([issue for issue in result.issues if issue.severity == "warning"]),
        "issues": [api.asdict(issue) for issue in result.issues],
    }


def route_health_for_context_pack(api: Any, files: list[str]) -> dict[str, Any]:
    issues: list[Any] = []
    source_files = [file for file in files if file.startswith("src/")]
    rationale_paths = set((api.load_source_rationale().get("files") or {}).keys())
    for file in files:
        if not (api.ROOT / file).exists():
            issues.append(api._issue("context_pack_input_missing", file, "input file does not exist"))
    for file in sorted(set(source_files) - rationale_paths):
        issues.append(
            api._issue(
                "context_pack_source_rationale_missing",
                file,
                "input source file has no source_rationale entry; review context cannot classify it",
            )
        )
    return strict_result_summary(api, api.StrictResult(ok=not issues, issues=issues))


def repo_health_for_context_pack(api: Any) -> dict[str, Any]:
    checks = {
        "context_packs": api.run_context_packs(),
        "module_books": api.run_module_books(),
        "module_manifest": api.run_module_manifest(),
        "context_budget": api.run_context_budget(),
        "core_claims": api.run_core_claims(),
        "core_maps": api.run_core_maps(),
        "reference_replacement": api.run_reference_replacement(),
    }
    return {
        "ok": all(result.ok for result in checks.values()),
        "checks": {
            name: {
                "ok": result.ok,
                "issue_count": len(result.issues),
                "blocking_count": len([issue for issue in result.issues if issue.severity == "error"]),
                "warning_count": len([issue for issue in result.issues if issue.severity == "warning"]),
            }
            for name, result in checks.items()
        },
    }


def proof_claims_for_files(api: Any, files: list[str]) -> list[dict[str, Any]]:
    wanted = set(files)
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()
    for claim_id, proof in sorted(api._claim_proof_index().items()):
        if claim_id == "__DUPLICATES__" or proof.get("claim_status") != "replaced":
            continue
        source = proof.get("source") or {}
        proof_paths = {str(source.get("path") or proof.get("entry_path") or "")}
        proof_paths.update(str(target.get("path") or "") for target in proof.get("proof_targets") or [])
        if not wanted & proof_paths or claim_id in seen:
            continue
        seen.add(claim_id)
        claims.append(
            {
                "claim_id": claim_id,
                "assertion": proof.get("assertion", ""),
                "confidence": "verified_claim",
                "source": source or {"path": proof.get("entry_path", "")},
                "proof_targets": proof.get("proof_targets", []),
                "gates": proof.get("gates", []),
            }
        )
    return claims


def lore_summary(card: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "id": card.get("id"),
        "status": card.get("status"),
        "severity": card.get("severity"),
        "zero_context_digest": card.get("zero_context_digest"),
        "match_reason": reason,
        "expansion_hint": f"Read architecture/history_lore.yaml:{card.get('id')} only if this lore becomes central to the review.",
    }


def layered_history_lore(api: Any, task: str, files: list[str]) -> dict[str, Any]:
    task_l = task.lower()
    direct: list[dict[str, Any]] = []
    broad: list[dict[str, Any]] = []
    expanded: list[dict[str, str]] = []
    seen_direct: set[str] = set()
    seen_broad: set[str] = set()
    for card in api.load_history_lore().get("cards") or []:
        card_id = str(card.get("id") or "")
        routing = card.get("routing") or {}
        terms = [str(term).lower() for term in routing.get("task_terms", [])]
        patterns = [str(pattern) for pattern in routing.get("file_patterns", [])]
        matched_files = [file for file in files for pattern in patterns if fnmatch(file, pattern)]
        term_hits = [term for term in terms if term and term in task_l]
        if matched_files and card_id not in seen_direct:
            direct.append(lore_summary(card, reason="file_pattern:" + ",".join(sorted(set(matched_files)))))
            seen_direct.add(card_id)
        elif term_hits and card_id not in seen_broad:
            broad.append(lore_summary(card, reason="task_term:" + ",".join(sorted(set(term_hits)))))
            seen_broad.add(card_id)
        if (matched_files or term_hits) and card_id:
            expanded.append(
                {
                    "id": card_id,
                    "hint": f"Read architecture/history_lore.yaml:{card_id} if the reviewer needs the full failure history.",
                }
            )
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    direct.sort(key=lambda item: (severity_rank.get(str(item.get("severity")), 99), str(item.get("id"))))
    broad.sort(key=lambda item: (severity_rank.get(str(item.get("severity")), 99), str(item.get("id"))))
    return {
        "direct_evidence": direct,
        "broad_relevant": broad,
        "expanded_available": expanded,
    }


def context_pack_contract_surfaces(impact: dict[str, Any], claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims_by_path: dict[str, list[str]] = {}
    for claim in claims:
        source = claim.get("source") or {}
        if source.get("path"):
            claims_by_path.setdefault(str(source["path"]), []).append(str(claim["claim_id"]))
        for target in claim.get("proof_targets") or []:
            if target.get("path"):
                claims_by_path.setdefault(str(target["path"]), []).append(str(claim["claim_id"]))
    surfaces: list[dict[str, Any]] = []
    for entry in impact.get("entries") or []:
        path = entry["path"]
        surfaces.append(
            {
                "path": path,
                "zone": entry.get("zone"),
                "authority_role": entry.get("authority_role"),
                "hazards": entry.get("hazards", []),
                "relations_complete": entry.get("relations_complete", False),
                "relation_confidence": entry.get("confidence", "provisional"),
                "proof_claims": sorted(dict.fromkeys(claims_by_path.get(path, []))),
                "tests": entry.get("tests", []),
            }
        )
    return surfaces


def context_pack_coverage_gaps(impact: dict[str, Any], files: list[str]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    entry_paths = {entry["path"] for entry in impact.get("entries") or []}
    for file in sorted(set(files) - entry_paths):
        if file.startswith("src/"):
            gaps.append(
                {
                    "kind": "missing_source_rationale",
                    "path": file,
                    "confidence": "blocking_for_this_pack",
                    "why": "source file is in the reviewed package but has no source_rationale entry",
                }
            )
    for entry in impact.get("entries") or []:
        if not entry.get("relations_complete"):
            gaps.append(
                {
                    "kind": "provisional_relation_gap",
                    "path": entry["path"],
                    "confidence": "provisional",
                    "why": entry.get("missing_relation_reason"),
                }
            )
        if not entry.get("tests"):
            gaps.append(
                {
                    "kind": "missing_targeted_tests",
                    "path": entry["path"],
                    "confidence": "provisional",
                    "why": "no targeted tests were derived from gates or test topology",
                }
            )
    return gaps


def context_pack_downstream_risks(impact: dict[str, Any]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    aggregate = impact.get("aggregate") or {}
    zones = aggregate.get("zones_touched") or []
    if aggregate.get("cross_zone"):
        risks.append(
            {
                "kind": "cross_zone_contract_review",
                "confidence": "manifest_grounded",
                "why": f"review spans zones {zones}; local slice reviews can miss boundary regressions",
            }
        )
    for entry in impact.get("entries") or []:
        for hazard in entry.get("hazards") or []:
            risks.append(
                {
                    "kind": "hazard",
                    "path": entry["path"],
                    "hazard": hazard,
                    "confidence": "manifest_grounded",
                }
            )
        for downstream in entry.get("downstream") or []:
            risks.append(
                {
                    "kind": "downstream_consumer",
                    "path": entry["path"],
                    "downstream": downstream,
                    "confidence": "source_rationale",
                }
            )
    return risks


def context_pack_questions(
    profile: dict[str, Any],
    impact: dict[str, Any],
    claims: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> list[str]:
    questions = list(profile.get("review_questions") or [])
    if impact.get("aggregate", {}).get("cross_zone"):
        questions.append("Which cross-zone assumptions changed, and which boundary test proves the handoff still works?")
    if claims:
        questions.append("For each verified claim touched here, is the downstream consumer actually forced to respect it?")
    if any(gap.get("kind") == "provisional_relation_gap" for gap in gaps):
        questions.append("Which provisional relation gaps need manual code reading before PASS/REVISE?")
    return sorted(dict.fromkeys(questions))


def debug_red_green_checks(
    *,
    files: list[str],
    impact: dict[str, Any],
    claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tests = impact.get("aggregate", {}).get("tests_required", [])
    checks: list[dict[str, Any]] = []
    if files:
        checks.append(
            {
                "id": "semantic_linter_target",
                "command": "python scripts/semantic_linter.py --check " + " ".join(files),
                "confidence": "targeted_static_check",
                "red_means": "A semantic boundary or provenance seam is violated near the target files.",
                "green_means": "Configured semantic seam checks passed for this target set; it is not proof of full correctness.",
            }
        )
    if tests:
        checks.append(
            {
                "id": "targeted_tests",
                "command": "pytest -q " + " ".join(tests),
                "confidence": "manifest_derived",
                "red_means": "The symptom may be covered by an existing target/law regression.",
                "green_means": "Manifest-derived tests passed; expand if the symptom is not reproduced.",
            }
        )
    for claim in claims:
        for gate in claim.get("gates") or []:
            checks.append(
                {
                    "id": f"claim_gate:{claim.get('claim_id')}",
                    "command": gate,
                    "confidence": "verified_claim_gate",
                    "red_means": f"Claim {claim.get('claim_id')} is not currently defended by its gate.",
                    "green_means": f"Claim {claim.get('claim_id')} gate passed; still inspect consumers if behavior remains wrong.",
                }
            )
    checks.append(
        {
            "id": "context_pack_route_health",
            "command": "python scripts/topology_doctor.py context-pack --pack-type debug --task \"<symptom>\" --files " + " ".join(files),
            "confidence": "topology_route_check",
            "red_means": "The debug packet cannot classify one or more target files.",
            "green_means": "The starting packet is usable, but context_assumption still controls expansion.",
        }
    )
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for check in checks:
        key = (str(check.get("id")), str(check.get("command")))
        if key not in seen:
            unique.append(check)
            seen.add(key)
    return unique


def debug_suspected_boundaries(
    impact: dict[str, Any],
    claims: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    boundaries: list[dict[str, Any]] = []
    for entry in impact.get("entries") or []:
        boundaries.append(
            {
                "kind": "authority_surface",
                "path": entry["path"],
                "zone": entry.get("zone"),
                "authority_role": entry.get("authority_role"),
                "confidence": "source_rationale",
            }
        )
        for downstream in entry.get("downstream") or []:
            boundaries.append(
                {
                    "kind": "downstream_boundary",
                    "path": entry["path"],
                    "downstream": downstream,
                    "confidence": "source_rationale",
                }
            )
        for hazard in entry.get("hazards") or []:
            boundaries.append(
                {
                    "kind": "hazard_boundary",
                    "path": entry["path"],
                    "hazard": hazard,
                    "confidence": "manifest_grounded",
                }
            )
    for claim in claims:
        boundaries.append(
            {
                "kind": "verified_claim_boundary",
                "claim_id": claim.get("claim_id"),
                "assertion": claim.get("assertion"),
                "confidence": "verified_claim",
            }
        )
    for gap in gaps:
        if gap.get("kind") == "provisional_relation_gap":
            boundaries.append(
                {
                    "kind": "unknown_relation_boundary",
                    "path": gap.get("path"),
                    "confidence": "provisional",
                    "why": gap.get("why"),
                }
            )
    return boundaries


def build_debug_context_pack(api: Any, task: str, files: list[str]) -> dict[str, Any]:
    profile = context_pack_profiles(api).get("debug")
    if not profile:
        raise ValueError("missing context pack profile: debug")
    target_files = sorted(dict.fromkeys(files))
    impact = build_impact(api, target_files)
    claims = proof_claims_for_files(api, target_files)
    gaps = context_pack_coverage_gaps(impact, target_files)
    risks = context_pack_downstream_risks(impact)
    route_health = route_health_for_context_pack(api, target_files)
    modules = module_context_for_files(api, target_files)
    payload = {
        "pack_type": "debug",
        "authority_status": profile.get("authority_status", "generated_debug_packet_not_authority"),
        "symptom": task,
        "target_files": target_files,
        "zones_touched": impact.get("aggregate", {}).get("zones_touched", []),
        "suspected_boundaries": debug_suspected_boundaries(impact, claims, gaps),
        "contract_surfaces": context_pack_contract_surfaces(impact, claims),
        "proof_claims_touched": claims,
        "module_context": modules,
        "red_green_checks": debug_red_green_checks(files=target_files, impact=impact, claims=claims),
        "coverage_gaps": gaps,
        "downstream_risks": risks,
        "code_impact_graph": api.build_code_impact_graph(target_files, task=task),
        "tests_required": impact.get("aggregate", {}).get("tests_required", []),
        "static_checks": impact.get("aggregate", {}).get("static_checks", []),
        "debug_questions": context_pack_questions(profile, impact, claims, gaps),
        "lore": layered_history_lore(api, task, target_files),
        "route_health": route_health,
        "repo_health": repo_health_for_context_pack(api),
        "blocking_for_this_pack": route_health.get("issues", []),
        "context_assumption": api.build_context_assumption(
            profile="debug",
            profile_kind="context_pack_profile",
            source_entries=impact.get("entries", []),
            confidence_basis=["impact_profile", "core_claims", "context_pack_profile"],
        ),
    }
    return payload


def looks_like_package_review(api: Any, task: str, files: list[str]) -> bool:
    profile = context_pack_profiles(api).get("package_review", {})
    task_l = task.lower()
    terms = [str(term).lower() for term in profile.get("trigger_terms") or []]
    if any(term and term in task_l for term in terms):
        return True
    zones = build_impact(api, files).get("aggregate", {}).get("zones_touched", []) if files else []
    return bool(files) and len(set(zones)) > 1 and "review" in task_l


def looks_like_debug(api: Any, task: str, files: list[str]) -> bool:
    if not files:
        return False
    profile = context_pack_profiles(api).get("debug", {})
    task_l = task.lower()
    terms = [str(term).lower() for term in profile.get("trigger_terms") or []]
    return any(term and term in task_l for term in terms)


def build_package_review_context_pack(api: Any, task: str, files: list[str]) -> dict[str, Any]:
    profile = context_pack_profiles(api).get("package_review")
    if not profile:
        raise ValueError("missing context pack profile: package_review")
    changed_files = sorted(dict.fromkeys(files))
    impact = build_impact(api, changed_files)
    claims = proof_claims_for_files(api, changed_files)
    gaps = context_pack_coverage_gaps(impact, changed_files)
    risks = context_pack_downstream_risks(impact)
    route_health = route_health_for_context_pack(api, changed_files)
    modules = module_context_for_files(api, changed_files)
    payload = {
        "pack_type": "package_review",
        "authority_status": profile.get("authority_status", "generated_review_packet_not_authority"),
        "review_objective": task,
        "changed_files": changed_files,
        "zones_touched": impact.get("aggregate", {}).get("zones_touched", []),
        "contract_surfaces": context_pack_contract_surfaces(impact, claims),
        "module_context": modules,
        "proof_claims_touched": claims,
        "cross_slice_questions": context_pack_questions(profile, impact, claims, gaps),
        "coverage_gaps": gaps,
        "downstream_risks": risks,
        "code_impact_graph": api.build_code_impact_graph(changed_files, task=task),
        "tests_required": impact.get("aggregate", {}).get("tests_required", []),
        "static_checks": impact.get("aggregate", {}).get("static_checks", []),
        "lore": layered_history_lore(api, task, changed_files),
        "route_health": route_health,
        "repo_health": repo_health_for_context_pack(api),
        "blocking_for_this_pack": route_health.get("issues", []),
        "context_assumption": api.build_context_assumption(
            profile="package_review",
            profile_kind="context_pack_profile",
            source_entries=impact.get("entries", []),
            confidence_basis=["impact_profile", "core_claims", "context_pack_profile"],
        ),
    }
    return payload


def attach_semantic_bootstrap(
    api: Any,
    payload: dict[str, Any],
    *,
    task_class: str | None,
    task: str,
    files: list[str],
) -> dict[str, Any]:
    selected_task_class = task_class or infer_task_class(api, task, files)
    if selected_task_class:
        payload["semantic_bootstrap"] = build_semantic_bootstrap(
            api,
            selected_task_class,
            task=task,
            files=files,
        )
    else:
        payload["semantic_bootstrap"] = {
            "ok": False,
            "authority_status": "generated_semantic_bootstrap_not_authority",
            "task_class": None,
            "task": task,
            "files": files,
            "issues": [
                {
                    "code": "semantic_bootstrap_task_class_not_inferred",
                    "path": "architecture/task_boot_profiles.yaml",
                    "message": "task class was not inferred; pass --task-class for semantic bootstrap",
                    "severity": "warning",
                }
            ],
        }
    return payload


def build_context_pack(
    api: Any,
    pack_type: str,
    *,
    task: str,
    files: list[str],
    task_class: str | None = None,
) -> dict[str, Any]:
    selected = pack_type
    if pack_type == "auto":
        if looks_like_package_review(api, task, files):
            selected = "package_review"
        elif looks_like_debug(api, task, files):
            selected = "debug"
        else:
            raise ValueError("auto context-pack currently selects package_review or debug only")
    if selected == "package_review":
        payload = build_package_review_context_pack(api, task, files)
        payload["selected_by"] = {"requested": pack_type, "selected": selected}
        return attach_semantic_bootstrap(api, payload, task_class=task_class, task=task, files=files)
    if selected == "debug":
        payload = build_debug_context_pack(api, task, files)
        payload["selected_by"] = {"requested": pack_type, "selected": selected}
        return attach_semantic_bootstrap(api, payload, task_class=task_class, task=task, files=files)
    raise ValueError(f"unknown context pack type {pack_type!r}")
