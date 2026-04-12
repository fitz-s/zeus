# Patch 02 — MarketAnalysis full tested family

## Problem

Current `find_edges()` only appends bins when:

- point edge > 0
- bootstrap CI lower bound > 0

That means active FDR is run over an already-prefiltered positive subset.

## Required change

Keep `find_edges()` for backwards compatibility, but add a sibling method:

```python
from src.strategy.market_analysis_family_scan import scan_full_hypothesis_family

class MarketAnalysis:
    ...
    def scan_full_family(self, n_bootstrap: int | None = None):
        return scan_full_hypothesis_family(self, n_bootstrap=n_bootstrap)
```

## Why

This lets evaluator and future audits see the **complete tested family** even before the final active-cutover deletes the old path.
