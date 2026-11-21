# Changelog

## 0.1.0 — 2026-10-23

First preview release of the refund decision SDK and local workbench.

- Deterministic allow/review/deny decisions over eight explicit object, policy and intent checks.
- Baseline-versus-draft comparison with rule evidence, policy hashes and JSON export.
- Saved regression snapshots with original-policy replay and current-draft checks.
- Optional Jev API adapter and local Ollama adapter, with explicit errors and provider provenance.
- Browser model trials with an editable message, single explicit provider call, rule trace and live evidence export.
- Runnable SDK examples, English/Chinese guides, bundled offline static assets, wheel and source packaging.

The ontology covers one refund domain with explicit Python rules. The workbench never executes a payment. Local Ollama integration has been exercised; Jev's wire contract is tested with mocked transport, with no real Jev efficacy claim.
