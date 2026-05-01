# Package Integrity

Status: no-reduction contract for package preservation.

## Preserved Review

- Path: `review_apr_30.md`
- Source before promotion: `.omx/context/review_apr_30.md`
- Lines: 2684
- Bytes: 177036
- SHA-256: `45fe3a06942bbb83b249315c46312ed7f21cd0015fb1ccee5919fda6c9cccda3`
- Verification file: `evidence/REVIEW_CHECKSUMS.md`

This review is preserved as full source material. It is not summarized or
excerpted in this package.

## Mirrored Source Package

- Source mirror: `evidence/source_package/zeus_pricing_semantics_cutover_package/`
- Original operations scratch source was collapsed during the 2026-04-30
  operations cleanup; the canonical in-repo copy is the source mirror above.
- Local duplicate archive after operations cleanup:
  `docs/archives/packets/zeus_pricing_semantics_cutover_package_2026-04-30/`
- Files mirrored: 28
- Markdown/source-package totals: 1935 lines, 72168 bytes
- Aggregate checksum over sorted per-file SHA-256 lines:
  `8b239401d3ebbd0d5268b3076d5dfc44cf0a7ecba2ab0652c8117bc5a70eedfc`
- Per-file checksum file: `evidence/SOURCE_PACKAGE_CHECKSUMS.md`

The mirror excludes `.DS_Store` because it is local OS metadata, not package
content. The source package's original `checksums.sha256` content is preserved
as `checksums_sha256.md` so the docs mesh does not reject a non-markdown file.

## No-Reduction Rule

Future updates may add:

- phase-specific execution plans
- behavior-lock tests
- critic/verifier findings
- closeout receipts
- supplemental source maps
- errata that explicitly point back to unchanged source material

Future updates must not:

- delete or replace `review_apr_30.md`
- replace source-package files with summaries
- relabel this package as authority or live authorization
- backfill old rows as corrected economics by package presence
- use this package to bypass topology navigation or planning lock

## Current Known Baseline From Preparation

- Corrected-semantics contracts already exist in the dirty worktree:
  `MarketPriorDistribution`, `ExecutableCostBasis`,
  `ExecutableTradeHypothesis`, and `FinalExecutionIntent`.
- Existing legacy runtime seams remain: executor limit recomputation,
  late executable reprice, monitor quote/probability coupling, and legacy
  report/backtest cohort risk.
- Focused tests observed before package landing:
  - `tests/test_market_analysis.py tests/test_executable_market_snapshot_v2.py tests/test_execution_intent_typed_slippage.py`: 97 passed.
  - `tests/test_executor.py tests/test_lifecycle.py tests/test_runtime_guards.py`: 203 passed, 1 skipped.
  - `tests/test_no_bare_float_seams.py tests/test_architecture_contracts.py`: 2 architecture-contract failures in discovery harness position materialization expectations.
