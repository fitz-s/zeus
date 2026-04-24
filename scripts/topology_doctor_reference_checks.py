"""Reference replacement and core-claim checker family for topology_doctor."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def reference_default_reads(api: Any) -> set[str]:
    agents = api.ROOT / "docs" / "reference" / "AGENTS.md"
    text = agents.read_text(encoding="utf-8", errors="ignore")
    start = text.find("**Default reads**")
    end = text.find("**Conditional reads**")
    if start == -1 or end == -1 or end < start:
        return set()
    block = text[start:end]
    return set(api.re.findall(r"`([^`]+\.md)`", block))


def reference_conditional_reads(api: Any) -> set[str]:
    agents = api.ROOT / "docs" / "reference" / "AGENTS.md"
    text = agents.read_text(encoding="utf-8", errors="ignore")
    start = text.find("**Conditional reads**")
    end = text.find("Replacement/deletion eligibility")
    if start == -1 or end == -1 or end < start:
        return set()
    block = text[start:end]
    return set(api.re.findall(r"`([^`]+\.md)`", block))


def validate_reference_claim_proofs(
    api: Any,
    entry_path: str,
    entry: dict[str, Any],
    manifest: dict[str, Any],
    seen_claim_ids: set[str],
) -> list[Any]:
    issues: list[Any] = []
    claim_proofs = entry.get("claim_proofs") or []
    if "claim_proofs" in entry and not isinstance(entry.get("claim_proofs"), list):
        return [
            api._issue(
                "reference_claim_proof_invalid",
                entry_path,
                "claim_proofs must be a list",
            )
        ]

    roles = set(manifest.get("allowed_claim_roles") or [])
    statuses = set(manifest.get("allowed_claim_statuses") or [])
    target_kinds = set(manifest.get("allowed_proof_target_kinds") or [])
    nonblocking_roles = {"breadcrumb", "stale_claim", "derived_diagnostic"}
    nonblocking_statuses = {"intentionally_unpromoted", "stale_superseded"}
    invariant_roles = {"fact_spec", "failure_lore", "authority_candidate"}

    for idx, proof in enumerate(claim_proofs):
        claim_id = str(proof.get("claim_id") or f"claim[{idx}]")
        proof_path = f"{entry_path}:{claim_id}"
        if not proof.get("claim_id"):
            issues.append(api._issue("reference_claim_proof_invalid", proof_path, "missing claim_id"))
        elif claim_id in seen_claim_ids:
            issues.append(api._issue("reference_claim_proof_invalid", proof_path, "duplicate claim_id"))
        else:
            seen_claim_ids.add(claim_id)

        source = proof.get("source") or {}
        if source.get("path") != entry_path:
            issues.append(
                api._issue(
                    "reference_claim_proof_invalid",
                    proof_path,
                    "source.path must equal parent reference entry path",
                )
            )
        if api._metadata_missing(source.get("locator")):
            issues.append(api._issue("reference_claim_proof_invalid", proof_path, "source.locator is required"))

        role = proof.get("claim_role")
        status = proof.get("claim_status")
        if role not in roles:
            issues.append(api._issue("reference_claim_proof_invalid", proof_path, f"invalid claim_role {role!r}"))
        if status not in statuses:
            issues.append(api._issue("reference_claim_proof_invalid", proof_path, f"invalid claim_status {status!r}"))
        if api._metadata_missing(proof.get("assertion")):
            issues.append(api._issue("reference_claim_proof_invalid", proof_path, "assertion is required"))

        proof_targets = proof.get("proof_targets") or []
        if proof_targets and not isinstance(proof_targets, list):
            issues.append(api._issue("reference_claim_proof_invalid", proof_path, "proof_targets must be a list"))
            proof_targets = []
        for target in proof_targets:
            kind = target.get("kind")
            target_path = str(target.get("path") or "")
            if kind not in target_kinds:
                issues.append(api._issue("reference_claim_proof_invalid", proof_path, f"invalid proof target kind {kind!r}"))
            if not target_path:
                issues.append(api._issue("reference_claim_proof_invalid", proof_path, "proof target path is required"))
                continue
            if any(char in target_path for char in "*?[]"):
                issues.append(api._issue("reference_claim_proof_invalid", proof_path, "proof target path must not be a glob"))
            elif not (api.ROOT / target_path).exists():
                issues.append(api._issue("reference_claim_proof_invalid", proof_path, f"proof target missing: {target_path}"))

        gates = proof.get("gates") or []
        if status == "replaced" and role not in nonblocking_roles:
            if not proof_targets:
                issues.append(api._issue("reference_claim_proof_invalid", proof_path, "replaced claim requires proof_targets"))
            if not gates:
                issues.append(api._issue("reference_claim_proof_invalid", proof_path, "replaced claim requires gates"))
        if role in invariant_roles and api._metadata_missing(proof.get("invalidation_condition")):
            issues.append(api._issue("reference_claim_proof_invalid", proof_path, f"{role} requires invalidation_condition"))
        if role in nonblocking_roles or status in nonblocking_statuses:
            if api._metadata_missing(proof.get("nonblocking_reason")):
                issues.append(api._issue("reference_claim_proof_invalid", proof_path, "nonblocking claim requires nonblocking_reason"))

    return issues


def claim_proof_index(api: Any) -> dict[str, dict[str, Any]]:
    manifest = api.load_reference_replacement()
    index: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for entry in manifest.get("entries") or []:
        entry_path = str(entry.get("path") or "")
        for proof in entry.get("claim_proofs") or []:
            claim_id = str(proof.get("claim_id") or "")
            if claim_id:
                if claim_id in index:
                    duplicates.add(claim_id)
                index[claim_id] = {"entry_path": entry_path, **proof}
    core_claims = api.load_core_claims()
    for proof in core_claims.get("claims") or []:
        claim_id = str(proof.get("claim_id") or "")
        if claim_id:
            if claim_id in index:
                duplicates.add(claim_id)
            source = proof.get("source") or {}
            index[claim_id] = {"entry_path": source.get("path", ""), **proof}
    if duplicates:
        index["__DUPLICATES__"] = {"claim_ids": sorted(duplicates)}
    return index


def run_core_claims(api: Any) -> Any:
    if not api.CORE_CLAIMS_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "core_claims_manifest_missing",
                    "architecture/core_claims.yaml",
                    "core claims manifest is missing",
                )
            ],
        )
    manifest = api.load_core_claims()
    issues: list[Any] = []
    required = manifest.get("required_claim_fields") or []
    statuses = set(manifest.get("allowed_claim_statuses") or [])
    source_kinds = set(manifest.get("allowed_source_kinds") or [])
    target_kinds = set(manifest.get("allowed_proof_target_kinds") or [])
    seen: set[str] = set()
    duplicate_claims = set(claim_proof_index(api).get("__DUPLICATES__", {}).get("claim_ids", []))
    for claim_id in sorted(duplicate_claims):
        issues.append(
            api._issue(
                "core_claim_duplicate_id",
                f"architecture/core_claims.yaml:{claim_id}",
                "claim_id duplicates another claim manifest",
            )
        )
    for claim in manifest.get("claims") or []:
        claim_id = str(claim.get("claim_id") or "<missing>")
        path = f"architecture/core_claims.yaml:{claim_id}"
        if claim_id in seen:
            issues.append(api._issue("core_claim_duplicate_id", path, "duplicate claim_id"))
        seen.add(claim_id)
        for field in required:
            if api._metadata_missing(claim.get(field)):
                issues.append(api._issue("core_claim_required_field_missing", path, f"missing {field}"))
        if claim.get("claim_status") not in statuses:
            issues.append(api._issue("core_claim_invalid_status", path, f"invalid status {claim.get('claim_status')!r}"))
        source = claim.get("source") or {}
        if source.get("kind") not in source_kinds:
            issues.append(api._issue("core_claim_invalid_source", path, f"invalid source kind {source.get('kind')!r}"))
        source_path = str(source.get("path") or "")
        if source_path and not (api.ROOT / source_path).exists():
            issues.append(api._issue("core_claim_source_missing", path, f"source path missing: {source_path}"))
        elif source_path and not api._locator_exists(source_path, source.get("locator")):
            issues.append(api._issue("core_claim_source_missing", path, f"source locator missing: {source_path}:{source.get('locator')}"))
        for target in claim.get("proof_targets") or []:
            if target.get("kind") not in target_kinds:
                issues.append(api._issue("core_claim_invalid_proof_target", path, f"invalid proof target kind {target.get('kind')!r}"))
            target_path = str(target.get("path") or "")
            if not target_path or not (api.ROOT / target_path).exists():
                issues.append(api._issue("core_claim_proof_target_missing", path, f"proof target missing: {target_path}"))
            elif not api._locator_exists(target_path, target.get("locator")):
                issues.append(
                    api._issue(
                        "core_claim_proof_target_missing",
                        path,
                        f"proof target locator missing: {target_path}:{target.get('locator')}",
                    )
                )
        for gate in claim.get("gates") or []:
            for ref in api._gate_path_tokens(str(gate)):
                if not api._history_lore_path_exists(ref):
                    issues.append(api._issue("core_claim_gate_target_missing", path, f"gate target missing: {ref}"))
    return api.StrictResult(ok=not issues, issues=issues)


def run_reference_replacement(api: Any) -> Any:
    manifest = api.load_reference_replacement()
    issues: list[Any] = []
    required = manifest.get("required_entry_fields") or []
    allowed_statuses = set(manifest.get("allowed_replacement_statuses") or [])
    allowed_actions = set(manifest.get("allowed_actions") or [])
    entries = manifest.get("entries") or []
    entry_by_path = {str(entry.get("path")): entry for entry in entries if entry.get("path")}

    actual_reference_docs = {
        path.relative_to(api.ROOT).as_posix()
        for path in (api.ROOT / "docs" / "reference").glob("*.md")
        if path.name != "AGENTS.md"
    }
    declared = set(entry_by_path)

    for path in sorted(actual_reference_docs - declared):
        issues.append(api._issue("reference_replacement_missing_entry", path, "reference doc has no replacement matrix entry"))
    for path in sorted(declared - actual_reference_docs):
        issues.append(api._issue("reference_replacement_stale_entry", path, "replacement matrix entry has no reference doc"))

    default_reads = reference_default_reads(api)
    conditional_reads = reference_conditional_reads(api)
    seen_claim_ids: set[str] = set()
    for path, entry in sorted(entry_by_path.items()):
        short = Path(path).name
        for field in required:
            if field not in entry:
                issues.append(api._issue("reference_replacement_required_field_missing", path, f"missing {field}"))
        if entry.get("replacement_status") not in allowed_statuses:
            issues.append(api._issue("reference_replacement_invalid_status", path, f"invalid status {entry.get('replacement_status')!r}"))
        if entry.get("allowed_action") not in allowed_actions:
            issues.append(api._issue("reference_replacement_invalid_action", path, f"invalid action {entry.get('allowed_action')!r}"))
        if bool(entry.get("default_read")) != (short in default_reads):
            issues.append(
                api._issue(
                    "reference_replacement_default_read_mismatch",
                    path,
                    f"default_read={entry.get('default_read')} but docs/reference/AGENTS.md default reads contain {short}: {short in default_reads}",
                )
            )
        if entry.get("allowed_action") == "keep_conditional" and short not in conditional_reads:
            issues.append(
                api._issue(
                    "reference_replacement_default_read_mismatch",
                    path,
                    f"keep_conditional entry is missing from docs/reference/AGENTS.md conditional reads: {short}",
                )
            )
        for replacement in entry.get("replaced_by") or []:
            if not any(char in str(replacement) for char in "*?[]") and not (api.ROOT / str(replacement)).exists():
                issues.append(api._issue("reference_replacement_replacement_missing", path, f"replacement target missing: {replacement}"))
        issues.extend(validate_reference_claim_proofs(api, path, entry, manifest, seen_claim_ids))
        if entry.get("delete_allowed") is True:
            if entry.get("replacement_status") != "replaced":
                issues.append(api._issue("reference_replacement_delete_unsafe", path, "delete_allowed requires replacement_status=replaced"))
            if entry.get("unique_remaining"):
                issues.append(api._issue("reference_replacement_delete_unsafe", path, "delete_allowed requires unique_remaining=[]"))
            if not entry.get("replaced_by"):
                issues.append(api._issue("reference_replacement_delete_unsafe", path, "delete_allowed requires replaced_by evidence"))
            claim_proofs = entry.get("claim_proofs") or []
            if not claim_proofs:
                issues.append(api._issue("reference_replacement_delete_unsafe", path, "delete_allowed requires claim_proofs"))
            for proof in claim_proofs:
                status = proof.get("claim_status")
                if status not in {"replaced", "stale_superseded", "intentionally_unpromoted"}:
                    issues.append(
                        api._issue(
                            "reference_replacement_delete_unsafe",
                            path,
                            f"delete_allowed requires final claim proof status, got {status!r}",
                        )
                    )

    return api.StrictResult(ok=not issues, issues=issues)
