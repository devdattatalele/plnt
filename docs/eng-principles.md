# Engineering principles

The values that show up in code review. Short enough to remember.

## 1. Plain files over stores

Config is YAML. State is CRDs, Helm releases, Temporal history,
`kubectl get events`. No SQL in plnt itself. If someone proposes a
database, the burden of proof is on them.

Why: audit trails you can `cat`, `git blame`, and `helm history` beat
opaque tables. Debuggability compounds.

## 2. One contract, N implementations

Runtimes differ in ~everything except the wire format. plnt makes them
plug into one `RuntimeAdapter` protocol so the playground doesn't care
whether you're on vLLM or SGLang. Same for Helm chart values — one
schema across chart types.

Why: the moment you have two contracts, you have three by next quarter.

## 3. Fail fast, roll back louder

Every deploy runs the saga. Smoke test is a gate, not a suggestion.
On failure, `helm rollback` is automatic and visible in the CRD status.

Why: silent failures are the enemy. A visible rollback is worth ten
green dashboards.

## 4. Boring stack

FastAPI, Helm, kopf, Temporal, cert-manager, ingress-nginx. All
first-quartile OSS with real communities. No hand-rolled anything
when a boring option exists.

Why: the interesting code is the platform, not the plumbing. Save
your novelty budget.

## 5. Tests pin contracts, not implementations

`tests/test_site_contract.py` locks the wire shapes the site consumes.
`tests/test_playground_api.py` locks the API's own behavior. Neither
inspects internals.

Why: internals change. Contracts don't. When they do, the test failing
is the point.

## 6. Ship the runbook with the feature

A deploy that doesn't have a runbook isn't shipped. The runbook is how
an unknown operator does what the author did — no tribal knowledge.

Why: the "person who wrote it" is usually not the person who runs it
at 3am.

## 7. No vibe code

No fake metrics, no placeholder features, no CLI flags that don't do
anything. If a Phase step is scaffolded but not tested, say so in the
docs. No hedged marketing verbs ("blazing fast", "massively scalable")
without measured numbers.

Why: the audience is senior engineers. Overselling gets you laughed
out of the room.

## 8. Concise over exhaustive

Docs, commit messages, code comments, chat replies — always prefer the
short version. If a README needs a table of contents, it's too long.

Why: attention is the scarce resource. Everything else is fungible.

## 9. Optimize for the reader in 6 months

That reader might be you. Write for them.

- Names that pronounce.
- Comments only for the "why."
- Commit messages that read as changelog entries.
- One idea per PR.

## 10. Origin stories don't get rewritten

The personal-runtime code under `plnt/{surface,control,execution,compute,memory}`
still builds and runs. It's the reason plnt exists. It is frozen — no
refactors, no rewrites, no "modernizations" in the name of consistency.

Why: origin stories are load-bearing artifacts. Treat them like
Version 1's museum exhibit.
