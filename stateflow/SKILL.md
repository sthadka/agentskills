---
name: stateflow
description: Orchestrates parallel execution using Beads issue graph and background AI workers, with awty workflow enforcement and TDD support. Dispatches implementation tasks to named worker agents, tracks progress, reuses workers by skill affinity, and maintains layered project context. Use for large multi-step projects, parallel implementation, or when a single context window would be insufficient.
allowed-tools:
  - Read
  - Write
  - "Bash(bd:*)"
  - "Bash(python3:*)"
  - "Bash(awty:*)"
  - Agent
---

# StateFlow — Orchestrated Parallel Execution

You are a **pure orchestrator**. You NEVER read or write project source code. You plan work using Beads (`bd`), spawn named background workers, track progress via `tf.py`, and maintain layered project context.

The awty workflow (`workflow.yaml`) enforces phases, tool budgets, and routing. Parsers auto-populate context from command output. Guards enforce transition preconditions. State-scoped instructions tell you what to do in each phase — this file covers only cross-cutting rules.

## Setup

```bash
awty init stateflow/workflow.yaml
```

Hooks (add to `.claude/settings.json`):
```json
{
  "hooks": {
    "PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "awty check-tool"}]}],
    "PostToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "awty parse-output"}]}]
  }
}
```

## Rules

1. **Never touch code** — only `.beads/` files, context docs, `bd`/`tf.py` commands. Never `git add`/`commit` source files.
2. **Beads is truth** — every strategic action = bead update.
3. **Name workers by domain** — `{domain}-{N}` (e.g., `commands-1`). Makes them addressable via SendMessage.
4. **Summaries only** — store 1-line summaries from `tf.py notify`, discard full `<result>` content.
5. **Layered context** — workers get project > epic > feature > task context. See [CONTEXT-MANAGEMENT.md](CONTEXT-MANAGEMENT.md).
6. **File boundaries** — never spawn parallel workers writing to the same files.
7. **JSON-compact** — `bd` commands with `--json | jq -c`. Prefer `tf.py close`/`tf.py dep` over raw `bd`.
8. **Workers close via `tf.py worker-close`** — validates commits, closes bead, verifies.
9. **Batch small tasks** — 3+ same-domain tasks → one worker. Max 4 tasks/batch.
10. **All state through `tf.py`** — never edit `registry.json` manually.
11. **Transitions through awty** — fire `awty transition <EVENT>` at every phase boundary.

## TDD

Tasks marked `[TDD]` get RED/GREEN/REFACTOR instructions in worker prompts (see [WORKER-PROMPT.md](WORKER-PROMPT.md) § TDD Instructions). The `tdd_verification` state does post-hoc checks since hooks don't fire in sub-agents.

**Mark [TDD] when:** spec-required behavior with clear I/O, parsers, validators, data pipelines.
**Skip TDD for:** wiring/glue, config, tasks with no testable output.

## tf.py Quick Reference

```
init, dispatch, notify, sync, stalled, status, registry, retire
conflict-check, phase-complete, phase-gate, smoke-test, routing
validate-plan, wire-plan, worker-prompt, update-context, close, dep, bd-path
Worker: claim, block, discover, heartbeat, worker-close
```

All output compact JSON. See [COMMANDS.md](COMMANDS.md) for full args.

## Planning Notes

- Target file paths in every task description
- `[parallel]` markers for independent tasks within a phase
- Acceptance criteria citing spec sections
- `python3 .beads/tf.py validate-plan plan.md` before `bd create -f`
- Reference identifiers (function/class names), not line numbers
- See [PLAN-FORMAT.md](PLAN-FORMAT.md) and [SCULPTOR-IMPORT.md](SCULPTOR-IMPORT.md)

## Context & Recovery

- Context in `.beads/context-{plan-name}/`. Archive files > 500 lines.
- After context compression: `awty state` → `tf.py status` → `tf.py registry` → `bd ready`
- Workers can't message orchestrator directly — they block beads, orchestrator surfaces questions.

## Anti-Patterns

- Reading/writing source code or running `git add`/`commit` on source
- Running `git stash -u` (stashes `.beads/context-*/`)
- Keeping full `<task-notification>` results in context
- Spawning workers without `name` parameter
- Spawning fresh without `tf.py sync` first
- Dispatching without `tf.py conflict-check`
- Skipping `awty transition` at phase boundaries
- Using `bd dep add` instead of `tf.py dep`
- Skipping TDD verification for `[TDD]` tasks
- Using bare `bd list --json` without `--limit 500` — bd defaults to 50 results, silently truncating large graphs
