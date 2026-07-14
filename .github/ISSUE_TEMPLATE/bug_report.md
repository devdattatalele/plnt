---
name: Bug report
about: Something broke. Help us fix it.
title: "[bug] "
labels: bug
assignees: ''
---

## What happened

A clear and concise description of the observed behavior.

## What you expected

What you expected to happen instead.

## Reproduction

Minimal steps or a curl / kubectl invocation that triggers the bug.

```bash
# example
plnt playground up &
curl -s http://127.0.0.1:8080/v1/models
```

## Environment

- plnt version: `plnt --version` output
- OS: (macOS 14 / Ubuntu 22.04 / ...)
- Python: `python --version`
- Deployed on: (local / kind / DOKS / EKS / other)
- Runtime backend involved: (mock / vllm / tgi / sglang / trt-llm / n-a)

## Logs

```
paste any relevant logs (kubectl logs, uvicorn stderr, helm output)
```

## Extra context

Anything else that would help — screenshots, related issues, hypotheses.
