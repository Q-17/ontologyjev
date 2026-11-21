"""Behavioral contracts for the pure refund decision SDK."""

import copy
import json
import unittest

from ontology_jev.engine import compare, evaluate, validate_policy


def policy(**changes):
    value = {
        "version": "baseline-v1",
        "refund_window_days": 7,
        "max_auto_refund_cents": 10000,
        "semantic_threshold": 0.85,
    }
    value.update(changes)
    return value


def case():
    return {
        "id": "case-1",
        "title": "Returned headphones",
        "message": "Please refund my order.",
        "customer": {"id": "customer-1", "name": "Ada", "tier": "standard"},
        "order": {
            "id": "order-1", "customer_id": "customer-1",
            "payment_id": "payment-1", "age_days": 7, "status": "delivered",
        },
        "payment": {
            "id": "payment-1", "order_id": "order-1",
            "paid_cents": 10000, "refunded_cents": 0, "status": "captured",
        },
        "requested_amount_cents": 10000,
        "semantic": {
            "refund_request": 0.9, "source": "synthetic_fixture", "model": "fixture-v1",
        },
    }


def rules(result):
    return {rule["id"]: rule for rule in result["rules"]}


class EngineTests(unittest.TestCase):
    def test_generated_scores_keep_provenance_without_claiming_probability(self):
        semantic = {
            "refund_request": 0.7, "source": "ollama", "model": "qwen3.5:9b",
            "score_kind": "generated_self_report", "calibrated": False,
        }
        result = evaluate(case(), policy(), semantic)
        self.assertEqual(result["outcome"], "review")
        self.assertEqual(result["semantic"].get("score_kind"), "generated_self_report")
        self.assertIs(result["semantic"].get("calibrated"), False)
        intent = rules(result)["refund_intent"]
        self.assertEqual(intent["expected"], {"min_score": 0.85})
        self.assertIn("score", intent["reason"])
        self.assertNotIn("probability", intent["reason"])
        semantic["refund_request"] = 1.0
        item = case()
        item["order"]["customer_id"] = "someone-else"
        self.assertEqual(evaluate(item, policy(), semantic)["outcome"], "deny")

    def test_exact_window_amount_and_semantic_boundaries_allow_proposal(self):
        item = case()
        item["semantic"]["refund_request"] = 0.85
        original = copy.deepcopy(item)
        result = evaluate(item, policy(), item["semantic"])
        self.assertEqual((result["outcome"], result["action"]), ("allow", "propose_refund"))
        self.assertEqual(len(result["rules"]), 8)
        self.assertTrue(all(rule["status"] == "pass" for rule in result["rules"]))
        self.assertEqual(item, original)
        self.assertEqual(result["semantic"]["source"], "synthetic_fixture")
        json.dumps(result, allow_nan=False)

    def test_policy_comparison_reuses_semantics_and_explains_changed_rule(self):
        item = case()
        item["order"]["age_days"] = 8
        original = copy.deepcopy(item)
        result = compare([item], policy(), policy(version="draft-v2", refund_window_days=14))
        row = result["rows"][0]
        self.assertEqual(row["before"]["outcome"], "deny")
        self.assertEqual(row["after"]["outcome"], "allow")
        self.assertTrue(row["changed"])
        self.assertEqual(row["changed_rules"], ["refund_window"])
        self.assertEqual([diff["id"] for diff in row["rule_diffs"]], ["refund_window"])
        self.assertTrue(row["rule_diffs"][0]["status_changed"])
        self.assertEqual(row["before"]["semantic"], row["after"]["semantic"])
        self.assertEqual(item, original)
        self.assertEqual(result["summary"], {"total": 1, "changed": 1, "allowed": 1, "review": 0, "denied": 0})

    def test_hard_failure_dominates_review_and_all_rules_are_evaluated(self):
        item = case()
        item["order"]["customer_id"] = "someone-else"
        item["payment"]["order_id"] = "another-order"
        item["semantic"]["refund_request"] = 0.2
        result = evaluate(item, policy(max_auto_refund_cents=500), item["semantic"])
        trace = rules(result)
        self.assertEqual(result["outcome"], "deny")
        self.assertEqual(trace["ownership"]["status"], "fail")
        self.assertEqual(trace["payment_link"]["status"], "fail")
        self.assertEqual(trace["amount_limit"]["status"], "review")
        self.assertEqual(trace["refund_intent"]["status"], "review")
        self.assertEqual(len(trace), 8)

    def test_each_payment_link_and_nonrefundable_status_is_enforced(self):
        for section, field, value, rule in [
            ("payment", "id", "unrelated", "payment_link"),
            ("payment", "order_id", "unrelated", "payment_link"),
            ("payment", "status", "pending", "payment_status"),
            ("order", "status", "cancelled", "order_status"),
        ]:
            with self.subTest(section=section, field=field):
                item = case()
                item[section][field] = value
                result = evaluate(item, policy(), item["semantic"])
                self.assertEqual(result["outcome"], "deny")
                self.assertEqual(rules(result)[rule]["status"], "fail")

    def test_refund_conserves_remaining_paid_balance(self):
        for refunded, requested, expected in [
            (0, 10001, "deny"), (10000, 1, "deny"),
            (2000, 8000, "allow"), (2000, 8001, "deny"), (0, 0, "deny"),
        ]:
            with self.subTest(refunded=refunded, requested=requested):
                item = case()
                item["payment"]["refunded_cents"] = refunded
                item["requested_amount_cents"] = requested
                result = evaluate(item, policy(), item["semantic"])
                self.assertEqual(result["outcome"], expected)

    def test_missing_or_malformed_facts_never_allow_and_never_crash(self):
        changes = [
            ("customer", None, None), ("order", None, []), ("payment", None, "absent"),
            ("customer", "id", ""), ("order", "payment_id", None),
            ("order", "age_days", True), ("order", "age_days", -1),
            ("order", "age_days", float("nan")), ("payment", "status", 1),
            ("payment", "paid_cents", True), ("payment", "refunded_cents", 10001),
            ("payment", "refunded_cents", -1), (None, "requested_amount_cents", "100"),
            (None, "requested_amount_cents", True),
        ]
        for section, field, value in changes:
            with self.subTest(section=section, field=field, value=value):
                item = case()
                if section is None:
                    item[field] = value
                elif field is None:
                    item[section] = value
                else:
                    item[section][field] = value
                result = evaluate(item, policy(), item["semantic"])
                self.assertEqual(result["outcome"], "review")
                self.assertIn("unknown", [rule["status"] for rule in result["rules"]])
                json.dumps(result, allow_nan=False)
        for item in ({}, None, [], "invalid"):
            with self.subTest(item=item):
                self.assertEqual(evaluate(item, policy(), {})["outcome"], "review")

    def test_uncertain_or_invalid_semantics_request_review(self):
        for score in (0.84, None, True, "0.99", float("nan"), float("inf"), -0.1, 1.1):
            with self.subTest(score=score):
                item = case()
                item["semantic"]["refund_request"] = score
                result = evaluate(item, policy(), item["semantic"])
                self.assertEqual(result["outcome"], "review")
                json.dumps(result, allow_nan=False)
        for semantic in (None, {}, {"refund_request": 0.99}, {"refund_request": 0.99, "source": "error", "model": "jev-latest"}):
            with self.subTest(semantic=semantic):
                self.assertEqual(evaluate(case(), policy(), semantic)["outcome"], "review")

    def test_amount_limit_reviews_without_changing_balance_evidence(self):
        item = case()
        result = evaluate(item, policy(max_auto_refund_cents=9999), item["semantic"])
        self.assertEqual(result["outcome"], "review")
        self.assertEqual(rules(result)["balance"]["status"], "pass")
        self.assertEqual(rules(result)["amount_limit"]["status"], "review")

    def test_policy_is_strict_and_hash_is_canonical(self):
        invalid = [None, {}, dict(policy(), unknown=True)]
        for field, values in {
            "version": [None, "", 1],
            "refund_window_days": [True, -1, 366, 7.0],
            "max_auto_refund_cents": [True, -1, 1000001, 100.1],
            "semantic_threshold": [True, 0.49, 1.01, float("nan"), float("inf"), "0.9"],
        }.items():
            invalid.extend(policy(**{field: value}) for value in values)
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_policy(value)
        item = case()
        first = evaluate(item, policy(semantic_threshold=1), item["semantic"])
        reversed_policy = dict(reversed(list(policy(semantic_threshold=1.0).items())))
        second = evaluate(item, reversed_policy, item["semantic"])
        self.assertEqual(first["policy_hash"], second["policy_hash"])
        changed = evaluate(item, policy(refund_window_days=14), item["semantic"])
        self.assertNotEqual(first["policy_hash"], changed["policy_hash"])

    def test_rule_change_without_outcome_change_is_not_a_changed_decision(self):
        item = case()
        item["order"]["customer_id"] = "someone-else"
        item["order"]["age_days"] = 10
        result = compare([item], policy(), policy(refund_window_days=14))
        self.assertFalse(result["rows"][0]["changed"])
        self.assertEqual(result["rows"][0]["changed_rules"], ["refund_window"])
        self.assertEqual(result["summary"]["denied"], 1)

    def test_rule_diffs_show_threshold_edits_even_when_all_statuses_stay_pass(self):
        result = compare([case()], policy(), policy(
            refund_window_days=14, max_auto_refund_cents=12000, semantic_threshold=0.8))
        row = result["rows"][0]
        self.assertFalse(row["changed"])
        self.assertEqual(row["changed_rules"], [])
        diffs = {diff["id"]: diff for diff in row["rule_diffs"]}
        self.assertEqual(set(diffs), {"refund_window", "amount_limit", "refund_intent"})
        self.assertEqual(diffs["refund_window"]["before"]["expected"], {"max_age_days": 7})
        self.assertEqual(diffs["refund_window"]["after"]["expected"], {"max_age_days": 14})
        self.assertEqual(diffs["amount_limit"]["after"]["expected"], {"max_auto_refund_cents": 12000})
        self.assertEqual(diffs["refund_intent"]["after"]["expected"], {"min_probability": 0.8})
        self.assertTrue(all(not diff["status_changed"] for diff in diffs.values()))
        self.assertEqual(row["before"]["semantic"], row["after"]["semantic"])

    def test_version_only_edit_does_not_claim_rule_evidence_changes(self):
        row = compare([case()], policy(), policy(version="renamed"))["rows"][0]
        self.assertFalse(row["changed"])
        self.assertEqual(row["rule_diffs"], [])


if __name__ == "__main__":
    unittest.main()
