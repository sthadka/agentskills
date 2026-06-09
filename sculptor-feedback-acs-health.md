# Sculptor Skill Feedback — From the ACS Customer Health Session

Based on a full sculptor session (research → idea → 2 annotation rounds → spec → 1 annotation round → plan → finalize → feedback) producing 30 use cases, a 1000-line spec, and a 20-task plan.

---

## 1. lint-plan flags sub-tasks as missing AC — massive false positive noise

**File:** `sculptor.py`, `cmd_lint_plan()`, lines 370-386

**Problem:** The linter treats every `- [ ]` line as a top-level task requiring its own `- AC:` line. Sub-tasks (indented `- [ ]` under a parent task) are covered by the parent's AC, but the linter doesn't distinguish them. This generated **~60 false positive warnings** in our session — more than the real issues.

**Root cause:** The AC check loop at line 376 uses `task_re.match(line)` which matches `^- \[ \] .+`. In practice, plans use two structures:

**Structure A (heading-grouped):** `### Task N: description` with flat `- [ ]` items underneath — all items are sub-tasks of the heading, only the heading-level needs an AC.

**Structure B (indent-grouped):** Top-level `- [ ]` with indented `  - [ ]` sub-tasks — only unindented lines need AC.

Our plan used Structure A. The linter only understands Structure B.

**Fix:** Detect which plan structure is in use:

```python
heading_task_re = re.compile(r'^###\s+Task\s+\d+')
if any(heading_task_re.match(l) for l in lines):
    # Heading-grouped: only heading-level tasks need AC
    for i, line in enumerate(lines):
        if heading_task_re.match(line):
            has_ac = False
            for j in range(i + 1, min(i + 30, len(lines))):
                if heading_task_re.match(lines[j]) or lines[j].startswith('## '):
                    break
                if 'AC:' in lines[j]:
                    has_ac = True
                    break
            if not has_ac:
                issues.append(...)
else:
    # Indent-grouped: current behavior (only unindented tasks need AC)
    ...
```

---

## 2. Spec coverage table matching is too granular

**File:** `sculptor.py`, `cmd_lint_plan()`, lines 404-438

**Problem:** The linter extracts ALL `##`-`######` headings from the spec and checks that each appears in the plan's Spec Coverage table. This means every leaf section — including report template sections like `### Executive Dashboard`, `### Deteriorated (N customers)`, `### High Value ($500K+) — N customers, $NM ARR` — generates a coverage gap warning.

Our session produced **~70 false positives** from report structure subsections alone. These are template sections within the spec, not implementable units.

**Root cause:** Line 407:
```python
spec_sections = [s.strip() for s in re.findall(r'^#{2,6}\s+(.+)$', spec_text, re.MULTILINE)]
```

This extracts every heading at every depth, including headings inside fenced markdown code blocks (spec report templates).

**Fix (recommended: hierarchical + code-block-aware):**

1. Skip headings inside fenced code blocks
2. Only require coverage at h2/h3 level
3. Parent section coverage implicitly covers children

```python
in_code = False
for line in spec_text.splitlines():
    if line.strip().startswith('```'):
        in_code = not in_code
        continue
    if in_code:
        continue
    m = re.match(r'^(#{2,3})\s+(.+)$', line)
    if m:
        spec_sections.append(m.group(2).strip())
