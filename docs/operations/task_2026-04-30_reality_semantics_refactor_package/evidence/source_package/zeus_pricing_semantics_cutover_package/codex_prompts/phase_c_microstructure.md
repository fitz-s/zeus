# Codex Prompt — Phase C Microstructure Snapshot and CLOB Sweep

Task: pricing semantics authority cutover, Phase C only.

Goal: create real executable cost basis from token-level Polymarket CLOB facts.

Implement or identify one canonical snapshot producer for `ExecutableMarketSnapshotV2`.

Snapshot must include:

- condition/question/market identity.
- YES and NO token ids.
- selected token id and outcome label.
- active/tradable status.
- orderbook bids/asks depth.
- tick size.
- min order size.
- fee metadata.
- neg-risk metadata.
- orderbook hash.
- source timestamp.

Implement `simulate_clob_sweep`:

- BUY sweeps asks ascending.
- SELL sweeps bids descending.
- Fee per level is `shares * fee_rate * price * (1 - price)`.
- Return all-in per-share price/value plus depth status.

Do not hardcode fee_rate=0.02 except in tests. Production path must read market fee metadata.

Add tests for:

- BUY/SELL sweep math.
- insufficient depth.
- stale snapshot.
- missing fee metadata.
- tick/min-order validation.
- neg-risk mismatch.

Stop if source-routing or live credentialed CLOB calls are required.
