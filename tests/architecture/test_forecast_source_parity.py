# Created: 2026-06-16
# Last reused or audited: 2026-07-19
# Authority basis: docs/evidence/qkernel_rebuild/spine_source_impact_and_residuals_2026-06-16.md
#   §4.3 "Add a source-parity antibody" + the spine source-divergence fix (9ee1936148:
#   _spine_multimodel_members_for_event sourcing raw_model_forecasts) + AGENTS.md
#   "Probability Authority" (mu* = T2 Bayesian precision fusion over DECORRELATED
#   providers = raw_model_forecasts, NEVER the single-model ECMWF ensemble) +
#   /tmp/exit_lane_source_fix_plan.md (exit primary belief = forecast_posteriors).
"""SOURCE-PARITY ANTIBODY — one forecast member-envelope source family across lanes.

THE DEFECT CLASS (source_divergence): a LIVE decision/belief lane reading the
single-model ``ensemble_snapshots`` (51 ``ecmwf_ens`` perturbations of ONE model)
while the strategy-of-record + the entry spine read the MULTI-MODEL deterministic
fusion ``raw_model_forecasts`` (~7-13 decorrelated providers). A 213-family
settlement audit found 0/213 ensemble-vs-multimodel member sets equal (mean
|Δμ*|=1.14°C, ensemble systematically colder) — the cold-center /
100%-buy_no-losing-book root cause. The fix repointed the live entry spine to
``raw_model_forecasts``; this antibody pins that the entry spine, the exit primary
belief authority, and the ARM-replay harness all key on the SAME source family, and
that no live-path docstring claims a validation parity the code does not enforce.

These assertions are CONTENT-based (read the files, parse the relevant callables),
not line-number-brittle. Each FAILS if its lane regresses to a divergent source or a
false-validated provenance claim.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


# ---------------------------------------------------------------------------------
# (a) The LIVE ENTRY SPINE producer sources raw_model_forecasts, NOT ensemble_snapshots.
#     The live producer block in _generate_candidate_proofs calls the multi-model
#     accessor _spine_multimodel_members_for_event, and that accessor SELECTs its
#     member VALUES from the raw_model_forecasts table (forecast_value_c), never from
#     ensemble_snapshots.members_json.
# ---------------------------------------------------------------------------------
def test_entry_spine_producer_reads_raw_model_forecasts() -> None:
    from src.engine import event_reactor_adapter as era

    # The live producer in _generate_candidate_proofs dispatches the multi-model accessor.
    producer_src = inspect.getsource(era._generate_candidate_proofs)
    assert "_spine_multimodel_members_for_event(" in producer_src, (
        "the live spine-input producer must call _spine_multimodel_members_for_event "
        "(the raw_model_forecasts accessor) — not source the ensemble envelope."
    )

    # The accessor dispatches the single shared raw-model reader. Keeping the SQL in
    # one helper prevents the forecast and Day0 lanes from drifting apart.
    accessor_src = inspect.getsource(era._spine_multimodel_members_for_event)
    assert "_raw_model_members_for_cycle(" in accessor_src, (
        "_spine_multimodel_members_for_event must call the shared raw-model reader."
    )
    reader_src = inspect.getsource(era._raw_model_members_for_cycle)
    assert '_authority_table_ref(conn, "raw_model_forecasts")' in reader_src, (
        "the shared member reader must resolve raw_model_forecasts as its source."
    )
    assert "forecast_value_c" in reader_src, (
        "the reader must SELECT the per-model forecast_value_c (raw_model_forecasts), "
        "the multi-model deterministic member values."
    )
    # The ensemble members_json must NOT be a VALUE source in the accessor. It may be
    # NAMED in the docstring (documenting the retired source), but it must never be read
    # as a column / attribute the member values are built from. AST distinguishes a real
    # read (`row.members_json` Attribute, or a `members_json` column in a SELECT string)
    # from a prose mention in the docstring (a plain Constant): assert no Attribute access
    # and no SELECT-string column named members_json.
    tree = ast.parse(accessor_src + "\n" + reader_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "members_json":
            raise AssertionError(
                "_spine_multimodel_members_for_event reads `.members_json` — the ensemble "
                "envelope must NOT be a member-value source (cold-center regression)."
            )
    sql = _select_strings(reader_src)
    assert "members_json" not in sql, (
        "no SELECT statement in the accessor may read ensemble members_json."
    )
    # The SELECT reads the multi-model forecast_value_c column; the table is bound via the
    # f-string `FROM {table_ref}` where table_ref = _authority_table_ref(..., raw_model_forecasts)
    # — asserted above. (The literal SQL constant ends at "FROM " because the table name is an
    # interpolated FormattedValue, not part of the string Constant.)
    assert "forecast_value_c" in sql and "FROM" in sql.upper(), (
        "the accessor's SELECT must read the per-model forecast_value_c from the "
        "(interpolated) raw_model_forecasts table_ref."
    )


def _select_strings(src: str) -> str:
    """Concatenate string literals that are actual SELECT statements (start with SELECT
    after stripping), excluding prose docstrings that merely contain the word FROM."""
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            stripped = node.value.strip().upper()
            if stripped.startswith("SELECT") or "\nSELECT" in ("\n" + node.value.upper()):
                # Require it to read like SQL (has FROM), not a sentence beginning "Select".
                if "FROM" in node.value.upper():
                    out.append(node.value)
    return "\n".join(out)


# ---------------------------------------------------------------------------------
# (b) The EXIT PRIMARY belief authority reads forecast_posteriors.
#     position_belief.load_replacement_belief — the freshness authority the monitor
#     consumes FIRST — keys on the forecast_posteriors table.
# ---------------------------------------------------------------------------------
def test_exit_primary_belief_reads_forecast_posteriors() -> None:
    from src.engine import position_belief as pb

    assert pb.BELIEF_SOURCE_TABLE == "forecast_posteriors", (
        "the exit primary belief authority source table must be forecast_posteriors "
        f"(got {pb.BELIEF_SOURCE_TABLE!r})."
    )
    belief_src = inspect.getsource(pb.load_replacement_belief)
    assert "forecast_posteriors" in belief_src, (
        "load_replacement_belief must read forecast_posteriors (the multi-model fused "
        "posterior, same source family as entry)."
    )
    # It must NOT have regressed to reading the ensemble envelope as the belief source.
    assert "ensemble_snapshots" not in belief_src, (
        "the exit primary belief authority must NOT read ensemble_snapshots."
    )


# ---------------------------------------------------------------------------------
# (c) The ARM-replay harness reads raw_model_forecasts.
#     scripts/qkernel_arm_replay.fresh_members_at_cycle — the validated member set the
#     live producer must reproduce — sources raw_model_forecasts.
# ---------------------------------------------------------------------------------
def test_arm_replay_harness_reads_raw_model_forecasts() -> None:
    arm_src = _read("scripts/qkernel_arm_replay.py")
    # Locate the fresh_members_at_cycle function body and assert it queries
    # raw_model_forecasts (its FROM clause), not ensemble_snapshots.
    tree = ast.parse(arm_src)
    fn = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "fresh_members_at_cycle"
        ),
        None,
    )
    assert fn is not None, "scripts/qkernel_arm_replay.py must define fresh_members_at_cycle"
    fn_src = ast.get_source_segment(arm_src, fn) or ""
    sql = _select_strings(fn_src)
    assert "raw_model_forecasts" in sql, (
        "fresh_members_at_cycle (the ARM-replay validated member source) must read "
        "raw_model_forecasts — the same multi-model source the live entry spine uses."
    )
    assert "ensemble_snapshots" not in sql, (
        "the ARM-replay harness must NOT source members from ensemble_snapshots."
    )


# ---------------------------------------------------------------------------------
# (d) No live-path docstring in qkernel_spine_bridge.py asserts a validation parity the
#     code does not enforce. The retired false-provenance claims (defect class c):
#       "ARM-replay-validated", "identical to the validated path",
#       "chain-of-record-debiased" — may appear ONLY in NEGATED/corrective form
#       (e.g. "NOT chain-of-record-debiased"). A bare POSITIVE assertion is forbidden.
# ---------------------------------------------------------------------------------
_FALSE_PROVENANCE_PHRASES = (
    "ARM-replay-validated",
    "ARM-validated",
    "identical to the validated",
    "chain-of-record-debiased",
    "chain-of-record-DEBIASED",
)


def test_no_false_validated_provenance_in_spine_bridge() -> None:
    bridge = _read("src/engine/qkernel_spine_bridge.py")
    # Whitespace-collapse so a negation that wraps across comment/docstring lines (e.g.
    # "... NOT lifted from an\n  'ARM-validated' run ...") is detectable in a flat window.
    flat = " ".join(bridge.split())
    # Negation markers that, appearing in the WINDOW BEFORE a phrase, mark it corrective.
    _NEG = ("NOT", "not", "no longer", "obsolete", "earlier", "removed", "false")
    _WINDOW = 140  # chars of preceding context to scan for a negation marker

    offenders: list[str] = []
    for phrase in _FALSE_PROVENANCE_PHRASES:
        start = 0
        while True:
            idx = flat.find(phrase, start)
            if idx == -1:
                break
            window = flat[max(0, idx - _WINDOW): idx]
            if not any(neg in window for neg in _NEG):
                offenders.append(flat[max(0, idx - 60): idx + len(phrase) + 20])
            start = idx + len(phrase)
    assert not offenders, (
        "qkernel_spine_bridge.py contains POSITIVE false-validated provenance claims "
        "(the spine center is recomputed live over a RAW raw_model_forecasts envelope; it "
        "is NOT lifted from an ARM-validated / chain-of-record-debiased run). Offending "
        "contexts:\n" + "\n".join(offenders)
    )
