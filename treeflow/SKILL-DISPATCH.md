# TreeFlow — Dispatch & Orchestration Loop

This module covers the full orchestration loop: wave planning, worker dispatch, completion processing, stall detection, and failure recovery. For core rules, quick paths, and entry protocol, see [SKILL.md](SKILL.md).

## Orchestration Loop

Run continuously until all beads are closed or user input is needed.

### Flat Task Mode

When working from existing beads with no epic hierarchy, skip `phase-gate` and `phase-complete`. Phase files, epic context files, and phase-complete are not needed.

**Wave-based orchestration pattern:**
1. `tf.py ready` → get all dispatchable beads
2. `tf.py wave-plan --beads id1,id2,...` → compute parallel dispatch groups
3. Dispatch wave 1 (all beads in group 1 can run in parallel), batch by domain
4. As workers complete: `tf.py notify` → `tf.py ready` → `tf.py wave-plan` with newly ready beads
5. Dispatch next wave. Loop until all beads closed.

**Build verification in flat-task mode:** Without phase gates, builds can silently diverge. Run the project's build command (`tf.py init --build-cmd "..."` stores it in registry) after Wave 1 completes and every 2-3 waves thereafter. If the build fails, fix before dispatching the next wave.

`wave-plan` handles file conflict analysis and active worker awareness automatically. Use section annotations (`Files (modifies): src/config.rs [StorageConfig]`) to enable fine-grained parallelism — beads modifying different sections of the same file are classified as `low_risk` and grouped into the same wave.

**Tracer-bullet first wave:** Prefer dispatching one complete vertical slice first (e.g., a single API endpoint with handler, service, and test) that validates the architecture. Run build + tests after Wave 1 before fanning out. This catches foundational mistakes before subsequent workers build on them.

### 1. Find Ready Work

```bash
python3 .beads/tf.py ready
```

Use `tf.py ready` instead of `bd ready` directly — it filters epics and supplements with `bd list --status=open` to catch tasks that `bd ready` silently caps. If `capped: true` in the output, `bd ready` missed tasks (the full set is in `ready`).

- No ready tasks → assess: `bd blocked --json | jq -c && bd list --status=open --json | jq -c`
  - Blocked issues → analyze and attempt to resolve
  - No open issues → work complete, report to user
- Ready tasks → proceed to step 2

### 2. Assess Parallelism

Run file-conflict analysis on ready beads (or use `wave-plan` for automated wave grouping):
```bash
python3 .beads/tf.py conflict-check --beads bead1,bead2,bead3
# Or: automatically compute dispatch waves
python3 .beads/tf.py wave-plan --beads bead1,bead2,bead3
```

`conflict-check` extracts `Files:` / `Files (new):` / `Files (modifies):` lists from bead descriptions (with fallback path inference) and returns:
- `safe` — beads with no file overlap, safe to dispatch concurrently
- `conflicts` — files touched by multiple beads (hard conflicts only)
- `low_risk` — same file, different `[section]` annotations (safe to parallelize). Use `Files (modifies): src/config.rs [StorageConfig]` to annotate sections.
- `modify_conflicts` — files both modified by 2+ beads (higher severity than create+modify)
- `serial` — sets of beads that must be serialized due to hard file conflicts
- `soft_deps` — `depends_on:` relationships (serialization constraints without blocking readiness)
- `inferred` — beads where files were inferred from description text (no explicit `Files:` line)

Additional rules:
1. **Same directory, different files** → safe with caution
2. Respect `[parallel]` markers from planning
3. **Max concurrent workers: 6.** Never more than independent ready beads.
4. Batch trivial related tasks into one worker assignment
5. **Default to batching** when 3+ ready tasks share a skill domain and are small. One worker with sequential sub-tasks is almost always better than N separate spawns. Only split if total estimated context would exceed ~60%.
6. **Package-level conflicts:** `conflict-check` detects file-level overlaps but not same-package naming collisions (e.g., two workers creating `categoryLabel()` in different files of the same Go package). When parallelizing within the same package, specify shared helper names in each bead description, or serialize the tasks.
7. **Accumulator files** (CLI entry points, routing tables, config registries) that multiple features write to are serialization bottlenecks even when the logical changes don't overlap. When two beads both add commands/routes/config to the same file, serialize them.

