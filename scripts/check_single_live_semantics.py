# Created: 2026-07-22
# Last reused/audited: 2026-07-24
# Authority basis: operator-directed single-live-semantics extinction pass.
"""Reject resurrection of dormant alternate-runtime concepts."""

from __future__ import annotations

import argparse
import ast
import plistlib
import re
from pathlib import Path
from xml.parsers.expat import ExpatError


ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (
    "src",
    "scripts",
    "architecture",
    "config",
    "deploy",
    ".github",
    "docs/authority",
    "docs/operations/current",
    "docs/reference",
)
SCAN_FILES = (
    "AGENTS.md",
    "docs/operations/current/GOAL.md",
    "docs/operations/current/package.yaml",
    "docs/operations/current/plans/INDEX.md",
    "docs/operations/current/plans/single_live_semantics_2026-07-22.md",
)
TEXT_SUFFIXES = {".json", ".md", ".plist", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml"}
EXCLUDED_DOCUMENT_SUFFIXES = {".md", ".txt"}
EXCLUDED = {Path("scripts/check_single_live_semantics.py")}
CUTOVER_SCRIPT = Path("scripts/migrations/202607_single_live_semantics_cutover.py")
CUTOVER_RETIRED_ASSIGNMENTS = frozenset(
    {
        "RETIRED_AUDIT_INDEX",
        "RETIRED_AUTHORITY_COLUMN",
        "RETIRED_CONFIG_KEYS",
        "RETIRED_CONFIG_NOTES",
        "RETIRED_CONFIG_PATHS",
        "RETIRED_CONVERSION_EVENTS",
        "RETIRED_CONVERSION_TABLE",
        "RETIRED_ELIGIBILITY_COLUMN",
        "RETIRED_EPOCH_TABLE",
        "RETIRED_FILES",
        "RETIRED_FORCE_EXIT_COLUMN",
        "RETIRED_LIVE_AUTHORITY_VALUE",
        "RETIRED_MANIFEST_FIELD",
        "RETIRED_PRE_SUBMIT_DECISION_CERTIFICATE",
        "RETIRED_PRE_SUBMIT_MODE",
        "RETIRED_PRE_SUBMIT_MODE_CERTIFICATE",
        "RETIRED_RECEIPT_COLUMNS",
        "RETIRED_REPLAY_MODE",
        "RETIRED_SIZING_CERTIFICATE",
        "RETIRED_TRANSFER_TABLE",
    }
)
_LIVE_CONTROL_TARGETS = frozenset({"category", "lane", "mode", "runtime", "semantics"})
EXCLUDED_SUBTREES = (
    Path("docs/archive"),
    Path("docs/evidence"),
    Path("docs/rebuild"),
    Path("docs/operations/current/plans/migration_preview"),
)
_EXCLUDED_PREFIXES = tuple(f"{path.as_posix()}/" for path in EXCLUDED_SUBTREES)
_LIVE_REFERENCE_ROOTS = frozenset({"src", "scripts", "config", "deploy", ".github"})

_PARALLEL_INACTIVE = "shadow_" + "veto_only"
_RETIRED_MEAN_SHIFT = "edli_" + "bias_correction"
_RETIRED_EXIT_MEAN_SHIFT = "exit_" + "bias_family_unify"
_RETIRED_AUTHORITY_COLUMN = "trade_" + "authority_status"
_FORBIDDEN = (
    _PARALLEL_INACTIVE,
    _RETIRED_AUTHORITY_COLUMN,
    "validated_calibration_" + "transfers",
    "ctf_conversion_" + "commands",
    "ctf_conversion_command_" + "events",
    "entry_forecast_" + "rollout",
    "entry_forecast_" + "promotion",
    "replacement_forecast_live_" + "dry_run",
    "experimental_" + "disabled",
    _RETIRED_MEAN_SHIFT,
    _RETIRED_EXIT_MEAN_SHIFT,
    "calibration_auto_" + "promote",
    "unified_uncertainty_" + "budget",
    "evaluator_entry_quote_" + "evidence_enabled",
    "force_exit_" + "review",
    "zeus_harvester_live_" + "enabled",
    "edli_intake_phase_filter_" + "enabled",
    "zeus_user_channel_ws_" + "enabled",
    "zeus_autonomous_redeem_" + "enabled",
    "zeus_autonomous_redeem_" + "dry_run",
    "zeus_autonomous_wrap_" + "dry_run",
    "wrap_dry_run_" + "logged",
    "kelly_dry_" + "run",
    "city_skill_gate_live_" + "enabled",
    "ingest_etl_forecast_" + "skill",
    "replacement_0_1_bayes_precision_fusion_" + "capture_enabled",
    "replacement_0_1_bayes_precision_fusion_" + "enabled",
    "openmeteo_ecmwf_ifs9_bayes_fusion_live_" + "enabled",
    "openmeteo_ecmwf_ifs9_bayes_fusion_kelly_increase_" + "enabled",
    "openmeteo_ecmwf_ifs9_bayes_fusion_direction_flip_" + "enabled",
    "source_time_" + "frontier",
)
_RUNTIME_CATEGORY_FORBIDDEN = (
    "telemetry_only",
    "observe_only",
    "observation_only",
)
_CONCEPT_TOKENS = (
    "sha" + "dow",
    "diag" + "nostic",
)


def violations(
    root: Path = ROOT, *, include_external_symlinks: bool = True
) -> list[str]:
    out: list[str] = []
    paths = _scan_paths(root)
    paths.update(_live_reachable_excluded_python_paths(root, paths))
    for path in paths:
        if not path.is_file():
            continue
        if path.is_symlink() and not include_external_symlinks:
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError:
                continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        rel_lower = rel.as_posix().lower()
        if rel in EXCLUDED:
            continue
        if _is_excluded_subtree(rel):
            out.extend(
                f"{rel}: {item}" for item in _excluded_artifact_violations(path)
            )
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        scan_value = source.lower()
        if rel.parts and rel.parts[0] in _LIVE_REFERENCE_ROOTS:
            out.extend(
                f"{rel}: {item}"
                for item in _excluded_reference_violations(rel, source)
            )
        if path.suffix.lower() == ".py":
            out.extend(
                f"{rel}: {item}"
                for item in _identifier_concept_violations(source)
            )
            scan_value += "\n" + "\n".join(
                _static_python_strings(
                    source,
                    allowed_retired_assignments=(
                        CUTOVER_RETIRED_ASSIGNMENTS
                        if rel == CUTOVER_SCRIPT
                        else frozenset()
                    ),
                )
            )
            if rel == CUTOVER_SCRIPT:
                out.extend(
                    f"{rel}: {item}"
                    for item in _retired_assignment_control_violations(source)
                )
        for token in _CONCEPT_TOKENS:
            if _contains_live_alternate_concept(token, scan_value):
                out.append(f"{rel}: forbidden alternate-runtime concept {token!r}")
        for token in _FORBIDDEN:
            if _contains_exact(token, rel_lower) or _contains_exact(token, scan_value):
                out.append(f"{rel}: forbidden dormant-runtime token {token!r}")
        if rel.parts and rel.parts[0] in {
            "src",
            "scripts",
            "config",
            "deploy",
            ".github",
        }:
            for token in _RUNTIME_CATEGORY_FORBIDDEN:
                if _contains_exact(token, rel_lower) or _contains_exact(token, scan_value):
                    out.append(
                        f"{rel}: forbidden vague runtime category {token!r}"
                    )
    return sorted(set(out))


def _identifier_concept_violations(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            identifiers.add(node.name)
        elif isinstance(node, ast.arg):
            identifiers.add(node.arg)
        elif isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
    return [
        f"forbidden alternate-runtime identifier {identifier!r}"
        for identifier in sorted(identifiers)
        if any(
            re.search(rf"(?:^|_){re.escape(token)}(?:s)?(?:_|$)", identifier.lower())
            for token in _CONCEPT_TOKENS
        )
    ]


def _scan_paths(root: Path) -> set[Path]:
    paths: set[Path] = set()
    for scan_root in SCAN_ROOTS:
        base = root / scan_root
        if not base.exists():
            continue
        paths.update(
            path
            for path in base.rglob("*")
            if (
                path.suffix.lower() == ".py"
                or not _is_excluded_subtree(path.relative_to(root))
            )
        )
    for subtree in EXCLUDED_SUBTREES:
        base = root / subtree
        if base.exists():
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    executable_document = bool(path.stat().st_mode & 0o111)
                    shebang_document = path.read_bytes()[:2] == b"#!"
                except OSError:
                    executable_document = True
                    shebang_document = True
                if (
                    path.suffix.lower() not in EXCLUDED_DOCUMENT_SUFFIXES
                    or executable_document
                    or shebang_document
                ):
                    paths.add(path)
    paths.update(root / name for name in SCAN_FILES)
    return paths


def _is_excluded_subtree(rel: Path) -> bool:
    return any(rel == subtree or subtree in rel.parents for subtree in EXCLUDED_SUBTREES)


def _excluded_artifact_violations(path: Path) -> list[str]:
    out: list[str] = []
    if path.suffix.lower() not in EXCLUDED_DOCUMENT_SUFFIXES:
        out.append("excluded subtree contains a non-document artifact")
    try:
        if path.stat().st_mode & 0o111:
            out.append("excluded subtree document has executable permission")
        if path.read_bytes()[:2] == b"#!":
            out.append("excluded subtree document has a shebang")
    except OSError as exc:
        out.append(f"excluded subtree artifact cannot be verified: {exc}")
    return out


def _excluded_reference_violations(path: Path, source: str) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _python_excluded_reference_violations(source)
    lower = source.lower()
    if suffix == ".sh":
        patterns = (
            r"(?m)^\s*(?:source|\.)\s+[^\n]*(?:"
            + "|".join(re.escape(prefix) for prefix in _EXCLUDED_PREFIXES)
            + ")",
            r"(?m)^\s*(?:exec\s+)?(?:ba|z|k)?sh\s+[^\n]*(?:"
            + "|".join(re.escape(prefix) for prefix in _EXCLUDED_PREFIXES)
            + ")",
        )
        return (
            ["live shell executes or sources an excluded subtree"]
            if any(re.search(pattern, lower) for pattern in patterns)
            else []
        )
    if suffix == ".plist":
        try:
            payload = plistlib.loads(source.encode("utf-8"))
        except (ExpatError, ValueError, plistlib.InvalidFileException):
            match = re.search(
                r"<key>\s*ProgramArguments\s*</key>\s*<array>(.*?)</array>",
                source,
                flags=re.DOTALL | re.IGNORECASE,
            )
            arguments = [match.group(1)] if match else []
        else:
            arguments = payload.get("ProgramArguments", []) if isinstance(payload, dict) else []
        if any(
            prefix in str(argument).lower()
            for argument in arguments
            for prefix in _EXCLUDED_PREFIXES
        ):
            return ["live plist ProgramArguments references an excluded subtree"]
        return []
    if path.parts and path.parts[0] in {"config", "deploy", ".github"}:
        key = r"(?:path|file|config|program|source|exec|command)"
        prefix = "|".join(re.escape(item) for item in _EXCLUDED_PREFIXES)
        if re.search(rf"{key}[^\n]{{0,120}}(?:{prefix})", lower):
            return ["live config references an excluded subtree"]
    return []


def _python_excluded_reference_violations(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    bindings = _literal_bindings(tree, excluded=frozenset())
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_path_consuming_call(node.func):
            continue
        values = {
            value.lower()
            for child in ast.walk(node)
            if (value := _literal_string(child, bindings)) is not None
        }
        if any(
            prefix in value
            for value in values
            for prefix in _EXCLUDED_PREFIXES
        ):
            out.append("live Python call consumes an excluded subtree")
    return sorted(set(out))


def _is_path_consuming_call(function: ast.AST) -> bool:
    name = _call_name(function)
    return name in {
        "open",
        "io.open",
        "os.system",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
        "subprocess.run",
    } or name.rsplit(".", 1)[-1] in {"open", "read_bytes", "read_text"}


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _live_reachable_excluded_python_paths(root: Path, paths: set[Path]) -> set[Path]:
    modules = _python_modules(root)
    pending = [path for path in paths if path.suffix == ".py"]
    seen = set(pending)
    reachable: set[Path] = set()
    while pending:
        path = pending.pop()
        for imported in _imported_modules(path, root):
            candidate = modules.get(imported)
            if candidate is None or candidate in seen:
                continue
            seen.add(candidate)
            pending.append(candidate)
            if _is_excluded_subtree(candidate.relative_to(root)):
                reachable.add(candidate)
    return reachable


def _python_modules(root: Path) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if path.name == "__init__.py":
            rel = rel.parent
        else:
            rel = rel.with_suffix("")
        if rel.parts:
            modules[".".join(rel.parts)] = path
    return modules


def _imported_modules(path: Path, root: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    imported: set[str] = set()
    package = ".".join(path.relative_to(root).with_suffix("").parts[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                package_parts = package.split(".") if package else []
                base_parts = package_parts[: max(0, len(package_parts) - node.level + 1)]
                if base:
                    base_parts.extend(base.split("."))
                base = ".".join(base_parts)
            if base:
                imported.add(base)
                imported.update(f"{base}.{alias.name}" for alias in node.names)
    return imported


def _static_python_strings(
    source: str, *, allowed_retired_assignments: frozenset[str] = frozenset()
) -> set[str]:
    """Return strings Python can construct entirely from literals in the AST."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    bindings = _literal_bindings(tree, excluded=allowed_retired_assignments)
    collector = _StaticStringCollector(
        allowed_retired_assignments=allowed_retired_assignments,
        bindings=bindings,
    )
    collector.visit(tree)
    return collector.values


class _StaticStringCollector(ast.NodeVisitor):
    def __init__(
        self,
        *,
        allowed_retired_assignments: frozenset[str],
        bindings: dict[str, str],
    ) -> None:
        self.allowed_retired_assignments = allowed_retired_assignments
        self.bindings = bindings
        self.values: set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        if node.targets and all(
            isinstance(target, ast.Name)
            and target.id in self.allowed_retired_assignments
            for target in node.targets
        ):
            return
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            isinstance(node.target, ast.Name)
            and node.target.id in self.allowed_retired_assignments
        ):
            return
        self.generic_visit(node)

    def generic_visit(self, node: ast.AST) -> None:
        value = _literal_string(node, self.bindings)
        if value is not None:
            self.values.add(value.lower())
        super().generic_visit(node)


def _literal_bindings(tree: ast.AST, *, excluded: frozenset[str]) -> dict[str, str]:
    assignments: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id not in excluded:
                assignments.setdefault(target.id, []).append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id not in excluded
            and node.value is not None
        ):
            assignments.setdefault(node.target.id, []).append(node.value)

    bindings: dict[str, str] = {}
    for _ in range(len(assignments)):
        added = False
        for name, value_nodes in assignments.items():
            if name in bindings:
                continue
            values = [_literal_string(value_node, bindings) for value_node in value_nodes]
            resolved = {value for value in values if value is not None}
            if values and len(resolved) == 1 and len(resolved) == len(values):
                bindings[name] = resolved.pop()
                added = True
        if not added:
            break
    return bindings


def _retired_assignment_control_violations(source: str) -> list[str]:
    """Reject use of cutover deletion constants as live control semantics."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    literal_bindings = _literal_bindings(tree, excluded=CUTOVER_RETIRED_ASSIGNMENTS)
    assignments: list[tuple[list[ast.expr], ast.expr]] = []
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            assignments.append((node.targets, node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assignments.append(([node.target], node.value))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node

    tainted = set(CUTOVER_RETIRED_ASSIGNMENTS)
    tainted_returns: set[str] = set()
    changed = True
    while changed:
        changed = False
        for function in functions.values():
            positional = [*function.args.posonlyargs, *function.args.args]
            default_parameters = (
                positional[-len(function.args.defaults) :]
                if function.args.defaults
                else []
            )
            for parameter, default in zip(
                default_parameters, function.args.defaults, strict=True
            ):
                if (
                    _expr_is_tainted(default, tainted, tainted_returns)
                    and parameter.arg not in tainted
                ):
                    tainted.add(parameter.arg)
                    changed = True
            for parameter, default in zip(
                function.args.kwonlyargs, function.args.kw_defaults, strict=True
            ):
                if (
                    default is not None
                    and _expr_is_tainted(default, tainted, tainted_returns)
                    and parameter.arg not in tainted
                ):
                    tainted.add(parameter.arg)
                    changed = True
            if function.name not in tainted_returns and any(
                isinstance(child, ast.Return)
                and child.value is not None
                and _expr_is_tainted(child.value, tainted, tainted_returns)
                for child in ast.walk(function)
            ):
                tainted_returns.add(function.name)
                changed = True
        for targets, value in assignments:
            if not _expr_is_tainted(value, tainted, tainted_returns):
                continue
            for target in targets:
                for name in _assigned_names(target):
                    if name not in tainted:
                        tainted.add(name)
                        changed = True
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            function = functions.get(node.func.id)
            if function is None:
                continue
            positional = [*function.args.posonlyargs, *function.args.args]
            position = 0
            for argument in node.args:
                if isinstance(argument, ast.Starred):
                    if _expr_is_tainted(argument.value, tainted, tainted_returns):
                        for parameter in positional[position:]:
                            if parameter.arg not in tainted:
                                tainted.add(parameter.arg)
                                changed = True
                        if (
                            function.args.vararg is not None
                            and function.args.vararg.arg not in tainted
                        ):
                            tainted.add(function.args.vararg.arg)
                            changed = True
                    position = len(positional)
                    continue
                if position < len(positional):
                    parameter = positional[position]
                    position += 1
                else:
                    parameter = function.args.vararg
                if (
                    parameter is not None
                    and _expr_is_tainted(argument, tainted, tainted_returns)
                    and parameter.arg not in tainted
                ):
                    tainted.add(parameter.arg)
                    changed = True
            parameters = {
                parameter.arg: parameter
                for parameter in [*positional, *function.args.kwonlyargs]
            }
            for keyword in node.keywords:
                if keyword.arg is None:
                    if not _expr_is_tainted(keyword.value, tainted, tainted_returns):
                        continue
                    if isinstance(keyword.value, ast.Dict):
                        for key, value in zip(
                            keyword.value.keys, keyword.value.values, strict=True
                        ):
                            name = (
                                _literal_string(key, literal_bindings)
                                if key is not None
                                else None
                            )
                            parameter = parameters.get(name or "")
                            if (
                                parameter is not None
                                and _expr_is_tainted(value, tainted, tainted_returns)
                                and parameter.arg not in tainted
                            ):
                                tainted.add(parameter.arg)
                                changed = True
                            elif (
                                parameter is None
                                and function.args.kwarg is not None
                                and _expr_is_tainted(value, tainted, tainted_returns)
                                and function.args.kwarg.arg not in tainted
                            ):
                                tainted.add(function.args.kwarg.arg)
                                changed = True
                        continue
                    for parameter in parameters.values():
                        if parameter.arg not in tainted:
                            tainted.add(parameter.arg)
                            changed = True
                    if (
                        function.args.kwarg is not None
                        and function.args.kwarg.arg not in tainted
                    ):
                        tainted.add(function.args.kwarg.arg)
                        changed = True
                    continue
                parameter = parameters.get(keyword.arg)
                if (
                    parameter is not None
                    and _expr_is_tainted(keyword.value, tainted, tainted_returns)
                    and parameter.arg not in tainted
                ):
                    tainted.add(parameter.arg)
                    changed = True
                elif (
                    parameter is None
                    and function.args.kwarg is not None
                    and _expr_is_tainted(keyword.value, tainted, tainted_returns)
                    and function.args.kwarg.arg not in tainted
                ):
                    tainted.add(function.args.kwarg.arg)
                    changed = True

    out: list[str] = []
    for targets, value in assignments:
        if not _expr_is_tainted(value, tainted, tainted_returns):
            continue
        for target in targets:
            control = _control_target(target, literal_bindings)
            if control is not None:
                out.append(f"retired deletion constant flows into {control!r}")
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in _LIVE_CONTROL_TARGETS:
            if _expr_is_tainted(node.value, tainted, tainted_returns):
                out.append(f"retired deletion constant flows into keyword {node.arg!r}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 3
            and (_literal_string(node.args[1], literal_bindings) or "").lower()
            in _LIVE_CONTROL_TARGETS
            and _expr_is_tainted(node.args[2], tainted, tainted_returns)
        ):
            control = (_literal_string(node.args[1], literal_bindings) or "").lower()
            out.append(
                "retired deletion constant flows into setattr control "
                f"{control!r}"
            )
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                control = (
                    (_literal_string(key, literal_bindings) or "").lower()
                    if key is not None
                    else ""
                )
                if (
                    control in _LIVE_CONTROL_TARGETS
                    and _expr_is_tainted(value, tainted, tainted_returns)
                ):
                    out.append(
                        "retired deletion constant flows into mapping key "
                        f"{control!r}"
                    )
        controlled: list[tuple[ast.AST, list[ast.AST]]] = []
        if isinstance(node, (ast.If, ast.While)):
            controlled.append((node.test, [*node.body, *node.orelse]))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            controlled.append((node.iter, [*node.body, *node.orelse]))
        elif isinstance(node, ast.Match):
            controlled.append(
                (node.subject, [item for case in node.cases for item in case.body])
            )
            controlled.extend(
                (case.guard, list(case.body))
                for case in node.cases
                if case.guard is not None
            )
        for condition, branches in controlled:
            if not _expr_is_tainted(condition, tainted, tainted_returns):
                continue
            controls = sorted(
                {
                    control
                    for branch in branches
                    for control in _mutated_controls(branch, literal_bindings)
                }
            )
            for control in controls:
                out.append(
                    "retired deletion constant controls mutation of "
                    f"{control!r}"
                )
    return sorted(set(out))


def _expr_uses_names(node: ast.AST, names: set[str] | frozenset[str]) -> bool:
    return any(
        isinstance(child, ast.Name)
        and isinstance(child.ctx, ast.Load)
        and child.id in names
        for child in ast.walk(node)
    )


def _expr_is_tainted(
    node: ast.AST,
    names: set[str] | frozenset[str],
    tainted_returns: set[str] | frozenset[str],
) -> bool:
    if _expr_uses_names(node, names):
        return True
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id in tainted_returns
        for child in ast.walk(node)
    )


def _assigned_names(node: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
    }


def _control_target(node: ast.AST, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name) and node.id.lower() in _LIVE_CONTROL_TARGETS:
        return node.id.lower()
    if isinstance(node, ast.Attribute) and node.attr.lower() in _LIVE_CONTROL_TARGETS:
        return node.attr.lower()
    if (
        isinstance(node, ast.Subscript)
        and (_literal_string(node.slice, bindings) or "").lower()
        in _LIVE_CONTROL_TARGETS
    ):
        return (_literal_string(node.slice, bindings) or "").lower()
    return None


def _mutated_controls(node: ast.AST, bindings: dict[str, str]) -> set[str]:
    controls: set[str] = set()
    for child in ast.walk(node):
        targets: list[ast.AST] = []
        if isinstance(child, ast.Assign):
            targets = list(child.targets)
        elif isinstance(child, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = [child.target]
        for target in targets:
            control = _control_target(target, bindings)
            if control is not None:
                controls.add(control)
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "setattr"
            and len(child.args) >= 2
        ):
            control = (_literal_string(child.args[1], bindings) or "").lower()
            if control in _LIVE_CONTROL_TARGETS:
                controls.add(control)
        elif isinstance(child, ast.Dict):
            for key in child.keys:
                if key is None:
                    continue
                control = (_literal_string(key, bindings) or "").lower()
                if control in _LIVE_CONTROL_TARGETS:
                    controls.add(control)
    return controls


def _literal_string(node: ast.AST, bindings: dict[str, str] | None = None) -> str | None:
    bindings = bindings or {}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string(node.left, bindings)
        right = _literal_string(node.right, bindings)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if (
                isinstance(value, ast.FormattedValue)
                and value.conversion in {-1, ord("s")}
                and value.format_spec is None
            ):
                rendered = _literal_string(value.value, bindings)
                if rendered is not None:
                    parts.append(rendered)
                    continue
                return None
            return None
        return "".join(parts)
    if (
        isinstance(node, ast.Call)
        and not node.keywords
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and len(node.args) == 1
    ):
        separator = _literal_string(node.func.value, bindings)
        values = node.args[0]
        if separator is None or not isinstance(values, (ast.List, ast.Tuple)):
            return None
        parts = [_literal_string(item, bindings) for item in values.elts]
        if any(part is None for part in parts):
            return None
        return separator.join(part for part in parts if part is not None)
    return None


def _contains_exact(token: str, value: str) -> bool:
    pattern = rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])"
    return re.search(pattern, value) is not None


def _contains_live_alternate_concept(token: str, value: str) -> bool:
    controls = r"(?:mode|category|lane|runtime|semantics)"
    pattern = (
        rf"(?:['\"]?{controls}['\"]?\s*[:=]\s*['\"]?"
        rf"{re.escape(token)}(?:[a-z0-9_-]*)|"
        rf"{re.escape(token)}(?:[a-z0-9_-]*[\s_-]+){controls})"
    )
    return re.search(pattern, value) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    found = violations()
    if found:
        print("\n".join(found))
        return 1
    print("single-live semantics: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
