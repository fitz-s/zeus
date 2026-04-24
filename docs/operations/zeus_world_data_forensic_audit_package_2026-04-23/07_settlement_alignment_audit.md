# 07 Settlement Alignment Audit

## Ruling

Settlement evidence exists but exact Polymarket settlement replay is not supported. The populated `settlements` table is high-only, market-slug-free, and keyed by city/date. It can be used as an evidence table after filtering, not as final canonical settlement truth for a trading system.

## DB-confirmed facts

- `settlements` rows: 1,561
- `settlements_v2` rows: 0
- `market_slug` null: 1,561
- `winning_bin` null: 92
- `settlement_value` null: 49
- `temperature_metric`: high only; C rows 1,104, F rows 457
- Same city/date observation missing for settlement rows: 1 examples; aggregate no same city/date from station-match audit: 15
- Parsed station matches: 1,506 of 1,561
- Exact value matches against same-station or any same-date observation: 1,501
- Rounded value matches: 1,509

## Semantic mismatch examples

HKO rows show a critical distinction: HKO observation data can be decimal (for example 27.8 C) while settlement evidence can be an integer (for example 27 C). That is not necessarily a data error; it may reflect oracle truncation/rounding/finalization semantics. It is a failure only if downstream training treats the observation row and settlement row as identical without applying the market's settlement rule.

## Required settlement schema contract

A canonical settlement row must include:

- market identifier: slug, condition id, token ids, event id, exchange source
- city/location plus normalized station/source identity
- target local date and temperature metric (`high` or `low`)
- unit and bin definition, including inclusive/exclusive shoulders
- exact source URL/page and finalization timestamp
- revision policy and whether late revisions are ignored
- settlement value and transformed oracle value
- raw observation value and transformation rule if applicable
- provenance JSON plus payload hash
- authority and quarantine status

## Settlement verdict

`settlements` should remain as **evidence**. Canonical replay/training should use `settlements_v2` or a stronger market-settlement table only after it is populated and validated.