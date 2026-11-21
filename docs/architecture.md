# How OntologyJev makes a decision

The first domain is a refund request. The Python SDK combines supplied business facts and a semantic observation, then returns a proposed action with the evidence for every rule.

```text
Customer message -> semantic provider -> intent observation
                                              |
Customer -> Order -> Payment -> rule checks -> decision + trace
                                    ^
                           versioned policy
```

## Objects and relationships

`Customer.id` must match `Order.customer_id`. `Order.payment_id` must match `Payment.id`, and `Payment.order_id` must match `Order.id`. These identifiers establish the relationships that the rules inspect. The caller supplies and authenticates these facts; the SDK does not query an account database.

The ontology metadata describes three entity types, two relationships and three proposal actions. Current rules are implemented in Python. Changing the metadata alone does not change execution. This release has no arbitrary schema compiler, OWL/RDF reasoner or automatic ontology induction.

## Observations and rules

The provider sees only the customer message and the refund-intent question. It does not authorize a refund. Jev uses its System One API; Ollama uses local structured JSON generation. Ollama scores carry `source=ollama`, `score_kind=generated_self_report` and `calibrated=False`.

The engine checks ownership, payment linkage, payment status, remaining balance, order status, refund window, automatic amount limit and intent threshold. A known hard failure produces `deny`; otherwise missing evidence or a review rule produces `review`; all passing checks produce `allow` / `propose_refund`. No payment is executed.

Each rule records the observed facts, expected condition, source paths, status and reason. Each decision includes the normalized observation and a hash of the policy. An input object or provider name is not a trust certificate.

## Comparison, live trial and regression

The main comparison uses 20 labeled synthetic cases. It fixes each observation while evaluating the baseline and draft policies, isolating policy changes from new model output.

A browser live trial copies the selected case and submitted message, makes one explicit provider request, and evaluates that observation under both policies. Its exported JSON contains the live evidence. It does not overwrite synthetic comparison rows or save a regression implicitly.

Regression snapshots preserve the case, observation, policy and a user-chosen expected outcome. Replay makes no model calls. These expectations test preserved behavior; they are not independent ground-truth labels and do not measure model accuracy on new messages.
