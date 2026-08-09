# Explainer Appendix Template

Create one explainer file per concept that stakeholders at different levels need to understand: `{idea-name}/appendix-explain-{concept}.md`. These build layered understanding of a single concept — each altitude assumes you read the one above it.

## When to Create an Explainer

- A concept is central to the idea and non-obvious to at least one stakeholder group
- The user annotates a passage with `>> explain` (or `>> ? what is {concept}`)
- Research surfaces a topic where misunderstanding would derail design decisions (CRDTs, eBPF, capability-based security, etc.)
- The idea crosses domain boundaries and different readers need different entry points

**Don't create explainers for:** well-known concepts your audience already shares, topics covered adequately by a single sentence in the idea doc, or implementation details that belong in the spec.

## Structure

```markdown
# Explainer: {Concept Name}

> One sentence: what this concept is and why it matters to this idea.

## Intuition
[For: non-technical stakeholders, newcomers, anyone who needs the "why" without the "how."]

[One analogy that maps the concept to something the reader already knows. Household,
physical-world, or everyday-business analogies work best. No jargon. One idea per sentence.]

[End with: why should they care? What breaks or gets better because of this concept?]

## Mental Model
[For: technical generalists — engineers from other domains, product managers with
technical background, senior leadership making architecture bets.]

[How it works conceptually. Diagrams-in-prose: "X talks to Y, which decides Z."
Introduce the 2-3 terms they'll hear in meetings and define each on first use.
Compare to concepts they likely know: "Like git branches, but for live data."]

[Trade-offs at this level: what you gain vs. what you give up, in terms they can
weigh against alternatives.]

## Mechanics
[For: implementers — the engineers who will build with or around this concept.]

[How it actually works. Data structures, algorithms, protocol steps, failure modes.
Use proper terminology — this audience expects it. Code snippets and pseudo-code
are welcome when they clarify.]

[Call out the non-obvious: ordering guarantees, consistency boundaries, performance
characteristics, failure semantics. These are the things that bite you during
implementation.]

## Edge Cases
[For: specialists, reviewers, and future-you debugging at 2am.]

[Where the concept breaks down or behaves unexpectedly. Boundary conditions,
known limitations, common misunderstandings. "Most people assume X, but actually Y
when Z happens."]

[If relevant: how this concept interacts with other concepts in the idea. Where do
the abstractions leak?]

## Sources
[Links, papers, docs, or commands used to build this explainer.
Prefer primary sources over blog posts.]
```

## Altitude Levels

Each level builds on the previous. A reader can stop at the level that matches their need.

| Level | Section | Audience | Registers |
|-------|---------|----------|-----------|
| 1 | Intuition | Non-technical stakeholders | Analogies, outcomes, no jargon |
| 2 | Mental Model | Technical generalists | Concepts, trade-offs, comparison to known patterns |
| 3 | Mechanics | Implementers | Data structures, algorithms, code, failure modes |
| 4 | Edge Cases | Specialists, reviewers | Boundary conditions, gotchas, abstraction leaks |

**Not all levels are always needed.** A concept familiar to engineers but alien to product managers might only need Intuition + Mental Model. A subtle algorithm that only implementers care about might skip Intuition entirely and start at Mechanics. Include the levels your actual audience needs — don't pad.

## Tone Guidance

| Level | Tone | Anchors to |
|-------|------|-----------|
| Intuition | Warm, confident, like explaining over coffee | Everyday experience |
| Mental Model | Clear, direct, respects intelligence | Adjacent technical concepts they know |
| Mechanics | Precise, dense, no hand-holding | The actual implementation |
| Edge Cases | Candid, slightly cautious | What will surprise or bite you |

**Never talk down.** A simpler altitude is not a dumber explanation — it's a different lens on the same concept. The Intuition level should feel delightful and empowering, not dumbed-down.

## Writing Rules

1. **Each level must stand alone enough to be useful.** A product manager should be able to read Intuition + Mental Model and make informed decisions without reading further.
2. **No forward references.** Don't say "as explained in the Mechanics section" from within Intuition. Each level can reference the ones above it, never below.
3. **One analogy per level, max.** A good analogy illuminates; two compete. Pick the one that covers the most surface area.
4. **Analogies must be accurate, not just vivid.** If the analogy breaks down in ways that matter, say where: "Unlike a post office, messages here can arrive out of order."
5. **Define jargon on first use at each level.** Mental Model can introduce terms; Mechanics can assume them. But each level defines its own terms — don't assume the reader came from the level above.
6. **Keep Intuition under 150 words.** If you need more, the analogy isn't working — find a better one.
7. **Code in Mechanics only.** Intuition and Mental Model use prose and maybe a diagram-in-words. Save code snippets for the audience that reads code.

## Guidance

- **Link from research.md and idea.md** — Reference the explainer from wherever the concept first appears. Use the form: `...relies on [concept name](appendix-explain-{concept}.md) for convergence.`
- **Link from spec.md at the Mechanics level** — When the spec references the concept, link directly to the Mechanics section: `[concept — mechanics](appendix-explain-{concept}.md#mechanics)`.
- **Name descriptively** — `appendix-explain-crdt-convergence.md`, not `appendix-explain-1.md`.
- **Cross-link between explainers** — When one concept depends on another (e.g., "vector clocks" in a CRDT explainer), link to its explainer if one exists: `See [vector clocks](appendix-explain-vector-clocks.md#mental-model)`.
- **Don't duplicate into the idea doc** — The idea doc should reference the explainer, not restate its content. The explainer is the canonical source for "what is this concept and why."
- **Revisit during annotation** — When user annotations suggest a concept isn't landing, update or create an explainer rather than adding more prose to the idea doc.
