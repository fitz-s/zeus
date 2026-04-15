"""Context-pack and impact builder family for topology_doctor."""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any


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
    payload = {
        "pack_type": "debug",
        "authority_status": profile.get("authority_status", "generated_debug_packet_not_authority"),
        "symptom": task,
        "target_files": target_files,
        "zones_touched": impact.get("aggregate", {}).get("zones_touched", []),
        "suspected_boundaries": debug_suspected_boundaries(impact, claims, gaps),
        "contract_surfaces": context_pack_contract_surfaces(impact, claims),
        "proof_claims_touched": claims,
        "red_green_checks": debug_red_green_checks(files=target_files, impact=impact, claims=claims),
        "coverage_gaps": gaps,
        "downstream_risks": risks,
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
    payload = {
        "pack_type": "package_review",
        "authority_status": profile.get("authority_status", "generated_review_packet_not_authority"),
        "review_objective": task,
        "changed_files": changed_files,
        "zones_touched": impact.get("aggregate", {}).get("zones_touched", []),
        "contract_surfaces": context_pack_contract_surfaces(impact, claims),
        "proof_claims_touched": claims,
        "cross_slice_questions": context_pack_questions(profile, impact, claims, gaps),
        "coverage_gaps": gaps,
        "downstream_risks": risks,
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


def build_context_pack(api: Any, pack_type: str, *, task: str, files: list[str]) -> dict[str, Any]:
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
        return payload
    if selected == "debug":
        payload = build_debug_context_pack(api, task, files)
        payload["selected_by"] = {"requested": pack_type, "selected": selected}
        return payload
    raise ValueError(f"unknown context pack type {pack_type!r}")
