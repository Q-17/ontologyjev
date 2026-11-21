"use strict";

(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  else root.OntologyWorkbench = helpers;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function filterRows(rows, filter, query) {
    const search = String(query || "").trim().toLowerCase();
    return rows.filter((row) => {
      const matches = filter === "all" || (filter === "changed" ? row.changed : row.after.outcome === filter);
      const c = row.case;
      const text = [c.id, c.title, c.message, c.customer && c.customer.name, c.customer && c.customer.id, c.order && c.order.id, c.payment && c.payment.id, row.after.reason].filter(Boolean).join(" ").toLowerCase();
      return matches && text.includes(search);
    });
  }

  function selectVisibleId(rows, selectedId) {
    if (rows.some((row) => row.case.id === selectedId)) return selectedId;
    return rows.length ? rows[0].case.id : null;
  }

  function pythonJson(value) {
    // Escape Unicode in the inner JSON, then quote that JSON as a Python
    // string. json.loads must receive the surrogate escapes so it can combine
    // non-BMP pairs into one code point, instead of Python decoding them early.
    const asciiJson = JSON.stringify(value).replace(/[\u007f-\uffff]/g, (char) => "\\u" + char.charCodeAt(0).toString(16).padStart(4, "0"));
    return "json.loads(" + JSON.stringify(asciiJson) + ")";
  }

  function buildSdkSnippet(caseData, policy, semantic) {
    return [
      "# Run with Python from the OntologyJev repository root.",
      "# Synthetic observation; proposes an action without executing it.",
      "import json",
      "from ontology_jev import evaluate",
      "",
      "case = " + pythonJson(caseData),
      "policy = " + pythonJson(policy),
      "semantic = " + pythonJson(semantic),
      "",
      "decision = evaluate(case, policy, semantic)",
      "print(json.dumps(decision, indent=2, ensure_ascii=True))",
    ].join("\n");
  }

  return { filterRows, selectVisibleId, buildSdkSnippet };
});
