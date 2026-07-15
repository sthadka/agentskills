# TreeFlow Feedback: BIP Narrative Layer Session (2026-07-15)

## Session Profile

- **Project:** BIP — Behavioral Intelligence Platform (Python 3.11 / Click CLI / SQLite)
- **Scope:** LLM narrative enrichment layer — 7 beads, 4-layer dependency chain
- **Result:** 7/7 beads closed, 725 tests (539→725), 7 commits
- **Workers spawned:** 2 (python-1, content-1), both reused multiple times via SendMessage
- **Waves:** 3 dispatch waves
- **Wall clock:** ~28 minutes total
- **Model:** Opus (orchestrator and all workers)
- **Orchestrator tokens:** in=428, out=41.2K, cache_read=11.3M

---

## What Worked Well

### 1. Worker reuse via SendMessage was highly effective

Both workers were reused 2-3 times each via SendMessage, carrying domain context forward. `python-1` handled narrator module → DB schema → CLI wiring → relational prompts → integration tests (5 tasks across 3 dispatches). `content-1` handled personal-summary prompt → 6 remaining prompts (2 tasks). The reuse prompt template ("Prior Task — COMPLETE AND CLOSED") worked correctly — no worker ever re-closed a prior bead.

This is a significant improvement over the prior session (2026-07-15 earlier run) which spawned ~30 workers for 27 tasks. Two reusable workers for 7 tasks is a 3.5:1 task-to-worker ratio — well above the 1.5 target.

### 2. Batching bip-4xb + bip-t94 into one worker was the right call

The narrator module and DB schema tasks shared code patterns and had no file overlap. Batching them into a single worker dispatch saved one spawn overhead and ensured the DB tests could reference the narrator module directly. `worker-prompt --beads id1,id2` assembled the batch prompt cleanly.

### 3. Zero worker failures

All 5 worker dispatches (2 initial + 3 reuse) completed successfully on the first attempt. No beads needed reopening, no workers stalled, no files were left uncommitted. This is a stark contrast to the earlier session which reported 5/30 worker failures.

Likely contributors: (a) tasks were well-scoped with clear acceptance criteria, (b) the `WORKER-PROMPT.md` "you MUST produce output" language was effective, (c) the worker context file was thorough enough that workers didn't spend excessive time exploring.

### 4. Dependency graph drove wave ordering naturally

The 3-wave dispatch pattern emerged naturally from the dependency graph:
- Wave 1: narrator module + DB schema + reference prompt (3 parallel)
- Wave 2: CLI wiring + remaining prompts (2 parallel, after wave 1 deps cleared)
- Wave 3: relational prompts → integration tests (serial, each after prior)

`tf.py ready` correctly surfaced newly unblocked tasks after each completion.

### 5. `--write-file` kept orchestrator context lean

The orchestrator never held full prompt text (2-4k tokens each) in context. `worker-prompt --write-file` wrote to temp files, and the orchestrator only read them when constructing the Agent/SendMessage call. Over 6 prompt constructions, this saved ~15-20k tokens of orchestrator context.

### 6. Orchestrator correctly avoided polling

The orchestrator did not poll `git log` or `git status` while waiting for workers. It dispatched, recorded via `tf.py dispatch`, and waited for `<task-notification>` events. Between dispatches, it checked `tf.py ready` and `tf.py status` only when processing a completion — not in a loop. This was a major improvement over the earlier session.

---

## Problems and Improvement Opportunities

### P1: Orchestrator read too many source files during planning

Before writing the plan file and creating beads, the orchestrator made 12 `tilth_read` and 3 `tilth_search` calls to understand the codebase (report.py, enricher.py, db.py, cli.py, template files, DIMENSION_NARRATIVES). This consumed ~4 minutes and ~8k output tokens of the 234-second first turn.

The plan file was provided as an argument — it already contained the architecture, file list, and implementation order. The orchestrator didn't need to re-derive any of this. A well-written plan with `Files:` annotations and code references should be sufficient for the orchestrator to create beads without reading source.

**Suggestion:** Add to SKILL.md Entry Protocol:

```
### Plan File Provided
When a plan file is given as argument:
1. Read the plan file
2. Verify it has `Files:` annotations and acceptance criteria
3. Proceed directly to `validate-plan` → `create` → dependency setup
Do NOT read project source files to validate the plan — trust it.
Workers will read source files when they execute tasks.
```

