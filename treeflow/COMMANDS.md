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

## `tf.py` Full Reference

State management commands — all output compact JSON:

```bash
# Orchestrator commands
python3 .beads/tf.py init {plan-name} [--bd-path PATH] [--worker-model MODEL] [--idle-timeout N]  # Create context dir + registry + gitignore (--bd-path auto-detected via shutil.which; idle-timeout: minutes before auto-retire, default 8)
python3 .beads/tf.py dispatch {worker} {bead-id}[,bead-id2] --skill {domain} [--output-file path] [--agent-id ID]  # Record dispatch (agent-id stores the Agent tool's runtime ID for compaction resilience)
python3 .beads/tf.py notify {worker} [bead] --context-pct N [--auto] [--summary "..."] [--skill domain] [--agent-id ID] [--files "f1,f2"] [--gotcha "..."] [--tokens N] [--duration-ms N]  # Record completion (--auto infers bead/files/skill from registry + git; --tokens/--duration-ms track cost)
python3 .beads/tf.py batch-notify --pairs "w1:bead1,w2:bead2" --context-pct N [--summary "..."] [--files "f1,f2"] [--gotcha "..."]  # Batch completion for multiple worker:bead pairs
python3 .beads/tf.py phase-gate {epic-id}                # Check phase complete
python3 .beads/tf.py smoke-test --build-cmd "cmd" --beads a,b  # Build + wiring check
python3 .beads/tf.py conflict-check --beads a,b,c                     # File-conflict analysis (section-aware: [section] annotations → low_risk)
python3 .beads/tf.py wave-plan --beads a,b,c                          # Compute dispatch waves from ready beads (uses conflict-check + active worker files)
python3 .beads/tf.py sync [--ready-count N]                           # Pre-dispatch: retire stale, flag stalled, return reusable workers
python3 .beads/tf.py stalled [--threshold-mins N]              # List stalled active workers (default 20 min)
python3 .beads/tf.py registry [--status idle] [--skill domain]  # Query workers
python3 .beads/tf.py registry --worker-model              # Print configured worker model
python3 .beads/tf.py retire {worker}                     # Mark worker retired
python3 .beads/tf.py routing --add "pattern:domain:prefix"  # Add routing entry
python3 .beads/tf.py status                              # One-line overview
python3 .beads/tf.py close {bead_id} --reason "..."                    # Close bead with normalized JSON output
python3 .beads/tf.py ready                                             # Dispatchable tasks (filtered epics, supplemented from bd list)
python3 .beads/tf.py recover                                           # Find orphaned in-progress beads (post-compaction recovery)
python3 .beads/tf.py ad-hoc --name {name} --worker {worker} [--skill domain]  # Register informal task for stall detection
python3 .beads/tf.py dep {blocker} {blocked} [--remove]                # Add (or --remove) dep idempotently; always emits JSON (bd dep remove does not)
python3 .beads/tf.py validate-graph [--plan plan.md]                    # Detect suspected sculptor over-linearization (serial chains); cross-check plan [parallel] markers
python3 .beads/tf.py archive-context [--file NAME] [--max-bytes N] [--force]  # Archive context files > ~48KB by BYTE size, replace with digest
python3 .beads/tf.py import-graph {file}                                # Import beads-graph.jsonl via bd create --graph
python3 .beads/tf.py worker-prompt --beads {id}[,id2,id3] [--reuse --prior-bead {prev}] [--parallel-with bead1,bead2] [--prompt-only] [--write-file] [--inline-context]  # Assemble worker prompt
# --prompt-only: print raw prompt to stdout (no JSON). --write-file: write prompt to temp file, return {"prompt_file": path} instead of inline prompt
python3 .beads/tf.py update-context --bead {id} --worker {name} --summary "..." --files "..." [--gotcha "..."]  # Append to context
python3 .beads/tf.py phase-complete --epic {id} [--build-cmd "cmd"] [--phase-num N]  # Gate + smoke test + summary (includes worker summaries)
python3 .beads/tf.py verify --build-cmd "cmd" [--test-cmd "cmd"] [--live]           # Build-only by default; --test-cmd runs ONLY with --live (guards costly live tests). Log result in registry
python3 .beads/tf.py git-cleanup {worker} [--commit]                               # List/commit uncommitted files from a worker's dispatch
python3 .beads/tf.py bd-path                                           # Print resolved bd binary path

# Worker commands (workers call these — no direct bd usage)
python3 .beads/tf.py claim {bead_id} [--expected-mins N] # Claim task (with optional time estimate for stall detection)
python3 .beads/tf.py block {bead_id} --question "..." [--context "..."]  # Mark blocked + create question
python3 .beads/tf.py discover {bead_id} --title "..." [--description "..."]  # Create discovered work
python3 .beads/tf.py heartbeat {bead_id} [--note "..."]  # Explicit heartbeat for long-running ops
python3 .beads/tf.py worker-close {bead_id} --context-pct N --files f1,f2 --summary "..." [--force]  # Validate + close (--force skips target file modification check)
```
