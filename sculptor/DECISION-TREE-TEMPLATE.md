# {Idea Name} — Decision Tree

The shape of the design and how it got there. Each node is a decision reached during the interview: the question, the options weighed, the answer, and why. Reading it top to bottom shows how the final shape emerged — the record that a plain spec throws away.

`export-beads` copies this to `.beads/decision-tree.md` so it travels with the handoff. Keep it a record of *decisions and rationale*, not implementation detail (that lives in the spec).

## How to read this

- Nodes are numbered by the order they were settled. Child nodes (`1.1`, `1.2`) are decisions that only became askable once the parent was settled.
- **Options** lists what was genuinely on the table. **Chosen** is the answer. **Why** is the rationale — the trade-off that decided it.
- Link out to an appendix (see `APPENDIX-TEMPLATE.md`) for any node backed by a substantive exploration.

## Tree

### 1. {First decision — the root question}

- **Options**: A — {…}; B — {…}; C — {…}
- **Chosen**: B
- **Why**: {the trade-off that decided it}
- **Evidence**: [appendix-{topic}.md](appendix-{topic}.md) *(if a substantive exploration informed it)*

#### 1.1 {Decision unblocked by choosing B above}

- **Options**: {…}
- **Chosen**: {…}
- **Why**: {…}

### 2. {Next root-level decision}

- **Options**: {…}
- **Chosen**: {…}
- **Why**: {…}

## Open questions

Decisions deferred (e.g. need prototyping). Each becomes an explicit open question in the idea doc.

- {question} — deferred because {reason}; resolve by {how}.