```

---

## 3. Spec Coverage task ref matching is broken

**File:** `sculptor.py`, `cmd_lint_plan()`, lines 417-438

**Problem:** The Spec Coverage table maps spec sections to tasks like `Task 2`, `Tasks 15, 16`, `Setup`. The linter tries to match these refs against `make_task_slug()` output, which produces slugs from task descriptions (e.g., `database-session-with-attach`).

The matching at line 434:
```python
if not any(slug.startswith(f'{part}:') or slug == part for slug in all_slugs)
```

So `Task 2` is compared against slugs like `database-session-with-attach` — it never matches. Every row in our Spec Coverage table generated a false positive.

**Fix:** Support multiple ref formats:

```python
def resolve_task_ref(ref: str, plan: dict) -> bool:
    ref = ref.strip()
    # "Task 2" or just "2" → match by sequential task index
    num_match = re.match(r'(?:Tasks?\s+)?(\d+)', ref)
    if num_match:
        idx = int(num_match.group(1))
        return idx <= total_task_count(plan)
    # "Setup" → match by phase name
    if any(ref.lower() in ph['name'].lower() for ph in plan['phases']):
        return True
    # "All tasks" or "All tasks (invariant N)" → always valid
    if ref.lower().startswith('all'):
        return True
    # Description slug match (current behavior)
    return ref in all_slugs
```

Also handle comma-separated refs like `Tasks 15, 16` and ranges like `Tasks 7-11`.

---

## 4. lint-spec type checking is TypeScript-only

**File:** `sculptor.py`, `extract_type_names()`, lines 185-191, and `cmd_lint_spec()`, lines 241-257

**Problem:** Dead type detection only looks for TypeScript patterns (`interface|type|enum|class`). For Python, Go, Rust, or SQL-heavy specs, it catches nothing. Our spec had Python `@dataclass` classes, SQL `CREATE TABLE` statements, and Python function signatures — none were validated.

The untagged code block detection (lines 276-297) also only looks for JS/TS signals (`{`, `=>`, `const`, `function`).

**Fix:** Make language detection broader:

```python
TYPE_PATTERNS = {
    'typescript': r'\b(?:interface|type|enum|class)\s+(\w+)',
    'python': r'\bclass\s+(\w+)',
    'sql': r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)',
    'go': r'\btype\s+(\w+)\s+(?:struct|interface)',
    'rust': r'\b(?:struct|enum|trait)\s+(\w+)',
}

CODE_SIGNALS = {
    'js/ts': ['{', '}', '=>', 'const ', 'function ', 'import '],
    'python': ['def ', 'import ', 'class ', 'from ', '    return'],
    'sql': ['SELECT ', 'CREATE ', 'INSERT ', 'FROM ', 'WHERE '],
    'go': ['func ', 'package ', 'type ', 'import ('],
    'shell': ['#!/', '$ ', ' | ', '&&'],
}
```

---

## 5. No guidance for user input via chat (not annotations)

**File:** `SKILL.md`, Phase 4 (ANNOTATE)

**Problem:** The user frequently injected new requirements as chat messages rather than `>>` annotations:
- "Another use case: Jira hygiene and linking..."
- "What other industry standard use cases can we build?"
- Finance tier data pasted as raw text in chat
- "Use case: As we plan to create features, is there a way to fuzzy match..."

The skill has no guidance for this pattern. The agent has to improvise whether to incorporate immediately, queue for annotation, or redirect.

**Fix:** Add to SKILL.md Phase 4:

```markdown
### User input via chat (not file annotations)

Users often provide new requirements, data, or corrections in chat rather than
as `>>` annotations. Handle these based on type:

1. **New requirements or use cases** — Incorporate directly into the current
   artifact. Don't ask the user to re-enter as annotations.
2. **Reference data** (tables, examples, API responses) — Add to research.md
   or create a new appendix. Reference from the relevant artifact.
3. **Corrections** — Apply immediately. Equivalent to `>>` annotations.
4. **Scope changes** — If the input changes the problem definition, treat as
   a late-stage change (see "Handling Late-Stage Changes").

The key principle: never ask the user to repeat themselves in a different format.
Chat input is as valid as file annotations.
```

---

## 6. Research template should encourage data validation

**File:** `RESEARCH-TEMPLATE.md` and `SKILL.md`

**Problem:** The most impactful moment in our session was a user spot-check that invalidated research. We concluded "EBS fields are empty across all 87K CIPOE issues" — true for the specific fields we checked, but wrong because we missed the array variant. A single query against the right field would have caught it.

The research template has no mention of validating claims against real data.

**Fix:** Add to `RESEARCH-TEMPLATE.md` after the `## Sources` section:

