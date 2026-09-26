# Sculptor — Git Tracking

Every sculptor session builds an iteration history through commits. Never squash or amend these commits — the iteration history is the point.

1. **Commit after every AI write.** Whenever the skill creates or updates a document (research.md, idea.md, spec.md, plan.md, appendix files), commit the `{idea-name}/` directory with a message like:
   - `my-idea: draft — initial idea document`
   - `my-idea: revision — addressed round 2 annotations`
   - `my-idea: spec — technical spec first draft`
   - The most recent such commit is the **annotation baseline**: `sculptor.py annotations` diffs the user's marks against it.
2. **Commit after the user annotates.** When the user says they're done and before the skill processes annotations, commit with:
   - `my-idea: annotate — round 2 feedback on idea.md`
   - This preserves the raw marks AND sets the diff baseline. `sculptor.py annotations` reads the change since the previous AI-write commit; the resolution then shows up as the following `revision` commit's diff, so nothing needs to persist inside the document.
3. **Commit message format:**
   - Prefix: `<idea-name>`
   - Phase: `research`, `draft`, `annotate`, `revision`, `spec`, `plan`, `finalize`, `feedback`
   - Description: one short clause, no period
4. **Only commit files inside `{idea-name}/`.** Don't stage anything outside the idea directory.
5. **Resolution is a report, not marks left in the file.** Every marker is removed on the `revision` commit (the document stays clean). After addressing a round, commit `feedback/round-N.md` (scaffold it with `sculptor.py report <file>`) alongside the revision so the user can see, per annotation, whether and how it was addressed — `git show` ties each row to the exact change.
