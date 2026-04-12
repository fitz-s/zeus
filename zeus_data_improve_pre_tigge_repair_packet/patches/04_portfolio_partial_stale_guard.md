# Patch 04 — portfolio partial-stale guard

## Problem

`partial_stale` is currently tolerated in the loader path, but stale open positions can still disappear if the canonical projection omits them.

## Replace the direct status check

Instead of:

```python
if snapshot.get("status") not in ("ok", "partial_stale"):
    return _load_portfolio_from_json_data(...)
```

use:

```python
from src.state.portfolio_loader_policy import choose_portfolio_truth_source

policy = choose_portfolio_truth_source(snapshot.get("status"), merge_supported=False)
if policy.source == "json_fallback":
    logger.warning("load_portfolio using JSON fallback: %s", policy.reason)
    return _load_portfolio_from_json_data(json_data, current_mode=current_mode)
```

## Later cutover

When you really have a stale-open merge surface, switch `merge_supported=True` and only then let `partial_stale` stay on the canonical path.
