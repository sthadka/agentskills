# BeadFlow Command Reference

## bf.py — Quality Layer

```bash
python3 .beads/bf.py init --bd-path "$(which bd)"     # Setup (copies bf.py, stores bd path)
python3 .beads/bf.py ready                             # Filtered ready (no epics, supplements capped)
python3 .beads/bf.py verify --files "f1,f2"            # Mid-task quality check
python3 .beads/bf.py close <id> --files "f1,f2" --summary "AC: pass. ..."  # Validate + close
python3 .beads/bf.py close <id> --force                # Skip validation
python3 .beads/bf.py smoke-test --build-cmd "cmd" --beads a,b  # Build + wiring check
python3 .beads/bf.py conflict-check --beads a,b,c      # File-conflict analysis for parallel dispatch
python3 .beads/bf.py dep <blocker> <blocked>            # Idempotent dep addition
python3 .beads/bf.py import-graph graph.jsonl           # Import sculptor graph
```

**Close validation:** uncommitted changes (error), dead-code markers (warning), `Task N:` in commit (error), missing `AC:` in summary (warning).

## bd — Issue Tracker

### Create
```bash
bd create -f plan.md --json                            # Batch from file (see PLAN-FORMAT.md)
bd create "Title" -t <type> -p <priority> -d "..." --json  # Single issue
bd q "Title" -t task -p 2                              # Quick capture (ID only)
```

### Find Work
```bash
python3 .beads/bf.py ready                             # PREFERRED
bd ready --json                                        # Raw (includes epics, may cap results)
bd blocked --json                                      # Blocked issues
bd list --json                                         # All issues
bd show <id> --json                                    # Full details
```

### Update
```bash
bd update <id> --status in_progress --json
bd update <id> --status blocked --json
bd update <id> --append-notes "Progress" --json
```

### Close
```bash
python3 .beads/bf.py close <id> --files "f1" --summary "AC: pass"  # PREFERRED
bd close <id> --reason "Done" --suggest-next --json    # Raw (no validation)
```

### Dependencies

> **Use `bd dep <blocker> --blocks <blocked>`** — reads naturally. `bd dep add A B` has reversed argument order.

```bash
bd dep <blocker> --blocks <blocked> --json             # Blocking
python3 .beads/bf.py dep <blocker> <blocked>           # Idempotent blocking
bd dep add <child> <parent> -t parent-child --json     # Hierarchy
```

### Other
```bash
bd comments add <id> "note" --json                     # Add comment
bd graph --all                                         # Full dependency graph
```

### Command Chaining

```bash
bd update <id> --status blocked --json && bd create "Unblock: <reason>" -t task -p 1 --deps "<id>" --json
bd create "Sub 1" -t task --parent <id> --json && bd create "Sub 2" -t task --parent <id> --json
```
