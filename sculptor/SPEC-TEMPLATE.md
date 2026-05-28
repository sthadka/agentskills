# Technical Spec Template

## Structure

```markdown
# Technical Spec: {Idea Name}

## Architecture
[High-level design, system boundaries, package/module layout]

## Data Model
[Exact table schemas with column names, types, and constraints.
Exact struct/class definitions. Not "a user table" — the actual CREATE TABLE or type definition.]

## API Surface
[Exact endpoint paths, request/response shapes, error formats.
Include code snippets for non-obvious logic — edge cases, parsing, retries.]

## Integrations
[External systems, dependencies, expected response formats.
Include representative sample payloads in fenced code blocks for complex external data.]

## Security & Privacy
[Authentication, authorization, data handling]

## Known Gotchas
[Language version constraints, common pitfalls with chosen frameworks,
initialization patterns to avoid, idiomatic preferences (e.g. `any` vs `interface{}`)]
```

## Quality Checklist

Before finalizing, verify the spec includes:
- Exact schemas/types (not prose descriptions of data)
- Code snippets for any non-obvious logic or edge cases
- Sample payloads for external data the system will consume
- Language/framework version requirements and feature availability
- Known pitfalls with the chosen tech stack

## Conditional Sections

The template above covers the common case. Some project types have domain-specific concerns that need their own sections. Recognize when generic headings don't capture the project's essential structure:

- A data pipeline may need "Stage Topology" or "Error Recovery"
- A CLI tool may need "Command Tree" and "Flag Conventions"
- A real-time system may need "Latency Budget" and "Backpressure"

When you identify these, add them as peer sections in the spec. The test: "Would an implementing agent have to guess about this without a dedicated section?" If yes, add the section.

## Using Appendix Files

When the spec references external data formats, integrations, or complex inputs, link to appendix files rather than duplicating sample payloads inline. See [APPENDIX-TEMPLATE.md](APPENDIX-TEMPLATE.md) for the appendix format.

- Reference appendices from the Integrations and Data Model sections: e.g., "See [appendix-jira-api.md](appendix-jira-api.md) for full response payloads"
- If no appendix exists yet but the spec needs sample data, create one during spec writing

## When to Split

Split the spec into `spec.md` and `spec-examples.md` when:

- **Distinct audiences**: Architecture decisions for reviewers vs. implementation details for workers. Split so each audience gets a focused document.
- **Code examples dominating**: When complete function bodies, field maps, or sample payloads push the spec past the point where the design narrative is hard to follow, move implementation-grade code to `spec-examples.md`.

Don't split by line count alone. A 600-line spec that reads linearly is better than a 400-line spec with a companion file that fragments the reader's attention.

Both files travel together. The spec links to examples: `See [spec-examples.md](spec-examples.md) §Implementation — ACSClient` rather than inlining the full code.

## Learnings

* **Specs must be implementation-grade, not description-grade.** The difference between "a user table with standard fields" and an exact `CREATE TABLE` statement with column names, types, and constraints is the difference between an implementing agent that guesses and one that executes cleanly.
* **Include sample data for complex external inputs.** If the system consumes external API responses (Jira, Stripe, GitHub, etc.), include 2-3 representative JSON payloads in appendix files. Link from the spec's Integrations section.
* **Note language version and feature availability.** If the spec uses language features (e.g. Go's `iter.Seq2`, Python 3.12 type parameter syntax), explicitly state the required version.
* **Surface known gotchas.** Every tech stack has pitfalls (cobra init cycles in Go, circular imports in Python, hydration mismatches in React). Document them in the spec.
* **Surface shared design surfaces.** When research appendices reveal data structures or config formats that serve multiple interfaces, pull these into the Architecture section so they're designed once, not discovered late.
