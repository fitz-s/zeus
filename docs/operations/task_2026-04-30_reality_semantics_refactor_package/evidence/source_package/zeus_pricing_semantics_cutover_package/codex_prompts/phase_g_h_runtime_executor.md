# Codex Prompt — Phase G/H Runtime and Executor

Task: pricing semantics authority cutover, Phases G and H only.

Goal: move executable snapshot/cost before FDR and make executor no-recompute.

Runtime must be:

```text
forecast -> posterior -> snapshots -> cost basis -> live economic FDR -> final intent -> executor
```

Corrected runtime must not:

- mutate edge after FDR.
- mutate size after FDR without invalidating hypothesis.
- call executor with only BinEdge/EdgeContext.

Executor corrected path:

```python
execute_final_intent(intent: FinalExecutionIntent)
```

Executor may submit or reject. It may not recompute limit price.

Validate:

- token id.
- snapshot hash.
- cost basis hash.
- tick alignment.
- min order.
- fee metadata.
- neg-risk flag.
- order policy mapping.
- risk/cutover/heartbeat/collateral.

Tests:

- submitted limit equals final intent limit.
- missing final limit rejects.
- token/snapshot/cost hash mismatch rejects.
- no dynamic jump in corrected mode.
- no legacy compatibility envelope in certified live path.
