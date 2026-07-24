# src/decision AGENTS — Zone K2/K3 (Decision)

Module book: none yet — `architecture/module_manifest.yaml` sets `module_book: null`.
Machine registry: `architecture/module_manifest.yaml`
Future authority: `architecture/invariants.yaml` §1-3
governs this subtree's W3/W5 disposition. It is the authority for what survives, what gets
swapped, and what gets deleted — not this file. Re-read it before touching any module here.

## WHY this zone matters

`src/decision` is the live filter-then-rank admission/sizing chain: which candidate trades,
how much, and why. `family_decision_engine.decide()` is the entry point; it is NOT a solver
(per the architecture doc §1) and is a scheduled BUILD-swap target at W3 (new `src/solve/`
module, seam at `src/engine/qkernel_spine_bridge.py:1332`). The selection machinery
(`qlcb_reliability_guard.py`, `selection_calibrator.py`) is a scheduled W5 deletion target. This package sat
unregistered in `module_manifest.yaml`/`source_rationale.yaml` until the W0.4 backfill
packet — do not let new files land here without a registry row in the same commit.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `family_decision_engine.py` | Filter-then-rank top-1 decision engine; entry point `decide()`/`_select()` | CRITICAL — selects live trades; W3 BUILD-swap target |
| `market_coherence.py` | Model-vs-market coherence veto (3 call sites in `decide()`) | HIGH — W3 deletion/inversion into re-decision router |
| `payoff_vector.py` | Edge (`q @ payoff - cost`) and sizing (robust ΔU argmax) | CRITICAL — anchors decision edge; math reused at W3 |
| `decision_receipt.py` | Typed carrier reconstructing forecast → q → route → size | HIGH — provenance for every live candidate |
| `qlcb_reliability_guard.py` | Price-blind OOF empirical reliability guard | MEDIUM — W5 deletion target (superseded by `selection_calibrator.py`) |
| `selection_calibrator.py` | Selection-aware settlement q_lcb calibrator | MEDIUM — W5 deletion target |
| `city_skill_gate.py` | Per-city historical settlement-skill admission gate | MEDIUM — disposition not confirmed in architecture doc |

## Domain rules

- `family_decision_engine.decide()` filter order is load-bearing: `direction_law_ok` ->
  `coherence_allows` -> (`edge_lcb>0` & `optimal_delta_u>0`) -> select by max total robust
  utility. Do not reorder without re-reading `docs/rebuild/consult_build_spec.md`.
- Edge is the vector dot product `q @ payoff - route.avg_cost.value`; it must not be
  reduced to a single-bin calculation.
- Selection-machinery files (`qlcb_reliability_guard.py`, `selection_calibrator.py`) must degrade
  to identity (no-op) on missing/malformed
  artifacts — never fabricate a bound or silently widen admission.
- `market_coherence.py` must never mutate the model `q`; it only blocks or (post-W3)
  reprioritizes — do not wire it into a q-adjustment path.

## Common mistakes

- Treating `family_decision_engine.py` as a reusable solver base for W3 work — the
  architecture doc verdict is BUILD (new module), not adapt this one in place.
- Adding a new file here without a same-commit row in `module_manifest.yaml` +
  `source_rationale.yaml` + `architecture/test_topology.yaml` — this package's own history
  is the counter-example to avoid repeating.
- Assuming every file here shares `family_decision_engine.py`'s W3 fate — `market_coherence.py`
  dies at W3, but the selection machinery is scheduled for W5, and `city_skill_gate.py`'s
  disposition is not stated in the architecture doc at all. Check per-file, not by directory.
