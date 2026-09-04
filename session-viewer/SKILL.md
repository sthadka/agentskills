---
name: session-viewer
description: Parses and displays Claude Code session JSONL files as a token-efficient map, drill-down sections, structured JSON, error reports, or file summaries. Use when the user wants to view, inspect, analyze, debug, or compare Claude Code sessions by ID or file path. Also lists available sessions.
---

# Session Viewer

Parses Claude Code session JSONL files from `~/.claude/projects/` and renders them
in **progressively disclosed**, agent-optimized formats: start with a cheap map,
then drill into exactly the part you need instead of loading a whole session.

## Usage

```bash
python session-viewer/claude_session.py <session-id|path> [mode] [flags]
python session-viewer/claude_session.py --list [project-filter]
```

## The disclosure ladder — start cheap, drill in

**Default is the map.** A bare `<session-id>` returns a one-line-per-turn outline
(large sessions) or the whole transcript (small sessions, auto-detected). Never
dump the full transcript first — it can be 80k+ tokens. Work down the ladder:

| Step | Command | Returns | Cost |
|------|---------|---------|------|
| 1. Map *(default)* | `<id>` | One line per user-prompt turn: headline, tool rollup, error/compaction markers, msg range, `~tokens`. | ~1 line/turn |
| 2. Section | `<id> --turn T2` / `--section 36:198` | Full detail for one work unit. | one turn |
| 3. Overview | `<id> --summary` | Aggregate stats: user prompts, tool counts, file ops, turns. | ~1k |
| 4. Structured | `<id> --json` | Compact JSON: metadata, sections, tool counts, errors, files. Add `--full` for the whole `tool_calls` array. | tunable |
| 5. Targeted | `<id> --errors` / `--files` / `--grep RX` / `--tool-result` | Just failures / paths / matches. | small |
| 6. Full | `<id> --full` | Full transcript. **Explicit opt-in.** | up to ~80k+ |

The map prints the exact `--turn`/`--section` argument next to each row — drilling
is copy-paste. Every level prints its own `~tokens` cost so you can decide whether
to go deeper. Use `--estimate` to compare all modes' sizes before choosing one.

## Modes

| Flag | Output | When to use |
|------|--------|-------------|
| *(none)* | Map (or whole transcript if small) | **Always start here** |
| `--map` | Force the map even for small sessions | Overview of a small session |
| `--turn T<n>` | Expand one section | Drill into a specific prompt's work |
| `--section A:B` | Expand a message range | Drill into arbitrary bounds |
| `--summary` | Aggregate stats | Overview before deeper analysis |
| `--json` | Compact structured JSON | Machine parsing; add `--full` for tool_calls |
| `--compact` | One line per event | Trace full conversation flow |
| `--errors` | Failed tool calls + API errors | Debugging failures |
| `--files` | File operations (Read/Write/Edit) | Understanding what changed |
| `--tools-only` | Tool calls and results | Reviewing tool usage |
| `--grep REGEX` | Only matching entries | Find where a term appears |
| `--estimate` | Per-mode size/token table | Choose the cheapest mode |
| `--full` | Full transcript | Complete end-to-end review |

## Flags

Combinable with any mode:

| Flag | Effect |
|------|--------|
| `--redact` | Strip secrets (tokens, passwords, API keys, bearer tokens, credential URLs) |
| `--thinking` | Include thinking blocks |
| `--no-results` | Omit tool results (calls only) |
| `--no-timestamps` | Drop `[HH:MM:SS]` prefixes |
| `--expand` | Resolve persisted tool results (large outputs saved to `<session-id>/tool-results/`) |
| `--subagents` | Include subagent sessions (spawned Agent calls with their own JSONL) |
| `--include-meta` | Keep meta/injected user turns (default: filtered from prompts) |
| `--pretty` | Pretty-print JSON (default is compact) |
| `--last N` | Restrict to the last N sections |
| `--max-result N` | Cap tool-result chars (default 3000) |
| `--max-text N` | Cap message-text chars (default 5000) |
| `--project NAME` | Disambiguate session-id lookup to one project |

## Determinism guarantees

- Session ids resolve to **exactly one** file; ambiguous ids error with the full
  candidate list rather than silently picking one. Use `--project` to disambiguate.
- Output is idempotent — same input, byte-identical output.
- JSON carries `"_schema": "session-viewer/1"` as its first key.
- `--list` timestamps are UTC; subagent traversal order is sorted.

## Workflow

1. **Start with the default map** to see the session's shape and get drill coordinates.
2. **`--turn T<n>`** into the section that matters.
3. **`--errors`** if failures occurred; **`--files`** to see what changed.
4. **`--grep`** to locate a term; **`--json`** for machine parsing.
5. **`--full`** only when a complete transcript is genuinely needed.

## Session JSONL Schema

See [SCHEMA.md](SCHEMA.md) for the Claude Code session file format reference.
