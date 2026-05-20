# Sculptor Feedback Implementation Verification

**Date:** 2026-05-20
**Source feedback:** `sculptor-feedback-voir.md` (6 issues)
**Test plan:** `/tmp/test-project/plan-voir.md` (10 phases, 35 tasks)
**Test spec:** `/tmp/test-project/spec-voir.md`

## Verification Method

Ran `sculptor export-beads /tmp/test-project` with the updated code, then compared old vs new `.beads/` output for each issue.

## Issue 1 (P0): Task body text in descriptions — FIXED

**Before:** Task 1.1 description was a single line:
```
**Task 1.1: Schema types and qualifier construction** — `db/schema.go`
```

**After:** Description includes all body content — bucket constants, struct definitions, Qualifier function spec, TDD rationale, test cases, and spec section references:
```
- Implement bucket name constants: `BucketAdvisory`, `BucketDetail`, `BucketAlias`, ...
- Implement `Qualifier(rec *claircore.IndexRecord) string` — repo key takes precedence...
- Write unit tests: qualifier for repo-keyed record, distro with DID+VersionID...
- Spec: spec-voir.md §Data Model — bbolt Schema, spec.md §Shared Data Model...
```

45 matches for body content keywords across the new plan.md vs 0 in the old.

**Implementation:** Added `body_lines` field to task/subtask dicts in `parse_plan()`, catch-all capture block for non-blank lines, and emission in `generate_beads_plan()`.

## Issue 2 (P0): Dependency graph uses explicit Dependencies section — FIXED

**Before:** Fully serial chain — Phase 3 was blocked by Phase 2, Phase 4 by Phase 3, etc.
```
"Task 2.1: Writer" blocks "Task 3.1: BboltMatcherStore"     # WRONG
"Task 3.1: BboltMatcherStore" blocks "Task 4.1: Source"      # WRONG
```

**After:** Matches the plan's `## Dependencies` section:
- Phase 3 blocked by Phase 1 (12 edges from 4 Phase 1 tasks to 3 Phase 3 tasks) — NOT Phase 2
- Phases 4, 5, 7 blocked by Setup only (parallel start)
- Phase 9 blocked by Phase 6 (not Phase 8)
- Phase 2 is a side branch (depends on Phase 1, nothing depends on it until Phase 10)

```
"Setup:..." blocks "Task 4.1: Source interface"              # Phase 4 from Setup
"Setup:..." blocks "Task 5.1: bbolt-backed layer cache"      # Phase 5 from Setup
"Setup:..." blocks "Task 7.1: Formatter interface"           # Phase 7 from Setup
"Task 1.1: Schema" blocks "Task 3.1: BboltMatcherStore"      # Phase 3 from Phase 1
```

Zero edges from Phase 2 to Phase 3. Zero edges from Phase 3 to Phase 4.

**Implementation:** New `parse_dependency_section()` function parses natural-language dependency descriptions into `{phase_idx: [depends_on_indices]}`. `generate_deps()` uses these explicit edges when available, falls back to linear chain for plans with `Dependencies: None`.

## Issue 3 (P1): Epic description from spec.md — FIXED

**Before:** Epic had no description (idea.md didn't exist in test project).

**After:** Epic description is the first paragraph of spec-voir.md:
```
The Clair vulnerability scanning ecosystem runs three products — Clair,
Scanner V2, and Scanner V4 — each with its own vulnerability data pipeline...
```

**Implementation:** `read_idea_description()` now falls back to first paragraph of `spec.md` when `idea.md` is missing or yields empty content.

## Issue 4: `make_task_slug` — NO CHANGE NEEDED

Feedback doc explicitly said "No change needed for the Voir case." Task slugs correctly preserve the `Task N.N:` prefix. Confirmed in output.

## Issue 5 (P2): TDD detection from body text — FIXED

**Before:** Zero `tdd` labels in old plan.md output. Only `[TDD]` in title was detected, but the Voir plan uses "TDD recommended" in body text.

**After:** 3 tasks have `tdd` label:
- Task 1.1 (line 75) — "TDD recommended" in body text
- Task 2.1 (line 170) — "TDD recommended" in body text
- Task 10.1 (line 617) — `[TDD]` in title (already worked)

**Implementation:** Post-processing pass in `parse_plan()` scans `body_lines` for "tdd recommended" (case-insensitive).

## Issue 6 (P1): wire-deps accepts bd create list output — FIXED

**Before:** `wire-deps --id-map created.json` crashed with `AttributeError: 'list' object has no attribute 'items'` when given raw `bd create -f --json` output (list format).

**After:** Accepts both formats:
- List: `[{"id": "x-1", "title": "A", ...}, ...]` (raw `bd create` output)
- Dict: `{"A": "x-1", ...}` (converted id-map, as produced by `--run` mode)

Verified: ran `wire-deps` with list-format JSON — parsed correctly, attempted `bd dep` commands (failed only because no beads DB at test path, not a format error).

**Implementation:** `isinstance(mapping, list)` check with appropriate list-of-dicts extraction.

## Test Coverage

| Test | Status |
|------|--------|
| `test_task_body_preserved` | PASS |
| `test_tdd_recommended_in_body` | PASS |
| `test_task_body_in_output` | PASS |
| `test_explicit_deps_override_linear_chain` | PASS |
| `test_parallel_phases_from_setup` | PASS |
| `test_side_branch_phase` | PASS |
| `test_fallback_to_spec` | PASS |
| `test_accepts_bd_create_list_output` | PASS |

All 84 pre-existing tests pass unchanged. 8 new tests added. Total: 92 passed.

## Summary

All 6 issues from `sculptor-feedback-voir.md` are addressed. The three P0 issues (empty descriptions, wrong dependency graph) that caused the full manual rewrite are fixed. The two P1 issues (spec fallback, wire-deps crash) that blocked workflows are fixed. The P2 issue (TDD body detection) is fixed. Backward compatibility is preserved — plans with `Dependencies: None` produce identical output to the old code.
