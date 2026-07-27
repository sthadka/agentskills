# TreeFlow — Sculptor Import

This module covers importing sculptor session artifacts into TreeFlow beads. For core rules and entry protocol, see [SKILL.md](SKILL.md).

## Sculptor Import

If the input is a sculptor session directory (contains `plan.md`, `spec.md`, `idea.md`), follow [SCULPTOR-IMPORT.md](SCULPTOR-IMPORT.md) for conversion.

**Sculptor import checklist** (lessons from large imports):
1. Read sculptor artifacts in parallel: `plan.md`, `spec.md`, `idea.md` in a single tool-call batch
2. Pre-sanitize the plan file: strip or convert `### Task N:` group headers to bold text — they corrupt `bd create -f` parsing
3. Run `validate-plan` and note the reported issue count
4. Run `bd create -f` and compare its created count against validate-plan's count — if they differ, stop and investigate
5. Run `tf.py dedup --dry-run` to check for duplicates before proceeding
6. Wire dependencies using `tf.py wire-plan` (preferred) — resolves inline `### Dependencies` sections and phase ordering in one command. Alternative: `tf.py import-deps` with a separate deps.txt file
7. Run `tf.py ready` to verify the ready set matches expectations
8. Always use `bd list --limit 500 --json` when calling `bd` directly

After import, proceed to [Entry Protocol](SKILL.md#entry-protocol) for initialization and dispatch.
