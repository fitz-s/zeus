# src/analysis AGENTS

Zone: K4 — Extension (analysis utilities)

## What this code does (and WHY)

Placeholder for analysis utilities. Currently empty (`__init__.py` only). Future analysis code that doesn't fit in K3 strategy or K4 observability goes here.

## Domain rules

- K4 zone — no planning lock required
- Analysis code is DERIVED — it may read truth surfaces but never write to them
- May import from K3 and below, never from K0/K1/K2 internals

## References
- Root rules: `../../AGENTS.md`
