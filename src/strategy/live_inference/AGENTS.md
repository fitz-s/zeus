# Zeus Live Inference

This package owns EDLI v1 pure inference helpers. It must not perform venue
side effects, write fill truth, or bypass source truth, FDR, Kelly, RiskGuard,
or final execution intent.

Rules:
- Use `SettlementSemantics` for settlement rounding.
- Do not use orderbook events to boost or alter `q_live` in v1.
- Do not read hindsight fields such as later outcome or regret buckets.
- Keep functions pure unless a module explicitly owns an evidence ledger.
