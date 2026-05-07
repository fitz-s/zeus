Must navigate to detailed project instructions, navigation, authority order, and working rules at the project root `AGENTS.md` (relative to this repo root, not a filesystem-absolute path).

Zeus-specific Code Review Graph rule: follow root AGENTS.md and topology_doctor before graph tools. Use code-review-graph only after topology routing for source/test/script impact analysis. Do not let graph-first instructions bypass planning-lock, manifests, source_rationale, test_topology, script_manifest, route receipts, authority order, or canonical truth rules.

## Review behavior

For review tasks (PR review, `/review`, ultrareview, manual review session): follow root `REVIEW.md` and `docs/review/code_review.md`. Prioritize runtime-risk surfaces (Tier 0: `src/execution/**`, `src/venue/**`, `src/contracts/{settlement_semantics,execution_price,venue_submission_envelope,fx_classification}.py`, truth-owning `src/state/**`, `src/riskguard/**`, `src/control/**`, `src/supervisor_api/**`, migrations) before docs, generated files, prompt archives, or style. For large PRs, state reviewed and unreviewed surfaces explicitly; empty findings + partial coverage is not a clean pass.
