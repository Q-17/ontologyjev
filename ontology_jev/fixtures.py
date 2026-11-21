"""Synthetic examples for the workbench, never model or benchmark results."""

import copy


BASELINE_POLICY = {
    "version": "v1", "refund_window_days": 7,
    "max_auto_refund_cents": 10000, "semantic_threshold": 0.8,
}
DRAFT_POLICY = dict(BASELINE_POLICY, version="draft", refund_window_days=14)

ONTOLOGY = {
    "name": "Refund decision ontology", "version": "1",
    "entities": [
        {"type": "Customer", "key": "id", "fields": ["id", "name", "tier"]},
        {"type": "Order", "key": "id", "fields": ["id", "customer_id", "payment_id", "age_days", "status"]},
        {"type": "Payment", "key": "id", "fields": ["id", "order_id", "paid_cents", "refunded_cents", "status"]},
    ],
    "relationships": [
        {"from": "Customer", "to": "Order", "name": "owns", "join": "customer.id == order.customer_id"},
        {"from": "Order", "to": "Payment", "name": "paid_by", "join": "order.payment_id == payment.id and order.id == payment.order_id"},
    ],
    "actions": ["propose_refund", "request_review", "decline"],
}


def _case(number, name, title, age=3, amount=4800, paid=10000, intent=0.98, message=None):
    suffix = "%03d" % number
    return {
        "id": "case-" + suffix, "title": title,
        "message": message or "Please refund my order. I would like my payment returned.",
        "customer": {"id": "cus-" + suffix, "name": name, "tier": "standard"},
        "order": {"id": "ord-" + suffix, "customer_id": "cus-" + suffix,
                  "payment_id": "pay-" + suffix, "age_days": age, "status": "delivered"},
        "payment": {"id": "pay-" + suffix, "order_id": "ord-" + suffix,
                    "paid_cents": paid, "refunded_cents": 0, "status": "captured"},
        "requested_amount_cents": amount,
        "semantic": {"refund_request": intent, "source": "synthetic_fixture", "model": "fixture-v1"},
    }


def load_cases():
    """Return fresh, mutable copies of 20 deliberately constructed scenarios."""
    cases = [
        _case(1, "Mara Chen", "A refund on day ten", age=10),
        _case(2, "Ian Park", "A straightforward refund", amount=2400),
        _case(3, "Noor Ali", "Just outside the old window", age=12, amount=8200),
        _case(4, "Ava Reed", "An order owned by someone else"),
        _case(5, "Leo Martin", "A payment already refunded"),
        _case(6, "Ella Kim", "A missing payment record"),
        _case(7, "Owen Wu", "Outside both refund windows", age=21),
        _case(8, "Finn Bell", "A refund above the automatic limit", age=4, amount=18500, paid=20000),
        _case(9, "Mina Shah", "An ambiguous customer message", intent=0.54,
              message="This order was disappointing. What can you do about it?"),
        _case(10, "Theo Diaz", "At the new fourteen-day boundary", age=14, amount=6700),
        _case(11, "Zoe Lin", "One day beyond the old window", age=8, amount=3000),
        _case(12, "Adam Scott", "A payment still pending"),
        _case(13, "Lily Patel", "An order not yet delivered"),
        _case(14, "Aria Jones", "A request greater than the balance", amount=12500),
        _case(15, "Luca Ross", "A valid partial refund", amount=5000),
        _case(16, "Noah Davis", "At the original seven-day boundary", age=7, amount=3200),
        _case(17, "Emma Brown", "A zero-dollar refund request", age=0, amount=0),
        _case(18, "Milo Green", "A payment linked to another order"),
        _case(19, "Iris Wang", "An unknown order age"),
        _case(20, "Ezra Lee", "An older, high-value request", age=11, amount=18000, paid=20000),
    ]
    cases[3]["order"]["customer_id"] = "cus-another"
    cases[4]["payment"]["refunded_cents"] = 10000
    cases[5]["payment"] = None
    cases[11]["payment"]["status"] = "pending"
    cases[12]["order"]["status"] = "processing"
    cases[14]["payment"]["refunded_cents"] = 3000
    cases[17]["payment"]["order_id"] = "ord-another"
    cases[18]["order"]["age_days"] = None
    return copy.deepcopy(cases)
