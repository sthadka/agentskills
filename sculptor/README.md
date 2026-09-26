# Sculptor

Turn a vague idea into an implementation-ready spec and plan through file-based
annotation cycles you drive. The agent writes markdown; you annotate; it addresses your
marks and revises. The agent instructions live in [SKILL.md](SKILL.md); this README is
for **you**, the human doing the annotating.

## How to annotate

Mark up the document however is convenient. When you're done, tell the agent — it runs
`sculptor.py annotations <file>` to collect your marks with their surrounding context, so
you never have to describe where a comment applies.

### 1. Fix small things directly

Just edit the prose. The tool picks up your change from git — no marker needed. Use this
for anything you'd simply correct yourself.

### 2. Leave a `>>` comment for things to discuss

A `>>` line attaches to the thing **directly above it**:

- under a line → comments on that line
- under a heading → comments on the whole section
- for a multi-line block → wrap it in a span fence (below)

Prefixes are optional but useful:

| Prefix | Meaning | Example |
|--------|---------|---------|
| `>>` | Correction / statement | `>> this should use WebSocket, not polling` |
| `>> ?` | Question | `>> ? why not use Redis instead of SQLite` |
| `>> +` | Addition | `>> + also needs to handle pagination` |
| `>> -` | Remove this | `>> - cut this section, out of scope` |
| `>> *` | Strong opinion | `>> * must be backwards compatible` |
| `>> explain` | Ask for an explainer | `>> explain what is CRDT convergence` |

Bare `>> free text` is always fine — intent is read from context.

### 3. Point at an inline phrase

Wrap a span inside a line with any marker: `// like this //` or `[[ like this ]]`. Put
spaces around the marker.

Offline alternatives (when the file isn't in git): name the phrase with a leading quote —
`>> "goes on and on" -> goes forever` — or point at it with column-aligned carets on the
next line:

```
The word polling is wrong
>>          ^^^^^^^ use WebSocket
```

### Span fences (multi-line block)

Open with `>>` + a delimiter alone; close with the matching delimiter + your comment.
Families: `{}`, `[]`, `()`, and symmetric `//`; a space after `>>` is optional.

```
>>{
first target line
second target line
>>} cut this block, out of scope
```

## What happens next

The agent reads your marks, addresses each one, and commits a **clean** revision (every
marker is removed — they never persist in the document). It then writes a per-round
resolution report so you can see, per annotation, whether and how it was addressed.

## Validation tool

`sculptor.py` provides deterministic checks (`annotations`, `verify-clean`, `report`, and
the lint/export commands). See [VALIDATION.md](VALIDATION.md) for the full command table.
