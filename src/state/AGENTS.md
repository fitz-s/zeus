File: src/state/AGENTS.md
Disposition: NEW
Authority basis: architecture/kernel_manifest.yaml; architecture/invariants.yaml; architecture/negative_constraints.yaml; docs/architecture/zeus_durable_architecture_spec.md; current runtime truth surfaces in src/state/db.py and src/state/portfolio.py.
Supersedes / harmonizes: ad hoc state conventions and JSON-first assumptions.
Why this file exists now: this is the most drift-prone truth zone in the repo.
Current-phase or long-lived: Long-lived.

# src/state AGENTS

This directory is the truth and transition zone.

## Current reality
- `position_events` is a real event spine
- open-position truth is still mixed
- JSON/state-object surfaces still exist as transitional runtime reality
- `position_current` is target-state, not current-state

## Required posture
- preserve truthful classification of current vs target
- do not promote JSON exports back to principal authority
- do not create new shadow persistence surfaces
- `strategy_key` remains the sole governance key

## High-risk files
- `db.py`
- `portfolio.py`
- lifecycle/projection/ledger additions
- any future canonical projection path

## Forbidden
- defaulting unknown strategy to a governance bucket
- silent fallback to legacy settlement truth when canonical truth should exist
- schema or truth-path changes without packet + rollback