### 3. Select or Reuse Workers

**Default: spawn fresh workers.** Batching multiple tasks into one fresh worker (via `worker-prompt --beads id1,id2`) is almost always better than reusing a completed worker via SendMessage. Reuse is only valuable for follow-up tasks discovered *after* a worker finishes in the same domain.

**Run sync before first dispatch** to retire stale workers from prior sessions:
```bash
python3 .beads/tf.py sync --ready-count {N}
```

`tf.py init` creates a new isolated context directory — it does NOT clean up workers from prior plans. If you see stale workers from prior sessions (via `tf.py status`), run `tf.py sync` to retire them. For a fresh plan with no prior workers in the current context, sync is unnecessary.

During the normal completion→dispatch flow, sync is optional — the `notify` response already provides worker state. Run sync again after anomalies (stalled workers, SendMessage failures, post-compaction).

**Target: ≥1.5 tasks/worker with good batching.** If your worker count exceeds task count, you're over-spawning.

<details>
<summary><strong>Advanced: Worker Reuse via SendMessage</strong></summary>

Sync handles all reuse housekeeping: auto-retires workers at ≥90% or <40% context, retires workers beyond the idle timeout (default 8 min, configurable via `--idle-timeout` on init — consider 12-15 min for projects where reuse yields prompt cache savings), flags stalled workers, and returns `available` workers by skill domain.

**Decision rule** based on sync output:
1. If `reuse_enforced: true` → **prefer reuse** of the freshest idle worker (lowest `idle_min`) via SendMessage
2. `available` has a worker in the same skill domain with `idle_min` ≤ 6 → **reuse** via SendMessage
3. Workers with `idle_min` > 8 may not be addressable — prefer spawning fresh (raise if reuse is yielding cache savings)
4. `available` is empty or no domain match → spawn fresh
5. **If SendMessage is unavailable** → always spawn fresh

Workers become non-addressable within a few minutes of stopping. If `SendMessage` fails, retire the worker and spawn fresh — do not retry.

</details>

### 4. Construct Worker Prompt

Use `tf.py worker-prompt --write-file` to assemble the prompt and write it to a temp file. The orchestrator sees ~100 tokens (JSON metadata), not the 2-5K token prompt:

```bash
# Primary: write to file — orchestrator never sees the prompt content
python3 .beads/tf.py worker-prompt --beads {bead-id} --write-file
# Returns: {"ok": true, "prompt_file": "/tmp/tf-prompt-xxx.md", "model": "", "beads": ["id"], "summary": "Implement login API"}

# Multiple beads (serial batch)
python3 .beads/tf.py worker-prompt --beads {id1},{id2},{id3} --write-file

# Reuse (shorter prompt for SendMessage — always inline, --write-file ignored)
python3 .beads/tf.py worker-prompt --beads {bead-id} --reuse --prior-bead {prev-id}
```

Use `summary` from the response for orchestrator tracking. Use `prompt_file` in the Agent prompt (section 5).

**Fallback:** `--prompt-only` (raw stdout capture) still works for debugging or when you need to inspect the prompt. But note that stdout capture still flows through orchestrator context — `--write-file` avoids this entirely.

**Only model aliases work:** `"sonnet"`, `"opus"`, `"haiku"`. Full model IDs (e.g., `"claude-sonnet-4-6"`) are rejected by the Agent tool.

### 5. Dispatch Workers

**New worker (self-assembling):**

```
# From worker-prompt --write-file response:
#   prompt_file = response["prompt_file"]
#   summary = response["summary"]
#   model = response["model"]

Agent tool:
  name: "{worker-name}"
  description: "{worker-name}: {summary}"
  prompt: "Read {prompt_file} — it contains your complete task instructions, execution rules, project context, and acceptance criteria. Your FIRST action must be to read that file. Then execute every instruction in it."
  run_in_background: true
  model: "{model}"    ← include only if non-empty
```

**Reused worker** (use `agent_id` from sync output if available, fall back to name):
```
SendMessage:
  to: "{agent-id or worker-name}"
  message: <reuse prompt from tf.py worker-prompt --reuse>
```

