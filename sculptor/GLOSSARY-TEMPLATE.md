# {Idea Name} — Glossary

One or two sentences on what this idea is, so a term's context is clear.

This file is the idea's **ubiquitous language**: the shared vocabulary every downstream worker must speak. It is a glossary and nothing else — no implementation details, no spec, no scratch notes. `export-beads` copies it into `.beads/glossary.md` so it travels with the beads handoff to treeflow/beadflow, where it becomes a worker context layer. One shared language across parallel workers is what stops cross-cutting behaviors silently diverging.

## Language

**Order**:
A customer's request to buy, from placement to fulfillment.
_Avoid_: purchase, transaction

**Invoice**:
A request for payment sent to a customer after delivery.
_Avoid_: bill, payment request

**Customer**:
A person or organization that places orders.
_Avoid_: client, buyer, account

## Rules

- **Be opinionated.** When multiple words name the same concept, pick the best one and list the rest under `_Avoid_`.
- **Keep definitions tight.** One or two sentences. Define what it IS, not what it does.
- **Only terms specific to this idea.** General programming concepts (timeouts, retries, error types) don't belong even if used heavily. Ask: unique to this domain, or generic? Only the former.
- **Group under subheadings** when natural clusters emerge; a flat list is fine when the terms cohere.
- **Capture terms the moment they resolve** during the interview — don't batch at the end.
