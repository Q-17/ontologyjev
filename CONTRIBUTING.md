# Contributing

Start with a concrete decision your agent should make, the facts it can verify, and the observation supplied by a semantic provider. Include the expected outcome and its reason.

## Run locally

Python 3.8+ is sufficient for the default demo; there are no runtime dependencies.

```sh
python -m ontology_jev serve
python -m unittest discover -s tests -v
node --test tests/test_workbench.js
node --check ontology_jev/static/app.js
```

Node 18+ is needed only for frontend development checks. No API key or local model is needed for automated tests. To install the package for development, use a virtual environment and a pip version supporting PEP 660:

```sh
python -m pip install --upgrade pip
python -m pip install -e .
```

## Keep decisions inspectable

- Keep factual constraints in deterministic code. Provider scores cannot override known ownership, linkage, or balance failures.
- Treat absent evidence as unknown. Never silently replace a failed provider request with a synthetic score.
- Record score provenance. Generated confidence, token probabilities, and calibrated estimates are different measurements.
- Make provider requests explicit. Tests should mock their transport; paid model calls are not part of CI.
- Add a focused behavioral test for a changed decision or failure mode. Avoid tests that merely mirror implementation details.
- Keep the local workbench dependency-free and usable with a keyboard and on a narrow screen.

Useful contributions include a small domain adapter, a reproducible provider comparison, or an agent-tool integration that retains the decision trace. A universal ontology runtime and actual payment execution are outside the current release.

## Report an issue

Include the command, Python version, operating system, expected outcome, and a minimal case. For a model-related issue include its provider/model and error category. Remove API keys, account information, private customer messages and local regression snapshots before posting.
