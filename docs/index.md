# plnt docs

The complete reading order.

## Start here

1. **[README](../README.md)** — 60-second quickstart. `pip install` ->
   `plnt playground up` -> first curl.
2. **[Getting started](getting-started.md)** — the same quickstart with
   more room to breathe, plus how to swap in your own model list.
3. **[Local dev](local-dev.md)** — run the playground API and the
   plnt-site chat UI on the same laptop. CORS is pre-configured.

## Design and product

4. **[PRD — platform](PRD.md)** — why plnt exists, users, goals,
   non-goals, market context, roadmap.
5. **[PRD — playground](PRD-playground.md)** — the demo surface at
   `playground.plnt.work`, users, success metrics.
6. **[Architecture](architecture.md)** — the layered platform
   diagram, where each component lives, kind demo.
7. **[ERD](ERD.md)** — data model: entities (InferenceModel,
   RuntimeAdapter, DeployModelWorkflow, ...), sequences (happy-path
   deploy, read-path), state machine.

## Reference

8. **[API contract](api-contract.md)** — the OpenAI subset the
   playground implements. Mechanically enforced by
   [`tests/test_site_contract.py`](../tests/test_site_contract.py).
9. **[Glossary](glossary.md)** — vocabulary used across code and docs.

## Operations

10. **[DigitalOcean K8s runbook](../deploy/RUNBOOK-do-k8s.md)** — ship
    `playground.plnt.work` end-to-end. ~40 min, ~$24/mo.
11. **[Kind demo](kind-demo.md)** — 60-second local-laptop demo of the
    full deploy flow, no cloud.

## Community

12. **[Contributing](../CONTRIBUTING.md)** — dev setup, PR flow, style,
    runtime chart contract.
13. **[Roadmap](../ROADMAP.md)** — public phase-by-phase status.
14. **[Changelog](../CHANGELOG.md)** — versioned release notes.
15. **[Security policy](../SECURITY.md)** — vuln disclosure, threat model summary.
16. **[Code of conduct](../CODE_OF_CONDUCT.md)** — Contributor Covenant v2.1.

## Related repos

- **[plnt-site](../../plnt-site)** — the marketing site and docs portal at
  `plnt.work`. Owns the `/playground` UI page.
- **[plnt-cloud](../../plnt-cloud)** — a bookings product built on plnt.
  Proves the platform end-to-end.
