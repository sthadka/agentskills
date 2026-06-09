# stateflow Skill Run Feedback — First awty Integration (2026-05-15)

Session: `b1781534-a820-44c7-9c57-51feb2669ec8`  
Model: Opus 4.6 (orchestrator), Sonnet 4.6 (workers)  
Workers: schema-1, engine-1, schema-2  
Total: 9 features shipped, 3 worker sessions, ~11 min worker time, 444 tests

---

## What Went Well

### Worker performance was excellent

All three workers self-directed cleanly from the prompts alone. No blockers, no SendMessage needed. Each worker:
- Read the relevant existing code before touching anything
- Followed TDD discipline (tests before implementation in most cases)
- Used conventional commits
- Ran `go build`, `go vet`, `go test` before each commit
- Called `worker-close` correctly

schema-1 handled the most complex batch (6 tasks, including a breaking API signature change across 5+ files) without any errors. engine-1 shipped in under 4 minutes (smallest batch). schema-2 (inline guards — the highest-risk task, custom unmarshaling) delivered clean TDD tests alongside implementation.

### Worker batching was correct

Group A tasks all touched `internal/schema/types.go` and `workflow-schema.json`, making parallel dispatch unsafe. Batching them under one worker (schema-1) was the right call. Group C (inline guards) was correctly isolated after Group B.

### Dependency chain worked

The A→B→C bead dependency chain sequenced work correctly. Group B unblocked immediately after Group A closed. Group C unblocked after Group B. No work was dispatched out of order.

### Token efficiency

11.3M cache_read tokens, 910 input tokens (non-cached). The orchestrator kept the main context lean — workers operated in isolated sub-sessions and their full transcripts never entered the orchestrator's context window. This is the architecture working as intended.

---

## What Did Not Go Well

### Problem 1: Plan format mismatch

The user provided `awty-impl-plan.md` — a rich analysis document with evaluation tables, rationale, and detailed specs. stateflow's `planning` state expects a beads-format plan: `## Epic:` and `### Task:` headings that `bd create -f` can parse.

**What happened:** `tf.py validate-plan awty-impl-plan.md` returned 15 errors (horizontal rules break the parser). The orchestrator had to rewrite the entire plan as `awty-feedback-plan.md`. This took ~4 minutes and multiple tool calls.

**Impact:** Lost time, confusion, two sets of intermediate beads created and cleaned up.

**Fix ideas:**
- stateflow's `entry_check` instructions should explicitly say: "If the user provided a spec/analysis document that is not in beads format, your first task in `planning` is to convert it. Read PLAN-FORMAT.md and create a new `.beads/{plan-name}-plan.md`."
- Alternatively, add a `tf.py convert-plan <input> <output>` command that extracts task headings and acceptance criteria from freeform docs into beads format.
- The planning state could detect the format mismatch earlier (run validate-plan on any file the user mentions) and set expectations.

---

### Problem 2: `bd create -f` does not capture task descriptions

The plan had 9 tasks across 3 epics, each with detailed Files and Acceptance sections. `bd create -f` only parsed top-level `## Epic:` headings as beads. The `### Task:` content was silently dropped — bead descriptions were empty.

**What happened:** `tf.py worker-prompt` produced nearly useless prompts (no description, just worker-context.md boilerplate). The orchestrator had to manually craft full task descriptions for each worker prompt — a significant effort, copy-pasting from the plan file.

**Impact:** Manual prompt authoring is fragile and time-consuming. It breaks the "orchestrator never reads source code" contract (the orchestrator was reading the plan file to extract task specs).

