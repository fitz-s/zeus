# Gamma pending-family fixtures

# Authority basis: FDR-gate Gamma parse incident 2026-06-10

Captured LIVE from `https://gamma-api.polymarket.com/events?slug=<slug>` on
2026-06-10 for the FDR-gate Gamma-parse incident. Used by
`tests/test_gamma_pending_family_harvest.py` to prove the live Gamma response for
a real pending family parses to the exact pending-family key (i.e. the
"did not parse to pending family" verdict was a false label, not a real parse miss).

- `gamma_wuhan_2026-06-12_high.json` — slug `highest-temperature-in-wuhan-on-june-12-2026` (11 markets)
- `gamma_milan_2026-06-12_high.json` — slug `highest-temperature-in-milan-on-june-12-2026` (11 markets)
