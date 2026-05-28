# Research Template

Write findings to `{idea-name}/research.md` with these sections:

```markdown
# Research: {Idea Name}

## Problem Statement
[What problem exists, who has it, constraints that shape the solution]

## Landscape
[Organized per-tool or per-project. Each subsection covers one existing solution,
competitor, or relevant project.]

### {Tool/Project A}
[What it does, how it works, strengths, weaknesses, relevance to our approach]

### {Tool/Project B}
[What it does, how it works, strengths, weaknesses, relevance to our approach]

## Available Resources
[APIs, libraries, data sources, infrastructure we can leverage.
Concrete assessment of each — not just a list.]

## Approach Options

### Option A: {Name}
[Description, trade-offs, effort estimate]

### Option B: {Name}
[Description, trade-offs, effort estimate]

### Recommendation
[Which option and why]

## Out of Scope
[Things we have explicitly decided to exclude]

## Open Questions
[Things we still need to figure out — flag which block the next phase]

## Sources
[Links, documents, commands used to gather findings]
```

## Appendix Files

For any topic that warrants a deep dive (competitor analysis, API exploration, benchmark results, technical deep-dives), create a separate appendix file rather than bloating the research document. See [APPENDIX-TEMPLATE.md](APPENDIX-TEMPLATE.md) for the format.

Link each appendix from the relevant section above, e.g.:
```markdown
## Landscape
...detailed analysis in [appendix-competitor-analysis.md](appendix-competitor-analysis.md)

## Available Resources
...API response formats documented in [appendix-stripe-api.md](appendix-stripe-api.md)
```
