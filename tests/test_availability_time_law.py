# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md
#   Part V §ANTIBODY 1 — proof_of_possession_available_at is the ONLY producer of available_at.
"""CI antibody: every write of `available_at` / `source_available_at` must route through
`proof_of_possession_available_at` (src/contracts/availability_time.py) OR carry an
explicit inline exemption comment `# AVAIL-POSSESSION-EXEMPTED: <reason>`.

WHY THIS EXISTS
---------------
Defect C1-AVAIL-CLOCK: ecmwf_open_data_ingest.py hard-fell-back to `run_init_dt` (=
model cycle time, ~8.4 h before data is published) as `available_at`, poisoning
315,470/1,265,824 decision_certificates.  The canonical producer was created
(src/contracts/availability_time.py) and the evaluator fix was shipped.  This test
BLOCKS any future regression by failing CI as soon as a new site bypasses the
canonical function.

WHAT FAILS
----------
Any assignment `available_at = <expr>` or keyword-arg `available_at=<expr>` where
<expr> does NOT contain `proof_of_possession_available_at` AND the source line does
NOT contain the exemption comment.  Same rule for `source_available_at`.

WHAT IS ALLOWED
---------------
* Lines that call `proof_of_possession_available_at(...)` (directly or in the RHS).
* Lines carrying `# AVAIL-POSSESSION-EXEMPTED: <reason>` (must state the reason).
* Reader sites: lines where the target is only READ from, not assigned FROM, and
  whose right-hand side is a DB row lookup or attribute access — these are listed in
  READER_SITE_ALLOWLIST below.

ADDING AN EXEMPTION
-------------------
1. Prefer routing through proof_of_possession_available_at.
2. If genuinely impossible, add the exemption comment inline with a stated reason.
3. Do NOT add entries to READER_SITE_ALLOWLIST without code-review and an explicit
   comment explaining why the site is a reader, not a writer.
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

CANONICAL_FUNCTION = "proof_of_possession_available_at"
EXEMPTION_COMMENT = "AVAIL-POSSESSION-EXEMPTED"

# Target column/arg names that must route through the canonical function when WRITTEN.
TARGET_NAMES = frozenset({"available_at", "source_available_at"})

# Known reader/pass-through sites that are NOT writing a new fabricated value but
# forwarding a value already stamped elsewhere.  Entries are "relpath:lineno" strings.
# This list must NOT be used to silence actual write violations — add the exemption
# comment inline instead.
READER_SITE_ALLOWLIST: frozenset[str] = frozenset(
    {
        # calibration readers — read forecast_available_at column from DB, no stamp
        "calibration/blocked_oos.py:93",
        "calibration/effective_sample_size.py:88",
        "calibration/effective_sample_size.py:96",
        "calibration/effective_sample_size.py:187",
        # decision_natural_key.py — reads from DB row, no fabrication
        "contracts/decision_natural_key.py:136",
        # execution_intent.py — reads existing context dict, no stamp
        "contracts/execution_intent.py:843",
        "contracts/execution_intent.py:853",
        "contracts/execution_intent.py:906",
        # replacement_pipeline_files.py — forwarding already-stamped values
        "contracts/replacement_pipeline_files.py:387",
        "contracts/replacement_pipeline_files.py:389",
        "contracts/replacement_pipeline_files.py:551",
        "contracts/replacement_pipeline_files.py:555",
        # replacement_forecast_materializer.py — INTENTIONALLY has no line-number
        # entries here. Every availability write in that file routes through the
        # sanctioned wrapper _role_possession_available_at (see SANCTIONED_PRODUCERS)
        # or carries an inline `# AVAIL-POSSESSION-EXEMPTED` comment. Line-number
        # keying desynced on edits (2026-06-16); inline + sanctioned keying is
        # drift-proof and self-documenting at the site.
        # executable_forecast_reader — reading from DB rows
        "data/executable_forecast_reader.py:862",
        "data/executable_forecast_reader.py:1068",
        "data/executable_forecast_reader.py:1134",
        # replacement_forecast_bundle_reader — reading from DB row
        "data/replacement_forecast_bundle_reader.py:747",
        # replacement_forecast_go_live_report — reading from dict
        "data/replacement_forecast_go_live_report.py:795",
        # decision_events.py — reading from DB row
        "state/decision_events.py:108",
        # decision_provenance.py — reading from bundle attribute
        "contracts/decision_provenance.py:393",
        # decision_group_id.py — hardcoded test fixture constant
        "contracts/decision_group_id.py:23",
        # day0 event triggers — reading from observation row (not a forecast stamp)
        "events/triggers/day0_extreme_updated.py:51",
        "events/triggers/day0_extreme_updated.py:64",
        "events/triggers/day0_extreme_updated.py:405",
        "events/triggers/day0_extreme_updated.py:496",
        # day0 authority — reading from DB row
        "events/day0_authority.py:72",
        # training_eligibility.py — static dummy value for schema test only
        "backtest/training_eligibility.py:66",
        # harvester.py — forwarding context["available_at"] already stamped upstream
        "execution/harvester.py:825",
        # event_reactor_adapter — clock aggregation from already-stamped per-source clocks
        "engine/event_reactor_adapter.py:15109",
        # decision_kernel/compiler.py — forwarding from event_clock / evidence objects
        "decision_kernel/compiler.py:162",
        "decision_kernel/compiler.py:283",
        "decision_kernel/compiler.py:331",
        # decision_kernel/clock.py and decision_kernel/adapters/quote_adapter.py removed
        # 2026-07-08 (R0-c zero-caller corpse purge); their allowlist rows removed with them.
        # --- C1-AVAIL-CLOCK triage 2026-06-16 (timing audit Part V ANTIBODY 1) ---
        # contracts/semantic_types.py — DecisionSnapshotRef.available_at default '' is a
        #   structural sentinel (no stamped time); producers fill it. Not a fabricated value.
        "contracts/semantic_types.py:181",
        # day0_fast_obs.py — observation_available_at = feed last_receipt_time (real publication
        #   clock); falls to obs valid-time only as a conservative lower bound AND live authority
        #   is DENIED on that fallback. Reads a real receipt clock, no cycle fabrication.
        "data/day0_fast_obs.py:456",
        # ensemble_client.py — :328 None-init; :330 reads raw.get('available_at') from payload.
        "data/ensemble_client.py:328",
        "data/ensemble_client.py:330",
        # executable_forecast_reader.py — :846 reads row['available_at'] for the not-yet-available
        #   freshness check; :865 forwards row['available_at'] into the snapshot. DB-row readers.
        "data/executable_forecast_reader.py:846",
        "data/executable_forecast_reader.py:865",
        # observation_client.py — observation_available_at = feed last_receipt_time (real
        #   publication clock); obs-time fallback is a conservative lower bound. DB/feed reader.
        "data/observation_client.py:434",
        # raw_forecast_artifact_manifest.py — from_file() classmethod forwards its
        #   source_available_at param into the dataclass; the caller supplies the honest value.
        "data/raw_forecast_artifact_manifest.py:154",
        # decision_kernel/certificate.py — build_certificate() normalizes its own
        #   source_available_at param (_utc_or_none); pure passthrough of the caller's value.
        "decision_kernel/certificate.py:116",
        # decision_kernel/certificates/no_submit.py — forwards the source_available_at param
        #   (supplied by the EvidenceClock at the caller) into build_certificate. Passthrough.
        "decision_kernel/certificates/no_submit.py:38",
        # engine/event_reactor_adapter.py:6086 — EvidenceClock PREFERS the event's real
        #   available_at (_parse_utc(event.available_at)); decision_time is only the absent-value
        #   fallback. Reads an upstream-stamped event field, not a fabricated cycle.
        "engine/event_reactor_adapter.py:6086",
        # events/opportunity_event.py — :190 forwards the available_at param into OpportunityEvent;
        #   :220 reads payload.observation_available_at (the Day0 honest publication clock).
        "events/opportunity_event.py:190",
        "events/opportunity_event.py:220",
        # events/triggers/forecast_snapshot_ready.py — :250 reads the snapshot/source_run/coverage
        #   DB-row chain for the not-future freshness check; :373 builds availability from the
        #   real possession chain (fetch_time first, then captured_at) — the FORECAST-AVAIL fix;
        #   :393/:421 forward that computed value into the payload / make_opportunity_event.
        "events/triggers/forecast_snapshot_ready.py:250",
        "events/triggers/forecast_snapshot_ready.py:373",
        "events/triggers/forecast_snapshot_ready.py:393",
        "events/triggers/forecast_snapshot_ready.py:421",
        # events/triggers/market_channel_ingestor.py — available_at = payload.quote_seen_at (the
        #   real quote-observed time from the market book feed). Feed reader.
        "events/triggers/market_channel_ingestor.py:979",
        # engine/replay.py — reads row['forecast_available_at'] to build a calibration string key
        #   (not a freshness gate, not a stamp). DB-row reader.
        "engine/replay.py:1075",
    }
)

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


class _AvailAtVisitor(ast.NodeVisitor):
    """Collect every assignment / keyword-arg that WRITES a TARGET_NAME.

    Returns (lineno, node_type, name, rhs_source) for each hit.
    """

    def __init__(self, source_lines: list[str]) -> None:
        self._lines = source_lines
        self.hits: list[tuple[int, str, str, str]] = []  # (lineno, kind, name, rhs_src)

    def _record(self, lineno: int, kind: str, name: str, rhs_node: ast.expr) -> None:
        rhs_src = ast.unparse(rhs_node) if hasattr(ast, "unparse") else repr(rhs_node)
        self.hits.append((lineno, kind, name, rhs_src))

    # assignment: available_at = <expr>
    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in TARGET_NAMES:
                self._record(node.lineno, "assign", target.id, node.value)
            elif isinstance(target, ast.Attribute) and target.attr in TARGET_NAMES:
                self._record(node.lineno, "attr_assign", target.attr, node.value)
        self.generic_visit(node)

    # annotated assignment: available_at: str = <expr>
    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is None:
            self.generic_visit(node)
            return
        target = node.target
        if isinstance(target, ast.Name) and target.id in TARGET_NAMES:
            self._record(node.lineno, "ann_assign", target.id, node.value)
        elif isinstance(target, ast.Attribute) and target.attr in TARGET_NAMES:
            self._record(node.lineno, "attr_ann_assign", target.attr, node.value)
        self.generic_visit(node)

    # keyword arg in a function call: f(available_at=<expr>)
    def visit_Call(self, node: ast.Call) -> None:
        for kw in node.keywords:
            if kw.arg in TARGET_NAMES:
                self._record(kw.value.lineno, "kwarg", kw.arg, kw.value)
        self.generic_visit(node)


# Sanctioned producers: the canonical function, plus wrappers that PROVABLY route
# every value through it. `_role_possession_available_at` (materializer) returns
# `proof_of_possession_available_at(possession)` on every path, so recognizing it
# here keeps the law drift-proof — no per-line allowlist entry that silently
# desyncs when lines move (the 2026-06-16 failure mode).
SANCTIONED_PRODUCERS = (CANONICAL_FUNCTION, "_role_possession_available_at")


def _rhs_is_canonical(rhs_source: str) -> bool:
    """True when the RHS textually calls the canonical function or a sanctioned wrapper."""
    return any(producer in rhs_source for producer in SANCTIONED_PRODUCERS)


def _line_has_exemption(source_lines: list[str], lineno: int) -> bool:
    """True when the source line at lineno carries the exemption comment."""
    # lineno is 1-based
    line = source_lines[lineno - 1] if 0 < lineno <= len(source_lines) else ""
    return EXEMPTION_COMMENT in line


def _relpath(path: Path) -> str:
    """Relative path from SRC_ROOT, forward slashes."""
    return str(path.relative_to(SRC_ROOT))


def _relpath_lineno(path: Path, lineno: int) -> str:
    return f"{_relpath(path)}:{lineno}"


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_available_at_routes_through_canonical_function() -> None:
    """Every write of available_at/source_available_at must call
    proof_of_possession_available_at or carry # AVAIL-POSSESSION-EXEMPTED.

    Failures → fix the site to route through proof_of_possession_available_at
    (src/contracts/availability_time.py).  If the site is a genuine reader/passthrough,
    add it to READER_SITE_ALLOWLIST with a justification comment, or add
    # AVAIL-POSSESSION-EXEMPTED: <reason> inline.
    """
    violations: list[str] = []

    py_files = sorted(SRC_ROOT.rglob("*.py"))
    for path in py_files:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        source_lines = source.splitlines()
        visitor = _AvailAtVisitor(source_lines)
        visitor.visit(tree)

        for lineno, kind, name, rhs_src in visitor.hits:
            relkey = _relpath_lineno(path, lineno)
            if relkey in READER_SITE_ALLOWLIST:
                continue
            if _rhs_is_canonical(rhs_src):
                continue
            if _line_has_exemption(source_lines, lineno):
                continue
            violations.append(
                f"  {relkey} ({kind}): `{name} = {rhs_src}` — does not call"
                f" {CANONICAL_FUNCTION}() and lacks {EXEMPTION_COMMENT} comment"
            )

    if violations:
        joined = "\n".join(sorted(violations))
        pytest.fail(
            f"\n\n{len(violations)} availability-time law violation(s):\n\n"
            + joined
            + "\n\n"
            + textwrap.dedent(
                f"""
                FIX: route each site through proof_of_possession_available_at()
                (src/contracts/availability_time.py).  If the site is a genuine
                reader / passthrough, add it to READER_SITE_ALLOWLIST in
                tests/test_availability_time_law.py with a comment.
                See: docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md
                     Part V ANTIBODY 1 / C1-AVAIL-CLOCK.
                """
            ).strip(),
        )
