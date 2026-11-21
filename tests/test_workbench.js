"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const helpers = require("../ontology_jev/static/workbench.js");

const rows = [
  { case: { id: "case-001", title: "Window changed", customer: { name: "Mara" } }, after: { outcome: "allow" }, changed: true },
  { case: { id: "case-004", title: "Ownership mismatch", customer: { name: "Ava" } }, after: { outcome: "deny" }, changed: false },
];

test("a filter cannot retain a selected case outside its visible rows", () => {
  const visible = helpers.filterRows(rows, "deny", "");
  assert.equal(helpers.selectVisibleId(visible, "case-001"), "case-004");
});

test("an empty search result clears the selected case", () => {
  assert.equal(helpers.selectVisibleId(helpers.filterRows(rows, "all", "no such customer"), "case-001"), null);
});

test("an existing visible selection is preserved", () => {
  assert.equal(helpers.selectVisibleId(rows, "case-004"), "case-004");
  assert.deepEqual(helpers.filterRows(rows, "all", "  AVA "), [rows[1]]);
  assert.deepEqual(helpers.filterRows(rows, "changed", ""), [rows[0]]);
});

test("copied Python executes and preserves quoted, Unicode and control-character input", () => {
  const caseData = {
    id: "custom-case", message: "退款 'quotes' \"double\" \\ path\nnext line\t\u0000\u2028😃",
    customer: { id: "c1" },
    order: { id: "o1", customer_id: "c1", payment_id: "p1", age_days: 10, status: "delivered" },
    payment: { id: "p1", order_id: "o1", paid_cents: 5000, refunded_cents: 0, status: "captured" },
    requested_amount_cents: 2500,
    semantic: { refund_request: 0.98, source: "synthetic_fixture", model: "fixture-v1" },
    optional: null, enabled: true,
  };
  const policy = { version: "draft", refund_window_days: 14, max_auto_refund_cents: 10000, semantic_threshold: 0.8 };
  const script = helpers.buildSdkSnippet(caseData, policy, caseData.semantic);
  const codePoints = Array.from(caseData.message, (char) => char.codePointAt(0));
  const pythonAssertions = "\nassert len(case['message']) == " + codePoints.length + "\nassert [ord(char) for char in case['message']] == " + JSON.stringify(codePoints);
  const result = spawnSync("python", ["-c", script + pythonAssertions + "\nprint(json.dumps(case, ensure_ascii=True))"], { cwd: path.resolve(__dirname, ".."), encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  const lastLine = result.stdout.trim().split("\n").at(-1);
  assert.deepEqual(JSON.parse(lastLine), caseData);
  assert.match(result.stdout, /"outcome": "allow"/);
  assert.match(script, /json\.loads\(/);
});

test("a non-BMP policy version stays one Python code point and can be hashed by evaluate", () => {
  const semantic = { refund_request: 0.98, source: "synthetic_fixture", model: "fixture-v1" };
  const policy = { version: "😃", refund_window_days: 14, max_auto_refund_cents: 10000, semantic_threshold: 0.8 };
  const script = helpers.buildSdkSnippet({ id: "unicode-case", message: "Refund please", semantic }, policy, semantic);
  const assertions = "\nassert len(policy['version']) == 1\nassert [ord(char) for char in policy['version']] == [128515]\nassert len(decision['policy_hash']) == 64";
  const result = spawnSync("python", ["-c", script + assertions], { cwd: path.resolve(__dirname, ".."), encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});

test("snippet evaluates the completed observation rather than replacing it with a fixture", () => {
  const script = helpers.buildSdkSnippet({ id: "case-x", semantic: { refund_request: 0.99 } }, { version: "draft" }, { refund_request: 0.54, source: "synthetic_fixture", model: "fixture-v1" });
  assert.match(script, /semantic = json\.loads\(/);
  assert.match(script, /evaluate\(case, policy, semantic\)/);
  assert.doesNotMatch(script, /JevProvider|FixtureProvider/);
});
