# Archive Registry

Status: active archive/report/evidence interface  
Authority rank: registry/index only. It does not make archive bodies authority.  
Updated: 2026-06-23

Archive, evidence, report, rebuild, and closed packet material is historical evidence only. It is not part of the default boot path and must not override source, tests, manifests, DB/runtime receipts, active authority, or current-fact pointers.

---

## 1. Default Rule

Do not default-read archive/report/evidence bodies. Use this registry to locate history when a task explicitly requires it. Label any claim derived from historical material as historical evidence.

Promote only rewritten durable lessons into active authority/reference. Do not copy dated source bodies wholesale.

---

## 2. Demotion Ledger

| Date | Old path | New path / interface | Old claimed class | Actual class after inspection | Default-read after demotion | Active replacement | Reason |
|---|---|---|---|---|---|---|---|
| 2026-06-23 | `docs/authority/replacement_final_form_2026_06_09.md` | `docs/reports/authority_history/replacement_final_form_2026_06_09.demoted.md` | authority | historical dated strategy note | false | `docs/authority/zeus_current_architecture.md`; `docs/reference/zeus_prediction_market_quant_reference.md`; `docs/reference/zeus_math_spec.md` | dated replacement probability paper carried current-tense strategy claims that must now be code/manifold anchored |
| 2026-06-23 | `docs/authority/regime_unification_2026-06-12.md` | `docs/reports/authority_history/regime_unification_2026_06_12.demoted.md` | authority | historical dated regime directive | false | `docs/authority/zeus_current_architecture.md`; `docs/authority/zeus_current_delivery.md`; `docs/reference/zeus_prediction_market_quant_reference.md` | surviving one-authority/degraded-mode law was promoted; source was dated authority pollution |
| 2026-06-23 | `docs/operations/current/**` recursive default route | registry/router classification only | operations default body | active work package / evidence body | false except current pointer files | `docs/operations/current_state.md`; `docs/operations/current_data_state.md`; `docs/operations/current_source_validity.md` | active package/evidence body must not be cold-start boot law |
| 2026-06-23 | `docs/evidence/**`, `docs/reports/**`, `docs/archive/**`, `docs/rebuild/**` | registry/router classification only | mixed evidence/history | evidence/report/archive/rebuild history | false | active authority/reference or current-fact pointer named by task | historical material remains discoverable but non-default |

---

## 3. Historical Categories

| Category | Use | Do not use for |
|---|---|---|
| Work packets | prior scope, decisions, closeout evidence | current active packet truth |
| Governance/design notes | historical rationale and rejected alternatives | present-tense authority without code/manifest backing |
| Audits/findings/investigations | repeated failure modes and risk patterns | runtime behavior claims without code/test proof |
| Migration/rebuild material | provenance for data or schema decisions | live DB mutation authority |
| Research/reports/results | evidence and hypotheses | strategy promotion by itself |
| Overlay/local scratch | explaining drift or abandoned modes | default onboarding or active law |
| Binary/mixed artifacts | provenance only after explicit handling | direct active docs authority |

---

## 4. Retrieval Decision Tree

1. Start with current law: `AGENTS.md`, `workspace_map.md`, active authority, manifests, source/tests.
2. Check canonical reference and `architecture/history_lore.yaml` for compressed durable lessons.
3. If a historical proof is still needed, locate the narrowest archive/report/evidence file here or through the quarter index.
4. Treat the material as contaminated until scanned for secrets, local-only paths, stale operating modes, and binary debris.
5. Promote only a current, rewritten lesson into active surfaces.

Stop if archive material would override current source, tests, manifests, DB/runtime truth, or active authority. That requires a new governed change, not archive lookup.

---

## 5. Promotion Checklist

Before promoting a historical lesson:

- prove the live need;
- prove consistency with current code/manifests/tests or explicitly supersede them;
- sanitize sensitive/local/binary debris;
- rewrite into compact current-tense law or reference;
- name the test, manifest, checker, runbook, or residual risk;
- update `architecture/docs_registry.yaml`, routers, and this registry.

---

## 6. Known Cold Storage Interfaces

Historical packet/archive bodies may live under:

- `docs/archive/<YYYY>-Q<N>/`;
- `docs/reports/**`;
- `docs/evidence/**`;
- `docs/rebuild/**`;
- older local-only `docs/archives/**` or bundles, when present.

Not all cold bodies are visible in a clean clone. Do not assume absence in git means the historical artifact never existed; use commit history and registry rows where necessary.

---

## 7. What Not To Do

- do not make archives default-read;
- do not copy archive bodies wholesale into active docs;
- do not promote binary/mixed/scratch artifacts into authority;
- do not let archive prose overrule current code/manifests/tests;
- do not preserve a stale authority path as a stub unless the stub clearly says archive-only and routes to active replacement.
