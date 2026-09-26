# Sculptor — Validation & Session Continuity

## Validation Tool

`sculptor.py` provides deterministic validation. Use it at the specified points — don't rely on manual grep or visual inspection for these checks.

```
python3 ~/.claude/skills/sculptor/sculptor.py <command> [args]
```

| Command | When to use | What it checks |
|---|---|---|
| `phase <dir>` | Session resumption | Which files exist, which phase we're in, pending annotations |
| `annotations <file>` | Before addressing annotations | Shows the round's marks with surrounding context. The mode (git vs in-file) is chosen automatically — the agent just runs it. Optional edge-case flags: `--since <ref>`, `--no-git`, `--context N`, `--json` |
| `verify-clean <file>` | After addressing annotations | Confirms all in-file markers (`>>` lines, span fences, inline quotes/carets) were removed (returns PASS/FAIL). Retained deliberately — deterministic and cheap |
| `report <file> [--round N] [--since <ref>]` | After addressing a round | Scaffolds `feedback/round-N.md`: one row per annotation + `git diff --stat`, for the agent to fill in status/how |
| `lint-spec <spec.md>` | Before asking user to annotate spec | Dead types, path consistency, TODOs, untagged code blocks |
| `lint-plan <plan.md> --spec <spec.md>` | Before asking user to annotate plan | Missing AC lines, missing sections, spec coverage table validation |
| `lint-cross <dir>` | After writing spec + plan | Appendix link resolution, spec type coverage in plan, cross-reference consistency |
| `export-beads <dir>` | Phase 6 (finalize) | Generates `.beads/beads-graph.jsonl` and `invariants.md`, and copies `glossary.md` (if present) to `.beads/glossary.md`. Add `--run` to execute `bd create --graph` atomically |

### Required integration points

1. **Session resumption**: Run `phase <dir>` instead of manually checking files.
2. **Before addressing annotations**: Run `annotations <file>` to get the marks in context — don't grep manually. It is an INDEX into the content; read the file (or the hunks) before editing, never act on the list alone.
3. **After addressing annotations**: Run `verify-clean <file>` before telling the user changes are done.
4. **After writing spec.md**: Run `lint-spec <spec.md>` and fix any issues before presenting to user.
5. **After writing plan.md**: Run `lint-plan <plan.md> --spec <spec.md>` and fix any issues before presenting to user.
6. **After writing spec + plan**: Run `lint-cross <dir>` to catch cross-document drift (broken appendix links, spec types missing from plan, bad spec section refs).
7. **Phase 6 (finalize)**: If the user wants beads integration, run `export-beads <dir> --run` to create issues with dependencies and parent-child relationships atomically via `bd create --graph`.
8. **After addressing a round**: Run `report <file>`, fill each row's status (addressed / partial / declined / deferred) and how, and commit `feedback/round-N.md` with the revision.

## Session Continuity

All state lives in the `{idea-name}/` directory. If a session ends and resumes later:

1. Run `sculptor.py phase {idea-name}/` to detect current state
2. Read files identified as present
3. Tell the user where you're picking up and confirm before continuing
