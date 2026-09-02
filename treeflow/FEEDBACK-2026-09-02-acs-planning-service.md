# TreeFlow Feedback — acs-planning-service Autonomous Build (2026-09-02)

## Session Profile

- **Project**: acs-planning-service (Go service — RICE planning skill service; jai MCP agent loop, SQLite persistence, OIDC identity, Jira 3LO OAuth, React/Vite SPA, Helm/compose deploy, Claude-vs-Gemini eval harness)
- **Plan source**: Sculptor import (`./docs` already had `idea.md` + `spec.md` + `plan.md` + 7 appendices + `beads-graph.jsonl`)
- **Scale**: 33 beads (1 epic + 32 tasks) across Setup + 7 phases; 31 named workers
- **Dispatch mode**: Parallel, **fully autonomous** after a single upfront `AskUserQuestion` (user logged off)
- **Outcome**: 33/33 beads + epic closed. 17 Go packages green (`go build`/`go vet`/`gofmt` clean, offline suite passing). Every phase had a passing **live** integration test (Vertex `claude-opus-4-8` + `gemini-2.5-pro`, jai read-only, compose-up full-pod e2e, Playwright UI click-through). No git remote → nothing pushed.
- **Model**: Opus 4.8 (orchestrator); workers inherited Opus 4.8 (no `--worker-model` override)
- **Worker reuse**: None — 31 fresh spawns, all ended idle (fresh-spawn is the skill default)
- **Source session**: `1ecd582b-f576-419b-b744-216a84c77115`

## Session Statistics

| Metric | Value |
|---|---|
| Wall-clock | ~7h02m (21:24 → 04:22) |
| Orchestrator turns | 38 |
| Tool calls | 157 (Bash 102, Agent 31, Edit 10, Read 7, Write 6, AskUserQuestion 1) |
| Errors | 2 (both recovered) |
| Orchestrator output tokens | 804K |
| Cache read / write | 86.0M / 22.3M |
| **Context compactions** | **0 over 7 hours** |
| Beads created | 33 (1 epic + 32 tasks) |
| Workers dispatched | 31 named, `{domain}-{N}` |
| Waves | 1–4 workers each, phase-by-phase |
| Task-notifications received | 35 (for 31 workers — 4 dup/late) |
| `tf.py` subcommands | status 33, worker-prompt 31, dispatch 31, notify 31, ready 21, dep 12, conflict-check 6, verify 5, init 2, close 2, stalled 2 |

Note: `.beads/context-*/` is `.gitignore`d and was cleaned up post-session, so the ballooned context files couldn't be re-measured directly — the 217KB figure is from a mid-session measurement; the *mechanism* is confirmed in `tf.py` source (below).

---

## What Worked Well (keep)

1. **Tracer-bullet first wave + build-verify before fan-out.** The orchestrator batched `scaffold-1` (2 beads), built, and verified before fanning out — exactly as SKILL-DISPATCH prescribes. De-risked the foundation.
2. **Context discipline held for 7 hours: zero compactions.** `--write-file` was used for 13 of 15 `worker-prompt` calls (prompts stayed out of orchestrator context as ~100-token metadata), and task-notification `<result>` bodies were consistently discarded — the orchestrator never Read a worker's notification output file, always using `notify --auto --summary`.
3. **Front-loading all decisions into one `AskUserQuestion`** (Go version, blocked-bead policy, Jira-write policy, worker model) before the user logged off, then running end-to-end unattended. This is the pattern to document for autonomous runs.
4. **Per-phase LIVE integration gates.** Each phase ended with a dedicated `phaseN-integ-1` worker running real Vertex/jai/compose/Playwright checks — execution-based verification, not just unit tests. Arguably stronger than the skill's default gate.
5. **Just-in-time graph correction + pre-dispatch `tf.py bd-path` smoke test.** The required pre-flight was actually run.
6. **Strong autonomous recovery by workers.** `docker-1` discovered a plan gap (no `serve` daemon command though container/Helm/CI all assumed `/acs-planning serve`) and added + live-verified it; `phase1-integ-1` diagnosed and fixed Gemini non-termination (`agent.Config.StopWhenOutputsComplete`); `phase5-integ-1` found a real `rox_ticket` typeahead parse bug.
7. **Clean, consistent worker naming** (`provider-1`, `store-1`, `ui-shell-1`, `phase3-integ-1`, …) kept the registry readable.

