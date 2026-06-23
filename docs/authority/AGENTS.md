# docs/authority AGENTS

This directory contains durable authority law only.

It is not a holding area for packet deliverables, consult raw, PR reviews, ADR fragments, fix-pack notes, rollback doctrine, dated statistical snapshots, current operational state, or historical governance evidence.

---

## Active Authority Set

| File | Class | Default-read posture |
|---|---|---|
| `zeus_current_architecture.md` | active durable architecture law | yes for runtime/money-path work |
| `zeus_current_delivery.md` | active durable delivery/docs/change-control law | yes for docs/governance/router work |
| `zeus_change_control_constitution.md` | durable deep-governance constitution | no by default; read for governance deep work |
| `ARCHIVAL_RULES.md` | durable archival/evidence isolation law | read for demotion/archive/registry work |

No other file in this directory is active authority unless this table and `architecture/docs_registry.yaml` both say so.

---

## Required Posture

- Code, manifests, tests, deploy artifacts, DB ownership, and runtime receipts outrank prose.
- Durable law must not contain live bankroll, PID, loaded SHA, active position inventory, current packet diary, transient rejection counts, or unexpired current-fact claims.
- Current facts belong under operations current pointers with freshness/evidence/expiry semantics.
- Historical material belongs in reports/evidence/archive and must be discoverable through registry, not default boot.
- If old prose contains surviving law, promote the law into the active authority/reference file first, then demote the source.
- If behavior cannot be proven, write `unknown` or `unresolved implementation ambiguity`; do not smooth it into law.

---

## Do

- Keep this directory small enough for a cold-start agent to see the full law surface.
- Update `docs/authority/zeus_current_architecture.md` when durable semantic/runtime law changes.
- Update `docs/authority/zeus_current_delivery.md` when boot, routing, docs, registry, packet, or validation law changes.
- Update `docs/archive_registry.md` for every demotion out of authority.
- Update `architecture/docs_registry.yaml` when active authority membership changes.
- Preserve demoted history under `docs/reports/**`, `docs/evidence/**`, or `docs/archive/**`.

---

## Do Not

- Leave `task_YYYY*`, `consult*`, `review*`, `raw*`, dated branch/PR doctrine, or one-off packet claims here.
- Present old ENS/Platt/market_fusion, q_lcb_5pct, AIFS, submit-disabled, shadow-only, packet-freeze, or dated replacement papers as current live authority unless current code/manifests prove it.
- Let runbooks, operations current-state files, evidence, or reports authorize architecture.
- Quote archive material as present-tense law.

Historical architecture/design files live under `docs/reports/**`, `docs/evidence/**`, or `docs/archive/**`. They are evidence, not active law.
