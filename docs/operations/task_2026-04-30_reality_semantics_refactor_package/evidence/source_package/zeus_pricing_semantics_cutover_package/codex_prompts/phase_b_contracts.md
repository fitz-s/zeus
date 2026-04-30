# Codex Prompt — Phase B Contracts and Import Fences

Task: pricing semantics authority cutover, Phase B only.

Goal: add minimal semantic contracts and physical import-fence tests.

Implement or adapt:

- `MarketPriorDistribution`.
- `ExecutableCostCurve`.
- `ExecutableCostBasis`.
- `ExecutableTradeHypothesis`.
- `FinalExecutionIntent` or strengthened immutable `ExecutionIntent`.
- `OrderPolicy` enum.

Rules:

- Reuse `ExecutableMarketSnapshotV2`, `ExecutionPrice`, `ExecutionIntent`, and `VenueSubmissionEnvelope`.
- Do not create a parallel venue model.
- `BinEdge` may be transitional but cannot become permanent live authority.
- Use Decimal for microstructure/final intent fields.

Add tests:

- Epistemic modules cannot import Polymarket/CLOB/orderbook/fee/token modules.
- Microstructure modules cannot import weather/calibration/posterior/Kelly modules.
- Corrected live executor cannot accept raw `BinEdge` authority.

Stop if contract changes require schema migration or runtime behavior rewrites in this phase.
