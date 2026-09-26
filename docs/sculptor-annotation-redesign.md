# Sculptor Annotation UX — Redesign Plan

Status: **implemented** — code, docs, and tests landed (117 tests pass).
Date: 2026-09-26.

## 1. Problem

Sculptor's annotation cycle (SKILL.md Phase 4) has the user mark up a markdown file
with `>>` lines in their editor (vim), then `sculptor.py annotations` greps them and
the agent removes them and commits a clean version. It works but is kludgy. Three
concrete failures:

1. **Anchor is positional, not semantic.** `parse_annotations` captured only
   `line` + `text`; the target ("the thing this refers to") was inferred from
   proximity, and every fix below an addressed note shifted all the line numbers.
2. **Feedback lives *inside* the artifact.** Responding meant deleting `>>` lines,
   then proving it with `verify-clean`. Annotation and content shared one mutable file.
3. **Git history conflated feedback with content.** The `annotate` commit interleaved
   review lines with prose; the `revision` commit removed them. `git log idea.md` was
   noisy and the review trail was not separable.

Goals: better UX to (a) annotate, (b) indicate *which* content an annotation targets,
(c) visualize, (d) capture effectively in git.

## 2. Core realization

**Git diff is a better anchor than any in-file convention** — git already computes
exactly what the user touched, in context. This collapses the anchoring problem:

- For a fix the user *knows*: they just **edit the prose**. The agent diffs against the
  last committed baseline (GIT-TRACKING already commits after every AI write) and sees
  the change with surrounding context. The diff **is** the anchor — no marker, no
  removal, no `verify-clean` dance.
