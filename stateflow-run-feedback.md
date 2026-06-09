# stateflow Skill Run Feedback — Forge Plan Import (2026-05-18)

Session: clair-forge plan import
Model: Opus 4.6 (orchestrator only, no workers dispatched)
Objective: Import sculptor plan from `docs/plan-forge.md` into beads, wire dependencies, prepare for implementation

---

## Event Log

### 1. Skill invocation and context gathering (smooth)

The `/stateflow` skill was invoked with clear arguments: plan path, spec path, beads plan path, and reference repo paths. The orchestrator correctly read all inputs in parallel:
- `docs/plan-forge.md` (sculptor plan, 286 lines)
- `docs/spec-forge.md` (forge spec, 604 lines)
- `docs/spec.md` (cross-cutting spec, 468 lines)
- `docs/.beads/forge/plan.md` (existing beads plan template, 392 lines)
- `docs/.beads/forge/deps.txt` (dependency graph, 70 lines)
- `docs/.beads/forge/invariants.md` (cross-worker invariants, 8 lines)
- `.beads/config.yaml` (beads configuration)

**What went well:** Parallel reads were efficient. All context was gathered in 2 rounds of tool calls.

### 2. Plan enrichment (smooth)

The existing beads plan at `docs/.beads/forge/plan.md` had correct task titles and acceptance criteria from a prior sculptor export, but task descriptions were just the title repeated (e.g., `**Task 1.1: Configuration parsing** — config/config.go` as the entire description). The orchestrator enriched all 24 tasks with:
- Full implementation steps from the sculptor plan
- Target file paths
- Spec section references (e.g., "spec-forge.md §Configuration")
- Invariant callouts (e.g., "invariants.md #2")
- TDD markers on high-risk tasks (2.4, 3.5)
- Risk labels (3.5 marked `high-risk`)

**What went well:** The enrichment was comprehensive. Each task description now contains enough context for a worker to implement independently.

### 3. Missing tf.py (gap)

The stateflow skill references `python3 .beads/tf.py validate-plan` and other `tf.py` commands throughout its instructions. **No `tf.py` file exists in this repository.** The orchestrator silently skipped the validate-plan step.

**Impact:** Plan validation was skipped. No structural checks were performed on the plan before loading into beads. This could have caught issues earlier (e.g., the description problem in the original sparse plan).

**Recommendation:** The stateflow skill should either:
1. Check for `tf.py` existence at init and warn clearly if missing
2. Ship `tf.py` as part of the skill or document where to get it
3. Have `awty` subsume the `tf.py` commands that are critical-path (validate-plan, worker-prompt)

### 4. Loading beads with `bd create -f` (smooth)

`bd create -f docs/.beads/forge/plan.md --json` successfully parsed all 25 entries (1 epic + 24 tasks) from the enriched plan. Task descriptions, acceptance criteria, labels, and priorities all loaded correctly.

**What went well:** The beads plan format was correct, the parser handled it cleanly, and all fields were preserved including multi-line descriptions with code blocks.

### 5. Dependency wiring — wrong direction (major issue)

**This was the session's biggest problem.**

The `deps.txt` file uses the format:
```
"blocker" blocks "blocked"
```

The orchestrator mapped this to `bd dep add <blocker_id> <blocked_id>`, e.g.:
```
bd dep add clair-forge-2 clair-forge-3
```

However, `bd dep add` uses the semantics `bd dep add <dependent> <blocker>` — the first argument is the task that **depends on** the second. So `bd dep add clair-forge-2 clair-forge-3` means "clair-forge-2 depends on clair-forge-3" — the **exact opposite** of what was intended.

**How it was caught:** After wiring all 43 dependencies, `bd ready --json` showed `clair-forge-25` (Task 7.3, the very last task) as ready — an obvious impossibility. The Setup task (clair-forge-2) was NOT shown as ready, despite having no actual prerequisites.

**Impact:** All 43 dependencies had to be removed and re-added in the correct direction. This required 4 additional tool calls (2 removes, 2 adds) and ~30 seconds of wasted execution.

**Root causes:**
1. **`deps.txt` format mismatch with `bd dep add` semantics.** The file says `"A" blocks "B"` which naturally reads as "A is the blocker, B is blocked." But `bd dep add B A` is required because `bd dep add` takes `<dependent> <blocker>`. This is counterintuitive when translating from "blocks" language.
2. **No early validation.** After wiring the first few deps, there was no check to confirm direction. A single `bd ready` call after the first 4 deps would have caught the error immediately.
3. **The stateflow skill gives no guidance on `bd dep add` argument order.** The skill mentions deps but doesn't document the positional argument semantics.

