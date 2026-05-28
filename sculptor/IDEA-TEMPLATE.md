# Idea Template

Write to `{idea-name}/idea.md`:

```markdown
# {Idea Name}

## One-liner
[Single sentence: what this is and why it matters]

## Problem
[Clear statement of the problem being solved]

## Who Is It For
[Target users/personas and their context]

## What Does Success Look Like
[Observable outcomes that mean this idea worked]

## Core Concept
[How it works — the essential design in prose. This is the heart of the document.]

## Key Design Decisions
[Choices already made and their rationale. Each as a numbered item or subsection
with the decision stated upfront and the trade-off discussed.]

## What This Is NOT
[Explicit non-goals and scope exclusions. Prevents scope creep during implementation.]

## Language / Runtime
[Tech stack choice and why]

## Open Questions
[Remaining uncertainties — flag which ones must resolve before spec]
```

## Scaling

For simple ideas, collapse to One-liner + Problem + Core Concept + Open Questions. For complex ones, add sections as needed (data model sketches, API shapes, user flows, etc.).

For early-stage exploration where the approach is not yet decided, use a lighter structure:
Problem + Context + Proposed Approaches (A, B, C) + Recommendation + Open Questions.

## Guidance

- **Deferred features**: When specifying Phase 2+ features in detail, explicitly call out cross-feature dependencies and shared interfaces. Users often ask for details on deferred features "so we can see how they influence each other" — surface these connections proactively.
- **Present design in sections** — Walk the user through each major section and get their reaction before moving on.
- **Reference appendix files**: When an approach relies on findings from a research appendix (API format, competitor behavior, technical constraint), link to it rather than restating. This keeps the idea document focused on the "what" while appendices hold the "evidence."
- **Link to appendices, don't restate**: When a design decision relies on research findings (benchmarks, API behavior, competitor analysis), link to the appendix rather than reproducing the evidence. Keep the idea document focused on decisions and rationale; appendices hold the supporting data.
