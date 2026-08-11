# BeadFlow Command Reference

## bf.py — Quality Layer

Always close via `bf.py` instead of raw `bd close`:

```bash
python3 .beads/bf.py init --bd-path "$(which bd)"     # Setup (copies bf.py, stores bd path)
python3 .beads/bf.py ready                             # Filtered ready (no epics, supplements capped results)
python3 .beads/bf.py verify --files "f1,f2"            # Mid-task quality check
python3 .beads/bf.py close <id> --files "f1,f2" --summary "AC: pass. ..."  # Validate + close
python3 .beads/bf.py close <id> --force                # Skip validation
python3 .beads/bf.py smoke-test --build-cmd "cmd" --beads a,b  # Build + wiring check
python3 .beads/bf.py conflict-check --beads a,b,c      # File-conflict analysis for parallel dispatch
python3 .beads/bf.py dep <blocker> <blocked>            # Idempotent dep addition
python3 .beads/bf.py import-graph graph.jsonl           # Import sculptor graph
```

## bd — Issue Tracker

### Batch Creation (preferred for multiple issues)
```bash
bd create -f plan.md --json
```
Write a `.md` file with all issues, then create them all in one command. See [PLAN-FORMAT.md](PLAN-FORMAT.md) for the file format.

### Single Issue Creation (with combined flags)
```bash
bd create "Title" -t <type> -p <priority> -d "Description" --parent <parent-id> --json
bd create "Title" -t bug -p 1 --deps "discovered-from:<id>" --json
bd q "Title" -t task -p 2                  # Quick capture: outputs only the ID
```
Use `--deps` to create with dependencies in one command. Use `--parent` for hierarchy.

### Find Work
```bash
python3 .beads/bf.py ready                  # PREFERRED: filtered, no epics, supplements capped
bd ready --json                             # Raw: unblocked, actionable issues
bd blocked --json                           # Blocked issues
bd list --json                              # All issues
bd show <id> --json                         # Full issue details
bd show <id1> <id2> --json                  # Batch show multiple issues
```

## Update (supports multiple IDs)
```bash
bd update <id> --status in_progress --json
bd update <id1> <id2> <id3> --priority 0 --json
bd update <id> --status blocked --json
bd update <id> --notes "COMPLETED: X. NEXT: Y" --json
bd update <id> --append-notes "Progress update" --json
```

## Close (supports multiple IDs)
```bash
bd close <id> --reason "Done" --suggest-next --json   # Close and get next ready issue
bd close <id1> <id2> <id3> --reason "Batch done" --json
```

## Dependencies

> **CRITICAL: argument order for `bd dep add` is `<blocked-id> <blocker-id>` (blocked first, blocker second).**
> Use `bd dep <blocker-id> --blocks <blocked-id>` to avoid confusion — it reads naturally and is unambiguous.

```bash
# Preferred: unambiguous --blocks syntax
bd dep <blocker-id> --blocks <blocked-id> --json               # blocker blocks blocked
bd dep <child-id> --blocks <parent-id> -t parent-child --json  # WRONG for hierarchy (see below)

# Hierarchy uses dep add (child depends on parent):
bd dep add <child-id> <parent-id> -t parent-child --json       # child belongs to parent

# Chain multiple with --blocks:
bd dep <id1> --blocks <id2> && bd dep <id3> --blocks <id4>     # chain multiple blockers
```

**Argument order reference:**
- `bd dep add A B` -> A depends on B (B blocks A). First arg is BLOCKED, second is BLOCKER.
- `bd dep A --blocks B` -> A blocks B. Reads naturally. Use this for all blocking deps.

## Comments
```bash
bd comments add <id> "Progress notes" --json
```

## Visibility
```bash
bd graph --all                              # Full dependency graph
bd graph <epic-id>                          # Epic-specific graph
```

## Session End
```bash
bd dolt push                                # ALWAYS run before session end (bd sync is deprecated)
```

## Command Chaining

Chain sequential operations in a single Bash tool call with `&&`:

```bash
# Claim and show in one call
bd update <id> --status in_progress --json && bd show <id> --json

# Block current + create unblocking task in one call
bd update <id> --status blocked --json && bd create "Unblock: <reason>" -t task -p 1 --deps "<blocked-id>" --json

# Decompose large issue into subtasks in one call
bd create "Subtask 1" -t task --parent <id> --json && bd create "Subtask 2" -t task --parent <id> --json && bd close <id> --json
```
