# Contributing to plnt

Thanks for taking a look. This is a small, opinionated project — a few
contributors, no committee. Here's how we work.

## TL;DR

1. Fork, branch off `main`, make your change.
2. Run `pytest -q` and `ruff check .` — both must be green.
3. Open a PR; use the template; link the issue if there is one.
4. One approving review + green CI -> merge.

## What we welcome

- **Bug fixes** — always.
- **Runtime chart additions** (TGI / SGLang / TRT-LLM / your favourite).
- **Doc improvements** — clarity, examples, corrections.
- **Contract tests** — the more mechanical the guarantee, the better.
- **Deploy overlays** for other cloud providers (EKS / GKE / AKS /
 Rancher / k3s).
- **Bench harness** contributions (TTFT / TPOT probes, dashboards).

## What we don't want (v1)

- New heavy dependencies without a strong justification.
- New abstractions without at least two concrete users.
- Features from the [PRD's non-goals list](docs/PRD.md#5-non-goals).
- Rewrites of the personal-runtime origin-story code
 (`plnt/{surface,control,execution,compute,memory}`) — that's frozen
 as origin story.

## Dev setup

```bash
git clone https://github.com/devdattatalele/plnt && cd plnt
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Verify:

```bash
plnt playground up & # port 8080
plnt playground models # should list plnt-mock-7b
pytest -q # 15/15 must pass
ruff check . # zero errors introduced
```

Full local-dev with plnt-site: see [`docs/local-dev.md`](docs/local-dev.md).

## Code style

- **Python**: 3.11+, type hints where they help, `ruff` for lint +
 imports. Line length 100.
- **Comments**: only when the *why* is non-obvious. No what-comments,
 no "used by X" pointers.
- **Tests**: pytest, no test framework beyond that. Prefer TestClient
 over spinning up uvicorn. Contract tests live in `tests/test_*_contract.py`.
- **YAML**: 2-space indent, no tabs. Helm templates use `{{- ... -}}`
 whitespace trims where it matters for output cleanliness.
- **Commit messages**: imperative present tense, first line ≤72 chars.
 Prefix with area: `feat(playground):`, `fix(chart):`, `docs:`,
 `chore(ci):`.

## PR checklist (also in template)

- [ ] `pytest -q` green locally.
- [ ] `ruff check .` reports no new errors.
- [ ] If you changed the wire format: `tests/test_site_contract.py`
 updated **and** [`docs/api-contract.md`](docs/api-contract.md) updated.
- [ ] If you added a new runtime chart:
 [`docs/ERD.md`](docs/ERD.md) entity dictionary updated.
- [ ] If your change affects deploy: [`deploy/RUNBOOK-do-k8s.md`](deploy/RUNBOOK-do-k8s.md)
 still works end-to-end (or you note what's changed).
- [ ] If you touched runtime pod code: image builds
 (`docker build -f docker/playground-api.Dockerfile .`) and starts.

## Contract tests are load-bearing

`tests/test_site_contract.py` pins the exact wire shapes the plnt.work
site consumes. If a change breaks these tests, the site's chat panel
silently degrades to canned replies — a bad failure mode.

**If a contract change is intentional**, update the test AND the site's
`src/islands/playground/api.ts` in the same PR pair, and call it out in
the PR description. Do not disable contract tests.

## Runtime chart contract

If you're adding a new runtime chart to `plnt/charts/`, the values
contract must include at least these fields so the operator can drive
it uniformly:

```yaml
model:
 name: string # HF ref
 storageUri: string # optional
resources:
 gpu: int
replicas:
 min: int
 max: int
runtime:
 image: string
 args: list[string]
service:
 port: int # OpenAI-compat port
```

## Release process

1. Bump `plnt.__version__` and the chart `appVersion`s in `Chart.yaml`.
2. Update [`CHANGELOG.md`](CHANGELOG.md) — move Unreleased to a dated version.
3. Tag: `git tag -a v0.X.Y -m "release v0.X.Y"`.
4. Push: `git push origin main --tags`.
5. GitHub Release from the tag, paste the CHANGELOG entry.
6. Docker image: `docker buildx build --platform linux/amd64 --push
 -t ghcr.io/devdattatalele/plnt-playground-api:v0.X.Y .`.

## Security

Please do NOT open a public issue for security bugs. See
[`SECURITY.md`](SECURITY.md).

## Code of conduct

By participating you agree to [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## Questions

- Bug or feature: [open an issue](https://github.com/devdattatalele/plnt/issues/new/choose).
- General discussion: no forum yet — start a
 [Discussion](https://github.com/devdattatalele/plnt/discussions) if you'd
 like one seeded.
- Contact: `bonde.sagar@gmail.com`.