This would have saved ~4 minutes and ~8k tokens in the first turn. The orchestrator's job is to decompose and dispatch, not to become a domain expert.

### P2: Redundant prompt reading before SendMessage

For every reuse dispatch, the orchestrator: (1) called `worker-prompt --write-file`, (2) read the temp file with the Read tool, (3) copy-pasted the content into SendMessage. Steps 2-3 are redundant — the orchestrator could use `--prompt-only` and pipe directly, or `worker-prompt` could return the prompt inline since reuse prompts are short (~500 tokens vs ~3k for fresh prompts).

In this session, 4 reuse dispatches each read a temp file unnecessarily, adding ~2k tokens of Read tool overhead.

**Suggestion for tf.py:** Add a `--inline` flag to `worker-prompt` for reuse prompts that returns the prompt in the JSON response field instead of writing to a file. Reuse prompts are small enough (~500 tokens) that inline is more efficient than the write-read-copy roundtrip.

**Suggestion for SKILL.md:** Clarify when `--write-file` vs inline is appropriate:
```
Use `--write-file` for fresh worker prompts (>2k tokens).
For reuse prompts via SendMessage, read the prompt file content
and pass it directly — or omit `--write-file` to get inline JSON.
```

### P2: `content-1` modified `bip/db.py` unexpectedly

The content worker (`content-1`, assigned to write `personal-summary.prompt.md`) also modified `bip/db.py` — adding a guard to skip `.prompt.md` files in `_load_templates()`. This was a code change outside the worker's assigned scope (content-only task), and it overlapped with `python-1`'s territory (DB schema task `bip-t94`).

It worked because `content-1` committed first and `python-1` read the updated file. But this was a file conflict that `conflict-check` couldn't have caught — the content task's `Files:` line only listed the new `.prompt.md` file, not `bip/db.py`.

**Root cause:** The worker discovered that adding the `.prompt.md` file broke existing tests (11 templates expected, but the new `.prompt.md` was being loaded as a template). Rather than calling `tf.py block` or `tf.py discover`, it fixed the issue directly by modifying `db.py`.

**Suggestion for WORKER-PROMPT.md:** Strengthen the out-of-scope modification rule:

```
If fixing a test failure requires modifying a file NOT in your Target Files:
1. Call `tf.py discover {bead_id} --title "cross-file fix: <file>" --description "..."`
2. If the fix is trivial (<5 lines) AND no other worker targets that file, proceed and note it
3. If another worker targets that file, call `tf.py block` — do NOT modify it
```

This session got lucky. In a larger project with more parallel workers, this pattern causes silent merge conflicts.

### P3: No post-completion verification by orchestrator

After all 7 beads closed, the orchestrator ran `uv run pytest` to verify — good. But it did not verify that the narrative layer actually works end-to-end as described in the plan. Specifically:
- Did not check that `build_narrative_prompt()` returns a prompt > 2k chars
- Did not check that the `--narrative` flag actually outputs to stderr
- Did not check that relational templates work without `--counterpart`

Tests passing ≠ feature working. The plan's verification section listed 5 specific checks, none of which were run.

**Suggestion:** Add to SKILL.md Session End Protocol:

```
### Acceptance Verification
After all beads close and tests pass, run the plan's verification steps
(if listed). Spot-check 2-3 key behaviors from the plan's acceptance criteria.
If the plan doesn't have verification steps, at minimum:
- Build/compile succeeds
- One happy-path CLI invocation works
- Tests pass
```

### P3: `tf.py dispatch` doesn't track agent IDs at dispatch time

The orchestrator records agent IDs via `tf.py notify --agent-id` after completion. But at dispatch time, `tf.py dispatch` doesn't capture the agent ID — it's only known from the Agent tool response. This means between dispatch and completion, the registry has no agent ID for the worker.

This matters for SendMessage reuse: if the orchestrator needs to address a worker mid-task (e.g., to answer a block question), it must remember the agent ID from the Agent tool response. If context compresses between dispatch and the need to message, the agent ID is lost.

