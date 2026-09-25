# Sculptor — Git Tracking

Every sculptor session builds an iteration history through commits. Never squash or amend these commits — the iteration history is the point.

1. **Commit after every AI write.** Whenever the skill creates or updates a document (research.md, idea.md, spec.md, plan.md, appendix files), commit the `{idea-name}/` directory with a message like:
   - `my-idea: draft — initial idea document`
   - `my-idea: revision — addressed round 2 annotations`
   - `my-idea: spec — technical spec first draft`
2. **Commit after user annotates.** When the user says they're done annotating and before the skill processes annotations, commit with:
   - `my-idea: annotate — round 2 feedback on idea.md`
   - This preserves the raw annotations before they get removed.
3. **Commit message format:**
   - Prefix: `<idea-name>`
   - Phase: `research`, `draft`, `annotate`, `revision`, `spec`, `plan`, `finalize`, `feedback`
   - Description: one short clause, no period
4. **Only commit files inside `{idea-name}/`.** Don't stage anything outside the idea directory.
