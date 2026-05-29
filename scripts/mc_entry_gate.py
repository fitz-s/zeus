#!/usr/bin/env python3
# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC review Preflight P0-P8 (SD6). Single go/no-go gate that
#   must pass BEFORE any full-transport Monte-Carlo rebuild. P0-P3 are CODE gates verified
#   here in-process against the REAL functions; P4-P8 are RUN gates (they execute, they are
#   not asserted) and are printed as the operator runbook.
"""MC entry gate — verify the domain-canonicality antibodies are wired before an MC rebuild.

P0  schema       model_bias_ens_v2 can hold a full canonical row (assert_model_bias_schema_ready)
P1  gate hash    current_gate_set_hash() is deterministic 16-char (the active gate generation)
P2  coverage     a CANONICAL read rejects empty coverage + out-of-scope target month (Blocker E/D)
P3  insufficiency conservative_identity_model floors residual to CONSERVATIVE_RESIDUAL_FLOOR_C,
                  zeroes correction (Blocker C) — insufficient prior is wide, never a narrow delta

P4-P8 (RUN gates — printed, executed by the operator/driver, not asserted here):
  P4  producer run on frozen STAGING source + row audit -> 100% servable
  P5  replay on A rows -> final_regen_manifest = B ∪ E ∪ A_failed (selective_refit_from_manifest)
  P6  pair-batch manifest recorded (gate_set_hash + fit-signature set + source snapshot)
  P7  scoped MC on final_regen_manifest only
  P8  post-MC row audit still passes + pair probabilities valid + Platt/identity routes explicit

Usage:
  python scripts/mc_entry_gate.py [--world-db state/zeus-world.db]
Exit 0 iff P0-P3 all PASS.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.calibration.ens_bias_repo import (  # noqa: E402
    assert_model_bias_schema_ready,
    init_ens_bias_schema,
    read_bias_model,
    write_bias_model,
)
from src.calibration.ens_bias_model import BiasPrior, LiveResidual, posterior_bias  # noqa: E402
from src.calibration.ens_error_model import (  # noqa: E402
    CONSERVATIVE_RESIDUAL_FLOOR_C,
    DEFAULT_RESIDUAL_FLOOR_C,
    conservative_identity_model,
    current_gate_set_hash,
    predictive_error_from_posterior,
)

_FAMILY = "full_transport_v1"


def _gate_p0_schema(world_db: str) -> tuple[bool, str]:
    try:
        conn = sqlite3.connect(f"file:{world_db}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return False, f"cannot open world DB {world_db}: {exc}"
    try:
        assert_model_bias_schema_ready(conn)
        return True, f"model_bias_ens_v2 schema ready ({world_db})"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    finally:
        conn.close()


def _gate_p1_hash() -> tuple[bool, str]:
    h = current_gate_set_hash()
    ok = len(h) == 16 and h == current_gate_set_hash()
    return ok, f"gate_set_hash={h}"


def _gate_p2_coverage() -> tuple[bool, str]:
    """Reader behaviour: a canonical read (gate-hash required) must reject empty coverage and
    a target month outside the covered set. Uses an isolated in-memory DB (no side effects)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_ens_bias_schema(conn)
    cur = current_gate_set_hash()
    common = dict(city="GATE", season="MAM", metric="high",
                  live_data_version="dv", prior_data_version="dvP",
                  error_model_family=_FAMILY, authority="STAGING",
                  estimator="gate", training_cutoff="2026-05-28", recorded_at="2026-05-28",
                  posterior_bias_c=0.0, posterior_sd_c=1.0, n_live=0, n_prior=9, weight_live=0.0,
                  bias_c=-1.0, bias_sd_c=1.0, residual_sd_c=2.0, heterogeneity_var_c2=0.0,
                  correction_strength=0.5, effective_bias_c=-0.5, total_residual_sd_c=2.0,
                  code_commit="gate", fit_signature_hash="gate", gate_set_hash=cur)
    # empty coverage canonical row
    write_bias_model(conn, **{**common, "coverage_months": ""})
    empty = read_bias_model(conn, city="GATE", season="MAM", metric="high",
                            live_data_version="dv", error_model_family=_FAMILY,
                            authority="STAGING", require_gate_set_hash=cur, target_month=4)
    if empty is not None:
        return False, "canonical read served an EMPTY-coverage row (Blocker E not wired)"
    # good coverage but out-of-scope month
    conn.execute("DELETE FROM model_bias_ens")
    write_bias_model(conn, **{**common, "coverage_months": "5"})
    oos = read_bias_model(conn, city="GATE", season="MAM", metric="high",
                          live_data_version="dv", error_model_family=_FAMILY,
                          authority="STAGING", require_gate_set_hash=cur, target_month=4)
    if oos is not None:
        return False, "canonical read served a row for a month outside coverage (Blocker D not wired)"
    return True, "canonical reads reject empty coverage + out-of-scope month"


