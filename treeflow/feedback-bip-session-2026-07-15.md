# TreeFlow Feedback: BIP Session (2026-07-15)

## Session Profile

- **Project:** Behavioral Intelligence Platform (Python/Click/SQLite)
- **Scope:** Full implementation from spec — 27 beads (1 epic + 26 tasks), 12-layer dependency chain
- **Result:** 27/27 beads closed, 539 tests, ~12,800 LOC, 27 commits
- **Workers spawned:** ~30 (including redispatches for failures)
- **Waves:** 10 dispatch waves across the dependency graph
- **Model:** Opus (orchestrator and all workers)

---

## What Worked Well

### 1. Wave-plan and conflict-check
`tf.py wave-plan` and `conflict-check` consistently produced correct parallel groupings. The section-annotation support (`Files (modifies): src/config.rs [StorageConfig]`) wasn't needed for this project, but the basic file-overlap detection prevented several potential conflicts.

### 2. Worker-prompt assembly
`worker-prompt --write-file` was essential for keeping the orchestrator's context lean. Without it, each prompt (~2-4k tokens) would have accumulated rapidly across 30+ dispatches. The `--prompt-only` variant was never needed — `--write-file` was strictly better.

### 3. Batch-notify
Closing multiple beads from a single worker in one call (`batch-notify --pairs`) saved round-trips for the skill workers (3 beads per worker).

### 4. Registry-based state tracking
After context compression, `tf.py status` and `tf.py registry` provided reliable recovery. The file-based registry survived every compaction event.

### 5. Ready filtering
`tf.py ready` correctly filtered epics and supplemented capped `bd ready` results. The `supplemented` count in the response was useful for understanding when `bd ready` missed tasks.

---

## Problems Encountered

### P0: Workers frequently fail to write code or commit

**The single biggest problem.** At least 5 of ~30 worker dispatches produced no code output — the worker "completed" with files still at stub state. This required:
- Detecting the failure (checking `wc -l` on target files)
- Reopening the bead
- Retiring the failed worker
- Redispatching with a more explicit prompt

**Root causes observed:**
1. Workers claim the task via `tf.py claim` but then spend their entire context reading source files without writing anything.
2. Workers write code but never commit — they run out of context or get stuck on test failures.
3. Workers call `worker-close` without having committed, and `notify` auto-closes the bead as if work was done.

**Impact:** This consumed ~30% of the orchestrator's context on failure detection and redispatch logistics. It was the primary bottleneck, not parallelism or dependency ordering.

**Suggested fixes:**
- `worker-close` should verify that at least one file in the target list was actually modified (check `git diff --name-only` against the target files).
- `notify` should NOT auto-close beads when `worker-close` was never called by the worker. Currently `auto_closed: true` triggers on every `notify` for an `in_progress` bead — there's no distinction between "worker called worker-close and it closed" vs "orchestrator called notify and it force-closed."
- The worker prompt template should include a **hard rule**: "You MUST write files using the Write or Edit tool before calling worker-close. If you have not created or modified any files, do NOT call worker-close — instead report what blocked you."

### P1: Orchestrator polling loop is extremely wasteful

The orchestrator has no notification-driven wake mechanism for worker completions. It degenerates into a `git log --oneline` polling loop, checking every few seconds whether a new commit appeared. In this session, the orchestrator made **hundreds** of identical `git log` / `git status` / `wc -l` calls while waiting for workers.

**Impact:** Massive context waste. Each poll costs ~200 tokens of output, and a worker that takes 5 minutes to complete generates 50+ redundant polls.

**Suggested fixes:**
- The skill should instruct the orchestrator to **stop polling after dispatching** and instead rely on `<task-notification>` events. The notification system exists — the orchestrator just doesn't trust it and polls anyway.
- Add a clear instruction: "After dispatching workers, do NOT poll for completion. You will receive `<task-notification>` events automatically. Only check `tf.py stalled` periodically (every 5-10 minutes) as a safety net."
- Consider adding a `tf.py await` command that the orchestrator calls in a single `run_in_background` Bash call, which blocks until a file-system signal (like a sentinel file) appears.

### P1: Parallel workers modifying the same file (cli.py)

Two CLI command workers both wrote to `bip/cli.py`. The conflict-check said "safe" because neither bead listed `bip/cli.py` as a target file explicitly — the descriptions mentioned different subcommands but the implementation file was the same.

**Impact:** One worker committed first, then the second worker's changes were based on the pre-commit version. In this case it worked because the second worker read the already-committed version, but this was luck.

**Suggested fixes:**
- `conflict-check` should look at the actual CLI file mentioned in the bead descriptions, not just the `Files:` line. If two beads both describe adding commands to the same CLI module, that's a conflict.
- The SKILL.md should explicitly warn: "CLI/routing/config files that accumulate registrations are serialization bottlenecks even when the logical changes don't overlap."

### P1: `notify --auto-close` is too aggressive

When the orchestrator calls `tf.py notify` for a worker that completed without calling `worker-close`, `notify` force-closes the bead. This masks failures — beads get closed even when no code was written.

**Suggested fix:** `notify` should return `bead_status: "in_progress"` and `auto_closed: false` when `worker-close` was not called. The orchestrator should then decide: inspect the git diff, and either close manually or redispatch. The current `--force` flag on `notify` is the right escape hatch, but it shouldn't be the default.

### P2: Stall detection doesn't actually trigger

`tf.py stalled --threshold-mins N` never reported any stalled workers in this session, despite several workers running for 30-40+ minutes without producing output. The heartbeat mechanism relies on workers calling `tf.py` subcommands (claim, block, discover, worker-close), but workers that are stuck reading files never call any `tf.py` command after `claim`.