**Suggestion:** Add `--agent-id` to `tf.py dispatch`:
```bash
python3 .beads/tf.py dispatch python-1 bip-4xb --skill python --agent-id a59bbdbe3d1ddea55
```
This stores the ID in the registry immediately, making it available via `tf.py registry` after compaction.

### P3: `tf.py sync` was never called

The SKILL.md says "Run sync before first dispatch to retire stale workers from prior sessions." This session had 3 prior context directories (`context-bip`, `context-bip-fixes`, `context-code-review-fixes`) with potentially stale workers. `tf.py sync` was never called.

It didn't matter because `tf.py init` created a fresh context directory, so no stale workers from prior contexts interfered. But the init/sync interaction isn't clear — does `init` implicitly handle stale state from prior plans?

**Suggestion:** Clarify in SKILL.md:
```
`tf.py init` creates a new isolated context directory — it does NOT
clean up workers from prior plans. If you see stale workers from prior
sessions (via `tf.py status`), run `tf.py sync` to retire them.
For a fresh plan with no prior workers, sync is unnecessary.
```

---

## Token Efficiency Analysis

| Phase | Tokens (est.) | Notes |
|-------|--------------|-------|
| Codebase exploration | ~8k out | Unnecessary — plan file had all info |
| Plan writing + bead creation | ~4k out | Efficient |
| Worker context writing | ~3k out | Necessary |
| Prompt construction (6x) | ~3k out | Efficient with `--write-file` |
| Notification processing (5x) | ~2k out | Clean — no late duplicates |
| Git push + cleanup | ~1k out | Efficient |
| **Status/ready checks** | ~2k out | Minimal, well-placed |
| **Total orchestrator output** | ~41k out | |

**Estimated waste:** ~8k tokens (20%) from unnecessary codebase exploration. All other orchestrator activity was purposeful.

**Comparison with prior session:** The earlier BIP session (same day) used ~300k orchestrator tokens for 27 beads with ~60k waste (20%). This session achieved 20% waste ratio at much lower absolute cost, suggesting the skill's efficiency improvements are working but the codebase-exploration pattern persists.

---

## Comparison with Previous Feedback

Checking against the earlier feedback file (`feedback-bip-session-2026-07-15.md`):

| Issue from Prior Session | Status in This Session |
|--------------------------|----------------------|
| P0: Workers fail to write code | **Resolved** — 0/5 failures |
| P1: Orchestrator polling loop | **Resolved** — no polling observed |
| P1: Parallel file conflicts (cli.py) | **Partially resolved** — content worker modified db.py out of scope |
| P1: notify auto-close too aggressive | **Not triggered** — all workers called worker-close |
| P2: Stall detection doesn't trigger | **Not tested** — no stalls occurred |
| P2: Worker couldn't commit 40+ min | **Resolved** — all commits successful |

The `WORKER-PROMPT.md` improvements (must-produce-output rule, incremental commit guidance) and the SKILL.md "don't poll" instruction appear to have been effective. The file-boundary issue remains partially unresolved — workers will still fix broken tests by editing files outside their scope.

---

## Suggestions Summary (Prioritized)

1. **P1: Skip codebase exploration when plan file is provided** — add "Plan File Provided" quick-path to Entry Protocol
2. **P2: Inline reuse prompts** — add `--inline` to `worker-prompt` for short reuse prompts, avoid write-read-copy roundtrip
3. **P2: Strengthen out-of-scope file modification rules** — workers should `tf.py block` or `tf.py discover` before modifying files outside Target Files, especially when another worker owns that file
4. **P3: Add acceptance verification step** — orchestrator should spot-check plan verification criteria after all beads close
5. **P3: Track agent IDs at dispatch time** — add `--agent-id` to `tf.py dispatch` for compaction resilience
6. **P3: Clarify init vs sync relationship** — document that `init` doesn't clean up prior contexts

---

## Session Metrics

| Metric | Value |
|--------|-------|
| Beads created | 7 |
| Beads closed | 7 |
| Workers spawned | 2 |
| Worker reuse dispatches | 3 |
| Task-to-worker ratio | 3.5:1 |
| Worker failures | 0 |
| Test count delta | 539 → 725 (+186) |
| Commits | 7 |
| Wall clock | ~28 min |
| Orchestrator turns | 7 |
| Orchestrator output tokens | 41.2k |