**Fix ideas (pick one or combine):**
- **Preferred:** Treat the plan file as the worker context source. In `tf.py worker-prompt`, if the bead has no description, look up the bead title in the plan file and include the full task section.
- Make `bd create -f` capture task bodies as descriptions (may require changes to bd's plan parser — `### Task:` subsections become child issues with the body as description).
- Add a `tf.py enrich-beads <plan-file>` command that reads the plan file and patches descriptions onto matching bead titles.

---

### Problem 3: `open_bead_count` guard fires on stale context — twice

Described in detail in `awty-run-feedback.md`. From the stateflow perspective:

**Root cause in the workflow:** `processing_completions` has no on_enter, so context counts from `tf_status` are never updated before `ASSESS_NEXT` fires. The `no_open_beads` guard reads `open_bead_count` from context — which initializes to `0` and only gets updated when `orchestration_ready`'s on_enter runs tf_status. The routing chain `processing_completions → ASSESS_NEXT` bypasses that update entirely.

**What happened:** Twice the workflow ended up in `session_end` with open beads remaining, requiring manual state file patches.

**Fix for stateflow workflow (now possible with new features):**
```yaml
processing_completions:
  on_enter:
    commands:
      - command: "python3 .beads/tf.py status"
        parser: tf_status
      - command: "bd ready --json | jq -c"
        parser: bd_ready
    clear_context: [open_bead_count, has_ready_work]
```

This uses multi-command on_enter (Task 2) and clear_context (Task 3) — both just implemented in this session. The workflow should be updated immediately.

---

### Problem 4: DISPATCHED transition was skipped for Group C

After Group B completed, the orchestrator went straight from conflict-check/sync to spawning the schema-2 agent without firing `DISPATCHED`. This caused extra state navigation issues on the return path.

**What happened:** The orchestrator used `NOTIFICATION_RECEIVED` from `orchestration_ready` — which is not in that state's `on:` map, but triggered via `safe_next` to `awaiting_completions`. This worked but was unintended.

**Why it happened:** There's no enforcement that `DISPATCHED` must be fired before the orchestrator leaves `orchestration_ready`. The workflow allows any event — non-matching events silently use `safe_next`.

**Fix ideas:**
- Add an explicit note in `orchestration_ready` instructions: "You MUST fire DISPATCHED after dispatching all workers before proceeding."
- Consider making `safe_next` transitions require an explicit `approve_safe_next: true` in the state config so they don't silently swallow unexpected events.

---

### Problem 5: Worker model selection is a stop-the-world question

The orchestrator asked the user which model workers should use via `AskUserQuestion` at the end of planning. This blocked for 22 seconds and added a turn.

**Fix:** Move model selection to the `entry_check` state, as the first contextual question ("What model should workers use? Default: Sonnet 4.6"). Store it in context under `worker_model`. The planning and orchestration states can skip asking if `worker_model` is already set. Alternatively, default to Sonnet 4.6 unless the user mentions a preference upfront.

---

## Feature Ideas for stateflow

### High priority

**Update `workflow.yaml` to use new awty features**  
The session implemented: multi-command on_enter, clear_context, skip_on_reentry, on_exit, state-scoped parsers. The stateflow workflow itself should adopt these immediately:
- `processing_completions` on_enter: run tf_status + bd_ready with clear_context to fix the stale guard bug
- `orchestration_ready` on_enter: add `skip_on_reentry: true` to avoid redundant tf_status calls on self-loops
- `awaiting_completions` state-scoped parsers: only activate tf_notify in this state

**`tf.py worker-prompt` fallback to plan file**  
When bead has no description, extract the matching task section from the plan file. This would have saved the manual prompt authoring step entirely.

**`tf.py convert-plan <input> <output>`**  
Convert a freeform analysis/spec document into beads format by extracting headings, task lists, and acceptance criteria.

### Medium priority

**Worker model default + override**  
Set a default model (Sonnet 4.6) in `config.yaml`. Allow per-worker override via dispatch. Don't ask if a default exists.

**`tf.py enrich-beads <plan-file>`**  
Patch bead descriptions from the plan file after `bd create -f`. Matches on title substring.

**DISPATCHED enforcement**  
Warn (or error) in `orchestration_ready` instructions when `tf.py dispatch` was called but `DISPATCHED` event was not fired.

**Stale worker detection improvement**  
The sync command correctly retired workers with <30% context. But 8 workers with 50–80% context were kept as "available" even though they were 15+ hrs idle with no memory of prior tasks. Consider an idle-time threshold (e.g., retire after 2 hrs) in addition to the context threshold.

### Low priority

**Plan file tracing**  
After `bd create -f`, store the plan file path in a registry entry. `tf.py worker-prompt` can look it up automatically.

**`tf.py phase-summary`**  
At end of each phase (A→B→C), auto-generate a context update summarizing what was done, files changed, test count. Workers provide this in their reports — tf.py should aggregate it.

**Safe-next audit log**  
When a safe_next transition fires, log it distinctly in the awty event log (e.g., `event: "safe_next"`, `original_event: "NOTIFICATION_RECEIVED"`). Currently indistinguishable from a normal transition.

---

## Metrics

| Metric | Value |
|--------|-------|
| Total session time | ~30 min |
| Worker time (schema-1) | ~11 min |
| Worker time (engine-1) | ~3.5 min |
| Worker time (schema-2) | ~4.5 min |
| Features shipped | 9 / 9 |
| Tests: start → end | 423 → 444 |
| Manual state patches | 2 |
| Plan rewrites | 1 |
| Bead cleanup operations | 1 |
| Worker blockers / SendMessages | 0 |
| Orchestrator tool calls | 88 |
| Orchestrator tokens (output) | ~61K |

---

## Conclusion

The core stateflow + awty architecture works. Parallel background workers with beads dependency tracking and state machine enforcement is the right model. The rough edges were almost entirely in the setup/routing layer (plan format, context staleness, workflow file resolution) — none of the fundamental dispatch/completion/tracking logic failed. All three issues that required workarounds are fixable and would be high-ROI: the stale-context guard fix alone eliminates the need for state file patching, and the worker-prompt plan-file fallback eliminates manual prompt authoring.
