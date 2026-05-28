# Implementation Plan Template

## Structure

```markdown
# Implementation Plan: {Idea Name}

## Setup
- [ ] Verify language/runtime version and available features
- [ ] Create all scaffolding directories and files
- [ ] Install all dependencies before writing source files
- [ ] Create package/module stubs so LSP resolves imports during implementation

## Phase 1: {Phase Name} [parallel]
- [ ] Task 1: {specific, actionable description}
  - Spec: spec.md §{Section A}, §{Section B}
  - [ ] Sub Task 1: {specific, actionable description}
  - [ ] Sub Task 2: {specific, actionable description}
  - AC: {observable, testable behavior from user's perspective}
- [ ] Task 2: {specific, actionable description}
  - Spec: spec.md §{Section}
  - [ ] Sub Task 1: {specific, actionable description}
  - [ ] Sub Task 2: {specific, actionable description}
  - AC: {observable, testable behavior}
- [ ] Task 3: Integration test — {what the test validates end-to-end}
  - AC: {integration test passes, exercising tasks 1-2 together}

## Phase 2: {Phase Name}
- [ ] Task 4: {specific, actionable description}
  - Spec: spec.md §{Section}
  - AC: {observable, testable behavior}
- [ ] Task 5: {specific, actionable description}
  - Spec: spec.md §{Section A}, §{Section B}
  - [ ] Sub Task 1: {specific, actionable description}
  - [ ] Sub Task 2: {specific, actionable description}
  - AC: {observable, testable behavior}
- [ ] Task 6: Integration test — {what the test validates end-to-end}
  - AC: {integration test passes, exercising tasks 4-5 together}

## Dependencies
[What blocks what]

## Risks
[What could go wrong and mitigation]
```

## Quality Rules

* Always include a **Setup** phase for environment verification and dependency installation
* Mark phases/tasks as **`[parallel]`** when tasks have no cross-dependencies — this signals to the implementing agent that sub-agents can run simultaneously
* Task descriptions must name specific files, endpoints, or functions — "implement sync" is too vague, "implement `internal/sync/engine.go`: field discovery, denormalization, ALTER TABLE for new custom fields" is actionable
* For data-heavy or edge-case-heavy packages, note **"TDD recommended"** — write test fixtures and cases before implementation
* Try to keep a task sufficiently detailed for the agent. Refer to other artifacts like the spec, idea, appendix files where the additional context helps the agent
* Every task must include `- AC:` lines stating observable, testable behavior from the user's perspective — not function names or file paths. "Function exists" is not acceptance; "function is called in the pipeline and produces observable result" is.
* When a spec requirement spans multiple commands or modules, create one task per command/module with its own acceptance criteria. Never combine them — cross-command tasks reliably produce one implementation and one omission.
* Include a `## Cross-worker Invariants` section listing contracts that span multiple workers (e.g., "all writes to table X must also update FTS index Y", "all file writes use tmp+rename"). These are copied to `worker-context.md` and `CLAUDE.md` before worker dispatch.
* When planning identifies technical friction (API shape mismatch, library constraints, ordering dependencies), write the obstacle and its resolution into the task description. Workers discovering obstacles mid-implementation defer; workers given the solution upfront implement it.
* Each task should cite the spec section(s) it implements (e.g., `Spec: spec.md §Data Model — SQLite Schema, §Architecture — Package Layout`). A single task may cover multiple spec sections. Add a `## Spec Coverage` table mapping spec sections to tasks — any uncovered section is a gap.
* Each phase should end with an **integration test task** that validates the phase's tasks compose correctly. Individual task ACs verify units; the integration test verifies they work together. The integration test task must explicitly list any inputs, accesses, data, or credentials it needs — surface these requirements upfront so they're available before implementation starts.

## Handoff to BeadFlow

This plan can be imported directly into beadflow for execution tracking:

```
/beadflow import sculptor {idea-name}/
```

BeadFlow reads the plan (plus spec, idea, and appendix files for context) and converts it into Beads issues automatically — no manual reformatting needed. See `beadflow/SCULPTOR-IMPORT.md` for the conversion mapping.
