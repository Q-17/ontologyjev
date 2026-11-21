"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const helpers = window.OntologyWorkbench;
  const state = { data: null, initialDraft: null, selectedId: null, filter: "all", search: "", revision: 0, pending: false, saving: false, compareTimer: null, controller: null, toastTimer: null, dialogRevision: 0, focusCase: null, livePending: false };
  const fields = { refund_window_days: $("window-input"), max_auto_refund_cents: $("amount-input"), semantic_threshold: $("threshold-input") };
  const money = (cents) => typeof cents === "number" && Number.isFinite(cents) ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cents / 100) : "Unknown";
  const pretty = (value) => value === undefined ? "Missing" : value === null ? "Unknown" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  const titleCase = (value) => String(value || "unknown").replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase());
  const allowedOutcomes = new Set(["allow", "review", "deny", "pass", "fail", "unknown"]);

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function badge(outcome) {
    const normalized = allowedOutcomes.has(outcome) ? outcome : "unknown";
    return el("span", "badge " + normalized, titleCase(normalized));
  }

  async function request(path, options = {}) {
    const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
    let result;
    try { result = await response.json(); } catch (_) { throw new Error("The server returned an unreadable response. Check the local server and try again."); }
    if (!response.ok) throw new Error(result.error || "Request failed (" + response.status + ").");
    return result;
  }

  function toast(message, isError = false) {
    window.clearTimeout(state.toastTimer);
    $("toast-message").textContent = message;
    $("toast").classList.toggle("error", isError);
    $("toast").querySelector(".toast-icon").textContent = isError ? "!" : "✓";
    $("toast").hidden = false;
    state.toastTimer = window.setTimeout(() => { $("toast").hidden = true; }, isError ? 7000 : 4200);
  }

  function setCompareStatus(message, status = "ready") {
    $("compare-status").textContent = message;
    $("compare-indicator").className = "compare-indicator" + (status === "ready" ? "" : " " + status);
  }

  function updateActions() {
    $("export-button").disabled = !state.data || state.pending;
    $("reset-button").disabled = !state.data;
    const live = $("try-model-button");
    live.disabled = !state.data || state.pending || state.livePending || !filteredRows().some((row) => row.case.id === state.selectedId);
    live.textContent = state.livePending ? "Model request running…" : "Try a model";
    live.title = state.livePending ? "Wait for the current model request to finish." : state.pending ? "Finish a valid draft comparison first." : "Try the visible selected case with one live model request.";
    const save = $("save-regression-button");
    if (save) save.disabled = state.pending || state.saving || !state.selectedId;
    document.querySelectorAll("[data-example]").forEach((button) => { button.disabled = !state.data; });
    const draftRun = $("run-draft-regressions");
    if (draftRun) draftRun.disabled = state.pending || !state.data || draftRun.dataset.running === "true" || draftRun.dataset.empty === "true";
  }

  function fillPolicy(policy) {
    fields.refund_window_days.value = policy.refund_window_days;
    fields.max_auto_refund_cents.value = (policy.max_auto_refund_cents / 100).toFixed(2);
    fields.semantic_threshold.value = Number(policy.semantic_threshold).toFixed(2);
    markPolicyFields();
  }

  function readPolicy() {
    for (const input of Object.values(fields)) {
      if (input.value.trim() === "" || !Number.isFinite(Number(input.value))) throw new Error("Complete all three policy fields to compare decisions.");
    }
    const days = Number(fields.refund_window_days.value);
    const dollars = Number(fields.max_auto_refund_cents.value);
    const threshold = Number(fields.semantic_threshold.value);
    if (!Number.isInteger(days) || days < 0 || days > 365) throw new Error("Refund window must be a whole number from 0 to 365 days.");
    if (dollars < 0 || dollars > 10000 || Math.abs(dollars * 100 - Math.round(dollars * 100)) > 0.00001) throw new Error("Auto-refund ceiling must be from $0.00 to $10,000.00, with at most two decimal places.");
    if (threshold < 0.5 || threshold > 1) throw new Error("Intent threshold must be between 0.50 and 1.00.");
    return { version: "draft", refund_window_days: days, max_auto_refund_cents: Math.round(dollars * 100), semantic_threshold: threshold };
  }

  function markPolicyFields() {
    if (!state.data) return;
    const baseline = state.data.baseline_policy;
    fields.refund_window_days.parentElement.classList.toggle("edited", Number(fields.refund_window_days.value) !== baseline.refund_window_days);
    fields.max_auto_refund_cents.parentElement.classList.toggle("edited", Math.round(Number(fields.max_auto_refund_cents.value) * 100) !== baseline.max_auto_refund_cents);
    fields.semantic_threshold.parentElement.classList.toggle("edited", Number(fields.semantic_threshold.value) !== baseline.semantic_threshold);
  }

  function scheduleCompare(immediate = false) {
    window.clearTimeout(state.compareTimer);
    if (state.controller) state.controller.abort();
    const revision = ++state.revision;
    state.pending = true;
    updateActions();
    markPolicyFields();
    let policy;
    try {
      policy = readPolicy();
      $("policy-error").hidden = true;
    } catch (error) {
      $("policy-error").textContent = error.message;
      $("policy-error").hidden = false;
      setCompareStatus("Check policy values", "error");
      return;
    }
    setCompareStatus("Comparing draft…", "busy");
    state.compareTimer = window.setTimeout(() => compare(policy, revision), immediate ? 0 : 350);
  }

  async function compare(policy, revision) {
    const controller = new AbortController();
    state.controller = controller;
    try {
      const data = await request("/api/compare", { method: "POST", body: JSON.stringify({ policy }), signal: controller.signal });
      if (revision !== state.revision) return;
      state.data = data;
      state.pending = false;
      renderWorkspace();
      setCompareStatus("Draft comparison up to date");
      if (state.focusCase) {
        const selectedRow = Array.from($("case-rows").children).find((item) => item.dataset.caseId === state.focusCase);
        if (selectedRow) { selectedRow.focus({ preventScroll: true }); selectedRow.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
        state.focusCase = null;
      }
    } catch (error) {
      if (error.name === "AbortError" || revision !== state.revision) return;
      $("policy-error").textContent = error.message + " The table still shows the last successful comparison.";
      $("policy-error").hidden = false;
      setCompareStatus("Comparison could not update", "error");
    } finally {
      if (revision === state.revision) updateActions();
    }
  }

  function renderWorkspace() {
    const data = state.data;
    const summary = data.summary;
    $("stat-total").textContent = summary.total;
    $("stat-changed").textContent = summary.changed;
    $("stat-change-percent").textContent = (summary.total ? Math.round(summary.changed / summary.total * 100) : 0) + "% of cases";
    $("stat-allow").textContent = summary.allowed;
    $("stat-review").textContent = summary.review;
    $("stat-deny").textContent = summary.denied;
    $("case-count").textContent = String(summary.total).padStart(2, "0");
    $("changed-filter-count").textContent = summary.changed;
    $("regression-count").textContent = data.regression_count === undefined ? "0" : data.regression_count;
    $("baseline-version").textContent = data.baseline_policy.version;
    $("window-note").textContent = "Baseline: " + data.baseline_policy.refund_window_days + " days";
    $("amount-note").textContent = "Baseline: " + money(data.baseline_policy.max_auto_refund_cents);
    $("threshold-note").textContent = "Baseline: " + Number(data.baseline_policy.semantic_threshold).toFixed(2);
    if (!data.rows.some((row) => row.case.id === state.selectedId)) {
      const preferred = data.rows.find((row) => row.changed) || data.rows[0];
      state.selectedId = preferred ? preferred.case.id : null;
    }
    renderVisibleSelection();
    markPolicyFields();
    updateActions();
  }

  function filteredRows() {
    return state.data ? helpers.filterRows(state.data.rows, state.filter, state.search) : [];
  }

  function renderVisibleSelection() {
    state.selectedId = helpers.selectVisibleId(filteredRows(), state.selectedId);
    renderRows();
    renderInspection();
    updateActions();
  }

  function renderRows() {
    const rows = filteredRows();
    const fragment = document.createDocumentFragment();
    rows.forEach((row) => {
      const tr = el("tr", "case-row" + (row.changed ? " changed" : "") + (row.case.id === state.selectedId ? " selected" : ""));
      tr.tabIndex = 0;
      tr.setAttribute("aria-selected", String(row.case.id === state.selectedId));
      tr.setAttribute("aria-label", row.case.title + ": " + row.before.outcome + " to " + row.after.outcome + ". Select to inspect.");
      tr.dataset.caseId = row.case.id;
      const nameCell = el("td");
      nameCell.append(el("span", "case-name", row.case.title));
      const meta = el("div", "case-meta");
      meta.append(el("span", "case-id", row.case.id), el("span", "", "·"), el("span", "", row.case.customer && row.case.customer.name || "Customer unknown"));
      nameCell.append(meta);
      const before = el("td"); before.append(badge(row.before.outcome));
      const arrow = el("td", "row-arrow" + (row.changed ? " changed" : ""), "→"); arrow.setAttribute("aria-hidden", "true");
      const after = el("td"); after.append(badge(row.after.outcome));
      tr.append(nameCell, before, arrow, after, el("td", "row-amount", money(row.case.requested_amount_cents)));
      const select = () => { state.selectedId = row.case.id; renderRows(); renderInspection(); };
      tr.addEventListener("click", select);
      tr.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault(); select();
          const newRow = Array.from($("case-rows").children).find((item) => item.dataset.caseId === row.case.id);
          if (newRow) newRow.focus({ preventScroll: true });
        }
      });
      fragment.append(tr);
    });
    $("case-rows").replaceChildren(fragment);
    $("table-empty").hidden = rows.length > 0;
    if (!rows.length) {
      $("table-empty").querySelector("strong").textContent = "No cases match this view";
      $("table-empty").querySelector("p").textContent = "Try another filter or a different search term.";
    }
    $("table-result-count").textContent = rows.length + " of " + (state.data ? state.data.rows.length : 0) + " cases";
  }

  function entityCard(kind, main, identifier, icon) {
    const card = el("div", "entity-card");
    const type = el("div", "entity-type"); type.append(el("span", "entity-icon", icon), document.createTextNode(kind));
    card.append(type, el("div", "entity-name", main || "Missing object"), el("div", "entity-id", identifier || "ID unknown"));
    return card;
  }

  function relationshipLine(leftLabel, left, rightLabel, right) {
    const line = el("div", "relationship-line");
    const known = left != null && right != null;
    const matches = known && left === right;
    line.classList.toggle("invalid", known && !matches);
    line.classList.toggle("unknown", !known);
    line.append(document.createTextNode(leftLabel + " "), el("strong", "", pretty(left)), document.createTextNode(!known ? " ? " : matches ? " = " : " ≠ "), el("strong", "", pretty(right)), document.createTextNode(" " + rightLabel));
    return line;
  }

  function renderInspection() {
    const row = state.data && state.data.rows.find((item) => item.case.id === state.selectedId);
    $("inspection-placeholder").hidden = !!row;
    $("case-inspection").hidden = !row;
    if (!row) {
      $("case-inspection").replaceChildren();
      delete $("case-inspection").dataset.caseId;
      $("inspection-index").textContent = "—";
      $("inspection-placeholder").querySelector("p").textContent = state.data ? "No visible case. Change the filter or search to inspect a decision." : "Select a case to follow its decision.";
      return;
    }
    const c = row.case;
    const after = row.after;
    const customer = c.customer || {};
    const order = c.order || {};
    const payment = c.payment || {};
    const previousId = $("case-inspection").dataset.caseId;
    const priorOpen = new Set();
    if (previousId === c.id) $("case-inspection").querySelectorAll("details[open]").forEach((detail) => priorOpen.add(detail.dataset.ruleId));
    $("inspection-index").textContent = String(state.data.rows.indexOf(row) + 1).padStart(2, "0") + " / " + String(state.data.rows.length).padStart(2, "0");
    const fragment = document.createDocumentFragment();
    const overview = el("div", "detail-overview");
    const titleRow = el("div", "detail-title-row");
    const titleGroup = el("div"); titleGroup.append(el("h3", "detail-title", c.title), el("div", "detail-id", c.id + " · DRAFT DECISION"));
    titleRow.append(titleGroup, badge(after.outcome));
    const message = el("blockquote", "detail-message", c.message || "No customer message supplied.");
    const action = el("div", "decision-action");
    const save = el("button", "text-button save-regression", "+ Save regression");
    save.id = "save-regression-button"; save.type = "button";
    save.addEventListener("click", () => saveRegression(row));
    action.append(el("span", "action-label", after.action), save);
    overview.append(titleRow, message, el("p", "decision-reason", after.reason), action);
    fragment.append(overview);

    const relations = el("section", "detail-section");
    const relationsHead = el("div", "detail-section-head"); relationsHead.append(el("h3", "", "Object relationships"), el("span", "", "EXPLICIT FACTS"));
    const flow = el("div", "relationship-flow");
    flow.append(entityCard("CUSTOMER", customer.name, customer.id, "◉"), el("span", "entity-arrow", "→"), entityCard("ORDER", order.age_days == null ? "Age unknown" : order.age_days + " days old", order.id, "▤"), el("span", "entity-arrow", "→"), entityCard("PAYMENT", money(payment.paid_cents), payment.id, "▱"));
    const assertions = el("div", "relationship-assertions");
    assertions.append(relationshipLine("customer.id", customer.id, "order.customer_id", order.customer_id), relationshipLine("order.payment_id", order.payment_id, "payment.id", payment.id), relationshipLine("order.id", order.id, "payment.order_id", payment.order_id));
    const semantic = after.semantic || c.semantic || {};
    const readout = el("div", "semantic-readout");
    const semanticDescription = el("div");
    semanticDescription.append(el("div", "semantic-label", "Refund intent observation"), el("div", "semantic-subtitle", pretty(semantic.source) + " · " + pretty(semantic.model)));
    const probability = el("div", "semantic-probability", typeof semantic.refund_request === "number" ? semantic.refund_request.toFixed(2) : "?");
    readout.append(semanticDescription, probability);
    relations.append(relationsHead, flow, assertions, readout);
    fragment.append(relations);

    const rules = Array.isArray(after.rules) ? after.rules : [];
    const ruleSection = el("section", "detail-section rule-section");
    const ruleHead = el("div", "detail-section-head");
    const counts = el("span", "rule-counts");
    const passed = rules.filter((rule) => rule.status === "pass").length;
    counts.append(el("span", "", passed + " PASS"));
    if (rules.length > passed) counts.append(el("span", "rule-issues", (rules.length - passed) + " TO INSPECT"));
    ruleHead.append(el("h3", "", "Rule trace"), counts);
    const trace = el("div", "rule-trace");
    rules.forEach((rule, index) => {
      const details = el("details", "rule-item"); details.dataset.ruleId = rule.id;
      const diff = (row.rule_diffs || []).find((item) => item.id === rule.id);
      const beforeRule = (row.before.rules || []).find((item) => item.id === rule.id);
      details.open = previousId === c.id ? priorOpen.has(rule.id) || !!diff : rule.status !== "pass" || !!diff || (row.changed_rules || []).includes(rule.id);
      const summary = el("summary");
      summary.append(el("span", "rule-number", String(index + 1).padStart(2, "0")), el("span", "rule-label", rule.label || titleCase(rule.id)), badge(rule.status), el("span", "rule-chevron", "›"));
      const body = el("div", "rule-body");
      const observations = el("div", "rule-observations");
      const observed = el("div"); observed.append(el("span", "observation-label", "OBSERVED"), el("code", "observation-value", pretty(rule.observed)));
      const baselineExpected = el("div"); baselineExpected.append(el("span", "observation-label", "BASELINE EXPECTATION"), el("code", "observation-value", pretty(beforeRule && beforeRule.expected)));
      const draftExpected = el("div"); draftExpected.append(el("span", "observation-label", "DRAFT EXPECTATION"), el("code", "observation-value", pretty(rule.expected)));
      observations.classList.add("three-observations");
      observations.append(observed, baselineExpected, draftExpected);
      const paths = el("div", "source-paths");
      (Array.isArray(rule.source_paths) ? rule.source_paths : []).forEach((path) => paths.append(el("code", "source-path", path)));
      body.append(el("p", "rule-reason", rule.reason), observations, paths);
      if (diff) {
        details.classList.add("rule-diff");
        body.append(el("p", "rule-diff-note", diff.status_changed ? "Rule status: " + titleCase(diff.before.status) + " → " + titleCase(diff.after.status) : "Rule evidence changed; status remains " + titleCase(rule.status) + "."));
      } else if ((row.changed_rules || []).includes(rule.id) && beforeRule) {
        body.append(el("p", "rule-diff-note", "Rule status: " + titleCase(beforeRule.status) + " → " + titleCase(rule.status)));
      }
      details.append(summary, body); trace.append(details);
    });
    ruleSection.append(ruleHead, trace); fragment.append(ruleSection);
    const provenance = el("div", "detail-provenance");
    const policyHash = typeof after.policy_hash === "string" ? after.policy_hash : "unavailable";
    const hash = el("span", "", "POLICY " + policyHash.slice(0, 12)); hash.title = policyHash;
    provenance.append(hash, el("span", "", "FIXTURE OBSERVATION · PROPOSAL ONLY"));
    fragment.append(provenance);
    $("case-inspection").replaceChildren(fragment);
    $("case-inspection").dataset.caseId = c.id;
    updateActions();
  }

  async function saveRegression(row) {
    if (state.pending || state.saving || !filteredRows().some((item) => item.case.id === row.case.id) || row.case.id !== state.selectedId) return;
    state.saving = true; updateActions();
    const button = $("save-regression-button");
    if (button) button.textContent = "Saving…";
    try {
      const result = await request("/api/regressions", { method: "POST", body: JSON.stringify({ case_id: row.case.id, policy: state.data.candidate_policy, expected_outcome: row.after.outcome }) });
      state.data.regression_count = result.count;
      $("regression-count").textContent = result.count;
      toast("Regression saved · " + row.case.id + " expects " + row.after.outcome);
    } catch (error) { toast(error.message, true); }
    finally {
      state.saving = false;
      const currentButton = $("save-regression-button");
      if (currentButton) currentButton.textContent = "+ Save regression";
      updateActions();
    }
  }

  function exportComparison() {
    if (!state.data || state.pending) return;
    const payload = { exported_at: new Date().toISOString(), evidence_note: "Synthetic fixture observations; not live model results or efficacy evidence. All actions are proposals only.", ...state.data };
    const blob = new Blob([JSON.stringify(payload, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = el("a"); link.href = url; link.download = "ontologyjev-comparison-" + new Date().toISOString().slice(0, 10) + ".json";
    document.body.append(link); link.click(); link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("Comparison exported with policies, facts, and rule evidence.");
  }

  function openDialog(title, kicker) {
    state.dialogRevision += 1;
    $("dialog-title").textContent = title;
    $("dialog-kicker").textContent = kicker || "ONTOLOGYJEV";
    $("dialog-body").replaceChildren();
    if (!$("workspace-dialog").open) $("workspace-dialog").showModal();
    return $("dialog-body");
  }

  function closeDialog() {
    state.dialogRevision += 1;
    if (state.livePending && $("live-form")) toast("Dialog closed. The model request may continue on the server; its result will be discarded.");
    $("workspace-dialog").close();
  }

  function policySummary(policy) {
    return policy.refund_window_days + " days · " + money(policy.max_auto_refund_cents) + " ceiling · intent score ≥ " + policy.semantic_threshold;
  }

  function addEvidenceDetails(container, title, evidence) {
    const details = el("details", "live-evidence");
    const pre = el("pre"); pre.append(el("code", "", JSON.stringify(evidence, null, 2)));
    details.append(el("summary", "", title), pre);
    container.append(details);
  }

  function renderLiveResult(container, result, submitted) {
    const semantic = result.semantic;
    const local = semantic.source === "ollama";
    container.append(el("h3", "", "Live observation"));
    const readout = el("div", "semantic-readout live-readout");
    const description = el("div");
    description.append(el("div", "semantic-label", local ? "Generated intent score · uncalibrated" : "Refund intent observation"), el("div", "semantic-subtitle", pretty(semantic.source) + " · " + pretty(semantic.model)));
    readout.append(description, el("div", "semantic-probability", typeof semantic.refund_request === "number" ? semantic.refund_request.toFixed(2) : "?"));
    container.append(readout);
    if (local) container.append(el("p", "live-caption", "This is a model-generated score, not a calibrated probability. Both policies below use this same observation."));
    else container.append(el("p", "live-caption", "Both policies below use this same live observation."));
    if (semantic.explanation) container.append(el("p", "live-explanation", semantic.explanation));
    container.append(el("h3", "", submitted.case.id + " · " + submitted.case.title), el("blockquote", "detail-message live-message-snapshot", submitted.message));
    container.append(el("p", "live-caption", "Submitted draft: " + policySummary(submitted.policy) + "."));
    const outcomes = el("div", "regression-outcome-pair live-outcomes");
    const baseline = el("div"); baseline.append(el("span", "", "Baseline"), badge(result.baseline_decision.outcome));
    const draft = el("div"); draft.append(el("span", "", "Draft"), badge(result.decision.outcome));
    outcomes.append(baseline, el("span", "live-outcome-arrow", "→"), draft);
    container.append(outcomes, el("p", "decision-reason", "Baseline: " + result.baseline_decision.reason), el("p", "decision-reason", "Draft: " + result.decision.reason));
    const trace = el("div", "rule-trace live-rule-trace");
    (Array.isArray(result.decision.rules) ? result.decision.rules : []).forEach((rule, index) => {
      const before = (result.baseline_decision.rules || []).find((item) => item.id === rule.id) || {};
      const details = el("details", "rule-item");
      const summary = el("summary");
      summary.append(el("span", "rule-number", String(index + 1).padStart(2, "0")), el("span", "rule-label", rule.label || titleCase(rule.id)), badge(rule.status), el("span", "rule-chevron", "›"));
      const body = el("div", "rule-body");
      body.append(el("p", "rule-reason", rule.reason), el("p", "rule-diff-note", "Baseline: " + titleCase(before.status) + " → Draft: " + titleCase(rule.status)));
      const observations = el("div", "rule-observations three-observations");
      [["OBSERVED", rule.observed], ["BASELINE EXPECTATION", before.expected], ["DRAFT EXPECTATION", rule.expected]].forEach(([label, value]) => {
        const item = el("div"); item.append(el("span", "observation-label", label), el("code", "observation-value", pretty(value))); observations.append(item);
      });
      const paths = el("div", "source-paths");
      (Array.isArray(rule.source_paths) ? rule.source_paths : []).forEach((path) => paths.append(el("code", "source-path", path)));
      body.append(observations, paths); details.append(summary, body); trace.append(details);
    });
    container.append(el("h3", "", "Draft rule trace"), trace);
    addEvidenceDetails(container, "Submitted case and policies", { case: submitted.case, baseline_policy: submitted.baseline_policy, policy: submitted.policy });
    addEvidenceDetails(container, "Complete provider evidence", semantic);
    addEvidenceDetails(container, "Complete baseline and draft decisions", { baseline: result.baseline_decision, draft: result.decision });
  }

  function showLive() {
    const row = filteredRows().find((item) => item.case.id === state.selectedId);
    if (!row || state.pending || state.livePending) return;
    try { readPolicy(); } catch (error) { toast(error.message, true); return; }
    const caseSnapshot = JSON.parse(JSON.stringify(row.case));
    delete caseSnapshot.semantic;
    const policySnapshot = { ...state.data.candidate_policy };
    const baselineSnapshot = { ...state.data.baseline_policy };
    const providerInfo = state.data.provider || {};
    const body = openDialog("Try a model", "ONE LIVE OBSERVATION");
    const revision = state.dialogRevision;
    const workspaceRevision = state.revision;
    body.append(el("p", "dialog-lead", "Send one customer message to a model, then inspect how the baseline and your completed draft use its observation. Every action remains a proposal."));
    const context = el("div", "dialog-callout live-context");
    context.append(el("strong", "", caseSnapshot.id + " · " + caseSnapshot.title), el("p", "", "Draft: " + policySummary(policySnapshot) + "."));
    body.append(context);
    const form = el("form", "live-form"); form.id = "live-form"; form.noValidate = true;
    const provider = el("select"); provider.id = "live-provider"; provider.name = "provider";
    [["ollama", "Ollama · local"], ["jev", "Jev · API"]].forEach(([value, label]) => { const option = el("option", "", label); option.value = value; provider.append(option); });
    provider.value = "ollama";
    const model = el("input"); model.id = "live-model"; model.name = "model"; model.type = "text"; model.maxLength = 120; model.required = true; model.autocomplete = "off"; model.spellcheck = false;
    const models = { ollama: providerInfo.ollama_model || "qwen3.5:9b", jev: "jev-latest" };
    model.value = models.ollama;
    let currentProvider = "ollama";
    const providerHint = el("p", "live-caption"); providerHint.id = "live-provider-hint";
    providerHint.setAttribute("aria-live", "polite");
    provider.setAttribute("aria-describedby", providerHint.id); model.setAttribute("aria-describedby", providerHint.id);
    const controls = el("div", "live-model-fields");
    [["Provider", provider], ["Model", model]].forEach(([text, input]) => { const field = el("div", "live-field"); const label = el("label", "", text); label.setAttribute("for", input.id); field.append(label, input); controls.append(field); });
    const messageField = el("div", "live-field");
    const messageLabel = el("label", "", "Customer message"); messageLabel.setAttribute("for", "live-message");
    const message = el("textarea"); message.id = "live-message"; message.name = "message"; message.maxLength = 4000; message.required = true; message.rows = 5; message.value = caseSnapshot.message || ""; message.setAttribute("aria-describedby", "live-message-note");
    const messageNote = el("p", "live-caption", "Up to 4,000 characters. The model judges this message; the selected case supplies the object facts."); messageNote.id = "live-message-note";
    messageField.append(messageLabel, message, messageNote);
    const actions = el("div", "live-actions");
    const run = el("button", "button primary", "Run one request"); run.id = "live-run-button"; run.type = "submit";
    const exportButton = el("button", "button secondary", "Export live JSON"); exportButton.id = "live-export-button"; exportButton.type = "button"; exportButton.disabled = true;
    actions.append(run, exportButton);
    const status = el("p", "live-status", "Ready. A model call starts only when you select Run one request."); status.id = "live-status"; status.setAttribute("role", "status");
    const errorBox = el("p", "live-error"); errorBox.id = "live-error"; errorBox.setAttribute("role", "alert"); errorBox.hidden = true;
    form.append(controls, providerHint, messageField, actions, status, errorBox);
    body.append(form, el("p", "live-caption live-close-note", "Closing this dialog discards its result. A request already sent may continue on the server."));
    const output = el("section", "live-result"); output.id = "live-result"; output.setAttribute("aria-label", "Live model result"); body.append(output);
    let inputRevision = 0;
    let liveResult = null;

    function inputError() {
      if (provider.value !== "ollama" && provider.value !== "jev") return "Select a supported provider.";
      if (provider.value === "jev" && !providerInfo.jev_available) return "Configure TYPESAFE_API_KEY in the server environment and restart the server to use Jev.";
      if (!model.value.trim() || model.value.length > 120 || /[^\x20-\x7e]/.test(model.value)) return "Enter a model name with 1–120 printable ASCII characters.";
      if (!message.value.trim() || Array.from(message.value).length > 4000) return "Enter a customer message with 1–4,000 characters.";
      if (state.pending || workspaceRevision !== state.revision || !filteredRows().some((item) => item.case.id === caseSnapshot.id) || state.selectedId !== caseSnapshot.id) return "The case or draft changed. Close and reopen this dialog to use the completed comparison.";
      return "";
    }

    function refreshControls() {
      const problem = inputError();
      providerHint.textContent = provider.value === "jev" ? providerInfo.jev_available ? "Jev is configured. This sends the message to the configured Jev API; credentials stay on the server." : "Configure TYPESAFE_API_KEY in the server environment and restart the server to use Jev." : "Uses Ollama on this server’s loopback address. The named local model must already be installed; no model is downloaded.";
      run.disabled = state.livePending || !!problem;
      run.textContent = state.livePending ? "Running model request…" : "Run one request";
      errorBox.textContent = problem; errorBox.hidden = !problem;
      exportButton.disabled = !liveResult || state.livePending;
    }

    function invalidate() {
      inputRevision += 1; liveResult = null; output.replaceChildren();
      status.textContent = state.livePending ? "Inputs changed. The running result will be discarded; wait for it to finish before another request." : "Inputs changed. Run one request to create a fresh result.";
      refreshControls();
    }

    provider.addEventListener("change", () => { models[currentProvider] = model.value; currentProvider = provider.value; model.value = models[currentProvider] || ""; invalidate(); });
    model.addEventListener("input", invalidate);
    message.addEventListener("input", invalidate);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (state.livePending || revision !== state.dialogRevision) return;
      if (inputError()) { refreshControls(); return; }
      const submitted = { case: { ...caseSnapshot, message: message.value }, policy: { ...policySnapshot }, baseline_policy: { ...baselineSnapshot }, provider: provider.value, model: model.value.trim(), message: message.value };
      const runRevision = inputRevision;
      liveResult = null; output.replaceChildren(); state.livePending = true;
      status.textContent = "Running one " + (submitted.provider === "ollama" ? "local Ollama" : "Jev API") + " request. This may take up to two minutes; no automatic retry is made.";
      form.setAttribute("aria-busy", "true"); refreshControls(); updateActions();
      try {
        const result = await request("/api/live", { method: "POST", body: JSON.stringify({ case_id: caseSnapshot.id, policy: submitted.policy, provider: submitted.provider, message: submitted.message, model: submitted.model }) });
        if (revision !== state.dialogRevision || runRevision !== inputRevision) return;
        if (!result.case || !result.policy || !result.semantic || !result.decision || !result.baseline_decision) throw new Error("The server returned an incomplete live result. No result is available to export.");
        renderLiveResult(output, result, submitted);
        liveResult = { exported_at: new Date().toISOString(), evidence_note: "One live semantic observation shared by baseline and draft. Ollama scores are uncalibrated model-generated estimates. All actions are proposals only.", submitted, ...result };
        status.textContent = "Request complete. The result below preserves the submitted message, case, and policies.";
      } catch (error) {
        if (revision !== state.dialogRevision || runRevision !== inputRevision) return;
        liveResult = null; output.replaceChildren();
        status.textContent = "Request failed. No live result was produced.";
        errorBox.textContent = error.message; errorBox.hidden = false;
      } finally {
        state.livePending = false; updateActions();
        if (revision === state.dialogRevision) {
          form.setAttribute("aria-busy", "false");
          run.disabled = !!inputError(); run.textContent = "Run one request";
          exportButton.disabled = !liveResult;
          if (runRevision !== inputRevision) status.textContent = "The request finished, but its inputs changed. Run again to create a fresh result.";
        }
      }
    });
    exportButton.addEventListener("click", () => {
      if (!liveResult || state.livePending || revision !== state.dialogRevision) return;
      const blob = new Blob([JSON.stringify(liveResult, null, 2) + "\n"], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = el("a"); link.href = url; link.download = "ontologyjev-live-" + caseSnapshot.id + "-" + new Date().toISOString().slice(0, 10) + ".json";
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast("Live result exported with the submitted inputs and provider evidence.");
    });
    refreshControls();
  }

  function addCodeSection(container, title, code) {
    const heading = el("div", "dialog-section-row");
    heading.append(el("h3", "", title));
    const copy = el("button", "text-button", "Copy"); copy.type = "button";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(code); copy.textContent = "Copied";
        window.setTimeout(() => { copy.textContent = "Copy"; }, 1800);
      } catch (_) { toast("Clipboard unavailable. Select and copy the code below.", true); }
    });
    heading.append(copy);
    const pre = el("pre"); pre.append(el("code", "", code));
    container.append(heading, pre);
  }

  function showSdk() {
    const body = openDialog("From workbench to Python", "THE ONTOLOGYJEV SDK");
    body.append(el("p", "dialog-lead", "Keep the facts, policy, and semantic observation explicit. Inspect a decision before your application proposes its next step."));
    const row = filteredRows().find((item) => item.case.id === state.selectedId);
    const ready = !!row && !state.pending;
    if (ready) {
      body.append(el("div", "dialog-callout", row.case.id + " · " + row.case.title + " · expected " + row.after.outcome + ". This snapshot contains the selected case, completed draft and synthetic observation. No model call is made."));
      addCodeSection(body, "Copy this complete Python example", helpers.buildSdkSnippet(row.case, state.data.candidate_policy, row.after.semantic));
      body.append(el("p", "", "Run this code in Python from the repository root, or save it as a .py file in that directory. It prints the same decision and complete rule trace shown in the workbench."));
    } else {
      body.append(el("div", "dialog-callout", state.pending ? "Finish a valid draft comparison, then reopen this panel to copy a current decision. The last successful result is not exported as your new draft." : "Select a visible case, then reopen this panel for a complete runnable Python example."));
    }
    addCodeSection(body, "Replay saved regression cases", "python -m ontology_jev regressions");
    body.append(el("h3", "", "Try a live model in the workbench"), el("p", "", "Close this panel and choose Try a model. It uses the visible selected case and completed draft, lets you edit the message and model, and makes one request only when you run it. Export its result from that dialog."));
    body.append(el("p", "", "For local Ollama, start Ollama with an installed model. Generated intent scores are uncalibrated. Model trials reuse the selected case facts and do not change the synthetic comparison or saved regressions."));
    if (ready) addCodeSection(body, "After the example above: make one local Ollama call", "from ontology_jev.providers import OllamaProvider\n\nsemantic = OllamaProvider(model=\"qwen3.5:9b\").judge(case)\ndecision = evaluate(case, policy, semantic)\nprint(decision[\"outcome\"])");
    body.append(el("h3", "", "Optional: connect a real Jev observation"));
    body.append(el("p", "", "Configure TYPESAFE_API_KEY in the server environment, then restart the local server. Credentials stay on the server. This browser never asks for or stores an API key."));
    if (ready) addCodeSection(body, "After the example above: make one explicit Jev call", "from ontology_jev.providers import JevProvider\n\nsemantic = JevProvider().judge(case)\ndecision = evaluate(case, policy, semantic)\nprint(decision[\"outcome\"])");
    const available = !!(state.data && state.data.provider && state.data.provider.jev_available);
    body.append(el("div", "dialog-callout", (available ? "The server reports Jev is configured. " : "Jev is not configured on this server. ") + "The workbench comparisons use labeled synthetic fixtures. Running the Python provider explicitly makes a real API request. Provider errors remain errors; there is no silent fixture fallback."));
  }

  function showOntology() {
    const body = openDialog("Three objects. Explicit relationships.", "THE REFUND ONTOLOGY");
    body.append(el("p", "dialog-lead", "The decision engine checks the supplied object IDs and facts. A customer message cannot establish ownership or payment authority."));
    const list = el("div", "dialog-list");
    const objects = [
      ["Customer", "id · name · tier"],
      ["Order", "id · customer_id · payment_id\nage_days · status"],
      ["Payment", "id · order_id · paid_cents\nrefunded_cents · status"]
    ];
    objects.forEach(([name, attributes], index) => {
      const card = el("div", "ontology-object"); card.append(el("h3", "", name), el("p", "", attributes)); list.append(card);
      if (index < 2) list.append(el("div", "ontology-link", index === 0 ? "↓ owns · customer.id = order.customer_id" : "↓ paid by · reciprocal order / payment IDs"));
    });
    body.append(list);
    body.append(el("div", "dialog-callout", "Unknown facts route to review. Failed hard rules block the proposal. Refund intent is one observation, not permission to refund."));
    const row = state.data && state.data.rows.find((item) => item.case.id === state.selectedId);
    if (row) addCodeSection(body, "Selected case relationships", JSON.stringify({ customer: row.case.customer || null, order: row.case.order || null, payment: row.case.payment || null }, null, 2));
  }

  async function showRegressions() {
    const body = openDialog("Saved decision expectations", "REGRESSION SUITE");
    const revision = state.dialogRevision;
    body.append(el("p", "dialog-lead", "Replay frozen expectations with their saved policies, or check what your current draft would change. Both reuse the saved facts and observations without model calls or snapshot edits."));
    const controls = el("div", "regression-controls");
    const savedButton = el("button", "button secondary", "Saved replay"); savedButton.type = "button"; savedButton.disabled = true;
    const draftButton = el("button", "button primary", "Check current draft"); draftButton.type = "button"; draftButton.id = "run-draft-regressions"; draftButton.disabled = true; draftButton.dataset.empty = "true";
    controls.append(savedButton, draftButton); body.append(controls);
    const notice = el("p", "regression-mode-note", state.pending ? "Current draft is unavailable until a valid comparison finishes." : "Saved outcomes are expectations you preserved, not independently verified ground truth.");
    body.append(notice);
    const runOutput = el("div", "regression-results"); runOutput.setAttribute("aria-live", "polite"); body.append(runOutput);
    let runRevision = 0;
    let count = 0;
    async function run(mode) {
      if (mode === "draft" && (state.pending || !state.data)) return;
      const policySnapshot = mode === "draft" ? { ...state.data.candidate_policy } : null;
      const runId = ++runRevision;
      savedButton.disabled = true; draftButton.disabled = true; draftButton.dataset.running = "true";
      runOutput.replaceChildren(el("p", "dialog-loading", mode === "draft" ? "Checking the submitted draft…" : "Replaying saved policies…"));
      try {
        const requestBody = mode === "draft" ? { mode, policy: policySnapshot } : { mode };
        const result = await request("/api/regressions/run", { method: "POST", body: JSON.stringify(requestBody) });
        if (revision !== state.dialogRevision || runId !== runRevision) return;
        runOutput.replaceChildren();
        runOutput.append(el("h3", "regression-results-title", mode === "draft" ? "Current draft check" : "Saved-policy replay"));
        if (policySnapshot) {
          runOutput.append(el("p", "regression-mode-note", "Submitted draft: " + policySnapshot.refund_window_days + " days · " + money(policySnapshot.max_auto_refund_cents) + " ceiling · intent ≥ " + policySnapshot.semantic_threshold + "."));
        } else {
          runOutput.append(el("p", "regression-mode-note", "Each case was evaluated with its own saved policy."));
        }
        if (!result.total) {
          runOutput.append(el("div", "dialog-callout", "No saved cases to evaluate. Save a decision in the workbench first; an empty suite is not validation."));
          return;
        }
        runOutput.append(el("div", "regression-result-summary" + (result.failed ? " has-differences" : ""), result.total + " checked · " + result.passed + " match · " + result.failed + " differ or could not run"));
        (result.results || []).forEach((item) => {
          const card = el("div", "regression-result" + (item.passed ? "" : " has-differences"));
          card.append(el("strong", "", item.title || item.case_id || "Invalid snapshot"));
          card.append(el("p", "regression-result-id", (item.case_id || "Snapshot") + " · " + item.id));
          if (item.error) card.append(el("p", "regression-error", "Could not evaluate: " + item.error));
          else {
            const outcomes = el("div", "regression-outcome-pair");
            const expected = el("div"); expected.append(el("span", "", "Expected"), badge(item.expected));
            const actual = el("div"); actual.append(el("span", "", "Actual"), badge(item.actual));
            outcomes.append(expected, actual, el("strong", "regression-verdict", item.passed ? "Matches" : "Differs")); card.append(outcomes);
            card.append(el("p", "", item.reason));
          }
          runOutput.append(card);
        });
      } catch (error) {
        if (revision === state.dialogRevision && runId === runRevision) runOutput.replaceChildren(el("div", "dialog-callout regression-error", error.message));
      } finally {
        if (revision === state.dialogRevision && runId === runRevision) {
          draftButton.dataset.running = "false";
          savedButton.disabled = !count;
          draftButton.disabled = !count || state.pending || !state.data;
        }
      }
    }
    savedButton.addEventListener("click", () => run("saved"));
    draftButton.addEventListener("click", () => run("draft"));
    const loading = el("p", "dialog-loading", "Loading saved regressions…"); body.append(loading);
    try {
      const result = await request("/api/regressions");
      if (revision !== state.dialogRevision) return;
      loading.remove();
      const regressions = Array.isArray(result) ? result : result.regressions || [];
      count = regressions.length;
      draftButton.dataset.empty = String(!count);
      savedButton.disabled = !count;
      draftButton.disabled = !count || state.pending || !state.data;
      $("regression-count").textContent = regressions.length;
      if (state.data) state.data.regression_count = regressions.length;
      if (!regressions.length) body.append(el("div", "dialog-callout", "No regression cases yet. Select a case in the workbench and choose “Save regression” to preserve its current draft expectation."));
      else {
        const list = el("div", "dialog-list");
        regressions.forEach((item) => {
          const entry = el("div", "regression-entry");
          const meta = el("div");
          const caseId = item.case_id || (item.case && item.case.id) || item.id || "Saved case";
          meta.append(el("strong", "", item.title || (item.case && item.case.title) || caseId));
          meta.append(el("p", "", caseId + " · " + (item.id || item.created_at || "saved")));
          entry.append(meta);
          if (item.error) entry.append(el("span", "regression-error", item.error));
          else entry.append(badge(item.expected_outcome));
          list.append(entry);
        });
        body.append(list);
      }
      addCodeSection(body, "Replay all saved expectations", "python -m ontology_jev regressions");
    } catch (error) {
      if (revision !== state.dialogRevision) return;
      loading.textContent = error.message;
      const retry = el("button", "button secondary dialog-retry", "Try again"); retry.type = "button"; retry.addEventListener("click", showRegressions); body.append(retry);
    }
  }

  async function initialize() {
    $("global-error").hidden = true;
    try {
      const data = await request("/api/workspace");
      state.data = data;
      state.initialDraft = { ...data.candidate_policy };
      for (const input of Object.values(fields)) input.disabled = false;
      fillPolicy(data.candidate_policy);
      renderWorkspace();
      setCompareStatus("Draft comparison up to date");
    } catch (error) {
      const errorBox = $("global-error"); errorBox.replaceChildren(document.createTextNode(error.message));
      const retry = el("button", "text-button", "Retry connection"); retry.type = "button"; retry.addEventListener("click", initialize); errorBox.append(retry); errorBox.hidden = false;
      setCompareStatus("Workspace unavailable", "error");
      $("table-empty").querySelector("strong").textContent = "Could not load the workspace";
      $("table-empty").querySelector("p").textContent = "Check the local server, then retry the connection.";
    }
  }

  Object.values(fields).forEach((input) => input.addEventListener("input", () => scheduleCompare()));
  $("reset-button").addEventListener("click", () => { if (!state.initialDraft) return; fillPolicy(state.initialDraft); scheduleCompare(true); });
  document.querySelectorAll("[data-example]").forEach((button) => button.addEventListener("click", () => {
    if (!state.initialDraft || !state.data) return;
    state.filter = "all"; state.search = ""; $("search-input").value = "";
    document.querySelectorAll("[data-filter]").forEach((tab) => { const active = tab.dataset.filter === "all"; tab.classList.toggle("active", active); tab.setAttribute("aria-pressed", String(active)); });
    state.selectedId = button.dataset.example;
    state.focusCase = button.dataset.example;
    fillPolicy(state.initialDraft);
    scheduleCompare(true);
    renderVisibleSelection();
  }));
  document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((tab) => { const active = tab === button; tab.classList.toggle("active", active); tab.setAttribute("aria-pressed", String(active)); });
    renderVisibleSelection();
  }));
  $("search-input").addEventListener("input", (event) => { state.search = event.target.value.trim().toLowerCase(); renderVisibleSelection(); });
  $("export-button").addEventListener("click", exportComparison);
  $("try-model-button").addEventListener("click", showLive);
  $("sdk-button").addEventListener("click", showSdk);
  $("sidebar-sdk").addEventListener("click", showSdk);
  $("nav-ontology").addEventListener("click", showOntology);
  $("nav-regressions").addEventListener("click", showRegressions);
  $("mobile-ontology").addEventListener("click", showOntology);
  $("mobile-regressions").addEventListener("click", showRegressions);
  $("nav-workbench").setAttribute("aria-label", "Decision workbench");
  $("nav-ontology").setAttribute("aria-label", "Ontology map");
  $("nav-regressions").setAttribute("aria-label", "Saved regressions");
  $("nav-workbench").addEventListener("click", () => { window.scrollTo({ top: 0, behavior: "smooth" }); });
  $("dialog-close").addEventListener("click", closeDialog);
  $("workspace-dialog").addEventListener("click", (event) => { if (event.target === $("workspace-dialog")) { const box = event.target.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) closeDialog(); } });
  $("workspace-dialog").addEventListener("cancel", (event) => { event.preventDefault(); closeDialog(); });
  initialize();
})();
