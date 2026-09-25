# Sculptor — Validation & Session Continuity

## Validation Tool

`sculptor.py` provides deterministic validation. Use it at the specified points — don't rely on manual grep or visual inspection for these checks.

```
python3 ~/.claude/skills/sculptor/sculptor.py <command> [args]
```

| Command | When to use | What it checks |
|---|---|---|
| `phase <dir>` | Session resumption | Which files exist, which phase we're in, pending annotations |
| `annotations <file>` | Before addressing annotations | Extracts all `>>` lines with line numbers and parsed prefixes |
| `verify-clean <file>` | After addressing annotations | Confirms all `>>` lines were removed (returns PASS/FAIL) |
| `lint-spec <spec.md>` | Before asking user to annotate spec | Dead types, path consistency, TODOs, untagged code blocks |
| `lint-plan <plan.md> --spec <spec.md>` | Before asking user to annotate plan | Missing AC lines, missing sections, spec coverage table validation |
| `lint-cross <dir>` | After writing spec + plan | Appendix link resolution, spec type coverage in plan, cross-reference consistency |
| `export-beads <dir>` | Phase 6 (finalize) | Generates `.beads/beads-graph.jsonl` and `invariants.md`, and copies `glossary.md` (if present) to `.beads/glossary.md`. Add `--run` to execute `bd create --graph` atomically |

### Required integration points

1. **Session resumption**: Run `phase <dir>` instead of manually checking files.
2. **Before addressing annotations**: Run `annotations <file>` to get the full list — don't grep manually.
3. **After addressing annotations**: Run `verify-clean <file>` before telling the user changes are done.
4. **After writing spec.md**: Run `lint-spec <spec.md>` and fix any issues before presenting to user.
5. **After writing plan.md**: Run `lint-plan <plan.md> --spec <spec.md>` and fix any issues before presenting to user.
6. **After writing spec + plan**: Run `lint-cross <dir>` to catch cross-document drift (broken appendix links, spec types missing from plan, bad spec section refs).
7. **Phase 6 (finalize)**: If the user wants beads integration, run `export-beads <dir> --run` to create issues with dependencies and parent-child relationships atomically via `bd create --graph`.

## Session Continuity

All state lives in the `{idea-name}/` directory. If a session ends and resumes later:

1. Run `sculptor.py phase {idea-name}/` to detect current state
2. Read files identified as present
3. Tell the user where you're picking up and confirm before continuing