def _gate_p3_insufficiency() -> tuple[bool, str]:
    prior = BiasPrior(mu_t=-3.0, v0=0.2)
    live = LiveResidual(e_bar=-3.2, n=40, sigma2=1.0)
    narrow = predictive_error_from_posterior(posterior_bias(prior, live),
                                             residual_sd_c=DEFAULT_RESIDUAL_FLOOR_C)
    ident = conservative_identity_model(narrow)
    ok = (ident.correction_strength == 0.0 and ident.effective_bias_c == 0.0
          and ident.residual_sd_c >= CONSERVATIVE_RESIDUAL_FLOOR_C
          and ident.total_residual_sd_c >= CONSERVATIVE_RESIDUAL_FLOOR_C)
    return ok, (f"identity residual_sd_c={ident.residual_sd_c:.2f} "
                f"(floor {CONSERVATIVE_RESIDUAL_FLOOR_C}), correction_strength={ident.correction_strength}")


def check_mc_entry_gates(world_db: str) -> dict:
    """Run P0-P3 and return {gate: (passed, detail)} plus 'overall'."""
    results = {
        "P0_schema_ready": _gate_p0_schema(world_db),
        "P1_gate_hash": _gate_p1_hash(),
        "P2_coverage_mandatory": _gate_p2_coverage(),
        "P3_insufficiency_wide": _gate_p3_insufficiency(),
    }
    results["overall"] = (all(v[0] for v in results.values()), "")
    return results


_P4_P8_RUNBOOK = """
P4-P8 RUN gates (execute in order on an ISOLATED STAGING DB; not asserted by this script):
  P4  python scripts/fit_full_transport_error_models.py --no-dry-run   (producer; SD5 preflight fires)
      then scripts/audit_error_model_row_reproducibility.py            -> require 100% servable
  P5  python scripts/selective_refit_from_manifest.py ...               (replay -> final_regen; this
      gate change => full reproduce branch)
  P6  pair-batch manifest auto-recorded by rebuild_calibration_pairs_v2 (zeus_meta pair_batch:<id>)
  P7  scoped MC on final_regen only
  P8  re-run row audit (still 100% servable) + verify pair probabilities + explicit Platt/identity
  Operator: run a SMALL-SAMPLE iso pass first ("rebuild on small sample to see diff improve")
  before the full STAGING reproduce.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="MC entry gate (P0-P3 code gates).")
    ap.add_argument("--world-db", default=str(_REPO / "state" / "zeus-world.db"),
                    help="Path to zeus-world.db (default: state/zeus-world.db).")
    args = ap.parse_args()

    results = check_mc_entry_gates(args.world_db)
    print("=== MC ENTRY GATE (P0-P3 code gates) ===")
    for gate, (passed, detail) in results.items():
        if gate == "overall":
            continue
        print(f"  [{'PASS' if passed else 'FAIL'}] {gate}: {detail}")
    overall = results["overall"][0]
    print(f"=== OVERALL: {'PASS — P0-P3 satisfied' if overall else 'FAIL — MC BLOCKED'} ===")
    print(_P4_P8_RUNBOOK)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