---

## What Needs Improvement

### Issue 1 — Context files balloon by BYTE size while the archive trigger watches LINES; the bloat then propagates into every later worker's prompt
**Severity: HIGH — NEW.** Highest-leverage fix.

**What happened**: `tf.py` `_write_context_update` appends each completion as a `summary_block` into both `epic-{slug}.md` and `task-summaries.md`, and `notify --gotcha` appends one long line to `worker-context.md`. Summaries ran 200–400 chars and gotchas 300–500 chars, each stored as **one very long line** — so 31 completions produced only ~120–150 lines but ~217KB per file. SKILL.md (§Context Management) says "Archive when any file exceeds **500 lines** → condense" — a **line-count trigger that never fires** on few-but-huge lines. There is no `tf.py archive-context` command and no byte-size check anywhere in the source.

**Why it matters (not theoretical)**: `cmd_worker_prompt` reads the **entire** epic file into `{epic_context}` (`epic_file.read_text()`, tf.py:2256) and the **entire** `worker-context.md` into `{project_context}` (tf.py:2197), with no truncation. So every worker dispatched later in the run received the full accumulated epic summaries inlined in its prompt — by Phases 6–7 that is ~150–200KB per dispatch. `--write-file` hid this from the *orchestrator's* token count (a blind spot), but the **workers** paid it — visible in the 22.3M cache-write total. Compounded by Issue 7: no concise `phase-*.md` summaries existed, so `worker-prompt` fell back to dumping the raw epic file.

**Recommendation [tf.py]**:
1. Trigger archiving on **byte size** (e.g. > ~48KB), not line count.
2. Add a `tf.py archive-context` command that condenses to a 50–80-line digest.
3. Truncate/summarize `{epic_context}` in `worker-prompt` (last-N completions or a condensed digest) so late workers don't ingest the entire epic history.

### Issue 2 — Sculptor over-linearization forced manual graph surgery across all 7 phases
**Severity: HIGH — RECURRING (4th session).**

**What happened**: The imported `beads-graph.jsonl` linearized every phase into a serial chain (`1.1→1.2→1.3→1.4→1.5`) despite `plan.md` documenting parallelism. The orchestrator detected it ("This is exactly the sculptor over-linearization the skill warns about"), and rewired the graph with **12 `tf.py dep` calls + several `bd dep remove`** across the run. It also hit that `bd dep remove` emits no JSON (`jq` choked). Setup tasks were similarly all parallel-ready even though `go build` needs `go mod init` first, requiring added sequencing deps.

**Root cause**: `sculptor export-beads` still doesn't preserve `[parallel]` annotations or the `## Dependencies` section — it emits a conservative linear chain.

**Recurrence**: This exact issue and the proposed fix (`tf.py validate-graph --plan`) were recommended in FEEDBACK-2026-08-24 (Issue 1) and appear in the 07-10 and 08-10 docs. **The command still does not exist.**

