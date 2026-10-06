# Created: 2026-07-22
# Last reused/audited: 2026-10-06
# Authority basis: operator-directed single-live-semantics extinction pass.
"""Reject resurrection of dormant alternate-runtime concepts."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import plistlib
import re
from pathlib import Path
from xml.parsers.expat import ExpatError

import yaml


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
_RETIRED_ROLLOUT_MODE = "rollout_" + "mode"
_FORBIDDEN = (
    _PARALLEL_INACTIVE,
    _RETIRED_AUTHORITY_COLUMN,
    _RETIRED_ROLLOUT_MODE,
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
    registry_path = ROOT / "architecture/money_path_objects.yaml"
    evidence_contexts = (yaml.safe_load(registry_path.read_text()) or {}).get(
        "single_live_evidence_contexts", {}
    )
    parsed = {}
    paths = _scan_paths(root)
    paths.update(_live_reachable_excluded_python_paths(root, paths, parsed=parsed))
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
        if path.suffix.lower() == '.py':
            source, tree = _read_python(path, parsed)
        else:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = None
        scan_value = _binding_text(source, path.suffix.lower()).lower()
        if rel.parts and rel.parts[0] in _LIVE_REFERENCE_ROOTS:
            out.extend(
                f"{rel}: {item}"
                for item in (_python_excluded_reference_violations(source, _tree=tree)
                             if path.suffix.lower() == '.py' else _excluded_reference_violations(rel, source))
            )
        if path.suffix.lower() == ".py":
            out.extend(
                f"{rel}: {item}"
                for item in _identifier_concept_violations(
                    source, evidence_contexts.get(rel.as_posix(), {}), _tree=tree
                )
            )
            out.extend(f"{rel}: {item}" for item in _alternate_control_violations(
                source, evidence_contexts.get(rel.as_posix(), {}), _tree=tree))
            scan_value += "\n" + "\n".join(
                _static_python_strings(
                    source,
                    _tree=tree,
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
        for token in (() if path.suffix.lower() == ".py" else _CONCEPT_TOKENS):
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


def _concept_name(value: str) -> bool:
    return any(re.search(rf"(?:^|_){token}(?:s)?(?:_|$)", value.lower())
               for token in _CONCEPT_TOKENS)


def _evidence_use_hash(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    while not isinstance(node, ast.stmt) and node in parents:
        node = parents[node]
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def _identifier_concept_violations(source: str, declaration: dict | None = None, *, _tree=None) -> list[str]:
    try:
        tree = _tree if _tree is not None else ast.parse(source)
    except SyntaxError:
        return []
    declaration = declaration or {}
    reviewed = declaration.get("reviewed_ast_uses", {}) if declaration.get("role") else {}
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    statement_hashes = {}
    out: set[str] = set()
    for node in ast.walk(tree):
        identifier = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            identifier = node.name
        elif isinstance(node, ast.arg):
            identifier = node.arg
        elif isinstance(node, ast.Name):
            identifier = node.id
        elif isinstance(node, ast.Attribute):
            identifier = node.attr
        if identifier is None or not (identifier.startswith("literal:") or _concept_name(identifier)):
            continue
        statement = node
        while not isinstance(statement, ast.stmt) and statement in parents:
            statement = parents[statement]
        if statement not in statement_hashes:
            statement_hashes[statement] = _evidence_use_hash(statement, parents)
        if statement_hashes[statement] not in reviewed.get(identifier, ()):
            out.add(f"forbidden alternate-runtime identifier {identifier!r}: UNKNOWN evidence use")
    return sorted(out)


def _binding_text(source: str, suffix: str) -> str:
    """Prose is not a live binding; executable fences and assignments still are."""
    if suffix in {".md", ".txt"}:
        fences = re.findall(r"```[^\n]*\n(.*?)```", source, re.S)
        bindings = [line for line in source.splitlines() if re.match(
            r"\s*(?:export\s+)?[\w.\[\]'\"]*(?:mode|category|lane|runtime|semantics)\s*[:=]", line, re.I)]
        return "\n".join([*fences, *bindings])
    if suffix in {".yaml", ".yml", ".json"}:
        try:
            value = yaml.safe_load(source)
        except yaml.YAMLError:
            return source
        def fields(value):
            if isinstance(value, dict):
                return {key: (_binding_text(str(item), ".md") if key in {"why", "description", "notes"}
                              else list(item.values()) if key == "reviewed_ast_uses"
                              and isinstance(item, dict) and all(
                                  isinstance(hashes, list) and all(isinstance(digest, str)
                                  and re.fullmatch(r"[0-9a-f]{64}", digest) for digest in hashes)
                                  for hashes in item.values())
                              else fields(item)) for key, item in value.items()}
            if isinstance(value, list):
                return [fields(item) for item in value]
            return value
        return repr(fields(value))
    return source


def _alternate_control_violations(source: str, declaration=None, *, _tree=None) -> list[str]:
    try:
        tree = _tree if _tree is not None else ast.parse(source)
    except SyntaxError:
        return ["alternate-runtime control AST unavailable"]
    seeded = any(isinstance(node, ast.Constant) and isinstance(node.value, str)
                 and re.fullmatch(r"[\w-]+", node.value) and _concept_name(node.value) for node in ast.walk(tree))
    declaration = declaration or {}
    approved = set().union(*[set(items) for items in declaration.get('reviewed_ast_uses', {}).values()]) if (
        declaration.get('role') and declaration.get('proof')) else set()
    if declaration.get('role') and declaration.get('proof'):
        approved.update(declaration.get('reviewed_evidence_effects', {}))
    report_status = set(declaration.get('reviewed_report_status_uses', ())) if (
        declaration.get('role') and declaration.get('proof')) else set()
    analysis_tree = copy.deepcopy(tree) if seeded else None
    if analysis_tree is not None:
        for node in ast.walk(analysis_tree):
            if isinstance(node, ast.stmt) and not isinstance(node, (
                    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.For,
                    ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)):
                node._source_use_hash = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
    out = _projected_control_violations(_lexical_flow_tree(analysis_tree), approved, report_status) if seeded else []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            ast.unparse(base).split(".")[-1] in {"Enum", "StrEnum", "IntEnum"} for base in node.bases
        ) and any(isinstance(child, ast.Constant) and isinstance(child.value, str)
                  and _concept_name(child.value) for child in ast.walk(node)):
            out.append("alternate-runtime choice in Enum")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            options = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
            if any(option.lstrip("-").replace("-", "_") in _LIVE_CONTROL_TARGETS for option in options):
                # The projected pass follows aliased choices/default values.
                pass
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Attribute)
             and target.attr == "help" for target in node.targets):
            text = _literal_string(node.value)
            if text and any(_contains_live_alternate_concept(token, text.lower()) for token in _CONCEPT_TOKENS):
                out.append("alternate-runtime CLI modifier")
    return sorted(set(out))


def _projected_control_violations(tree: ast.AST, approved=frozenset(), report_status=frozenset()) -> list[str]:
    """Paths describe alternate values, not the whole object holding them.

    () is a scalar; ('mode',) and (0,) are selected container fields. Unknown
    projection is conservative. Function returns are evaluated with actual
    arguments and only their own lexical returns, never nested unused returns.
    """
    nodes = tuple(ast.walk(tree))
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}
    functions = {node.name: node for node in nodes
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    classes = {node.name for node in nodes if isinstance(node, ast.ClassDef)}
    assignments = {}
    writes = {}
    aliases = {}
    out = set()
    controls = _LIVE_CONTROL_TARGETS | {"probability_authority", "q_authority", "trade_authority",
                                      "state", "status", "side", "action"}

    def root(name):
        while name in aliases:
            name = aliases[name]
        return name

    def location(node):
        path = []
        while isinstance(node, (ast.Subscript, ast.Attribute)):
            key = (node.attr if isinstance(node, ast.Attribute) else
                   node.slice.value if isinstance(node.slice, ast.Constant) else '*')
            path.insert(0, key)
            node = node.value
        return (node.id, tuple(path)) if isinstance(node, ast.Name) else (None, ())

    def own_nodes(node):
        pending = list(node.body)
        while pending:
            child = pending.pop()
            yield child
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                pending.extend(ast.iter_child_nodes(child))

    returns = {name: tuple(child.value for child in own_nodes(fn)
                          if isinstance(child, ast.Return) and child.value is not None)
               for name, fn in functions.items()}
    function_refs = {name: {root(child.id) for child in ast.walk(fn) if isinstance(child, ast.Name)}
                     for name, fn in functions.items()}
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Name):
                    a, b = root(target.id), root(node.value.id)
                    if a != b:
                        aliases[a] = b
    for node in nodes:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
            node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) else []
        value = getattr(node, 'value', None)
        if value is None:
            continue
        for target in targets:
            name, path = location(target)
            if name:
                (writes if path else assignments).setdefault(root(name), []).append((path, value))

    def join(values):
        return set().union(*values) if values else set()

    def project(paths, key, node):
        if key == '*':
            if paths:
                return {('*',)}
            return set()
        return {path[1:] for path in paths if path and path[0] == key} | {
            ('*',) for path in paths if not path or path[0] == '*'}

    memo = {}
    call_mutations = {}
    active = set()
    call_active = set()
    opaque_calls = set()

    def value(node, bindings):
        if node is None:
            return set()
        context = tuple(sorted((name, tuple(sorted(paths, key=repr))) for name, paths in bindings.items()))
        cache_key = (node, context)
        if cache_key in memo:
            return memo[cache_key]
        if cache_key in active:
            return set()
        active.add(cache_key)
        result = set()
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str) and re.fullmatch(r"[\w-]+", node.value) and _concept_name(node.value):
                result = {()}
        elif isinstance(node, ast.Name):
            name = root(node.id)
            result = set(bindings.get(name, ()))
            result |= join([value(expr, bindings) for _, expr in assignments.get(name, ())])
            result |= join([{path + item for item in value(expr, bindings)}
                            for path, expr in writes.get(name, ())])
            result |= call_mutations.get(name, set())
        elif isinstance(node, ast.Dict):
            for key, expr in zip(node.keys, node.values, strict=True):
                path = key.value if isinstance(key, ast.Constant) else '*'
                result |= {(path,) + item for item in value(expr, bindings)}
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            result = join([{(index,) + item for item in value(expr, bindings)}
                           for index, expr in enumerate(node.elts)])
        elif isinstance(node, ast.Subscript):
            key = node.slice.value if isinstance(node.slice, ast.Constant) else '*'
            result = project(value(node.value, bindings), key, node)
        elif isinstance(node, ast.Attribute):
            result = project(value(node.value, bindings), node.attr, node)
        elif isinstance(node, ast.Call):
            name = root(_call_name(node.func))
            arguments = [value(arg, bindings) for arg in node.args]
            keywords = {arg.arg: value(arg.value, bindings) for arg in node.keywords}
            fn = functions.get(name)
            if fn is not None and fn not in call_active:
                call_active.add(fn)
                supplied = dict(bindings)
                params = [*fn.args.posonlyargs, *fn.args.args]
                defaults = dict(zip([param.arg for param in params[len(params) - len(fn.args.defaults):]],
                                    fn.args.defaults, strict=True)) if fn.args.defaults else {}
                for index, param in enumerate(params):
                    original = getattr(param, '_parameter_name', param.arg)
                    supplied[root(param.arg)] = (arguments[index] if index < len(arguments)
                        else keywords[original] if original in keywords else
                        project(keywords[None], original, node) if None in keywords else
                        value(defaults.get(param.arg), bindings))
                for param, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True):
                    original = getattr(param, '_parameter_name', param.arg)
                    supplied[root(param.arg)] = (keywords[original] if original in keywords else
                        project(keywords[None], original, node) if None in keywords else value(default, bindings))
                if fn.args.vararg:
                    supplied[root(fn.args.vararg.arg)] = join([{(i,) + path for path in arg}
                        for i, arg in enumerate(arguments[len(params):])])
                if fn.args.kwarg:
                    supplied[root(fn.args.kwarg.arg)] = join([{(key,) + path for path in arg}
                        for key, arg in keywords.items() if key is not None])
                if None in keywords and any(not path or path[0] == '*' for path in keywords[None]):
                    result |= {('*',)}
                supplied = {key: paths for key, paths in supplied.items() if key in function_refs[name]}
                for index, param in enumerate(params):
                    if index < len(node.args) and isinstance(node.args[index], ast.Name):
                        destination = root(node.args[index].id)
                        for path, expr in writes.get(root(param.arg), ()):
                            # A call-side write must be visible through all aliases.
                            paths = {path + item for item in value(expr, supplied)}
                            if not paths.issubset(call_mutations.get(destination, set())):
                                call_mutations.setdefault(destination, set()).update(paths)
                                memo.clear()
                for child in own_nodes(fn):
                    check(child, supplied)
                result = join([value(expr, supplied) for expr in returns[name]])
                call_active.remove(fn)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == 'get':
                key = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else '*'
                result = project(value(node.func.value, bindings), key, node)
                result |= arguments[1] if len(arguments) > 1 else set()
            elif name == 'getattr' and len(node.args) >= 2:
                key = node.args[1].value if isinstance(node.args[1], ast.Constant) else '*'
                result = project(arguments[0], key, node) | (arguments[2] if len(arguments) > 2 else set())
            elif isinstance(node.func, ast.Attribute) and node.func.attr == 'update' and isinstance(node.func.value, ast.Name):
                destination = root(node.func.value.id)
                additions = join(arguments) | join([{(key,) + path for path in arg}
                    for key, arg in keywords.items() if key is not None])
                if not additions.issubset(call_mutations.get(destination, set())):
                    call_mutations.setdefault(destination, set()).update(additions)
                    memo.clear()
                # Updating a proved dictionary is a structured mutation, not
                # an opaque selector. The written fields remain tainted.
                if additions:
                    opaque_calls.add(node)
            elif name in classes or name in {'dict', 'replace'}:
                result = arguments[0].copy() if name == 'replace' and arguments else set()
                result |= join([{(key,) + path for path in arg} if key is not None else arg
                                for key, arg in keywords.items()])
            elif name in {'str', 'float', 'int', 'bool', 'bytes', 'len', 'repr', 'list', 'tuple'}:
                result = join(arguments)
            elif isinstance(node.func, ast.Attribute) and node.func.attr in {'upper', 'lower', 'strip', 'copy'}:
                result = value(node.func.value, bindings)
            else:
                inputs = join([*arguments, *keywords.values(), value(node.func, bindings)])
                if inputs:
                    result = {('*',)}
                    if name not in {'json.dumps', 'json.loads'}:
                        opaque_calls.add(node)
        else:
            result = join([value(child, bindings) for child in ast.iter_child_nodes(node)])
        active.remove(cache_key)
        memo[cache_key] = result
        return result

    def is_control(name):
        return name.lower() in controls or bool(re.search(r'(?:^|_)(?:state|status)$', name.lower()))

    def reject(name, expr, bindings, node):
        paths = value(expr, bindings) if is_control(name) else set()
        statement = node
        while not isinstance(statement, ast.stmt) and statement in parents:
            statement = parents[statement]
        # A whole report dictionary is not a scalar state; its field writes
        # stay separately checked, and copying a field into runtime still fails.
        if name in {'status', 'state'} and paths and all(path and path[0] != '*' for path in paths):
            return
        if name == 'status' and getattr(statement, '_source_use_hash', None) in report_status:
            return
        if paths:
            out.add(f"alternate-runtime value flows into {name!r} at line {node.lineno}")

    def check(node, bindings):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                name = getattr(target, '_control_field', target.id) if isinstance(target, ast.Name) else (
                    target.attr if isinstance(target, ast.Attribute) else target.slice.value
                    if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) else '')
                reject(str(name), node.value, bindings, node)
        elif isinstance(node, ast.Dict):
            for key, expr in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant):
                    reject(str(key.value), expr, bindings, node)
        elif isinstance(node, ast.Compare):
            expressions = [node.left, *node.comparators]
            for i, expr in enumerate(expressions):
                name = _control_target(expr, {}, controls=controls)
                if name:
                    for other in expressions[:i] + expressions[i + 1:]:
                        reject(name, other, bindings, node)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                reject(kw.arg or '', kw.value, bindings, node)
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'add_argument':
                options = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
                if any(option.lstrip('-').replace('-', '_') in controls for option in options):
                    for kw in node.keywords:
                        if kw.arg in {'default', 'const', 'choices'}:
                            reject('mode', kw.value, bindings, node)
            value(node, bindings)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            value(node.value, bindings)
            if node.value in opaque_calls and getattr(node, '_source_use_hash', None) not in approved:
                out.add(f"alternate-runtime UNKNOWN opaque side effect at line {node.lineno}")
    # Resolve call-side mutations before reading sink expressions; statement
    # traversal order must not make a previously cached alias look clean.
    for node in nodes:
        if isinstance(node, ast.Call):
            value(node, {})
    for node in nodes:
        check(node, {})
    return sorted(out)


def _lexical_flow_tree(tree: ast.AST) -> ast.AST:
    """Keep equal local spellings in different functions out of one flow set."""
    class Bindings(ast.NodeTransformer):
        def __init__(self):
            self.scopes = []
            self.sequence = 0

        def bound(self, name):
            return next((scope[name] for scope in reversed(self.scopes) if name in scope), name)

        def visit_Name(self, node):
            node._control_field = node.id
            node.id = self.bound(node.id)
            return node

        def visit_FunctionDef(self, node):
            node.name = self.bound(node.name)
            node.decorator_list = [self.visit(item) for item in node.decorator_list]
            node.args.defaults = [self.visit(item) for item in node.args.defaults]
            node.args.kw_defaults = [self.visit(item) if item is not None else None
                                     for item in node.args.kw_defaults]
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                         *([node.args.vararg] if node.args.vararg else []),
                         *([node.args.kwarg] if node.args.kwarg else [])]
            local = {arg.arg for arg in arguments}
            global_names = set()
            pending = list(node.body)
            while pending:
                child = pending.pop()
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    local.add(child.name)
                    continue
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                    local.add(child.id)
                elif isinstance(child, (ast.Global, ast.Nonlocal)):
                    global_names.update(child.names)
                elif isinstance(child, ast.ExceptHandler) and child.name:
                    local.add(child.name)
                elif isinstance(child, (ast.Import, ast.ImportFrom)):
                    local.update(alias.asname or alias.name.split('.')[0] for alias in child.names)
                pending.extend(ast.iter_child_nodes(child))
            self.sequence += 1
            self.scopes.append({name: f"__scope_{self.sequence}_{name}"
                                for name in local - global_names})
            for arg in arguments:
                arg._parameter_name = arg.arg
                arg.arg = self.bound(arg.arg)
            node.body = [self.visit(item) for item in node.body]
            self.scopes.pop()
            return node

        visit_AsyncFunctionDef = visit_FunctionDef
    return Bindings().visit(tree)


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


def _python_excluded_reference_violations(source: str, *, _tree=None) -> list[str]:
    try:
        tree = _tree if _tree is not None else ast.parse(source)
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


def _read_python(path, parsed):
    if path not in parsed:
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tree = ast.Module(body=[], type_ignores=[])
        parsed[path] = source, tree
    return parsed[path]


def _live_reachable_excluded_python_paths(root: Path, paths: set[Path], *, parsed=None) -> set[Path]:
    modules = _python_modules(root)
    pending = [path for path in paths if path.suffix == ".py"]
    seen = set(pending)
    reachable: set[Path] = set()
    while pending:
        path = pending.pop()
        for imported in _imported_modules(path, root, parsed=parsed):
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


def _imported_modules(path: Path, root: Path, *, parsed=None) -> set[str]:
    try:
        _, tree = _read_python(path, {} if parsed is None else parsed)
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
    source: str, *, allowed_retired_assignments: frozenset[str] = frozenset(), _tree=None
) -> set[str]:
    """Return strings Python can construct entirely from literals in the AST."""

    try:
        tree = _tree if _tree is not None else ast.parse(source)
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

    bindings = {
        name: target
        for name, target in _path_import_aliases(tree).items()
        if name not in assignments and name not in excluded
    }
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


def _path_import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name not in {"os", "os.path", "pathlib", "posixpath"}:
                    continue
                local = item.asname or item.name.split(".", 1)[0]
                aliases[local] = item.name if item.asname else local
        elif isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            for item in node.names:
                target = f"{module}.{item.name}" if module else item.name
                if target not in {
                    "os.path",
                    "os.path.join",
                    "posixpath.join",
                } | _PATHLIB_CONSTRUCTORS:
                    continue
                aliases[item.asname or item.name] = target
    return aliases


def _retired_assignment_control_violations(
    source: str, *, _tree=None, _seeds=None, _label="retired deletion constant", _controls=None
) -> list[str]:
    """Reject use of cutover deletion constants as live control semantics."""

    try:
        tree = _tree if _tree is not None else ast.parse(source)
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

    tainted = set(CUTOVER_RETIRED_ASSIGNMENTS if _seeds is None else _seeds)
    control_fields = _LIVE_CONTROL_TARGETS if _controls is None else _controls
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
                getattr(parameter, "_parameter_name", parameter.arg): parameter
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
            control = _control_target(target, literal_bindings, controls=control_fields)
            if control is not None:
                out.append(f"retired deletion constant flows into {control!r}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            values = [node.left, *node.comparators]
            for index, value in enumerate(values):
                control = _control_target(value, literal_bindings, controls=control_fields)
                if control and any(_expr_is_tainted(other, tainted, tainted_returns)
                                   for other in values[:index] + values[index + 1:]):
                    out.append(f"retired deletion constant compared with {control!r}")
        if isinstance(node, ast.keyword) and node.arg in control_fields:
            if _expr_is_tainted(node.value, tainted, tainted_returns):
                out.append(f"retired deletion constant flows into keyword {node.arg!r}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 3
            and (_literal_string(node.args[1], literal_bindings) or "").lower()
            in control_fields
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
                    control in control_fields
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
                    for control in _mutated_controls(branch, literal_bindings, controls=control_fields)
                }
            )
            for control in controls:
                out.append(
                    "retired deletion constant controls mutation of "
                    f"{control!r}"
                )
    return sorted({item.replace("retired deletion constant", _label) for item in out})


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


def _control_target(node: ast.AST, bindings: dict[str, str], *, controls=None) -> str | None:
    controls = _LIVE_CONTROL_TARGETS if controls is None else controls
    if isinstance(node, ast.Name) and getattr(node, "_control_field", node.id).lower() in controls:
        return getattr(node, "_control_field", node.id).lower()
    if isinstance(node, ast.Attribute) and node.attr.lower() in controls:
        return node.attr.lower()
    if (
        isinstance(node, ast.Subscript)
        and (_literal_string(node.slice, bindings) or "").lower()
        in controls
    ):
        return (_literal_string(node.slice, bindings) or "").lower()
    return None


def _mutated_controls(node: ast.AST, bindings: dict[str, str], *, controls=None) -> set[str]:
    control_fields = _LIVE_CONTROL_TARGETS if controls is None else controls
    controls: set[str] = set()
    for child in ast.walk(node):
        targets: list[ast.AST] = []
        if isinstance(child, ast.Assign):
            targets = list(child.targets)
        elif isinstance(child, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = [child.target]
        for target in targets:
            control = _control_target(target, bindings, controls=control_fields)
            if control is not None:
                controls.add(control)
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "setattr"
            and len(child.args) >= 2
        ):
            control = (_literal_string(child.args[1], bindings) or "").lower()
            if control in control_fields:
                controls.add(control)
        elif isinstance(child, ast.Dict):
            for key in child.keys:
                if key is None:
                    continue
                control = (_literal_string(key, bindings) or "").lower()
                if control in control_fields:
                    controls.add(control)
    return controls


_PATHLIB_CONSTRUCTORS = frozenset(
    {
        "Path",
        "PosixPath",
        "PurePath",
        "PurePosixPath",
        "PureWindowsPath",
        "WindowsPath",
        "pathlib.Path",
        "pathlib.PosixPath",
        "pathlib.PurePath",
        "pathlib.PurePosixPath",
        "pathlib.PureWindowsPath",
        "pathlib.WindowsPath",
    }
)


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
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _literal_string(node.left, bindings)
        right = _literal_string(node.right, bindings)
        if left is not None and right is not None:
            return f"{left.rstrip('/')}/{right.lstrip('/')}"
        return None
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
        if separator is not None and isinstance(values, (ast.List, ast.Tuple)):
            parts = [_literal_string(item, bindings) for item in values.elts]
            if any(part is None for part in parts):
                return None
            return separator.join(part for part in parts if part is not None)
    if isinstance(node, ast.Call) and not node.keywords:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "joinpath":
            base = _literal_string(node.func.value, bindings)
            parts = _literal_call_parts(node.args, bindings)
            if base is not None and parts is not None:
                return "/".join(
                    part.strip("/") if index else part.rstrip("/")
                    for index, part in enumerate((base, *parts))
                )
        name = _bound_call_name(node.func, bindings)
        if name in _PATHLIB_CONSTRUCTORS and node.args:
            parts = _literal_call_parts(node.args, bindings)
            if parts is not None:
                return "/".join(
                    part.strip("/") if index else part.rstrip("/")
                    for index, part in enumerate(parts)
                )
        if name == "str" and len(node.args) == 1:
            return _literal_string(node.args[0], bindings)
        if name in {"os.path.join", "posixpath.join"} and node.args:
            parts = _literal_call_parts(node.args, bindings)
            if parts is not None:
                return "/".join(
                    part.strip("/") if index else part.rstrip("/")
                    for index, part in enumerate(parts)
                )
    return None


def _literal_call_parts(
    args: list[ast.expr],
    bindings: dict[str, str],
) -> list[str] | None:
    parts: list[str] = []
    for item in args:
        values = item.value.elts if isinstance(item, ast.Starred) and isinstance(
            item.value, (ast.List, ast.Tuple)
        ) else (item,)
        for value_node in values:
            value = _literal_string(value_node, bindings)
            if value is None:
                return None
            parts.append(value)
    return parts


def _bound_call_name(node: ast.AST, bindings: dict[str, str]) -> str:
    name = _call_name(node)
    head, separator, tail = name.partition(".")
    bound = bindings.get(head)
    if bound not in {
        "os",
        "os.path",
        "os.path.join",
        "pathlib",
        "posixpath",
        "posixpath.join",
    } | _PATHLIB_CONSTRUCTORS:
        return name
    return f"{bound}.{tail}" if separator else bound


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
