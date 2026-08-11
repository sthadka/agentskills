# BeadFlow — Execution Loop

The full execution loop: find work, claim, execute, validate, close. For core rules, planning, and entry protocol, see [SKILL.md](SKILL.md).

## Execution Loop

Run continuously until no ready issues or user input needed.

### 1. Find Work

```bash
python3 .beads/bf.py ready
```

`bf.py ready` is better than raw `bd ready`:
- Filters out epics (you execute tasks, not epics)
- Supplements results from `bd list` to catch beads that `bd ready` silently caps
- Includes descriptions in output (no separate `bd show` needed)

**If no ready tasks:**
```bash
bd blocked --json | jq -c && bd list --status=open --json | jq -c
```
- Blocked issues → analyze and resolve blockers
- In-progress issues → resume or close stale items
- No open issues → work complete, report to user

**If ready tasks exist:** select highest priority (lowest number), proceed to step 2.

### 2. Claim

```bash
bd update <id> --status in_progress --json | jq -c
```

### 3. Execute

Do EXACTLY what the issue describes. No scope creep.

**Before writing any new type/class/struct** — search first:
```bash
grep -r "type TypeName\|class TypeName\|TypeName =" ./src/
```

**Before writing any call site** — verify the callee's actual signature with LSP or grep. Wrong signatures are a common error class.

**After writing or editing any file** — use the compiler/build tool as ground truth, not LSP diagnostics. LSP can lag on recently-modified files.

**When writing a stub** — document the contract in comments. The comment is the real value; the function body is a placeholder.

**TDD for complex logic** (tasks marked `[TDD]`):
1. Write test fixtures (sample inputs)
2. Write test cases with **computed** expected values
3. Implement until tests pass

### 4. Verify (optional mid-task check)

```bash
python3 .beads/bf.py verify --files "src/auth.py,src/models.py"
```

Catches issues early without closing:
- Uncommitted changes
- Dead-code markers (TODO, FIXME, HACK)
- Commit message containing task numbers

### 5. Commit

```bash
git add <specific-files>
git commit -m "description of what changed"
```

Never commit `.beads/` files. Never include bead/task IDs in commit messages.

### 6. Close

```bash
python3 .beads/bf.py close <id> --files "src/auth.py,src/models.py" --summary "AC: all pass. Implemented login endpoint with JWT."
```

**Close validation checks (blocking):**
- Uncommitted changes in working tree → must commit first
- Commit message contains `Task N:` prefix → must amend

**Close validation checks (warnings):**
- Dead-code markers in target files (TODO, FIXME, etc.)
- Summary missing `AC:` status

Use `--force` to skip all validation (escape hatch for edge cases).

### 7. Loop

Go back to step 1. `bf.py ready` returns the next actionable task.

## Handling Outcomes

### Work completed successfully
Standard path — steps 5 and 6 above.

### Blocked (need external input)
```bash
bd update <id> --status blocked --json | jq -c && bd create "Unblock: <what's needed>" -t task -p 1 --deps "<blocked-id>" -d "<how to resolve>" --json | jq -c
```
Return to step 1.

### Discovered new work
```bash
bd create "Found: <new thing>" -t task -p 2 -d "<what needs doing>" --json | jq -c
```
Continue current work. Don't context-switch.

### Issue too large
```bash
bd create "Subtask 1: <part>" -t task --parent <id> -d "..." --json | jq -c && bd create "Subtask 2: <part>" -t task --parent <id> -d "..." --json | jq -c && bd close <id> --json | jq -c
```
Return to step 1.

## Parallel Execution

When `bf.py ready` returns multiple tasks marked `[parallel]` or with no cross-dependencies:

### 1. Check for conflicts
```bash
python3 .beads/bf.py conflict-check --beads id1,id2,id3
```

Returns:
- `safe` — beads with no file overlap, safe to dispatch concurrently
- `conflicts` — files touched by multiple beads
- `low_risk` — same file but different `[section]` annotations
- `serial` — groups that must be serialized
- `soft_deps` — `depends_on:` references

### 2. Dispatch safe beads

For each safe bead, claim it and spawn a sub-agent:

```bash
bd update <id> --status in_progress --json | jq -c
```

Then dispatch via the `Agent` tool:
```
Agent:
  description: "Implement <task summary>"
  prompt: "<full task description from bead, including files and AC>"
  run_in_background: true
```

Dispatch ALL safe beads in a single response turn (multiple Agent tool calls).

### 3. Process completions

When sub-agents complete:
1. Check that target files exist and were committed
2. Close via `bf.py close`
3. Check for newly ready beads

### 4. Serial beads

For `serial` groups, execute them yourself sequentially (steps 2-6 of the main loop).

## Phase Transitions

Between phases (when all tasks in a phase are closed):

```bash
python3 .beads/bf.py smoke-test --build-cmd "npm test" --beads id1,id2,id3
```

- `build: "pass"` → proceed to next phase
- `build: "fail"` → fix before moving on
- `wiring` checks verify files from closed beads exist on disk

## State Assessment

### When `bd ready` returns empty

```bash
bd blocked --json | jq -c && bd list --status=open --json | jq -c && bd list --status=in_progress --json | jq -c
```

1. Blocked issues? → focus on unblocking
2. Stale in-progress? → resume or close
3. No open issues? → work complete
4. All clear? → report to user

### When encountering errors
- DO NOT immediately mark blocked
- Attempt to resolve (check code, read docs, fix issues)
- ONLY mark blocked if truly cannot proceed without external input

### When user provides new goal mid-session
- Complete current issue or leave in_progress (don't abandon)
- Create new epic for new goal
- Ask user if they want to switch focus
