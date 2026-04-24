# 00 — Executive Ruling

## Soundness ruling

Zeus topology is **partially sound**. More precisely: it is **structurally sound but cognitively underfed**.

It is structurally sound because the repo already has an authority hierarchy, scoped routers, machine manifests, topology doctor lanes, graph protocol boundaries, docs registry, source/test/script manifests, and map maintenance. It is not structurally sufficient because online-only agents still cannot infer enough Zeus system knowledge from the currently durable surfaces without reading tests, packet plans, or hidden graph/archive context.

## Is topology currently too coupled to repo-health noise?

Yes. The coupling is not uniform, but it is still too high. Navigation currently behaves like a global health aggregator in too many cases. Closeout has partial scoping, but it still uses always-on lanes and issue severity instead of a principled lane/mode policy.

## Is topology_doctor over-blocking the wrong lanes?

Yes for navigation and partially for closeout. Navigation should block only when route generation, required authority reads, or requested-file scoped obligations are unusable. It should not fail simply because unrelated docs/source/history/reference drift exists. Closeout should block on changed-file and required-companion obligations, not on unrelated global health.

## Is the issue model rich enough?

No. `code`, `path`, `message`, and `severity` are not enough. Zeus needs issue metadata that can drive routing, gating, repair drafts, ownership, expiration, and mode-specific blocking.

## Is current topology good enough for online-only reasoning?

Not yet. It is usable for orientation and route discovery, but not strong online-only reasoning. The machine layer is denser than the human/agent cognition layer. Module books are a promising surface, but the current topology/graph books themselves acknowledge thinness and missing textual extraction.

## Top 10 deep problems

1. **Navigation blocks on broad repo health.** Route generation and health audit are still entangled.
2. **Closeout scoping is partial.** Changed-file lanes exist, but companion expansion and always-on lanes still create false-positive risk.
3. **Issue objects are not machine-routable.** Missing lane, scope, owner manifest, repair kind, related paths, blocking modes, maturity, confidence, and expiration.
4. **The cognition layer is underfed.** Dense Zeus knowledge remains in tests, code, packet plans, history lore, and graph artifacts.
5. **Module books are uneven.** Topology and graph books exist but are thin relative to manifest density; docs/manifests/topology-doctor/closeout books need durable expansion.
6. **Manifest ownership boundaries are blurred.** `topology.yaml`, `docs_registry.yaml`, `module_manifest.yaml`, `source_rationale.yaml`, `test_topology.yaml`, and `script_manifest.yaml` overlap without enough explicit ownership contracts.
7. **Tests carry law that docs do not.** Topology-doctor tests encode archive, graph, compiled-topology, receipt, current-state, and fixture laws that should be promoted to durable references.
8. **Graph value is under-realized for online agents.** The graph is correctly derived context, but a binary `graph.db` without committed textual sidecars is not enough for online-only reasoning.
9. **Repairability is weak.** The system reports failures but does not produce high-quality owner-manifest repair blueprints or proposed stubs.
10. **Topology doctor is too central.** Helper modules exist, but the main facade still defines policy, issue shape, mode semantics, and aggregation behavior.

## Top 10 recommended structural improvements

1. Add a **lane policy kernel**: `navigation`, `closeout`, `strict`, `global_health`, `packet_prefill`, and `context_pack` must have separate blocking semantics.
2. Replace the thin issue shape with a **typed issue model**.
3. Make navigation **advisory-first** and request-scoped.
4. Make closeout **changed-file plus companion scoped**, with global health reported but not automatically blocking.
5. Create dense module/system books for topology, graph, docs, manifests, topology_doctor, and closeout/receipts.
6. Normalize manifest ownership into a written matrix and enforce duplicate/conflict checks.
7. Promote hidden laws from tests into durable reference books before tightening tests further.
8. Add repair-draft generation with owner manifest, confidence, required human fields, and proposed commands.
9. Generate small graph-derived textual sidecars for module/context-pack use, clearly labeled derived/not-authority.
10. Split deterministic topology doctor tests from live repo-health tests.

## What to do first

Run **P0 — scope and lane repair**. Do not begin by editing every manifest or expanding every doc. First make topology_doctor distinguish route usability, scoped closeout blocking, strict global health, and advisory repo-health warnings.

## What must not be done

- Do not delete topology.
- Do not treat graph output as authority.
- Do not make archives default-read.
- Do not add more manifest rows before deciding ownership.
- Do not fix tests by weakening laws.
- Do not let navigation become a universal repo-health gate.
- Do not mix runtime/source behavior changes with topology-system reform.
- Do not turn repair drafts into auto-applied patches.
- Do not promote packet evidence into durable law without extraction.
- Do not compress module knowledge into YAML where prose cognition is required.

## Final answer

Topology is worth keeping. The next evolution is not “more YAML” or “fewer failures.” The next evolution is a typed, scoped, repairable routing kernel backed by dense reference cognition.
