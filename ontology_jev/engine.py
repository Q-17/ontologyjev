"""Pure, deterministic refund decisions over explicit facts and semantic intent.

An ``allow`` result proposes a refund; this module never executes a payment.
Semantic observations describe refund intent and cannot override hard rules.
"""

import copy
import hashlib
import json
import math


_POLICY_FIELDS = {
    "version", "refund_window_days", "max_auto_refund_cents", "semantic_threshold",
}


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _probability(value):
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and 0 <= value <= 1
    )


def _safe(value):
    """Keep evidence serializable even when supplied facts contain NaN or objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return None


def validate_policy(policy):
    """Return a canonical policy, rejecting missing, unknown or invalid fields."""
    if not isinstance(policy, dict):
        raise ValueError("Policy must be an object.")
    keys = set(policy)
    if keys != _POLICY_FIELDS:
        unknown = keys - _POLICY_FIELDS
        missing = _POLICY_FIELDS - keys
        details = []
        if unknown:
            details.append("unknown fields: " + ", ".join(sorted(str(key) for key in unknown)))
        if missing:
            details.append("missing fields: " + ", ".join(sorted(missing)))
        raise ValueError("Invalid policy; " + "; ".join(details) + ".")
    if not _text(policy["version"]):
        raise ValueError("version must be a nonempty string.")
    for field, maximum in (("refund_window_days", 365), ("max_auto_refund_cents", 1000000)):
        if not _integer(policy[field]) or not 0 <= policy[field] <= maximum:
            raise ValueError("{} must be an integer between 0 and {}.".format(field, maximum))
    threshold = policy["semantic_threshold"]
    if not _probability(threshold) or threshold < 0.5:
        raise ValueError("semantic_threshold must be a finite number between 0.5 and 1.0.")
    return {
        "version": policy["version"],
        "refund_window_days": policy["refund_window_days"],
        "max_auto_refund_cents": policy["max_auto_refund_cents"],
        "semantic_threshold": float(threshold),
    }


def _semantic_observation(semantic):
    value = semantic if isinstance(semantic, dict) else {}
    score = value.get("refund_request")
    normalized = {
        "refund_request": float(score) if _probability(score) else None,
        "source": value.get("source") if _text(value.get("source")) else None,
        "model": value.get("model") if _text(value.get("model")) else None,
    }
    if _text(value.get("score_kind")):
        normalized["score_kind"] = value["score_kind"]
    if isinstance(value.get("calibrated"), bool):
        normalized["calibrated"] = value["calibrated"]
    if "error" in value:
        normalized["error"] = _safe(value["error"])
    valid = (
        _probability(score) and _text(value.get("source"))
        and _text(value.get("model")) and value.get("source") != "error"
        and not value.get("error")
    )
    return normalized, valid


def evaluate(case, policy, semantic):
    """Evaluate every rule and return an auditable allow/review/deny proposal.

    Invalid policy configuration raises ``ValueError``. Missing or malformed
    case facts produce unknown rule evidence and require review, unless a
    separate known hard-rule failure already requires denial.
    """
    policy = validate_policy(policy)
    item = case if isinstance(case, dict) else {}
    customer = item.get("customer")
    customer = customer if isinstance(customer, dict) else {}
    order = item.get("order")
    order = order if isinstance(order, dict) else {}
    payment = item.get("payment")
    payment = payment if isinstance(payment, dict) else {}
    observation, valid_semantic = _semantic_observation(semantic)
    trace = []

    def rule(rule_id, label, status, reason, observed, expected, paths):
        trace.append({
            "id": rule_id, "label": label, "status": status, "reason": reason,
            "observed": _safe(observed), "expected": _safe(expected),
            "source_paths": paths,
        })

    owner_ids = [customer.get("id"), order.get("customer_id")]
    if not all(_text(value) for value in owner_ids):
        status, reason = "unknown", "Customer ownership identifiers are missing or invalid."
    elif owner_ids[0] != owner_ids[1]:
        status, reason = "fail", "The order belongs to a different customer."
    else:
        status, reason = "pass", "The current customer owns the order."
    rule("ownership", "Customer ownership", status, reason,
         {"customer_id": owner_ids[0], "order_customer_id": owner_ids[1]},
         "customer.id == order.customer_id", ["customer.id", "order.customer_id"])

    links = [(payment.get("id"), order.get("payment_id")),
             (payment.get("order_id"), order.get("id"))]
    if any(_text(left) and _text(right) and left != right for left, right in links):
        status, reason = "fail", "The payment and order identifiers do not link to each other."
    elif not all(_text(value) for pair in links for value in pair):
        status, reason = "unknown", "Payment linkage identifiers are missing or invalid."
    else:
        status, reason = "pass", "The order and payment reference each other."
    rule("payment_link", "Payment linkage", status, reason,
         {"payment_id": links[0][0], "order_payment_id": links[0][1],
          "payment_order_id": links[1][0], "order_id": links[1][1]},
         "payment.id == order.payment_id and payment.order_id == order.id",
         ["payment.id", "order.payment_id", "payment.order_id", "order.id"])

    payment_status = payment.get("status")
    if not _text(payment_status):
        status, reason = "unknown", "Payment status is missing or invalid."
    elif payment_status != "captured":
        status, reason = "fail", "Only captured payments are refundable."
    else:
        status, reason = "pass", "Payment has been captured."
    rule("payment_status", "Captured payment", status, reason, payment_status,
         "captured", ["payment.status"])

    paid = payment.get("paid_cents")
    refunded = payment.get("refunded_cents")
    amount = item.get("requested_amount_cents")
    valid_money = all(_integer(value) and value >= 0 for value in (paid, refunded, amount))
    remaining = paid - refunded if valid_money and refunded <= paid else None
    if remaining is None:
        status, reason = "unknown", "Refund amounts are missing, invalid, or inconsistent."
    elif amount == 0:
        status, reason = "fail", "The requested refund must be greater than zero."
    elif remaining == 0:
        status, reason = "fail", "No refundable balance remains; another refund would duplicate payment."
    elif amount > remaining:
        status, reason = "fail", "The requested refund exceeds the remaining paid balance."
    else:
        status, reason = "pass", "The requested refund fits within the remaining paid balance."
    rule("balance", "Refundable balance", status, reason,
         {"paid_cents": paid, "refunded_cents": refunded,
          "requested_amount_cents": amount, "remaining_cents": remaining},
         "0 < requested_amount_cents <= paid_cents - refunded_cents",
         ["payment.paid_cents", "payment.refunded_cents", "requested_amount_cents"])

    order_status = order.get("status")
    if not _text(order_status):
        status, reason = "unknown", "Order status is missing or invalid."
    elif order_status != "delivered":
        status, reason = "fail", "Only delivered orders are refundable under this policy."
    else:
        status, reason = "pass", "The order has been delivered."
    rule("order_status", "Refundable order", status, reason, order_status,
         "delivered", ["order.status"])

    age = order.get("age_days")
    window = policy["refund_window_days"]
    if not _integer(age) or age < 0:
        status, reason = "unknown", "Order age is missing or invalid."
    elif age > window:
        status, reason = "fail", "The order is outside the {}-day refund window.".format(window)
    else:
        status, reason = "pass", "The order is within the {}-day refund window.".format(window)
    rule("refund_window", "Refund window", status, reason, age,
         {"max_age_days": window}, ["order.age_days", "policy.refund_window_days"])

    limit = policy["max_auto_refund_cents"]
    if not _integer(amount) or amount < 0:
        status, reason = "unknown", "The requested refund amount is missing or invalid."
    elif amount > limit:
        status, reason = "review", "The refund exceeds the automatic proposal limit."
    else:
        status, reason = "pass", "The refund is within the automatic proposal limit."
    rule("amount_limit", "Automatic proposal limit", status, reason, amount,
         {"max_auto_refund_cents": limit}, ["requested_amount_cents", "policy.max_auto_refund_cents"])

    threshold = policy["semantic_threshold"]
    if not valid_semantic:
        status, reason = "unknown", "Refund intent observation or its source metadata is missing or invalid."
    elif observation["refund_request"] < threshold:
        status, reason = "review", "Refund intent score is below the review threshold."
    else:
        status, reason = "pass", "Refund intent score meets the threshold; authorization comes from the other rules."
    threshold_key = "min_score" if observation.get("score_kind") == "generated_self_report" else "min_probability"
    rule("refund_intent", "Refund intent", status, reason, observation,
         {threshold_key: threshold}, ["semantic.refund_request", "semantic.source", "semantic.model", "policy.semantic_threshold"])

    failures = [entry for entry in trace if entry["status"] == "fail"]
    reviews = [entry for entry in trace if entry["status"] in ("unknown", "review")]
    if failures:
        outcome, action, reason = "deny", "decline", failures[0]["reason"]
    elif reviews:
        outcome, action, reason = "review", "request_review", reviews[0]["reason"]
    else:
        outcome, action = "allow", "propose_refund"
        reason = "All required rules passed. A refund may be proposed; no payment has been executed."
    encoded = json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {
        "case_id": _safe(item.get("id")), "outcome": outcome, "action": action,
        "reason": reason, "policy_version": policy["version"],
        "policy_hash": hashlib.sha256(encoded).hexdigest(),
        "semantic": observation, "rules": trace,
    }


def compare(cases, baseline, candidate):
    """Replay identical observations, separating outcome changes from rule edits.

    ``changed_rules`` retains its status-change meaning. ``rule_diffs`` also
    explains changed expectations when a rule passes under both policies.
    """
    baseline = validate_policy(baseline)
    candidate = validate_policy(candidate)
    if not isinstance(cases, list):
        raise ValueError("cases must be a list.")
    rows = []
    summary = {"total": len(cases), "changed": 0, "allowed": 0, "review": 0, "denied": 0}
    count_keys = {"allow": "allowed", "review": "review", "deny": "denied"}
    for item in cases:
        semantic = item.get("semantic") if isinstance(item, dict) else None
        before = evaluate(item, baseline, semantic)
        after = evaluate(item, candidate, semantic)
        changed = (before["outcome"], before["action"]) != (after["outcome"], after["action"])
        before_rules = {entry["id"]: entry for entry in before["rules"]}
        rule_diffs = []
        for entry in after["rules"]:
            previous = before_rules[entry["id"]]
            if previous != entry:
                rule_diffs.append({
                    "id": entry["id"], "label": entry["label"],
                    "before": copy.deepcopy(previous), "after": copy.deepcopy(entry),
                    "status_changed": previous["status"] != entry["status"],
                })
        rows.append({
            "case": copy.deepcopy(_safe(item)), "before": before, "after": after,
            "changed": changed,
            "changed_rules": [entry["id"] for entry in after["rules"]
                              if before_rules[entry["id"]]["status"] != entry["status"]],
            "rule_diffs": rule_diffs,
        })
        summary["changed"] += int(changed)
        summary[count_keys[after["outcome"]]] += 1
    return {"baseline_policy": baseline, "candidate_policy": candidate,
            "rows": rows, "summary": summary}