**All workers in a wave MUST be dispatched in a single response.** Use multiple parallel Agent/SendMessage tool calls in one turn. This maximizes prompt cache hits (stable conversation prefix across the batch) and reduces latency. Never dispatch workers one-at-a-time across separate turns.

**For `[integration]` tasks:** these involve data downloads, large datasets, or external services. `tf.py worker-prompt` auto-appends heartbeat discipline and `--expected-mins` guidance when the bead title contains `[integration]`. The orchestrator should actively poll `tf.py stalled` every 10 minutes while integration workers are running.

**After each dispatch**, record in registry:
```bash
python3 .beads/tf.py dispatch {worker-name} {bead-id} --skill {domain} [--output-file {path}] [--agent-id {agent-id}]
```

### Waiting for Workers

After dispatching, DO NOT poll `git log`, `git status`, or `wc -l` in a loop. You will receive `<task-notification>` events automatically when workers complete. Only run `tf.py stalled --threshold-mins 20` every 5-10 minutes as a safety net. Between dispatches, use the idle time to prepare prompts for the next wave.

When `late: true` appears in a `tf.py notify` response, skip entirely — no action, no logging, no context update.

### 6. Process Completions

When a `<task-notification>` arrives:

1. **Extract essentials from `<result>`**: worker name, context %, and agent ID (from the task-notification metadata)
2. **Record in registry** — use `--auto` to infer bead, files, and skill from registry + git diff:
   ```bash
   # Preferred: auto-infer bead, files, and skill from registry + git diff
   python3 .beads/tf.py notify {worker-name} --auto --context-pct {N} --agent-id {agent-id}

   # Explicit (edge cases, or when auto-inference needs correction):
   python3 .beads/tf.py notify {worker-name} {bead-id} --context-pct {N} --summary "{1-line}" --agent-id {agent-id} --files "{file1},{file2}" [--gotcha "{issue}"]
   ```
   `--auto` reads the worker's dispatched bead and skill from registry, and infers modified files via `git diff` from the dispatch SHA. Explicit parameters always override auto-inferred values.
3. **Check response fields**:
   - `late: true` → duplicate/late notification for an already-processed bead — skip, no action needed. `<task-notification>` fires multiple times per agent (on compaction, re-render, etc.) — this is normal, not an error.
   - `bead_status` → included automatically, no separate `bd show` needed:
     - `closed` → normal flow
     - `blocked` → worker hit a question: surface to user, wait, SendMessage to resume
     - `in_progress` + `auto_closed: false` → worker finished without calling `worker-close`. Follow the Worker Failure Recovery steps below
4. **For batched workers** (2-3 beads per worker), `--auto` handles multi-bead dispatch automatically (infers the bead list from registry). Explicit form:
   ```bash
   python3 .beads/tf.py notify {worker-name} --beads "{bead1},{bead2}" --context-pct {N} --files "{files}" --summary "{summary}"
   ```
   This processes each bead in sequence under the same worker. (`batch-notify` with `--pairs` is still supported for cross-worker batches.)
5. **Update context** is handled by `notify --files` above. Use `tf.py update-context` only for manual orchestrator notes outside of notification flow.
6. **Discard the full `<result>` content** — it's now captured in registry and context files
7. **Ignore transient LSP diagnostics** — during active worker runs, `go.sum` missing entries, `could not import` errors, build-tag exclusion warnings, and `undefined: <symbol>` in partially-written files are almost always transient. Do not act on these until the responsible worker completes.
8. Check for newly ready beads: `python3 .beads/tf.py ready`
9. **Phase transition** — if all beads for a phase are done, run the combined gate + smoke test + summary:
   ```bash
   python3 .beads/tf.py phase-complete --epic {epic-id} [--build-cmd "{build}"] [--phase-num {N}]
   ```
   - If `pass: false` → wait for `blocking` items to resolve
   - If `pass: true`:
     a. **Spec-trace verification (mandatory)** — run targeted grep checks against the spec for the completed phase. For each CLI command: verify every flag name exists in the code. For each data model: verify every JSON field name matches. For each package: verify the import path exists. If any spec reference can't be grepped, create a fix task before proceeding. A `phase-complete` that returns `beads_closed: 0` without running grep verification is a failed spec-trace.
     b. Phase summary is already written to `phase-{N}.md` by the command
     c. Check `build` field: if `"fail"` → dispatch integration worker to fix
     d. If clean → proceed to next phase
