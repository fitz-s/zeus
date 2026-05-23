# Ultimate Plan — Active Truth Index

**Purpose:** Phase-complete system design, authority manifests, and settlement semantics for live rollout (April 26 — May 2, 2026). Master specification for data contracts, resolution rules, and live trading mechanics. Extensively referenced throughout codebase.

**Status:** ACTIVE. Referenced by 202 items in `architecture/`, 25 in `src/`. PRIMARY authority source for Zeus design.

## Master Design Documents (Specification Authority)

Core system design and decision records:

- **PLAN.md** — Overall system design, phase gates, and rollout timeline (master doc)
- **2026-05-01_live_alpha/** — Live alpha evidence and authority basis (see subdirectory INDEX below)

## Live Alpha Evidence (2026-05-01/)

Authority documents for data sources and live mechanics:

- **evidence/** — Supporting analysis
  - **tigge_ingest_decision_2026-05-01.md** — TIGGE data ingest authority (cited in `src/data/tigge_db_fetcher.py`)
- **\[supporting files\]** — Methodology validation and test protocols

---

**Canonical Use:** This directory is the PRIMARY authority for all system design decisions. Every major code path, registry, and policy document in `architecture/` traces back to files in this directory. Do NOT consolidate into architecture/ — this separation preserves decision traceability and phase timeline.

**Scratchpad vs. Truth:** All top-level files are canonical truth. Subdirectories may contain debate, intermediate analysis, or discarded branches; these are safe to archive when this directory exceeds 100 files, but preserve the top-level PLAN.md and 2026-05-01_live_alpha/ subdirectory.
