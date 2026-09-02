# BeadFlow Command Reference

## Batch Creation (preferred for multiple issues)
```bash
bd create -f plan.md --json
```
Write a `.md` file with `## Title` + description body per issue, then create them all in one command. For graph-based creation with deps, use `bd create --graph beads-graph.jsonl`.

## Single Issue Creation (with combined flags)
```bash
bd create "Title" -t <type> -p <priority> -d "Description" --parent <parent-id> --json
bd create "Title" -t bug -p 1 --deps "discovered-from:<id>" --json
bd q "Title" -t task -p 2                  # Quick capture: outputs only the ID
```
Use `--deps` to create with dependencies in one command. Use `--parent` for hierarchy.

## Find Work
```bash
bd ready --json                             # Unblocked, actionable issues (includes full details)
bd blocked --json                           # Blocked issues
bd list --json                              # All issues
bd show <id> --json                         # Full issue details (use only when ready output is insufficient)
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

> **Prefer `tf.py close`** when you need reliable JSON output — `bd close --json | jq` can fail due to inconsistent output format. `tf.py close <id> --reason "..."` normalizes the result to `{"ok":true,"id":"...","status":"closed"}`.

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

# Preferred: idempotent tf.py wrapper (handles UNIQUE constraint errors gracefully)
python3 .beads/tf.py dep <blocker-id> <blocked-id>             # blocker blocks blocked
python3 .beads/tf.py dep <blocker-id> <blocked-id> --remove    # remove edge; emits JSON (bd dep remove does not)
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
git remote -v | grep -q push && git push || echo "No remote configured, skipping push."
```
`bd sync` and `bd dolt push` are deprecated — use `git push` directly.

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

## `tf.py` Extended Commands

These commands extend the base orchestration beyond what `bd` provides:

```bash
# Find dispatchable tasks (filters epics, supplements missed unblocked beads)
python3 .beads/tf.py ready

# Find orphaned beads after context compaction (in_progress with no active worker)
python3 .beads/tf.py recover

# Register ad-hoc task for stall detection (no bead required)
python3 .beads/tf.py ad-hoc --name "refactor-tests" --worker refactor-1 --skill python

# Record completion with agent ID for reliable reuse
python3 .beads/tf.py notify {worker} {bead} --context-pct N --summary "..." --agent-id {id}

# Detect sculptor over-linearization before dispatch (serial chains that should be parallel)
python3 .beads/tf.py validate-graph --plan plan.md

# Archive oversized context files by BYTE size (not line count) and replace with a digest
python3 .beads/tf.py archive-context                          # all files > ~48KB
python3 .beads/tf.py archive-context --file epic-foo.md --force

# Build/test gate — build-only by default; tests run ONLY with --live (guards against
# slow/costly live test runs triggered by cloud env vars). Never chain with notify.
python3 .beads/tf.py verify --build-cmd "go build ./..."
python3 .beads/tf.py verify --build-cmd "go build ./..." --test-cmd "go test -short ./..." --live
```