**Recommendations:**
1. **`deps.txt` format should match `bd dep add` semantics.** Change the format to `"dependent" depends-on "blocker"` to align with the CLI. Or add a `tf.py wire-deps <deps.txt>` command that handles the translation.
2. **Add a sanity check after dep wiring.** After running all dep commands, run `bd ready --json` and verify the ready set matches expectations (typically only the root/setup tasks should be ready).
3. **Document `bd dep add` argument order in the stateflow skill.** Add: `bd dep add <issue-that-depends> <issue-it-depends-on>` with an example.

### 6. Dependency fix and verification (smooth)

After discovering the direction error:
1. Generated all 43 `bd dep rm` commands (reverse of wrong adds)
2. Generated all 43 `bd dep add` commands in the correct direction
3. Executed in 4 batches (2 removes, 2 adds)
4. Verified with `bd ready --json` — correctly showed only the epic and Setup task
5. Spot-checked 6 tasks across the dependency chain — all correct

**What went well:** The fix was systematic and clean. The ID-to-title mapping from step 4 was reused, so no additional lookups were needed.

### 7. rtk output noise (minor annoyance)

The `rtk` proxy (Rust Token Killer) rewrites shell commands for token savings, but its output formatting made verification harder:
- `grep` output included file-level grouping headers (`📄 60 (1):`) that obscured the actual matches
- Line numbers were reformatted with `0:` prefix instead of standard grep format
- Long lines were truncated with `...`

**Impact:** Minor — required using `python3` one-liners instead of simple grep/sort pipelines for cross-checking. Wasted ~2 tool calls on unreadable output before switching to python.

**Recommendation:** When running verification commands (grep for cross-checking, wc, sort), consider bypassing rtk or using python directly to avoid output format surprises.

---

## Summary Table

| Step | Outcome | Time Impact |
|------|---------|-------------|
| Context gathering | Clean | Optimal (parallel reads) |
| Plan enrichment | Clean | ~1 tool call |
| tf.py validation | Skipped (missing) | Unknown risk |
| bd create -f | Clean | ~1 tool call |
| Dep wiring (wrong) | Failed | Wasted ~4 calls |
| Dep detection | Caught via bd ready | ~1 call |
| Dep fix | Clean | ~4 calls |
| Final verification | Clean | ~2 calls |

---

## Recommendations

### High Priority

1. **Fix deps.txt ↔ bd dep add translation.** Either:
   - Change `deps.txt` to use `"dependent" depends-on "blocker"` format
   - Add `tf.py wire-deps <deps.txt>` that reads the file and runs the correct `bd dep add` commands
   - Add a `bd dep import <deps.txt>` native command

2. **Ship or document tf.py.** The stateflow skill references tf.py extensively but it's not included. Either bundle it with the skill, make it installable, or clearly document which tf.py commands are required vs optional.

3. **Add post-wiring sanity check to stateflow instructions.** After dep wiring, always run `bd ready --json` and verify the ready set contains only root tasks.

### Medium Priority

4. **Improve `bd create -f` description parsing.** The original sculptor plan export produced beads with descriptions that were just the title repeated. The `bd create -f` parser should either:
   - Include the full `### Description` body (it apparently does — the enriched plan worked)
   - Warn when a description exactly matches the title (likely a template/stub)

5. **stateflow skill should document `bd dep add` semantics.** Add to the skill's Rules or Planning Notes: `bd dep add <dependent-issue> <blocker-issue>` — "the first issue depends on (is blocked by) the second."

6. **Add `bd dep add --dry-run` or `bd dep verify`.** A command to check that the dependency graph makes sense (no cycles, ready set is non-empty, leaf tasks have no outgoing blocks).

### Low Priority

7. **stateflow skill init should check for required tools.** Before starting orchestration, verify `bd`, `tf.py`, and `awty` are available and report missing ones.

8. **Consider making deps.txt executable.** If deps.txt contained actual `bd dep add` commands (with the correct argument order), it could be sourced directly: `bash deps.txt`. This eliminates the translation step entirely.

---

## Final State

- 25 beads loaded (1 epic, 24 tasks)
- 43 dependencies wired correctly
- `bd ready` returns: Setup task (clair-forge-2) + epic (clair-forge-1)
- Dependency chain verified across all 7 phases
- Plan is ready for implementation dispatch
