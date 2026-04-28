# R1 learning note — 2026-04-27

Category: topology-routing + redemption authority split

## Learned

- Generic `settlement`/R3 packet language can route to older heartbeat or settlement-rounding profiles unless R1 has strong `settlement_commands` / `REDEEM_TX_HASHED` / `Q-FX-1` phrases.
- Redemption command durability is distinct from settlement terminalization. A redeem command may fail or require review while the settlement/lifecycle truth remains governed by harvester/canonical settlement paths.
- Legacy USDC.e payout must be preserved as a separate review classification; treating it as pUSD would silently decide Q-FX-1 in code.

## Future guidance

- Add digest regressions before adding new R3 phase profiles when phase terms overlap existing profiles.
- Keep R1 command events independent from `venue_commands`; they model settlement/redeem chain state, not CLOB order state.
- Never use successful redeem submission as proof that a position should be marked settled.
