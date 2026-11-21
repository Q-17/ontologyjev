# Releasing 0.1.0

This is a preview release of a local decision SDK and workbench. Publish the source repository first; install instructions work directly from a source checkout, without implying that a PyPI package already exists.

## Build and check

Use a virtual environment for build tooling:

```sh
python -m pip install --upgrade pip build
python -m build
python -m unittest discover -s tests -v
node --test tests/test_workbench.js
node --check ontology_jev/static/app.js
```

Install the resulting wheel into a separate environment and run `ontology-jev demo` and `ontology-jev serve` from outside the repository. Check that the HTML, CSS and JavaScript are served from the installed package. The default demo must not require Ollama, an API key, external fonts or a frontend build.

Run `python -I scripts/check_installed.py` with that environment's interpreter for an isolated check of the installed package, its four static assets, and the 20-case comparison. This also rejects accidental imports from the source package.

## Publication material

- Description: **Inspect how policy changes affect agent decisions, with explicit object relationships, semantic observations and rule traces.**
- Suggested topics: `agents`, `ontology`, `ollama`, `jev`, `explainability`, `python`.
- Release title: **v0.1.0 — Decision workbench preview**.
- Release notes: use the 0.1.0 entry in `CHANGELOG.md`.
- Attach the wheel, source archive and checksum file produced by the build.

Keep `.ontologyjev/`, virtual environments, API keys, private case data and interpreter caches out of published artifacts. The repository's local trial reports are not included in distribution files.

The Jev adapter has mocked wire-contract coverage; live Ollama observations establish integration behavior only. This release does not claim generic ontology reasoning, Jev weight reproduction, calibrated model scores, production authorization or demonstrated accuracy gains.