**Recommendation [tf.py / sculptor]**: Ship `tf.py validate-graph --plan plan.md` (diff graph edges against the plan's dependency section, flag/auto-repair mismatches), **or** make `import-graph` honor `[parallel]` markers. Until then, SKILL.md should state graph correction is *expected* after every sculptor import.

### Issue 3 — `cmd_claim` throws `UnboundLocalError: cap_warning` when `CLAUDE_AGENT_NAME` is unset
**Severity: MEDIUM — NEW tf.py bug, confirmed in current source.**

**What happened**: In `cmd_claim`, `cap_warning = ""` is assigned only **inside** the `if worker_name and worker_name in reg.get("workers", {}):` block (tf.py:574, under the guard at tf.py:562). If `worker_name` is empty (env unset) or not in the registry, and the `try` doesn't raise, line 583 `if cap_warning:` references an **unbound local**. Crucially, `bd update … in_progress` at tf.py:552 runs first — so the bead **is** claimed, but the command still exits non-zero with a traceback.

**Impact**: The orchestrator worked around it by prepending `export CLAUDE_AGENT_NAME=orchestrator` to **66 of 102** Bash calls (shell env doesn't persist between tool calls) — noise on nearly every command.

**Recommendation [tf.py]**: Hoist `cap_warning = ""` above the `if worker_name…` block, and/or default an empty `CLAUDE_AGENT_NAME` to `"orchestrator"`.

### Issue 4 — `worker-close` diffs the WHOLE tree vs the dispatch SHA, so parallel workers' uncommitted files block each other's close
**Severity: MEDIUM — NEW tf.py design flaw, confirmed in source.**

**What happened**: `cmd_worker_close` runs `git diff {dispatch_sha} --name-only` and `git diff {dispatch_sha} HEAD --name-only` (tf.py:388–389, 424) **unscoped** to the worker's own files. In a parallel wave, worker A's close sees workers B/C's uncommitted changes. `engine-1` reported this mid-run; workers mitigated by "git add ONLY your own paths, commit promptly; if blocked by others' files, retry or use `--force`."

**Recommendation [tf.py]**: Scope the diff to the worker's declared `--files` (already a parameter): `git diff {dispatch_sha} -- {files}`. The per-file checks at tf.py:402/405 already do this — the initial validation at tf.py:388 should too.

### Issue 5 — `tf.py verify --test-cmd "go test ./..."` ran live Vertex tests → 2-min timeout (Error 2)
**Severity: MEDIUM-HIGH.**

**What happened**: At 23:25 the orchestrator chained, in one Bash call, `tf.py notify … && tf.py verify --build-cmd … --test-cmd "go test -short ./..." && tf.py ready && tf.py conflict-check && for … worker-prompt`. It exited 143 (timeout) with `{"ok":true,"bead_status":"closed"}` already printed — i.e. `notify` succeeded fast, but the chained `verify` **hung** running the suite. The orchestrator correctly diagnosed it: `GOOGLE_CLOUD_PROJECT` was set, so `go test ./...` ran the live Vertex tests (minutes, real cost). It recovered by switching to build-only wave gates (`timeout 100 go build ./...`).

**Root cause**: The skill's between-wave verification guidance (SKILL.md §Session End; SKILL-DISPATCH) recommends running the test command but never warns that cloud env vars turn `go test ./...` into a slow, costly live run — and it packs a fast state op and a slow test into one 2-min-timeout Bash call.

**Recommendation [SKILL-DISPATCH.md / tf.py]**: Default wave gating to **build-only or `-short`**; document the live-test env-leak; make `tf.py verify` build-only by default with an explicit `--live` opt-in; never chain `notify` with a potentially multi-minute test in one Bash call.

### Issue 6 — Duplicate/late `<task-notification>` events burned ~10 near-empty turns
**Severity: MEDIUM — RECURRING, worsens at scale.**

**What happened**: 35 task-notifications arrived for 31 workers; the orchestrator handled dup/late/re-stop events **~21 times**, of which ~10 turns were pure "Duplicate — skipping" acknowledgements (each a full wake-up + a paragraph of narration). FEEDBACK-2026-07-10-ORCHESTRATION flagged this (then 4 duplicates) and the `late:true` mechanism was added — but the per-duplicate wake-up + narration cost persists and **scaled with worker count** (4 → ~10).

**Recommendation [SKILL-DISPATCH.md]**: Instruct a **silent, zero-narration no-op** for late/duplicate notifications; note the per-duplicate turn cost scales with worker count.

### Issue 7 — Phase machinery entirely bypassed → no `spec-trace`, no `phase-*.md` summaries (which starved `worker-prompt`)
**Severity: MEDIUM — deviation.**

**What happened**: Despite an epic + 7-phase hierarchy, `phase-gate`, `phase-complete`, and `spec-trace` were **never called** (0×). The orchestrator substituted per-phase integration **beads** + `tf.py verify --build-cmd` as gates. This satisfied (and arguably exceeded) the live-integration directive, but: (a) the automated `spec-trace` never ran — spec coverage was never machine-verified; (b) no code-review agent ran at gates; (c) no `phase-*.md` files were emitted, so `worker-prompt`'s "Key Interfaces from completed phase summaries" (tf.py:2236) had nothing to read and fell back to the raw epic dump — directly feeding Issue 1.

**Recommendation [SKILL-DISPATCH.md]**: Clarify that even when integration beads serve as gates, still run `tf.py spec-trace` and emit `phase-*.md` so workers get concise interfaces instead of the raw epic dump. Also reconcile the **doc contradiction** between the anti-pattern "never spawn fresh workers without running `tf.py sync` at session start" and SKILL-DISPATCH's "sync unnecessary for a fresh plan."

### Issue 8 — `tf.py status` closed-count unreliable → progress tracked by mental math
**Severity: LOW — RECURRING (08-24 Issue 6).**

Every completion turn queried `tf.py status` for `active` and `open` only; the orchestrator tracked "19/32 closed (~59%)" manually throughout. **Recommendation [tf.py]**: Make `tf.py status` report an accurate `closed`/`done` count.

### Issue 9 — `wave-plan` never used despite being the prescribed automated path
**Severity: LOW — deviation.** The orchestrator used `conflict-check` (6×) + manual reasoning, even narrating "conflicts will be managed via `wave-plan`" — but never called it. No harm, but the prescribed automation went unexercised. Either fix `wave-plan` ergonomics or downgrade it in the docs in favor of `conflict-check`.

### Issue 10 — Worker-to-worker SendMessage can duplicate work the orchestrator is also handling
**Severity: LOW.** When `phase5-integ-1` found the typeahead bug, it SendMessage'd `api-1` to fix it *while* the orchestrator independently created fix bead `uef` and dispatched `argfix-1`. Both converged on the same commit (`2828703`). **Recommendation [SKILL.md]**: Warn that worker-to-worker coordination can duplicate orchestrator-mediated fixes; prefer routing discovered fixes through the orchestrator.

### Issue 11 — Go 1.27 toolchain probe consumed orchestrator context (Error 1)
**Severity: LOW — environment-specific.** ~6 tool calls probing `golang.org/dl` (no `go1.27` wrapper published), `GOTOOLCHAIN`, `GOSUMDB`. Resolved via `go env -w GOSUMDB=sum.golang.org GOTOOLCHAIN=auto`. Not a skill defect, but toolchain bring-up could be delegated to a setup worker instead of run inline.

---

## Deviations from Skill Guidance (summary)

| Deviation | Prescribed | Helped / Hurt |
|---|---|---|
| Never ran `tf.py sync` before first dispatch | Anti-pattern says always sync at session start; SKILL-DISPATCH says unnecessary for fresh plan | Neutral (fresh plan) — exposes a **doc contradiction** |
| Never ran `phase-gate`/`phase-complete`/`spec-trace` | The phase loop | **Hurt** — no machine spec-trace, no phase summaries (Issue 7) |
| Never ran `wave-plan` (used `conflict-check`) | "compute waves via `wave-plan`" | Neutral (Issue 9) |
| No proactive `phase-summary` compaction checkpoints | "Proactive Compaction Between Waves" | Helped by luck — 0 compactions, but risky |
| ~1.03 tasks/worker | Target ≥1.5 | Defensible — tasks were large distinct packages, not near-identical small ones |

No **hard** anti-pattern was violated: no orchestrator source-code writes, no `git add/commit` on source (workers committed their own), no `git stash -u`, no dispatch-before-gate, notification results discarded.

---

## Context & Token Management

- **Orchestrator discipline was excellent** — 0 compactions across 7h / 804K output tokens; `--write-file` for 13/15 prompts; notification `<result>` bodies discarded.
- **But the bloat was displaced onto workers, not eliminated.** Because `worker-prompt` inlines the full ballooning `epic-*.md` (tf.py:2256) and `worker-context.md` (tf.py:2197), and gotchas append unbounded via `notify --gotcha`, each later worker's prompt grew as the run progressed. `--write-file` masks this from the orchestrator's own token count — **the orchestrator's lean context is not the whole story** (22.3M cache-write / 86M cache-read). Fixing Issue 1 addresses this directly.

---

## Worker Management

- 31 fresh spawns, **0 reuse**, all ended idle. SendMessage was available (workers used it among themselves) — the orchestrator chose fresh spawns (the skill default, compliant).
- **No stalls needed recovery**; `tf.py stalled` used 2× plus `status` health checks. The orchestrator did **not** poll git in a loop (compliant with the "Waiting for Workers" rule).
- **All workers self-closed** via `worker-close`; the `notify` auto-close fallback was never needed.
- One coordination inefficiency (Issue 10).

---

## Top Recommendations (ranked by leverage)

1. **[tf.py]** Switch the context-archive trigger from **line-count to byte-size**, add `tf.py archive-context`, and **truncate/summarize `{epic_context}` in `worker-prompt`** (tf.py:2256). Single highest-leverage fix (Issues 1, 7). *New.*
2. **[tf.py]** Fix `cmd_claim` `UnboundLocalError`: hoist `cap_warning = ""` above the `if worker_name…` block (tf.py:574) and default empty `CLAUDE_AGENT_NAME` → `"orchestrator"`. Eliminates the 66× `export` workaround (Issue 3). *New, confirmed.*
3. **[tf.py]** Scope `worker-close`'s diff to the worker's `--files` (`git diff {dispatch_sha} -- {files}`, tf.py:388–424) so concurrent workers don't block each other's close (Issue 4). *New, confirmed.*
4. **[tf.py / sculptor]** Ship `tf.py validate-graph --plan plan.md` (auto-detect + repair sculptor linearization) or make `import-graph` honor `[parallel]` (Issue 2). *Recommended in 3 prior docs; still unbuilt.*
5. **[SKILL-DISPATCH.md / tf.py]** Between-wave verification defaults to **build-only or `-short`**; document that `go test ./...` runs slow, costly **live** tests when cloud env vars are set; never chain a fast state op with a multi-minute test (Issue 5).
6. **[SKILL-DISPATCH.md]** Handle late/duplicate `<task-notification>` events as a **silent, zero-narration no-op**; note cost scales with worker count (Issue 6). *Recurring.*
7. **[SKILL-DISPATCH.md]** Clarify phase-mode vs flat-mode: when integration beads serve as gates, **still run `spec-trace` and emit `phase-*.md`**. Reconcile the `sync` doc contradiction (Issue 7).
8. **[tf.py]** Make `tf.py status` report an accurate `closed`/`done` count (Issue 8). *Recurring — 08-24 Issue 6.*
9. **[SKILL.md]** Cap/rotate the `## Known Gotchas` section that `notify --gotcha` appends to `worker-context.md`, since `worker-prompt` inlines the whole file (relates to Issue 1).
10. **[SKILL.md]** Warn that worker-to-worker SendMessage coordination can duplicate orchestrator-mediated fixes; prefer routing fixes through the orchestrator (Issue 10).

## Recurrence Summary

- **Recurring despite prior recommendations**: sculptor linearization (Issue 2, 4th time), `status` closed-count (Issue 8, 2nd), duplicate-notification cost (Issue 6 — *worsened* at scale even after `late:true` was added).
- **New this session (all confirmed in `tf.py` source)**: context byte-vs-line balloon + epic-dump into worker prompts (Issue 1), `cmd_claim` UnboundLocalError (Issue 3), `worker-close` unscoped diff (Issue 4), live-test env-leak on `verify` (Issue 5).

The headline: **the skill's orchestration model is working well** (0 compactions, clean autonomous 33-task build with live per-phase verification). The remaining friction is concentrated in **`tf.py` mechanics** — two outright bugs and a context-management heuristic that measures the wrong dimension — not in the orchestration strategy.
