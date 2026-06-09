# TreeFlow Skill Feedback — Advisory Monitor Setup Session

**Date:** 2026-06-05
**Session scope:** Setup only (sculptor import → beads creation → dependency wiring → context files). No workers dispatched.

## What Went Well

### 1. `tf.py validate-plan` caught issues early
Validating the plan file before `bd create -f` is a good gate. It correctly counted 1 epic + 28 tasks and warned about missing `### Dependencies` sections. Saved a potential silent-failure situation.

### 2. `tf.py init` is clean and reliable
Single command created the context directory, wrote `registry.json`, copied `tf.py` to `.beads/`, and resolved the `bd` binary path. No manual steps needed.

### 3. `tf.py dep` idempotency saved time
Being able to loop through dependency pairs without worrying about duplicates or ordering made the bulk wiring scriptable. The `already_existed` field in the response is a nice touch for debugging.

### 4. Worker context template is well-structured
The template in `WORKER-CONTEXT-TEMPLATE.md` covers the right sections. The stack-specific gotcha comments (Go, TypeScript, Python, Chrome Extensions) are helpful — delete what doesn't apply, keep what does.

### 5. Pre-dispatch smoke test (`tf.py bd-path`) is a good safety net
Catching a broken `bd` path before workers hit it avoids a class of silent failures.

### 6. Skill routing is simple and effective
`tf.py routing --add "pattern:domain:prefix"` is easy to batch. 14 entries covered the whole project in one compound command.

## What Could Be Better

### 1. **Dependency wiring was the biggest friction point** (HIGH)

The sculptor plan at `docs/.beads/plan.md` already encoded all dependency information in `### Blocked By` sections with title-based references. But `bd create -f` silently ignored these — the recognized sections in PLAN-FORMAT.md are `### Dependencies` (with `blocks:` prefix syntax), not `### Blocked By`.

Result: I had to manually script 50+ `tf.py dep` calls in nested loops. This took ~40% of the session's effort and was entirely mechanical work that the tooling should have handled.

**Suggestions:**
- `tf.py wire-plan` should parse `### Blocked By` sections (not just `### Dependencies` with `blocks:` syntax). The sculptor plan format naturally produces `### Blocked By` with title references.
- Alternatively, `SCULPTOR-IMPORT.md` should explicitly say "rewrite `### Blocked By` sections to `### Dependencies` with `blocks:` prefix before running `bd create -f`" — at least then the orchestrator knows upfront.
- Best option: `bd create -f` should wire deps from `### Blocked By` sections during creation, since those are first-class `bd` metadata.

### 2. **SCULPTOR-IMPORT.md mentions `wire-plan` but the flow doesn't connect** (MEDIUM)

The doc says:
```bash
bd create -f plan.md --json > created.json
python3 .beads/tf.py wire-plan plan.md --ids created.json
```

But the plan file used `### Blocked By` (not `### Dependencies`), so `wire-plan` wouldn't have helped either. The two formats are incompatible. If `wire-plan` only handles `### Dependencies` with `blocks:` prefixes, the sculptor import path should say so and include a conversion step.

### 3. **Phase grouping is implicit — no first-class support** (LOW)

The plan has clear phases (Setup, Phase 1-6) with `[parallel]` markers, but beads/treeflow have no concept of phases beyond the skill instructions saying "use labels" or "add to descriptions." This means:
- Phase transitions require manual `phase-gate` calls with the epic ID
- There's no way to query "all tasks in Phase 2" without grep
- `tf.py conflict-check` operates on individual beads, not phase groups

This is fine for small projects but would be painful at 50+ tasks across 8+ phases.

### 4. **Setup tasks could be auto-batched** (LOW)

The 4 setup tasks (go mod init, directory scaffolding, config.local.yaml, Makefile) are tiny, closely related, and all touch the repo root. The skill instructions say "right-size dispatch" and "batch small related tasks," but identifying these as a batch is left to the orchestrator's judgment every time. A heuristic like "all P1 tasks with label `setup` → suggest single worker" would help.

### 5. **`bd doctor` version warning is noisy but not actionable mid-session** (LOW)

`bd doctor` reported v0.49.6 vs latest 1.0.4 — a major version gap. But upgrading mid-session is risky, and the skill doesn't say what minimum version it requires. Adding a `tf.py` check like "bd version >= X required" at init time would be more useful than surfacing this during doctor.

## Worker Usage & Reuse — Not Yet Tested

No workers were dispatched (setup-only session per user request). Observations on the planned dispatch:

- **Planned worker domains:** `go-core`, `go-claircore`, `go-grype`, `go-cli`, `duckdb`, `frontend`, `k8s`, `containers`
- **Planned batching opportunities:**
  - Setup (4 tasks) → 1 worker
  - Phase 1 core (Tasks 1-4) → 1-2 workers (all `go-core`, same package domain)
  - Phase 4 DuckDB (Tasks 13-16) → 1-2 workers (all `duckdb`)
  - Phase 5 Web (Tasks 17-19) → 1 worker (all `frontend`, sequential chain)
  - Phase 6 K8s (Tasks 22-23) → 1 worker (both `k8s`)
- **Estimated total workers:** ~10-12 for 28 tasks (0.4x ratio), well under the 1:1 target
- **Reuse candidates:** `go-core` worker from Phase 1 could handle Phase 1 integration test (Task 5), then potentially the CLI skeleton (Task 9) if context allows

## Token Efficiency Observations

- The skill instructions themselves are very long (~4k tokens). This is thorough but consumes significant context on every session start.
- The `bd create -f` output was the largest single response (~8k tokens for 29 issues with full descriptions). Piping through `jq -c` helped but the initial create dump is unavoidable.
- The dependency wiring loop produced ~50 lines of JSON confirmations — compact but still ~2k tokens. A batch `tf.py dep-bulk` command that accepts a file of pairs would reduce this to one call.

## Summary

| Aspect | Rating | Notes |
|--------|--------|-------|
| Init & setup | Great | Single command, clean output |
| Plan validation | Great | Caught format issues early |
| Dependency wiring | Needs work | Manual scripting for 50+ deps that were already declared in the plan file |
| Worker context | Great | Template covers the right sections |
| Skill routing | Good | Simple API, easy to batch |
| Documentation | Good | Thorough but could clarify sculptor → beads format conversion |
| Token efficiency | Good | `jq -c` piping helps; could use batch dep command |

**Top recommendation:** Make `### Blocked By` a recognized section in `bd create -f` or `tf.py wire-plan`, so dependency information from sculptor plans flows through without manual scripting. This single change would have saved ~40% of this session's effort.