- For a phrase the user wants to *discuss* (can't fix themselves): they **wrap it with
  any marker** (`// text //`, `[[text]]`, `>>text<<` — marker-agnostic). `git diff
  --word-diff` shows the inserted marker around untouched words, pinpointing the exact
  target.
- `>>` comment lines still carry structured feedback text; in the diff they simply show
  up as added lines in context.

Resolution needs no explicit "remove annotations" step: the *next* commit's diff shows
what got addressed.

## 3. Decisions

### 3.1 Three annotation granularities (in-file fallback)
A `>>` (or fence) attaches to **the thing directly above it**:

- **Phrase / single line** — `>>` on the next line under the target line.
- **Whole section** — `>>` directly under a heading (targets the section).
- **Multi-line block** — a span fence pair wrapping the block.
- **Inline (intra-line) span** — name the phrase with a leading quote
  (`>> "goes on and on" -> goes forever`) or point at it with column-aligned carets
  (`>>            ^^^^^^^^^ tighten this`).

### 3.2 Span-fence delimiter families (multi-variant, backward compatible)
Open = `>>` + optional space + an opening delimiter **alone** on the line; close =
`>>` + optional space + the matching delimiter + the comment. Families:
`{}`, `[]`, `()`, and symmetric `//`. Spaces after `>>` optional. Spans do not nest.
Unterminated fences are surfaced so `verify-clean` stays honest.

### 3.3 Anchor capture (references become visible, not inferred)
The parser now records, per annotation: `kind` (`line`/`span`), `anchor` (the target
line/first block line quoted), `target` (the inline phrase, if any), `heading`
(enclosing markdown heading), `block`, `end_line`. `sculptor annotations` prints each
note *with what it points at*, so the agent and user see the referent directly.

### 3.4 Dual mode: git primary, in-file fallback
`sculptor annotations <file>`:
- **Auto**: when the file is tracked in a work tree and the baseline ref resolves, run
  the **git word-diff** (primary). Direct edits and marker-wrapped phrases both appear
  in context.
- **Fallback**: otherwise (no git, untracked, ref missing) parse in-file `>>`/fence
  markers as in 3.1–3.3.
- Flags: `--since <ref>` (baseline, default `HEAD`), `--no-git` (force in-file),
  `--diff`/`--git` (force git; error if unavailable).

### 3.5 Marker policy
Marker-agnostic — detection is by *diff*, not by a fixed token. Recommendation for
wrap markers: **put spaces around them** (`// text //`) so word-diff tokenizes cleanly;
`//` collides with URLs and the `//` span fence, so a distinctive pair (`[[ ]]`,
`>> <<`) is safer but optional.

## 4. Options considered and rejected

- **Sidecar-only extract (`feedback/round-N.md`)** — clean file + git-separable trail,
  but the user must read two files to see doc+comments, and it does not solve "which
  content." Superseded by the git-diff anchor.
- **git-appraise** (comments in git-notes, anchored file+line+commit) — closest
  git-native prior art, but code/commit-review shaped, Go dep, dormant. Too heavy for a
  single markdown doc.
- **prr** (download a PR as an editor review file, mark up, submit) — good *authoring*
  model but bound to a real GitHub PR.
- **Real GitHub PR + `gh`** — fully git-based, familiar UI, but needs a remote/network
  and leaves the local/vim flow.
- **Drive plannotator (`annotate --gate --json`)** — richest visualization, reuses an
  existing tool; kept as a possible opt-in (`--ui`), not the default.

Key insight from the PR-review detour: **PR review = anchor store + renderer**. Git
provides the anchor store for free; the "renderer" is `git diff --word-diff` (and
optionally a future `--show` overlay).

## 5. Implemented so far (`sculptor.py`)

- New regexes: `LINE_ANNOTATION_RE`, `OPEN_FENCE_RE`/`CLOSE_FENCE_RE` + `OPEN_TO_CLOSE`
  (`{} [] () //`), `CARET_ANNOTATION_RE`, `INLINE_QUOTE_RE`, `HEADING_RE`.
- `parse_annotations` rewritten: line/span/inline kinds, span fences (all four
  families, optional spacing, non-nesting, unterminated-fence surfacing), inline
  quote + caret (absolute-column slice), anchor + enclosing-heading capture. All
  existing fields preserved → backward compatible.
- Helpers `_preceding_anchor`, `_enclosing_heading`.
- `cmd_annotations` split into `_annotations_git` (word-diff primary) and
  `_annotations_infile` (fallback), with `_run_git` / `_git_annotatable` and flag
  parsing (`--since`, `--no-git`, `--diff`/`--git`).
- Verified: 9 existing annotation tests pass; git-mode + `--no-git` smoke both work
  (direct edit, `// polling //` wrap, and `>>` comment all shown in context).

## 6. Status of next steps

**Done.**
- `sculptor.py`: multi-variant span fences (`{} [] () //`), inline quote/caret, anchor +
  heading capture, git word-diff primary + in-file fallback, baseline auto-pick,
  `--context`/`--json`, and the `report` scaffold command; `main()`/help updated.
- SKILL.md Phase 4 rewritten (mode-transparent), GIT-TRACKING.md and VALIDATION.md updated.
- Tests: parser variants + git-mode selection + report scaffold (117 pass).

**Deferred (optional).**
- `annotations --show` overlay (splice comments inline for reading).
- plannotator `--ui` opt-in bridge.

## 7. Resolved decisions

- **`verify-clean` is retained** as the in-file guard — deterministic and a simple run.
  Under git mode "clean" means no `>>`/fence/inline markers remain in the revised file.
- **Markers are always removed on the revision commit.** They are agent-facing (and a
  convenience for a human re-reading the raw round); the new version of the file must be
  clean. This holds for `>>` lines, span fences, inline quotes/carets, and wrap markers.
- **Baseline auto-picks the AI-authored version.** When the annotate step is itself
  committed (GIT-TRACKING step 2), `annotations` diffs `HEAD~1` (the last AI write)
  rather than requiring `--since`; explicit `--since <ref>` still overrides.
- **The dual mode is an internal implementation detail.** The agent contract is the
  `sculptor.py` commands only; SKILL.md teaches the user how to MARK, never how the tool
  gathers context. `annotations` chooses git vs in-file itself; the flags are edge-case
  escape hatches, not part of the normal flow.
- **`annotations` output is an index, not a substitute for the file.** The agent reads
  the real content (or the hunks) before editing and never acts on the list alone —
  context correctness beats saving a read.

## 8. Recommendation

Ship the dual-mode `annotations` as the default. Lead the user experience with the two
git-native gestures — **edit the prose you can fix, wrap the phrase you want to
discuss** — and keep the `>>`/fence/inline conventions as the offline fallback. Defer
git-appraise/prr/plannotator; the git word-diff already delivers the PR-review feel
(anchor + renderer) with zero new dependencies.

## 9. Resolution visibility — round reports

**Problem.** After the agent addresses N annotations and commits a clean file, the user
cannot tell, per annotation, *whether* it was addressed, *how*, and *what* changed. The
clean file erased the marks; a verbal summary is ephemeral.

**Decision: a per-round resolution report, git-anchored.** The agent writes
`{idea-name}/feedback/round-N.md` and commits it *with* the revision, so the report and
the diff it describes travel together. One row per annotation:

```
# Round N resolutions — baseline <shaA> → <shaB>
1. [addressed]  §7 verify-clean — kept as in-file guard.            (hunk: idea.md L141)
2. [addressed]  wrap markers — always stripped; file stays clean.
3. [declined]   use Redis — out of scope this round; <reason>.
4. [deferred]   pagination — tracked as open item.
```

Status vocabulary: `addressed` / `partial` / `declined` / `deferred` (declined and
deferred carry a reason). Because we now diff, each row can cite the exact commit + hunk,
so "what changed" is precise rather than prose-only. This makes durable and structured
what SKILL.md Phase 4 step 7 already does verbally.

- **Commit message** carries the one-line headline; `git log --oneline` is the skim,
  the report is the per-item table, `git show` is the exact change. No third mechanism.
- **Scales to 10+ annotations** as a checklist the user can tick against their marks.

**Does the decision tree augment it?** Only for the subset of annotations that trigger a
genuine *design* decision (hard to reverse, a real trade-off). Those update
`decision-tree.md` and the report row links to the node. Ordinary corrections stay in
the round report — the decision tree is not the general resolution log.

**Next step:** add report emission to the Phase-4 cycle and, optionally, a
`sculptor.py report --round N` scaffold that pre-fills rows from `annotations` +
`git diff --stat` for the agent to fill in.
