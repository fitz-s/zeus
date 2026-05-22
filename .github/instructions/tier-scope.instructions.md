---
applyTo: "**"
---

# Copilot review — tier scope (Zeus)

Live quant trading on Polymarket weather derivatives. Real money flows.
Review by runtime risk, not file order.

## Order

Read PR body's "AI Review Scope" first. Group changed paths into Tiers.
Exhaust Tier 0 before Tier 1. Tier 2 verifies 0/1. Tier 3 only if budget
remains.

## Tier 0 — live money / runtime safety

`src/execution/**`, `src/venue/**`, `src/main.py`,
`src/engine/{cycle_runner,evaluator,monitor_refresh}.py`,
`src/contracts/{settlement_semantics,execution_price,venue_submission_envelope,fx_classification}.py`,
`src/state/{lifecycle_manager,chain_reconciliation,db,ledger,projection,collateral_ledger,venue_command_repo,readiness_repo}.py`,
`src/riskguard/**`, `src/control/**`, `src/supervisor_api/**`,
`migrations/**`, `architecture/2026_04_02_architecture_kernel.sql`.

## Tier 1 — data / probability / persistence

`src/calibration/**`, `src/signal/**`, `src/strategy/**`, `src/data/**`,
`src/ingest/**`, `src/oracle/**`, `src/observability/**`,
`src/risk_allocator/**`, `src/types/**`, `src/runtime/**`, rest of
`src/contracts/**` and `src/state/**`.

## Tier 2 — tests

`tests/contracts/**`, `tests/test_*invariant*.py`,
`tests/test_architecture_contracts.py`, paired tests for Tier 0/1.

## Tier 3 — docs / agent surfaces

`AGENTS.md`, `.agents/**`, `.claude/**`, `.github/**`, `architecture/**`,
`docs/authority/**`, `docs/operations/current_*.md`, `docs/reference/**`.

## Skip

`.claude/worktrees/**`, `.omc/**`, `.zeus/**`, `docs/archives/**`,
`docs/artifacts/**`, `docs/operations/archive/**`, `logs/**`, `raw/**`,
`state/**`, `evidence/**`, generated/cache files.
Canonical list: `docs/review/review_scope_map.md`.

## Severity

**Critical** (block): live-money loss; venue identity error (condition_id,
token_id, YES/NO); SettlementSemantics bypass; transaction split (INV-08);
RED not cancel+sweep (INV-19); void on CHAIN_UNKNOWN (INV-18); secret
exposure; market order; `place_limit_order` outside gateway (INV-24);
V2 preflight bypass (INV-25); side effect missing `venue_commands`
(INV-28, 30); schema data loss; LLM as authority (INV-10).

**Important**: probability/economics crossing without provenance
(INV-21, 33–35); held-token quote into posterior (INV-36);
DB-before-JSON inversion (INV-17); exit-as-close (INV-01);
settlement-as-exit (INV-02); non-canonical phase string (INV-07);
`strategy_key` drift (INV-04, 22); missing relationship test;
planning-lock bypass on `architecture/**`, truth-owning `src/state/**`;
`authority="VERIFIED"` on degraded projection (INV-23);
`runtime_posture` not blocking entry (INV-26).

**Nit**: style / formatting / typos. Suppress when Critical/Important exist.

## Evidence + coverage

Every finding cites `path:line` and invariant ID. No speculation.
Mark **Uncertain** if unresolvable from diff.

**Empty findings + partial coverage ≠ clean pass** — report as partial,
list reviewed slice + unreviewed paths.

## Reporting

Per finding: `Severity | Path:line | What | Why | Fix | Evidence (INV-NN)`.
Header: `Reviewed: <by tier>; Skipped: <paths>; Coverage: full|partial; Findings: N C, N I, N N`.

Deeper: `REVIEW.md`, `docs/review/code_review.md`, `architecture/invariants.yaml`.
