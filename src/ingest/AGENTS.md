# src/ingest AGENTS — Zone K2 (Runtime Ingest)

Module book: `docs/reference/modules/ingest.md`
Machine registry: `architecture/module_manifest.yaml`

## WHY this zone matters

Runtime ingest turns external venue/event streams into append-only Zeus truth
facts. It may observe and normalize external messages, but canonical writes must
flow through `src/state/venue_command_repo.py` APIs and must not invent command
or lifecycle states.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `polymarket_user_channel.py` | R3 M3 Polymarket authenticated user WebSocket ingestor, gap status, and U2 fact append bridge | HIGH — live venue truth ingest |
| `forecast_live_daemon.py` | Dedicated ECMWF OpenData live forecast producer scheduler | HIGH — live forecast authority producer |

## Domain rules

- WebSocket messages are venue observations, not command grammar authority.
- Forecast-live daemon owns only OpenData forecast production; it must not import
  engine, execution, riskguard, strategy, signal, venue, or trading entrypoints.
- `MATCHED/MINED/CONFIRMED/FAILED` belong to U2 trade facts, not `CommandState`.
- WS gaps must block new submits and require a future M5 reconciliation sweep before unblocking.
- REST fallback/reconciliation is evidence recovery only; it must not bypass M5 reconciliation law or mutate lifecycle phases directly.
- API credentials must never be logged or serialized into evidence payloads.

## Common mistakes

- Subscribing the user channel by token/asset ID instead of condition ID.
- Treating `MATCHED` as final fill/PnL authority instead of optimistic exposure.
- Clearing a WS gap just because the socket reconnected, without recording recovery/sweep evidence.
- Writing directly to provenance tables instead of using `venue_command_repo` append APIs.