10. Loop back to step 2

### Proactive Compaction Between Waves

After every 2-3 completed waves, write a compaction checkpoint using the existing `phase-summary` command:

```bash
python3 .beads/tf.py phase-summary --epics {epic-ids}
```

This writes completed bead summaries and decisions to a phase file. After context compression/recovery, the orchestrator reads the clean summary instead of reconstructing state from scattered conversation history. For flat-task mode (no epics), periodically save `tf.py status` output to a context file as a lightweight checkpoint.

### 7. Detect and Handle Stalled Workers

Workers send heartbeats automatically when they call `tf.py` commands (claim, block, discover, worker-close). For long operations, workers call `tf.py heartbeat` explicitly. A worker with no heartbeat for >20 minutes is flagged as stalled.

**Check for stalls periodically during the loop:**
```bash
python3 .beads/tf.py stalled
```

Or use `tf.py sync` / `tf.py status` — both include stalled workers in their output.

**When stalled workers are reported:**
1. If SendMessage is available, send a status check first
2. If no response after ~5 min, or SendMessage is unavailable: retire the worker, reopen the bead, spawn fresh
3. If the task has stalled twice, surface to user — do NOT auto-retry indefinitely

**Cross-session idle workers:** `tf.py sync` auto-retires cross-session workers and workers beyond the idle timeout. Only workers idle ≤6 min are counted as addressable. Do not attempt SendMessage on workers with `idle_min` > 6.

**Post-SendMessage claim check (mandatory for all reuse dispatches):**
After any SendMessage reuse, verify the bead transitions from `open` to `in_progress` within ~5 min:
```bash
bd show <bead_id> --json | jq -c '.[0].status'
# "open"        → worker stalled — retire + spawn fresh
# "in_progress" → worker is healthy, continue waiting
```

**Do NOT kill workers** — let them complete or self-report. The stall detection is advisory; the orchestrator decides whether to wait longer or retire.

### 8. Worker Failure Recovery

When `tf.py notify` returns `auto_closed: false`, the worker completed without calling `worker-close`. This usually means it failed to produce output.

1. **Check target files:** `wc -l` on expected outputs from the bead description
2. **If files are stubs or missing:** `bd update <id> --status open`, `tf.py retire <worker>`, redispatch with a more explicit prompt
3. **If files written but uncommitted:** commit them manually, then call `tf.py notify` with `--force` on the `bd close` to complete the bead
4. **If tests fail:** SendMessage to worker to fix (if addressable), else retire and redispatch

## Worker-to-User Communication

Workers cannot message the orchestrator directly. The question flow is:

1. Worker marks bead `blocked` and creates a question task
2. Worker **stops** (notification arrives at orchestrator)
3. Orchestrator processes via `tf.py notify`, reads bead status, sees blocked + question
4. Orchestrator surfaces question to user
5. User answers → orchestrator `SendMessage({to: "worker-name"})` with the answer
6. Worker **auto-resumes** with full conversation context intact

## Context Compression Recovery (Reorientation Protocol)

After the system compresses your context, your in-memory state is lost. Run these steps in order before resuming orchestration:

1. **Get overview:** `python3 .beads/tf.py status` — active workers, pending notifications, overall counts
2. **Check worker states:** `python3 .beads/tf.py registry` — who is active/idle/retired, their beads and context %
3. **Find orphaned beads:** `python3 .beads/tf.py recover` — beads in_progress with no active worker (common after compaction)
4. **Find unblocked work:** `python3 .beads/tf.py ready` — dispatchable tasks (filtered, supplemented)
5. **Process pending notifications** before dispatching new work — any `task-notification` messages in the queue should be handled first via `tf.py notify`
6. **Read context files** if needed: `cat .beads/context-*/worker-context.md` and the latest `phase-*.md` file

The registry is file-based and survives compression — trust it over any summary the system provides. Workers from before compaction are likely unreachable — retire them and spawn fresh.
