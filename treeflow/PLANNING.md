# Treeflow — Planning Rules

**Additional treeflow requirements for task descriptions:**

1. **Include target file paths** — every task MUST include a `Files:` line listing all files it will create or modify. Use `Files (new):` and `Files (modifies):` to distinguish. Without it, `conflict-check` cannot detect file-level parallelism conflicts.
2. **Mark parallel groups** — add `[parallel]` for tasks within a phase that have no cross-dependencies.
3. **Add skill hints** — when obvious, note the skill domain (e.g., "Go implementation", "React component", "test suite", "CI/CD setup").
4. **Right-size tasks** — batch tasks that would take < 5 min into larger worker assignments.
5. **Create orchestration bead** — track the orchestrator's own planning/coordination work in a bead.
6. **Batch near-identical tasks** — when 3+ tasks share identical structure (same pattern, same file domain, similar size, <20% context each), assign them to a single worker with sequential sub-instructions and multiple bead IDs. This avoids wasting ~80% context per single-task worker spawn.
7. **Reference identifiers, not line numbers** — use function/struct/class names in task descriptions (e.g., "update `update_session()` in `src/store.rs`"). Line numbers drift as parallel workers modify files.
8. **Limit batch diversity** — 4+ domain-diverse tasks in one worker risks context exhaustion. Prefer 2-3 tasks per batch, all in the same domain. File-adjacent but conceptually distinct tasks can go to separate workers even if serialized.
9. **Acceptance criteria** — every task must include acceptance criteria stating observable, testable behavior from the spec's perspective. "Function exists" is not acceptance; "function is called in the pipeline and produces observable result" is.
10. **Cross-command features** — if a spec requirement spans multiple commands or modules, create one task per command/module with its own acceptance criteria. Never combine — cross-command tasks reliably produce one implementation and one omission.
11. **Pre-surface technical obstacles** — when planning identifies technical friction (API shape mismatch, library constraints, ordering dependencies), write the obstacle and its resolution into the task description. Workers discovering obstacles mid-implementation defer; workers given the solution upfront implement it.
12. **Spec-section references** — each task should cite the spec section it implements (e.g., `Spec: spec.md §3 — VAD preprocessing`). After all tasks are created, verify coverage: every spec section should map to at least one task.
13. **Soft dependencies (depends_on)** — if task A creates types/interfaces that task B imports, add `depends_on:Task A title` in Task B's Dependencies section. This prevents batching them into the same parallel group without blocking readiness.
14. **Every "implement package" task must produce tests** — add to the task description: "Write unit tests for all pure functions. Table-driven tests for normalization/conversion helpers are mandatory."
15. **Producer tasks must name their consumer** — "implement `pkg/cache`" is incomplete without "used by Task N — `scan.go` to gate feed downloads on `cache.IsStale()`". Without an explicit consumer, the package becomes dead code. If no consumer task exists, create a corresponding "wire X into Y" task.
16. **Never paraphrase spec identifiers** — flag names, command names, field names, and type names in worker prompts must be copied verbatim from the spec. Do not type `--date1` from memory when the spec says `--date-a`. Reference the spec section instead: "implement the diff command as defined in spec.md §CLI Surface — read that section and use the exact flag names."

**Good treeflow task description:**
> "Create `internal/workflow/oom_report.go`: OOMReportWorkflow(ctx) error — runs weekly. Files (new): `internal/workflow/oom_report.go`, `internal/workflow/oom_report_test.go`. Used by: Task 12 — `cmd/pipeline.go` calls OOMReportWorkflow in the weekly schedule. Spec: spec.md §4.2. [Go implementation]"
