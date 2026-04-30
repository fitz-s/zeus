# Codex Prompt — Phase 0 Authority Admission

You are working in `fitz-s/zeus` branch `plan-pre5`.

Task: pricing semantics authority cutover, Phase 0 only.

Read and obey root `AGENTS.md`. Run topology doctor before modifying anything. Do not create a new highest authority file. Do not change live/prod/config/source-routing behavior.

Goal: modify existing authority surfaces so Zeus law physically separates Epistemic belief, Microstructure CLOB facts, and Execution/Risk economics.

Required conceptual changes:

1. Root money path must include posterior belief, executable snapshot, executable cost basis, live economic FDR, cost-basis Kelly, immutable final intent, monitor/exit sell quote.
2. Architecture invariants must forbid probability/quote/cost conflation.
3. Negative constraints must forbid raw quote/VWMP/midpoint/last trade/sparse monitor vector from entering posterior or Kelly.
4. Math spec must supersede old live edge formula `edge = P_posterior - P_market` for live economic trading.
5. Execution AGENTS must replace ambiguous provide-liquidity/dynamic-jump language with explicit order policy vocabulary.

Commands to start:

```bash
python3 scripts/topology_doctor.py --navigation --task "Phase 0 pricing semantics authority cutover" --files AGENTS.md architecture/invariants.yaml architecture/negative_constraints.yaml docs/reference/zeus_math_spec.md src/strategy/AGENTS.md src/execution/AGENTS.md src/state/AGENTS.md
python3 scripts/topology_doctor.py --task-boot-profiles
```

Stop if topology forbids the authority files, if a planning lock is required but absent, or if you need to touch source behavior.

Closeout must include changed files, authority conflicts harmonized, tests/checks run, unresolved uncertainty, and confirmation no live/prod mutation occurred.
