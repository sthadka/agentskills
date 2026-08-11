# BeadFlow — Execution Loop

For rules, planning, and entry protocol, see [SKILL.md](SKILL.md).

## Loop

Run continuously until no ready issues or user input needed.

### 1. Find Work

```bash
python3 .beads/bf.py ready
```

Prefer `bf.py ready` over raw `bd ready` — it filters epics, supplements capped results, and includes descriptions.

**If no ready tasks:** `bd blocked --json | jq -c && bd list --status=open --json | jq -c` to assess state.

### 2. Claim

```bash
bd update <id> --status in_progress --json | jq -c
```

### 3. Execute

Do what the issue describes. Use `bf.py verify --files "f1,f2"` mid-task to catch uncommitted changes or dead-code markers early.

### 4. Commit

```bash
git add <specific-files>
git commit -m "description of what changed"
```

Never commit `.beads/` files. Never include bead/task IDs in commit messages.

### 5. Close

```bash
python3 .beads/bf.py close <id> --files "f1,f2" --summary "AC: all pass. Implemented X."
```

**Blocking checks:** uncommitted changes, `Task N:` in commit message.
**Warnings:** dead-code markers, missing `AC:` in summary.
**Escape hatch:** `--force` skips all validation.

### 6. Loop

Back to step 1.

## Handling Outcomes

**Blocked** (need external input):
```bash
bd update <id> --status blocked --json | jq -c && bd create "Unblock: <what's needed>" -t task -p 1 --deps "<blocked-id>" -d "<how to resolve>" --json | jq -c
```

**Discovered new work:**
```bash
bd create "Found: <new thing>" -t task -p 2 -d "<what needs doing>" --json | jq -c
```
Continue current work — don't context-switch.

**Issue too large:**
```bash
bd create "Subtask 1: <part>" -t task --parent <id> -d "..." --json | jq -c && bd create "Subtask 2: <part>" -t task --parent <id> -d "..." --json | jq -c && bd close <id> --json | jq -c
```

## Parallel Execution

When `bf.py ready` returns multiple independent tasks:

1. **Conflict-check:** `python3 .beads/bf.py conflict-check --beads id1,id2,id3`
   - `safe` — no file overlap, dispatch concurrently
   - `conflicts` / `serial` — must serialize
   - `low_risk` — same file, different `[section]` annotations
2. **Dispatch** all `safe` beads in a single response turn via multiple Agent tool calls with `run_in_background: true`
3. **Process completions:** verify files, close via `bf.py close`, check for newly ready beads
4. **Serial beads:** execute yourself sequentially

## Phase Transitions

When all tasks in a phase are closed:

```bash
python3 .beads/bf.py smoke-test --build-cmd "npm test" --beads id1,id2,id3
```

- `"pass"` → proceed to next phase
- `"fail"` → fix before moving on
- `wiring` checks verify files from closed beads exist on disk