```markdown
## Validation

When research makes claims about data (field availability, coverage percentages,
format assumptions), validate with queries against real data:

- "Field X is empty" → `SELECT COUNT(*) WHERE field_x IS NOT NULL AND field_x != ''`
- "System A links to System B via field Y" → Run the actual join, check match rate
- "There are N records matching criteria" → `COUNT(*)` with the actual `WHERE` clause
- "Field X contains values of format Y" → `SELECT field_x LIMIT 10` to check actual format

Document the queries and results. A conclusion built on unvalidated assumptions
is a liability — it costs more to debug later than to verify now.
```

Add to `SKILL.md` Phase 2 (RESEARCH), after "Resolve Key Architectural Decisions":

```markdown
### Validate data claims

When the idea involves existing databases, APIs, or data sources, run queries
to validate assumptions before writing conclusions. Don't assume — verify.
Check edge cases: is the field populated? Is it the right field (check for
name collisions or alternate fields)? Does the join actually produce results?
A single spot-check that contradicts an assumption saves hours of downstream work.
```

---

## 7. verify-clean should show remaining annotations on failure

**File:** `sculptor.py`, `cmd_verify_clean()`, lines 98-116

**Problem:** When verify-clean fails, it outputs only `FAIL — N unaddressed annotation(s) in file.md` without showing which annotations remain. The agent then needs to run `annotations` again.

**Fix:** Show the remaining annotations inline:
```python
if annotations:
    print(f'FAIL — {len(annotations)} unaddressed annotation(s) in {filepath.name}:\n')
    for a in annotations:
        print(f'  L{a["line"]}: {a["text"][:80]}')
    return 1
```

This saves one tool call per verify-clean failure.

---

## 8. Annotation command should show prefix summary

**File:** `sculptor.py`, `cmd_annotations()`, lines 65-95

**Problem:** The `annotations` command outputs a flat list. With many annotations, a summary would help the agent prioritize — `*` (strong opinion) should be addressed first; `?` (question) needs an answer, not just incorporation.

**Fix:** Add a summary before the list (~5 lines):

```python
from collections import Counter
prefix_counts = Counter(a['prefix'] for a in annotations)
parts = []
for prefix, label in prefix_labels.items():
    count = prefix_counts.get(prefix, 0)
    if count > 0:
        parts.append(f"{count} {label}{'s' if count > 1 else ''}")
if parts:
    print(f'  Summary: {", ".join(parts)}\n')
```

---

## 9. No lint-idea or lint-research commands

**File:** `sculptor.py`

**Problem:** `lint-spec` and `lint-plan` exist, but idea.md and research.md go through multiple rounds without any validation beyond `verify-clean`.

**Catchable issues:**

**`lint-idea`:**
- Open Questions with strikethrough (`~~`) should have a resolution note nearby
- Appendix links resolve to existing files
- Template section completeness (Problem, Core Concept, What This Is NOT)
- TODO/FIXME markers

**`lint-research`:**
- TODO/FIXME markers
- Appendix links resolve
- Sources section exists and is non-empty

**Effort:** ~50 lines each, following the `cmd_lint_spec` pattern.

---

## 10. Spec template is web/API-centric

**File:** `SPEC-TEMPLATE.md`

**Problem:** The template's "API Surface" section describes endpoint paths and request/response shapes — web-specific. CLI tools need "Command Tree" which is listed as a conditional section, but CLI tools are as common as web apps.

**Fix:** Generalize the section name:

```markdown
## Interface Surface
[For web APIs: endpoint paths, request/response shapes, error formats.
For CLI tools: command tree, flag conventions, output formats, exit codes.
For libraries: public API, function signatures, return types.
Include code snippets for non-obvious logic — edge cases, parsing, retries.]
```

---

## 11. No guidance on managing large specs

**File:** `SKILL.md`, Phase 5

