# Zeus Reality-Semantics Refactor Package

Date: 2026-04-30
Status: durable package input, not implementation authority

## Purpose

This package preserves the full reality-semantics review and the pricing
semantics cutover package in tracked repo context. It exists because chat
compaction can lose the precision needed for this refactor.

The package is intentionally additive. Do not replace `review_apr_30.md` with a
summary. Do not shrink the mirrored source package. Future agents may add
phase-specific plans, receipts, critic/verifier results, and implementation
notes, but reduction of the preserved source material is out of scope.

## Contents

- `START_HERE.md` - package entrypoint and startup commands.
- `WORKFLOW.md` - phase workflow, skills, review gates, and stop conditions.
- `REFERENCED_FILES.md` - durable file reference map for source, tests, and package evidence.
- `ENGINEERING_ETHIC.md` - engineering ethics for a live quant-machine refactor.
- `review_apr_30.md` - full original April 30 review, preserved verbatim from local runtime context.
- `evidence/source_package/zeus_pricing_semantics_cutover_package/` - complete mirror of the existing pricing semantics cutover package, excluding only local OS metadata.
- `00_manifest.yml` - package identity, non-authorizations, and next-step guardrails.
- `PACKAGE_INTEGRITY.md` - counts, checksums, and no-reduction contract.
- `COMPACTION_HANDOFF.md` - high-signal handoff for future agents, with pointers back to full source files.
- `evidence/REVIEW_CHECKSUMS.md` - checksum for the preserved review.
- `evidence/SOURCE_PACKAGE_CHECKSUMS.md` - per-file checksums for the mirrored cutover package.

## Operating Contract

This package does not authorize live deploy, production DB mutation, source
routing changes, schema migration apply, config flips, strategy promotion, or
live venue submission.

Before source edits, use the package to freeze a narrow phase, then run topology
navigation and planning-lock for that exact file set. The correct first
implementation lane remains tests and authority/guardrails before runtime
rewiring.

## Core Refactor Target

Zeus must stop allowing raw price-like scalars to cross live-money boundaries
as trading authority. The durable target is physical isolation between:

1. Epistemic belief: `P_raw`, `P_cal`, posterior distributions, source and calibration trace.
2. Microstructure reality: token order books, bid/ask/depth, tick, min order, fee metadata, freshness, orderbook hash.
3. Execution and risk economics: executable hypothesis, live economic edge, FDR identity, Kelly, immutable final intent, fills, exits, reporting cohorts.

If a future agent can only read one short file after compaction, read
`COMPACTION_HANDOFF.md` first, then immediately open the preserved full review
and source package files listed there.
