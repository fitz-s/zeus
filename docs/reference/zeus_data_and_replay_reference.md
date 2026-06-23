# Zeus Data And Replay Reference

Status: canonical durable reference  
Authority rank: reference. `architecture/db_table_ownership.yaml`, source code, tests, DB/runtime receipts, and authority docs win on disagreement.  
See also: `docs/reference/zeus_prediction_market_quant_reference.md`.

---

## 1. Canonical DB Topology

`architecture/db_table_ownership.yaml` is the source of truth for table-to-DB ownership. Ownership is `(table, db)`, not table name alone.

| DB | Physical path | Role |
|---|---|---|
| world | `state/zeus-world.db` | world/runtime records that remain world-owned |
| forecasts | `state/zeus-forecasts.db` | observations, settlement outcomes, source runs, readiness, raw forecast artifacts, raw model forecasts, forecast posteriors |
| trade | `state/zeus_trades.db` | trade decisions, execution facts, position events/current/lots, venue commands/events, settlement commands |

Legacy shells may exist for compatibility and must remain registered as legacy/archive until removed.

---

## 2. Forecast And Source Provenance

Forecast rows must preserve physical product identity. For replacement/multi-model forecasts this includes model, provider, product id, endpoint, request URL hash, cell/coordinate/timezone/elevation identity, source cycle, and value units.

A numeric forecast value without product identity is not a durable training or live q authority.

Source roles are separate:

- settlement source;
- Day0/monitoring observation source;
- historical hourly source;
- forecast skill source;
- venue/CLOB source.

Do not collapse them because names or station codes match.

---

## 3. Posterior And q Persistence

Live q carriers must connect:

```text
forecast raw/provenance
  -> predictive distribution
  -> family Ω / topology hash
  -> q point map
  -> q_lcb/q_ucb maps
  -> posterior identity/dependency hashes
  -> decision/receipt fields
```

A q row without coherent family topology, source cycle, q band, dependency/provenance, and live eligibility is not sufficient for live admission.

---

## 4. Trade Truth

Trade/lifecycle truth belongs in the trade DB:

- venue commands/events;
- trade decisions;
- execution facts;
- position events/current/lots;
- settlement commands.

Derived JSON/status exports are projections only. DB commit must precede exports. Chain/CLOB facts outrank local projections where reconciliation detects disagreement.

---

## 5. Current Data State

Current operational data/source health belongs in:

- `docs/operations/current_data_state.md`;
- `docs/operations/current_source_validity.md`;
- runtime DB/status/source receipts.

Those surfaces must carry checked_at/evidence/freshness/expiry. This reference intentionally does not contain current per-city/source verdicts.

---

## 6. Replay / Backtest Boundary

Replay and backtest are diagnostic unless promoted through parity evidence. A valid Zeus replay must model:

1. settlement contract identity and bin topology;
2. local date, unit, metric high/low;
3. source availability at decision time;
4. forecast cycle, raw model provenance, and q/q-band as available then;
5. executable orderbook, tick, fee, depth, maker/taker/FOK behavior, and fill assumptions;
6. family-level payoff/exposure/selection;
7. command/lifecycle/settlement truth;
8. no hindsight leakage.

Backtest may evaluate strategy. It cannot authorize live behavior by itself.

---

## 7. Data Change Checklist

Before changing data/replay/schema/docs:

- update `architecture/db_table_ownership.yaml` if table ownership changes;
- preserve product/source identity for forecast rows;
- preserve high/low and local-date identity;
- preserve append-first lifecycle/command truth;
- keep replay settlement-graded and point-in-time;
- update registry/router docs when new data references become active;
- keep current facts out of durable docs.