**Problem:** Our spec grew to 1000+ lines. `SPEC-TEMPLATE.md` has "When to Split" guidance, but `SKILL.md` doesn't reference it or prompt the agent to check. The agent just keeps writing.

**Fix:** Add to SKILL.md Phase 5, after writing the spec:

```markdown
### Check spec length

After writing the spec, check its line count. If >500 lines, consider splitting:
- Move sample data and code examples to `spec-examples.md`
- Move report structures or template definitions to an appendix
- Keep the core spec focused on architecture, data model, and interface surface

Ask: "The spec is at N lines. Want me to split [specific section] into a
separate file to keep the core spec focused?"
```

---

## 12. export-beads doesn't carry use case mapping

**File:** `sculptor.py`, `generate_beads_plan()` and `cmd_export_beads()`

**Problem:** Our spec had a "Use Case Coverage Verification" table mapping 30 UCs to CLI commands. This mapping isn't carried into the beads export. An implementing agent can't verify UC coverage.

**Fix:** If the spec or plan has a UC coverage table, extract and append to `.beads/invariants.md`:

```markdown
## Use Case Coverage
Verify each use case is exercised end-to-end:
| UC-1 | ARR-weighted health ranking | `report portfolio` |
...
```

---

## 13. Phase detection could be more precise

**File:** `sculptor.py`, `cmd_phase()`

**Problem:** Phase detection only checks file existence. It could also check:
- Git modification state (files changed since last commit)
- Annotation presence (already partially done — shows count, but doesn't influence phase assessment)

When we resumed, it showed `Phase 3: DRAFT` but didn't highlight that research.md had been externally modified, which was the actual work state.

**Fix:** Check `git diff --name-only` for the idea directory and show modification state:
```
Phase 3: DRAFT
  research.md ✓ (modified since last commit)
  idea.md ✓
```

---

## 14. Feedback template should capture invalidated assumptions

**File:** `FEEDBACK-TEMPLATE.md`

**Problem:** The template doesn't guide toward the most valuable feedback type: what assumptions were wrong and how they were caught.

Our key learning — "initial research was wrong about EBS field emptiness, caught by a user spot-check" — is the kind of insight that prevents future sessions from repeating the same class of error.

**Fix:** Add a section:

```markdown
## Assumptions Invalidated
[Claims from earlier phases that turned out to be wrong. For each: what was
claimed, what turned out to be true, and how it was discovered. These reveal
blind spots in the research process and are the highest-value learnings.]
```

---

## Priority Summary

| # | Issue | Impact | Effort | Files |
|---|-------|--------|--------|-------|
| 1 | lint-plan sub-task AC noise | **High** — makes output unusable | Medium | `sculptor.py` |
| 2 | Spec coverage too granular | **High** — makes output unusable | Medium | `sculptor.py` |
| 3 | Task ref matching broken | **High** — always fails | Medium | `sculptor.py` |
| 4 | Type checking TS-only | Medium | Medium | `sculptor.py` |
| 5 | No chat input guidance | Medium | Low | `SKILL.md` |
| 6 | Research lacks validation guidance | Medium | Low | `RESEARCH-TEMPLATE.md`, `SKILL.md` |
| 7 | verify-clean silent on details | Medium | Low | `sculptor.py` |
| 8 | Annotation prefix summary | Low | Low | `sculptor.py` |
| 9 | No lint-idea/lint-research | Low | Medium | `sculptor.py` |
| 10 | Spec template web-centric | Low | Low | `SPEC-TEMPLATE.md` |
| 11 | No large-spec guidance | Low | Low | `SKILL.md` |
| 12 | Beads export lacks UC mapping | Low | Medium | `sculptor.py` |
| 13 | Phase detection precision | Low | Medium | `sculptor.py` |
| 14 | Feedback template gaps | Low | Low | `FEEDBACK-TEMPLATE.md` |

**Fix first:** Issues 1, 2, and 3. They make `lint-plan` produce so much noise (~130 false positives in our session vs ~10 real issues) that the output is ignored entirely. Fixing these three would make the linter trustworthy.
