"""Test topology checker family for topology_doctor."""

from __future__ import annotations

import ast
from typing import Any


def run_tests(api: Any) -> Any:
    topology = api.load_test_topology()
    actual = {
        path.relative_to(api.ROOT).as_posix()
        for path in (api.ROOT / "tests").glob("test_*.py")
    }
    categories = topology.get("categories") or {}
    classified: dict[str, str] = {}
    issues: list[Any] = []

    for category, paths in categories.items():
        for path in paths or []:
            if path in classified:
                issues.append(
                    api._issue(
                        "test_topology_duplicate_classification",
                        path,
                        f"classified in both {classified[path]} and {category}",
                    )
                )
            classified[path] = category

    classified_set = set(classified)
    for path in sorted(actual - classified_set):
        issues.append(api._issue("test_topology_missing", path, "test file has no topology classification"))
    for path in sorted(classified_set - actual):
        issues.append(api._issue("test_topology_stale", path, "classified test file is absent"))

    required_law = {
        "WMO_ROUNDING",
        "LIFECYCLE",
        "CANONICAL_DB_TRUTH",
        "LIVE_BACKTEST_SHADOW",
        "STRATEGY_KEY",
        "UNIT_BIN_TOPOLOGY",
        "P_RAW_PROVENANCE",
        "FDR_FAMILY",
        "NO_DIAGNOSTIC_PROMOTION",
    }
    law_gate = topology.get("law_gate") or {}
    law_test_exceptions = set(topology.get("law_test_category_exceptions") or [])
    for law in sorted(required_law - set(law_gate)):
        issues.append(api._issue("test_law_gate_missing", law, "required law gate topic missing"))
    for law, spec in law_gate.items():
        if not spec.get("tests"):
            issues.append(api._issue("test_law_gate_missing_tests", law, "law gate has no tests"))
        for path in spec.get("tests", []):
            if path not in actual:
                issues.append(api._issue("test_law_gate_stale_test", path, f"{law} references absent test"))
            elif classified.get(path) != "core_law_antibody" and path not in law_test_exceptions:
                issues.append(
                    api._issue(
                        "test_law_gate_non_core",
                        path,
                        f"{law} law-gate test is classified as {classified.get(path)}",
                    )
                )
        for path in spec.get("protects", []):
            if not (api.ROOT / path).exists():
                issues.append(api._issue("test_law_gate_stale_protects", path, f"{law} protects missing path"))

    wmo_protects = set((law_gate.get("WMO_ROUNDING") or {}).get("protects", []))
    for path in (
        "src/engine/replay.py",
        "src/engine/monitor_refresh.py",
        "src/execution/harvester.py",
        "src/calibration/store.py",
    ):
        if path not in wmo_protects:
            issues.append(api._issue("test_law_gate_incomplete_protects", path, "WMO_ROUNDING missing Packet 1 downstream"))

    high_sensitivity = topology.get("high_sensitivity_skips") or {}
    high_sensitivity_required = {
        path
        for path in actual
        if classified.get(path) == "core_law_antibody"
        and api.SKIP_PATTERN.search((api.ROOT / path).read_text(encoding="utf-8", errors="ignore"))
    }
    for path in sorted(high_sensitivity_required - set(high_sensitivity)):
        issues.append(
            api._issue(
                "test_high_sensitivity_missing",
                path,
                "core law test contains skip markers but has no high-sensitivity skip status",
            )
        )
    for path, spec in high_sensitivity.items():
        if path not in actual:
            issues.append(api._issue("test_high_sensitivity_stale", path, "skip status references absent test"))
        for key in ("owner", "packet", "reason", "sunset"):
            if not spec.get(key):
                issues.append(api._issue("test_high_sensitivity_incomplete", path, f"missing {key}"))
        if path in actual:
            text = (api.ROOT / path).read_text(encoding="utf-8", errors="ignore")
            skip_count = len(api.SKIP_PATTERN.findall(text))
            if spec.get("skip_count") != skip_count:
                issues.append(
                    api._issue(
                        "test_high_sensitivity_skip_count_mismatch",
                        path,
                        f"expected {spec.get('skip_count')} skips, found {skip_count}",
                    )
                )
            for pattern in spec.get("reason_patterns", []):
                if pattern not in text:
                    issues.append(
                        api._issue(
                            "test_high_sensitivity_reason_missing",
                            path,
                            f"missing skip reason pattern {pattern!r}",
                        )
                    )

    reverse = topology.get("reverse_antibody_status") or {}
    for item in reverse.get("active", []) or []:
        issues.append(
            api._issue(
                "test_reverse_antibody_active",
                str(item),
                "active reverse-antibody must be rewritten or quarantined",
            )
        )
    for path in sorted(actual):
        text = (api.ROOT / path).read_text(encoding="utf-8", errors="ignore")
        for pattern in api.DANGEROUS_REVERSE_ANTIBODY_PATTERNS:
            if pattern.search(text):
                issues.append(
                    api._issue(
                        "test_reverse_antibody_detected",
                        path,
                        f"dangerous assertion shape matched {pattern.pattern}",
                    )
                )

    for manifest in topology.get("relationship_test_manifests") or []:
        rel = manifest.get("path")
        if not rel:
            issues.append(api._issue("test_relationship_manifest_missing_path", "relationship_test_manifests", "missing path"))
            continue
        manifest_path = api.ROOT / str(rel)
        if not manifest_path.exists():
            issues.append(api._issue("test_relationship_manifest_missing", str(rel), "relationship manifest file missing"))
            continue
        module = ast.parse(manifest_path.read_text(encoding="utf-8"))
        defined: set[str] = set()
        for node in module.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                defined.add(node.target.id)
        for symbol in manifest.get("required_symbols") or []:
            if symbol not in defined:
                issues.append(api._issue("test_relationship_manifest_missing_symbol", str(rel), f"missing {symbol}"))
        for protected in manifest.get("protects") or []:
            if not (api.ROOT / str(protected)).exists():
                issues.append(api._issue("test_relationship_manifest_protects_missing", str(protected), f"{rel} protects missing test"))

    return api.StrictResult(ok=not issues, issues=issues)
