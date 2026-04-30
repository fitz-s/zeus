# Phase 0/A Execution Plan

Status: active execution plan for the first source-adjacent packet.

## Objective

Prepare Zeus to safely execute the pricing/reality semantics refactor by making
the topology admission layer recognize the work, then registering the first
authority and guardrail tests before any runtime source rewiring.

## Scope

This first slice has two bounded parts.

Topology-admission files:

- `architecture/topology.yaml`
- `architecture/digest_profiles.py`
- `tests/test_digest_profile_matching.py`

Authority and guardrail files:

- `architecture/invariants.yaml`
- `architecture/negative_constraints.yaml`
- `tests/test_architecture_contracts.py`
- `tests/test_no_bare_float_seams.py`

It does not edit runtime trading code, production DB state, source routing,
schema migrations, config, live venue submission, or strategy promotion gates.

## Behavior Lock

Add digest-profile matching tests before changing the profile:

- the phrase `pricing semantics authority cutover` admits Phase 0/A authority
  and guardrail test files
- the profile blocks live/prod side-effect scope such as `state/*.db` or venue
  adapter edits

Add authority/guardrail tests before future runtime source edits:

- corrected posterior modes reject raw quote/VWMP vectors
- corrected prior modes require named `MarketPriorDistribution`
- fee-adjusted implied probability still fails Kelly/executable-cost authority
- `FinalExecutionIntent` is submit-ready without probability/quote recompute fields
- monitor bootstrap refresh uses model-only posterior mode for corrected belief

## Cleanup/Refactor Discipline

This is a prerequisite routing and guardrail repair, not a broad cleanup pass.
The smells being removed are:

- an admission-boundary gap: the package defines a real refactor lane, but
  topology falls back to `generic` and blocks all requested files
- a law-registration gap: the corrected semantics already exist in parts of the
  dirty worktree, but the first no-conflation guardrails are not yet named in
  active invariants/negative constraints

Order:

1. Add failing admission tests.
2. Add the narrow digest profile to canonical `architecture/topology.yaml`.
3. Regenerate `architecture/digest_profiles.py`.
4. Run focused topology/profile gates.
5. Rerun Phase 0/A navigation with the new profile before editing authority or
   guardrail files.
6. Add authority-law registration tests.
7. Add `INV-33` through `INV-36` and `NC-20` through `NC-23`.
8. Add corrected-semantics guardrail tests in `tests/test_no_bare_float_seams.py`.
9. Run focused contract/topology gates.

## Acceptance

- `tests/test_digest_profile_matching.py` proves the new profile admits only
  declared files.
- `python3 scripts/digest_profiles_export.py --check` passes.
- `python3 scripts/topology_doctor.py --schema` passes.
- Phase 0/A navigation for authority/guardrail files returns `admitted`.
- `architecture/invariants.yaml` registers `INV-33` through `INV-36`.
- `architecture/negative_constraints.yaml` registers `NC-20` through `NC-23`
  and links them to the new invariants.
- Focused posterior/Kelly/final-intent/monitor guardrail tests pass.