**Suggested fixes:**
- The stall threshold should be configurable per-task, not just per-session. A seed-data authoring task (96 questions) legitimately takes 15+ minutes, while a stub test file should take 3 minutes.
- Add `--expected-mins` to `claim` (already supported) and actually use it in `_is_stalled()` — if a worker claims with `--expected-mins 5` and is silent for 15 minutes, that's a stall even if the global threshold is 20.

### P2: Templates-1 worker couldn't commit for 40+ minutes

The templates worker wrote all 10 files but couldn't commit. It eventually made 3 duplicate commits. The orchestrator had to commit the files manually. Root cause unknown — possibly the worker's context was exhausted before reaching the commit step.

**Suggested fix:** The worker prompt should instruct workers to commit files incrementally if there are many (e.g., "commit every 3-4 files" for batch content tasks). A single atomic commit of 10 template files is less resilient than 2-3 incremental commits.

### P3: Duplicate late notifications

Workers generate multiple `<task-notification>` events — on completion, on re-render, on compaction. The `late` flag in `notify` handles this correctly, but the orchestrator still processes them mentally, wasting context on "already processed" checks.

**Suggested fix:** Already handled well by `notify`'s `late: true` response. The SKILL.md could just emphasize: "When `late: true`, skip entirely — no action, no logging, no context update."

---

## Skill Prompt (SKILL.md) Suggestions

### 1. Add a "Worker Failure Recovery" section
Currently, recovering from a failed worker requires the orchestrator to improvise: check files, reopen bead, retire worker, redispatch with a better prompt. This is a common pattern that should be codified:

```
### Worker Failure Recovery
1. Check target files: `wc -l` on expected outputs
2. If files are stubs: `bd update <id> --status open`, `tf.py retire <worker>`, redispatch
3. If files written but uncommitted: commit them manually, then `tf.py notify`
4. If tests fail: SendMessage to worker to fix (if addressable), else retire and redispatch
```

### 2. Add explicit "don't poll" instruction
```
### Waiting for Workers
After dispatching, DO NOT poll `git log` or `git status` in a loop.
You will receive `<task-notification>` events automatically.
Only run `tf.py stalled --threshold-mins 20` every 5-10 minutes as a safety net.
Between dispatches, use the idle time to prepare prompts for the next wave.
```

### 3. Worker prompt needs stronger "you must write code" language
The current WORKER-PROMPT.md says "Execute exactly what the issue describes." This is too soft. Workers routinely spend their entire context reading files and never write. Add:

```
## CRITICAL: You must produce output
- You MUST create or modify at least one file listed in Target Files
- Reading source files is preparation, not the task — budget at most 40% of your work on reading
- If you have read 5+ files and haven't started writing, STOP READING and START WRITING
- If you cannot complete the task, call `tf.py block` with an explanation — do NOT silently finish
```

### 4. Batching guidance needs a max-files rule
The current guidance says "batch 3+ similar tasks." But in practice, batch tasks that touch many files (like 10 templates) overwhelm workers. Add: "For content-heavy batch tasks (>5 output files), instruct the worker to commit incrementally."

### 5. The "Scope Detection" quick-path is valuable but under-documented
The scope detection paragraph saves unnecessary initialization for simple bead operations. It should be promoted to a decision flowchart at the top of the skill.

---

## tf.py Code Suggestions

### 1. `worker-close` should validate file changes
```python
# In cmd_worker_close, before closing the bead:
target_files = args.files.split(",") if args.files else []
if target_files:
    diff = _run("git diff --name-only HEAD", cwd=beads_dir.parent)
    changed = set(diff.stdout.strip().split("\n")) if diff.stdout.strip() else set()
    missing = [f for f in target_files if f.strip() not in changed]
    if missing:
        warnings.append(f"target files not modified: {', '.join(missing)}")
```

### 2. `notify` should distinguish self-closed vs force-closed
Add a `closed_by` field to the response:
- `"closed_by": "worker"` when the worker called `worker-close` (registry has `closed_self: true`)
- `"closed_by": "notify"` when notify auto-closes
- `"closed_by": "orchestrator"` when the orchestrator manually closed

### 3. Add `dispatch --expected-mins` passthrough
Currently `--expected-mins` is only on `claim`. If the orchestrator sets it at dispatch time, it can be passed to the worker prompt and auto-included in the `claim` call.

### 4. `wave-plan` should consider worker-model throughput
When using faster/cheaper models (sonnet, haiku), more workers can run in parallel. The max-concurrent-6 rule in SKILL.md is model-agnostic but should scale with model tier.

---

## Token Efficiency Observations

| Source | Estimated Waste | Fix |
|--------|----------------|-----|
| Polling loops (git log/status/wc) | ~40k tokens | Stop polling, trust notifications |
| Late notification processing | ~5k tokens | Skip entirely on `late: true` |
| Worker failure redispatch overhead | ~15k tokens | Better worker prompts, `worker-close` validation |
| Repeated `tf.py stalled` with no stalls | ~3k tokens | Only check after significant time gaps |

Total estimated waste: **~60k tokens** out of an estimated ~300k orchestrator tokens (20% waste).

---

## Summary

TreeFlow's core architecture is sound — the layered context system, `tf.py` state management, and wave-based dispatch work well. The main issues are operational:

1. **Workers silently failing** is the #1 problem. Fix via `worker-close` validation and stronger prompt language.
2. **Orchestrator polling** is the #2 problem. Fix via explicit "don't poll" instructions in SKILL.md.
3. **Auto-close on notify** masks failures. Fix by requiring `worker-close` as the primary close path.

These three fixes would eliminate ~80% of the waste observed in this session.
