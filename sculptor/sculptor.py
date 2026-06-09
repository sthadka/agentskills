#!/usr/bin/env python3
"""Sculptor validation tool — deterministic checks for sculptor sessions."""

import json
import re
import subprocess
import sys
from pathlib import Path

ANNOTATION_RE = re.compile(r'^[ \t]*(>>)\s*(.*)', re.MULTILINE)
ANNOTATION_PREFIX_RE = re.compile(r'^([?+\-*])?\s*(.*)')

PHASE_FILES = {
    'research.md': 2,
    'idea.md': 3,
    'spec.md': 5,
    'plan.md': 5,
    'feedback.md': 7,
}

PHASE_NAMES = {
    1: 'INTAKE',
    2: 'RESEARCH',
    3: 'DRAFT',
    4: 'ANNOTATE',
    5: 'SPECS & PLAN',
    6: 'FINALIZE',
    7: 'FEEDBACK',
}

CODE_BLOCK_RE = re.compile(r'^```(\w*)\s*$', re.MULTILINE)


def parse_annotations(filepath: Path) -> list[dict]:
    text = filepath.read_text()
    lines = text.splitlines()
    annotations = []

    in_code_block = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        match = re.match(r'^[ \t]*(>>)\s*(.*)', line)
        if match:
            rest = match.group(2)
            prefix_match = ANNOTATION_PREFIX_RE.match(rest)
            prefix = prefix_match.group(1) if prefix_match and prefix_match.group(1) else ''
            text = prefix_match.group(2) if prefix_match else rest

            annotations.append({
                'line': i,
                'prefix': prefix,
                'text': text,
                'raw': line,
            })

    return annotations


def cmd_annotations(args: list[str]) -> int:
    if not args:
        print('Usage: sculptor annotations <file.md>')
        return 1

    filepath = Path(args[0])
    if not filepath.exists():
        print(f'File not found: {filepath}')
        return 1

    annotations = parse_annotations(filepath)
    if not annotations:
        print(f'No annotations found in {filepath.name}')
        return 0

    print(f'{len(annotations)} annotation(s) in {filepath.name}:\n')

    prefix_labels = {
        '': 'correction/statement',
        '?': 'question',
        '+': 'addition',
        '-': 'remove',
        '*': 'strong opinion',
    }

    for a in annotations:
        prefix_str = f' [{prefix_labels.get(a["prefix"], a["prefix"])}]' if a['prefix'] else ''
        text_preview = a['text'][:80] + ('...' if len(a['text']) > 80 else '')
        print(f'  L{a["line"]}{prefix_str}: {text_preview}')

    return 0


def cmd_verify_clean(args: list[str]) -> int:
    if not args:
        print('Usage: sculptor verify-clean <file.md>')
        return 1

    filepath = Path(args[0])
    if not filepath.exists():
        print(f'File not found: {filepath}')
        return 1

    annotations = parse_annotations(filepath)
    if not annotations:
        print(f'PASS — 0 annotations remaining in {filepath.name}')
        return 0

    print(f'FAIL — {len(annotations)} unaddressed annotation(s) in {filepath.name}:\n')
    for a in annotations:
        print(f'  L{a["line"]}: {a["raw"].strip()}')
    return 1


def cmd_phase(args: list[str]) -> int:
    if not args:
        print('Usage: sculptor phase <idea-dir>')
        return 1

    idea_dir = Path(args[0])
    if not idea_dir.is_dir():
        print(f'Directory not found: {idea_dir}')
        return 1

    existing = {}
    for filename, phase in PHASE_FILES.items():
        fpath = idea_dir / filename
        if fpath.exists():
            existing[filename] = phase

    if not existing:
        current_phase = 1
    else:
        current_phase = max(existing.values())

    print(f'Phase {current_phase}: {PHASE_NAMES.get(current_phase, "UNKNOWN")}')
    print()

    for filename in PHASE_FILES:
        fpath = idea_dir / filename
        if fpath.exists():
            anns = parse_annotations(fpath)
            ann_str = f'  ({len(anns)} pending annotations)' if anns else ''
            print(f'  {filename} ✓{ann_str}')
        else:
            print(f'  {filename} ✗')

    pending_files = [
        f for f in PHASE_FILES
        if (idea_dir / f).exists() and parse_annotations(idea_dir / f)
    ]
    if pending_files:
        print(f'\n⚠ Pending annotations in: {", ".join(pending_files)}')

    return 0


def extract_code_blocks(text: str, lang: str | None = None) -> list[str]:
    blocks = []
    lines = text.splitlines()
    in_block = False
    block_lang = ''
    current = []

    for line in lines:
        if line.strip().startswith('```') and not in_block:
            in_block = True
            block_lang = line.strip().removeprefix('```').strip()
            current = []
        elif line.strip() == '```' and in_block:
            if lang is None or block_lang == lang:
                blocks.append('\n'.join(current))
            in_block = False
            current = []
        elif in_block:
            current.append(line)

    return blocks


def extract_type_names(code: str) -> set[str]:
    names = set()
    for match in re.finditer(r'\b(?:interface|type|enum|class)\s+(\w+)', code):
        name = match.group(1)
        if re.match(r'^[A-Z]', name):  # PascalCase only — filters out lowercase noise
            names.add(name)
    return names


def extract_directory_tree_paths(text: str) -> set[str]:
    paths = set()
    tree_blocks = extract_code_blocks(text, '')
    for block in tree_blocks:
        if '├' in block or '└' in block or '│' in block:
            for line in block.splitlines():
                cleaned = re.sub(r'[├└│─\s]+', '', line)
                cleaned = cleaned.split('#')[0].strip()
                if cleaned and '.' in cleaned:
                    paths.add(cleaned)
    return paths


def extract_manifest_paths(text: str) -> set[str]:
    paths = set()
    json_blocks = extract_code_blocks(text, 'json')
    for block in json_blocks:
        if '"manifest_version"' in block:
            for match in re.finditer(r'"([^"]*\.\w+)"', block):
                val = match.group(1)
                if '/' in val or val.endswith(('.js', '.html', '.png', '.json', '.css')):
                    paths.add(val)
    return paths


def cmd_lint_spec(args: list[str]) -> int:
    if not args:
        print('Usage: sculptor lint-spec <spec.md>')
        return 1

    filepath = Path(args[0])
    if not filepath.exists():
        print(f'File not found: {filepath}')
        return 1

    text = filepath.read_text()
    issues = []

    # 1. Check for TODO/FIXME/HACK
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith('```'):
            continue
        for marker in ('TODO', 'FIXME', 'HACK', 'XXX'):
            if marker in line.upper() and not line.strip().startswith('```'):
                issues.append(f'L{i}: {marker} marker found: {line.strip()[:80]}')

    # 2. Dead types — defined in code blocks but never referenced elsewhere
    ts_blocks = extract_code_blocks(text, 'typescript') + extract_code_blocks(text, 'ts')
    all_code = '\n'.join(ts_blocks)
    defined_types = extract_type_names(all_code)

    prose_and_other_code = text
    for block in ts_blocks:
        prose_and_other_code = prose_and_other_code.replace(block, '', 1)

    for tname in defined_types:
        definition_pattern = re.compile(
            rf'\b(?:interface|type|enum|class)\s+{re.escape(tname)}\b'
        )
        all_occurrences = len(re.findall(rf'\b{re.escape(tname)}\b', text))
        definition_count = len(definition_pattern.findall(text))
        if all_occurrences <= definition_count:
            issues.append(f'Dead type: `{tname}` is defined but never referenced elsewhere')

    # 3. Path consistency — directory tree vs manifest
    #    Skip paths under build output dirs (dist/, build/, out/) — those are generated
    BUILD_DIRS = ('dist/', 'build/', 'out/', '.next/', 'target/')
    tree_paths = extract_directory_tree_paths(text)
    manifest_paths = extract_manifest_paths(text)

    if tree_paths and manifest_paths:
        for mpath in manifest_paths:
            if any(mpath.startswith(d) for d in BUILD_DIRS):
                continue
            basename = mpath.split('/')[-1]
            if not any(basename in tp for tp in tree_paths):
                if not mpath.startswith('http'):
                    issues.append(
                        f'Manifest references `{mpath}` but no matching file in directory tree'
                    )

    # 4. Code blocks without language tags
    #    Heuristic: look for lines with multiple code-like characters (not just ASCII art)
    all_lines = text.splitlines()
    in_block = False
    for i, line in enumerate(all_lines):
        stripped = line.strip()
        if stripped.startswith('```') and not in_block:
            if stripped == '```':
                look_ahead = all_lines[i + 1:i + 4]
                code_signals = sum(
                    1 for l in look_ahead
                    for c in ('{', '}', '=>', '()', ';', 'const ', 'function ', 'import ')
                    if c in l
                )
                if code_signals >= 2:
                    issues.append(
                        f'L{i + 1}: Code block without language tag '
                        f'— add ```typescript, ```json, etc.'
                    )
            in_block = True
        elif stripped == '```' and in_block:
            in_block = False

    # 5. Remaining annotations
    annotations = parse_annotations(filepath)
    if annotations:
        issues.append(f'{len(annotations)} unaddressed annotation(s) remaining')

    if not issues:
        print(f'PASS — {filepath.name} looks clean')
        return 0

    print(f'{len(issues)} issue(s) in {filepath.name}:\n')
    for issue in issues:
        print(f'  ✗ {issue}')
    return 1


def parse_spec_coverage_table(text: str) -> list[dict]:
    """Parse ## Spec Coverage markdown table into list of {spec_section, task_ref}."""
    rows: list[dict] = []
    in_section = False
    header_seen = False

    for line in text.splitlines():
        if line.strip().startswith('## Spec Coverage'):
            in_section = True
            header_seen = False
            continue
        if in_section and line.startswith('## '):
            break
        if not in_section:
            continue

        stripped = line.strip()
        if not stripped or not stripped.startswith('|'):
            if header_seen and not stripped:
                continue
            if not stripped.startswith('|'):
                continue
        if re.match(r'^\|[\s\-:|]+\|$', stripped):
            header_seen = True
            continue
        if not header_seen and '|' in stripped:
            header_seen = False
            continue

        cells = [c.strip() for c in stripped.strip('|').split('|')]
        if len(cells) >= 2 and cells[0] and cells[1]:
            rows.append({'spec_section': cells[0], 'task_ref': cells[1]})

    return rows


def cmd_lint_plan(args: list[str]) -> int:
    if not args:
        print('Usage: sculptor lint-plan <plan.md> [--spec <spec.md>]')
        return 1

    filepath = Path(args[0])
    if not filepath.exists():
        print(f'File not found: {filepath}')
        return 1

    spec_path = None
    if '--spec' in args:
        idx = args.index('--spec')
        if idx + 1 < len(args):
            spec_path = Path(args[idx + 1])

    text = filepath.read_text()
    lines = text.splitlines()
    issues = []

    # 1. Every top-level task should have an AC line
    task_re = re.compile(r'^- \[ \] .+')
    ac_re = re.compile(r'^\s+- AC:')
    subtask_re = re.compile(r'^\s+- \[ \] .+')

    for i, line in enumerate(lines):
        if task_re.match(line):
            has_ac = False
            for j in range(i + 1, min(i + 20, len(lines))):
                if task_re.match(lines[j]):
                    break
                if lines[j].strip().startswith('- AC:') or lines[j].strip().startswith('AC:'):
                    has_ac = True
                    break
            if not has_ac:
                task_desc = line.strip()[:70]
                issues.append(f'L{i + 1}: Task missing AC: {task_desc}')

    # 2. Check for Cross-worker Invariants section
    if '## Cross-worker Invariants' not in text and '## Cross-Worker Invariants' not in text:
        issues.append('Missing `## Cross-worker Invariants` section')

    # 3. Check for Dependencies section
    if '## Dependencies' not in text:
        issues.append('Missing `## Dependencies` section')

    # 4. Check for Setup phase
    if '## Setup' not in text:
        issues.append('Missing `## Setup` phase')

    # 5. Check for Risks section
    if '## Risks' not in text:
        issues.append('Missing `## Risks` section')

    # 6. Spec coverage validation
    if spec_path and spec_path.exists():
        spec_text = spec_path.read_text()
        spec_sections = [s.strip() for s in re.findall(r'^#{2,6}\s+(.+)$', spec_text, re.MULTILINE)]

        coverage_table = parse_spec_coverage_table(text)
        if coverage_table:
            # Substring match: "Architecture" matches "§Architecture — Package Layout"
            for section in spec_sections:
                covered = any(section in row['spec_section'] for row in coverage_table)
                if not covered:
                    issues.append(f'Spec Coverage: section `{section}` not covered in table')

            plan_data = parse_plan(text)
            # Include both top-level tasks and subtasks for ref matching
            all_slugs = {
                make_task_slug(t['description'])
                for ph in plan_data['phases'] for t in ph['tasks']
            } | {
                make_task_slug(st['description'])
                for ph in plan_data['phases'] for t in ph['tasks']
                for st in t.get('subtasks', [])
            }
            _range_re = re.compile(r'[–—]\d')
            for row in coverage_table:
                for part in [p.strip() for p in row['task_ref'].split(',')]:
                    if _range_re.search(part):  # skip ranges like "2.1–2.12"
                        continue
                    if not re.match(r'^[A-Za-z0-9.]+$', part):  # skip phrases
                        continue
                    if not any(slug.startswith(f'{part}:') or slug == part
                               for slug in all_slugs):
                        issues.append(
                            f'Spec Coverage: task ref `{part}` does not match any plan task'
                        )
        else:
            plan_mentions_spec = (
                'spec.md' in text.lower() or 'Spec:' in text or 'spec §' in text
            )
            if spec_sections and not plan_mentions_spec:
                issues.append(
                    'Plan does not reference spec.md — consider adding '
                    'a `## Spec Coverage` table or `Spec: spec.md §N` citations'
                )

    # 7. Remaining annotations
    annotations = parse_annotations(filepath)
    if annotations:
        issues.append(f'{len(annotations)} unaddressed annotation(s) remaining')

    if not issues:
        print(f'PASS — {filepath.name} looks clean')
        return 0

    print(f'{len(issues)} issue(s) in {filepath.name}:\n')
    for issue in issues:
        print(f'  ✗ {issue}')
    return 1


def cmd_lint_cross(args: list[str]) -> int:
    if not args:
        print('Usage: sculptor lint-cross <idea-dir>')
        return 1

    idea_dir = Path(args[0])
    if not idea_dir.is_dir():
        print(f'Directory not found: {idea_dir}')
        return 1

    issues = []

    # Collect all markdown files
    md_files = list(idea_dir.glob('*.md'))

    # 1. Appendix link resolution
    appendix_link_re = re.compile(r'\[([^\]]*)\]\((appendix-[^)]+\.md)\)')
    for md_file in md_files:
        text = md_file.read_text()
        for match in appendix_link_re.finditer(text):
            target = match.group(2)
            target_path = idea_dir / target
            if not target_path.exists():
                issues.append(
                    f'{md_file.name}: broken appendix link `{target}`'
                )

    # 2. Spec type coverage in plan
    spec_path = idea_dir / 'spec.md'
    plan_path = idea_dir / 'plan.md'

    if spec_path.exists() and plan_path.exists():
        spec_text = spec_path.read_text()
        plan_text = plan_path.read_text()

        # Skip type coverage when plan has an explicit Spec Coverage table —
        # section-level coverage already accounts for all types within those sections.
        if '## Spec Coverage' not in plan_text:
            spec_types = extract_type_names(
                '\n'.join(extract_code_blocks(spec_text))
            )
            for tname in sorted(spec_types):
                if tname not in plan_text:
                    issues.append(
                        f'Spec type `{tname}` not referenced in plan.md'
                    )

        # 3. Cross-reference consistency — Spec: spec.md §X citations
        spec_sections = [
            s.strip() for s in re.findall(r'^#{2,6}\s+(.+)$', spec_text, re.MULTILINE)
        ]
        spec_refs = re.findall(r'[Ss]pec(?::\s*spec\.md)?\s*§\s*(.+?)(?:\s*[—\-–]|$)', plan_text)
        for ref in spec_refs:
            ref_clean = ref.strip()
            if not any(ref_clean in s or s in ref_clean for s in spec_sections):
                issues.append(
                    f'Plan references `Spec §{ref_clean}` but no matching '
                    f'section in spec.md'
                )

    if not issues:
        print(f'PASS — cross-document checks clean in {idea_dir.name}/')
        return 0

    print(f'{len(issues)} cross-document issue(s) in {idea_dir.name}/:\n')
    for issue in issues:
        print(f'  ✗ {issue}')
    return 1


## ── Plan parser (shared by lint-plan and export-beads) ──────────────────────


TASK_RE = re.compile(r'^- \[ \] (.+)')
SUBTASK_RE = re.compile(r'^  - \[ \] (.+)')
AC_RE = re.compile(r'^\s+- AC:\s*(.*)')
PHASE_RE = re.compile(r'^## (Phase \d+:\s*.+?|Setup)(\s+\[.*\])?$')


def parse_plan(text: str) -> dict:
    """Parse plan.md into structured phases, tasks, sub-tasks."""
    lines = text.splitlines()

    # Extract title
    title = ''
    for line in lines:
        m = re.match(r'^# (?:Implementation Plan:\s*)?(.*)', line)
        if m:
            title = m.group(1).strip()
            break

    phases: list[dict] = []
    current_phase: dict | None = None
    current_task: dict | None = None
    current_subtask: dict | None = None

    # Track non-phase sections
    in_invariants = False
    in_deps = False
    in_risks = False
    invariants_lines: list[str] = []
    deps_lines: list[str] = []
    risks_lines: list[str] = []

    for line in lines:
        # Detect section boundaries
        if line.startswith('## Cross-worker Invariants') or line.startswith('## Cross-Worker Invariants'):
            in_invariants = True
            in_deps = in_risks = False
            current_phase = None
            continue
        if line.startswith('## Dependencies'):
            in_deps = True
            in_invariants = in_risks = False
            current_phase = None
            continue
        if line.startswith('## Risks'):
            in_risks = True
            in_invariants = in_deps = False
            current_phase = None
            continue

        if in_invariants:
            if line.startswith('## ') and not line.startswith('## Cross'):
                in_invariants = False
            else:
                invariants_lines.append(line)
                continue
        if in_deps:
            if line.startswith('## '):
                in_deps = False
            else:
                deps_lines.append(line)
                continue
        if in_risks:
            if line.startswith('## '):
                in_risks = False
            else:
                risks_lines.append(line)
                continue

        # Phase headings
        phase_match = PHASE_RE.match(line)
        if phase_match:
            markers_str = phase_match.group(2) or ''
            is_parallel = '[parallel]' in markers_str.lower()
            is_setup = phase_match.group(1).strip() == 'Setup'
            phase_name = phase_match.group(1).strip()

            current_phase = {
                'name': phase_name,
                'is_setup': is_setup,
                'is_parallel': is_parallel,
                'tasks': [],
            }
            phases.append(current_phase)
            current_task = None
            current_subtask = None
            continue

        if current_phase is None:
            continue

        # Top-level tasks
        task_match = TASK_RE.match(line)
        if task_match:
            desc = task_match.group(1).strip()
            is_tdd = '[TDD]' in desc or '[tdd]' in desc
            current_task = {
                'description': desc,
                'ac': [],
                'subtasks': [],
                'is_tdd': is_tdd,
                'extra_lines': [],
                'body_lines': [],
            }
            current_phase['tasks'].append(current_task)
            current_subtask = None
            continue

        # Sub-tasks
        subtask_match = SUBTASK_RE.match(line)
        if subtask_match and current_task is not None:
            desc = subtask_match.group(1).strip()
            current_subtask = {
                'description': desc,
                'ac': [],
                'extra_lines': [],
                'body_lines': [],
            }
            current_task['subtasks'].append(current_subtask)
            continue

        # AC lines
        ac_match = AC_RE.match(line)
        if ac_match:
            ac_text = ac_match.group(1).strip()
            if current_subtask is not None:
                current_subtask['ac'].append(ac_text)
            elif current_task is not None:
                current_task['ac'].append(ac_text)
            continue

        # Extra description lines (indented bullet points under tasks/subtasks)
        if current_task is not None and line.startswith('    ') and line.strip():
            stripped = line.strip()
            if stripped.startswith('- ') and not stripped.startswith('- [ ]'):
                if current_subtask is not None:
                    current_subtask['extra_lines'].append(stripped)
                else:
                    current_task['extra_lines'].append(stripped)
                continue

        # Body text: any other non-blank line under the current task/subtask
        if current_task is not None and line.strip() and not line.startswith('## '):
            target = current_subtask if current_subtask else current_task
            target['body_lines'].append(line.rstrip())

    # Scan body_lines for "TDD recommended" (not just [TDD] in title)
    for phase in phases:
        for task in phase['tasks']:
            if not task['is_tdd'] and any('tdd recommended' in bl.lower() for bl in task.get('body_lines', [])):
                task['is_tdd'] = True

    return {
        'title': title,
        'phases': phases,
        'invariants': '\n'.join(invariants_lines).strip(),
        'dependencies': '\n'.join(deps_lines).strip(),
        'risks': '\n'.join(risks_lines).strip(),
    }


def make_task_slug(desc: str) -> str:
    """Readable slug from a task description, used as title in both plan.md and deps.txt."""
    cleaned = re.sub(r'\*\*`?|`?\*\*|`', '', desc)
    cleaned = re.sub(r'^src/\S+\s*—?\s*', '', cleaned)
    cleaned = cleaned.replace('"', "'")
    cleaned = cleaned.strip()
    return cleaned if cleaned else desc.strip()[:80]


def read_idea_description(idea_dir: Path) -> str:
    """Extract problem + solution from idea.md for the epic description.
    Falls back to first paragraph of spec.md if idea.md is missing."""
    idea_path = idea_dir / 'idea.md'
    if idea_path.exists():
        text = idea_path.read_text()
        sections: list[str] = []
        capture = False

        for line in text.splitlines():
            if line.startswith('## Problem') or line.startswith('## Solution'):
                capture = True
                continue
            if line.startswith('## ') and capture:
                capture = False
                continue
            if capture:
                sections.append(line)

        result = '\n'.join(sections).strip()
        if result:
            return result

    spec_path = idea_dir / 'spec.md'
    if spec_path.exists():
        lines = spec_path.read_text().splitlines()
        para: list[str] = []
        started = False
        for line in lines:
            if not started and line.startswith('# '):
                continue
            if not started and not line.strip():
                continue
            if not started and line.strip():
                started = True
            if started:
                if not line.strip() and para:
                    break
                if line.strip():
                    para.append(line)
        result = '\n'.join(para).strip()
        if result:
            return result

    return ''


def generate_beads_plan(plan: dict, epic_description: str) -> str:
    """Generate .beads/plan.md in bd create -f format."""
    out: list[str] = []

    # Epic
    out.append(f'## Goal: {plan["title"]}')
    out.append('')
    out.append('### Type')
    out.append('epic')
    out.append('')
    out.append('### Priority')
    out.append('0')
    out.append('')
    if epic_description:
        out.append('### Description')
        out.append(epic_description)
        out.append('')
    if plan['risks']:
        out.append('### Design')
        out.append('**Risks:**')
        out.append(plan['risks'])
        out.append('')

    # Tasks by phase
    task_index = 0
    for phase in plan['phases']:
        for task in phase['tasks']:
            task_index += 1
            task_title = make_task_slug(task['description'])

            out.append(f'## {task_title}')
            out.append('')
            out.append('### Type')
            out.append('task')
            out.append('')
            out.append('### Priority')
            out.append('1' if phase['is_setup'] else '2')
            out.append('')

            # Description: task desc + body + sub-tasks + extra lines
            out.append('### Description')
            out.append(task['description'])
            if task.get('body_lines'):
                out.append('')
                for bl in task['body_lines']:
                    out.append(bl)
            if task['extra_lines']:
                out.append('')
                for el in task['extra_lines']:
                    out.append(el)
            if task['subtasks']:
                out.append('')
                out.append('Sub-tasks:')
                for st in task['subtasks']:
                    out.append(f'- {st["description"]}')
                    for el in st.get('extra_lines', []):
                        out.append(f'  {el}')
                    for bl in st.get('body_lines', []):
                        out.append(f'  {bl.strip()}')
            out.append('')

            # Acceptance criteria
            ac_lines = list(task['ac'])
            for st in task['subtasks']:
                ac_lines.extend(st['ac'])
            if ac_lines:
                out.append('### Acceptance Criteria')
                for ac in ac_lines:
                    out.append(f'- {ac}')
                out.append('')

            # Labels
            labels = []
            if phase['is_parallel']:
                labels.append('parallel')
            if task['is_tdd']:
                labels.append('tdd')
            if phase['is_setup']:
                labels.append('setup')
            if labels:
                out.append('### Labels')
                out.append(', '.join(labels))
                out.append('')

    return '\n'.join(out)


def parse_dependency_section(deps_text: str, phases: list[dict]) -> dict[int, list[int]] | None:
    """Parse '## Dependencies' text into explicit phase dependency map.

    Returns {phase_idx: [depends_on_phase_idx, ...]} or None if unparseable
    (caller falls back to linear chain).
    """
    if not deps_text or deps_text.strip().lower() in ('none', ''):
        return None

    phase_idx_by_num: dict[int, int] = {}
    for i, phase in enumerate(phases):
        m = re.match(r'Phase\s+(\d+)', phase['name'])
        if m:
            phase_idx_by_num[int(m.group(1))] = i

    if not phase_idx_by_num:
        return None

    result: dict[int, list[int]] = {}
    found_any = False

    for line in deps_text.splitlines():
        line = line.strip().lstrip('- ')
        if not line:
            continue

        # "Phase N depends on Phase M" or "Phase N depends on Phases M, O, and P"
        m = re.match(r'Phase\s+(\d+)\b.*?depends on\s+(.*)', line, re.IGNORECASE)
        if m:
            src_num = int(m.group(1))
            if src_num not in phase_idx_by_num:
                continue
            src_idx = phase_idx_by_num[src_num]
            rest = m.group(2).strip()

            if 'all preceding' in rest.lower():
                result[src_idx] = [phase_idx_by_num[n] for n in sorted(phase_idx_by_num) if n < src_num]
                found_any = True
                continue

            dep_nums = [int(x) for x in re.findall(r'\d+', rest)]
            dep_indices = [phase_idx_by_num[n] for n in dep_nums if n in phase_idx_by_num]
            if dep_indices:
                result[src_idx] = dep_indices
                found_any = True
            continue

        # "Phase N has no (internal) dependencies"
        m = re.match(r'Phase\s+(\d+)\b.*?has no\s+(?:internal\s+)?dependenc', line, re.IGNORECASE)
        if m:
            src_num = int(m.group(1))
            if src_num in phase_idx_by_num:
                result[phase_idx_by_num[src_num]] = []
                found_any = True
            continue

    if not found_any:
        return None

    # Phases not mentioned: fall back to "blocked by previous phase"
    all_phase_nums = sorted(phase_idx_by_num.keys())
    for i, num in enumerate(all_phase_nums):
        idx = phase_idx_by_num[num]
        if idx not in result and i > 0:
            prev_idx = phase_idx_by_num[all_phase_nums[i - 1]]
            result[idx] = [prev_idx]

    return result


def generate_deps(plan: dict) -> str:
    """Generate dependency relationships as a readable list."""
    out: list[str] = []
    out.append('# Dependency graph for beads')
    out.append('# After running: bd create -f .beads/plan.md --json')
    out.append('# Match task titles to returned IDs, then run bd dep commands.')
    out.append('#')
    out.append('# Format: "blocker" blocks "blocked"')
    out.append('#         "child" child-of "parent"')
    out.append('')

    # Build a flat list of (phase_index, task_index, task_slug, phase)
    all_tasks: list[tuple[int, int, str, dict]] = []
    for pi, phase in enumerate(plan['phases']):
        for ti, task in enumerate(phase['tasks']):
            slug = make_task_slug(task['description'])
            all_tasks.append((pi, ti, slug, phase))

    # Build phase_idx -> list of (task_idx, slug)
    phase_task_map: dict[int, list[tuple[int, str]]] = {}
    for pi, phase in enumerate(plan['phases']):
        if not phase['is_setup']:
            phase_task_map[pi] = [(ti, slug) for ppi, ti, slug, ph in all_tasks if ppi == pi]

    explicit_deps = parse_dependency_section(plan.get('dependencies', ''), plan['phases'])

    # Setup blocks target phases
    setup_slugs = [slug for pi, ti, slug, ph in all_tasks if ph['is_setup']]
    first_non_setup = [
        (pi, ti, slug, ph) for pi, ti, slug, ph in all_tasks if not ph['is_setup']
    ]
    if setup_slugs and first_non_setup:
        first_phase_idx = first_non_setup[0][0]
        setup_target_indices = {first_phase_idx}
        if explicit_deps is not None:
            for pi_key, dep_list in explicit_deps.items():
                if dep_list == []:
                    setup_target_indices.add(pi_key)
        setup_target_slugs = [
            slug for pi, ti, slug, ph in all_tasks
            if pi in setup_target_indices and not ph['is_setup']
        ]
        if setup_target_slugs:
            out.append('# Setup blocks initial/independent phases')
            for ss in setup_slugs:
                for stt in setup_target_slugs:
                    out.append(f'"{ss}" blocks "{stt}"')
            out.append('')

    # Cross-phase and within-phase deps
    prev_phase_idx = -1
    prev_phase_last_slugs: list[str] = []

    for pi, phase in enumerate(plan['phases']):
        if phase['is_setup']:
            continue

        phase_tasks = phase_task_map.get(pi, [])
        if not phase_tasks:
            continue

        # Cross-phase deps
        if explicit_deps is not None:
            dep_phase_indices = explicit_deps.get(pi)
            if dep_phase_indices is None:
                # Not mentioned — fall back to previous phase
                if prev_phase_last_slugs:
                    phase_label = phase['name']
                    out.append(f'# {phase_label} — cross-phase dependencies')
                    first_slugs = (
                        [slug for _, slug in phase_tasks]
                        if phase['is_parallel'] or plan['phases'][prev_phase_idx]['is_parallel']
                        else [phase_tasks[0][1]]
                    )
                    for ps in prev_phase_last_slugs:
                        for fs in first_slugs:
                            out.append(f'"{ps}" blocks "{fs}"')
                    out.append('')
            elif dep_phase_indices:
                # Explicit dependency list
                phase_label = phase['name']
                out.append(f'# {phase_label} — cross-phase dependencies')
                for dep_pi in dep_phase_indices:
                    dep_tasks = phase_task_map.get(dep_pi, [])
                    if not dep_tasks:
                        continue
                    dep_phase = plan['phases'][dep_pi]
                    if dep_phase['is_parallel']:
                        dep_last_slugs = [slug for _, slug in dep_tasks]
                    else:
                        dep_last_slugs = [dep_tasks[-1][1]] if dep_tasks else []
                    first_slugs = (
                        [slug for _, slug in phase_tasks]
                        if phase['is_parallel']
                        else [phase_tasks[0][1]]
                    )
                    for ds in dep_last_slugs:
                        for fs in first_slugs:
                            out.append(f'"{ds}" blocks "{fs}"')
                out.append('')
            # else: dep_phase_indices == [] — only Setup (already wired)
        else:
            # Fallback: linear chain (existing behavior)
            if prev_phase_last_slugs and phase_tasks:
                phase_label = phase['name']
                out.append(f'# {phase_label} — cross-phase dependencies')
                first_slugs = (
                    [slug for _, slug in phase_tasks]
                    if phase['is_parallel'] or plan['phases'][prev_phase_idx]['is_parallel']
                    else [phase_tasks[0][1]]
                )
                for ps in prev_phase_last_slugs:
                    for fs in first_slugs:
                        out.append(f'"{ps}" blocks "{fs}"')
                out.append('')

        # Within-phase deps (unchanged)
        if not phase['is_parallel'] and len(phase_tasks) > 1:
            out.append(f'# {phase["name"]} — sequential task chain')
            for i in range(len(phase_tasks) - 1):
                out.append(f'"{phase_tasks[i][1]}" blocks "{phase_tasks[i + 1][1]}"')
            out.append('')

        prev_phase_idx = pi
        if phase['is_parallel']:
            prev_phase_last_slugs = [slug for _, slug in phase_tasks]
        else:
            prev_phase_last_slugs = [phase_tasks[-1][1]] if phase_tasks else []

    return '\n'.join(out)


def parse_deps_file(deps_text: str) -> list[dict]:
    """Parse deps.txt into structured dependency entries."""
    deps = []
    for line in deps_text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        # Format: "name1" blocks "name2" or "name1" child-of "name2"
        # Since names may contain single quotes, split on the keyword between quotes
        for keyword, dep_type in [(' blocks ', 'blocks'), (' child-of ', 'child-of')]:
            if keyword not in line:
                continue
            # Find keyword outside of the quoted strings
            # Line format: "left" keyword "right"
            idx = line.index(keyword)
            left = line[1:idx - 1]   # strip leading " and trailing "
            right = line[idx + len(keyword) + 1:-1]  # strip leading " and trailing "
            if dep_type == 'blocks':
                deps.append({'type': 'blocks', 'blocker': left, 'blocked': right})
            else:
                deps.append({'type': 'child-of', 'child': left, 'parent': right})
            break

    return deps


def match_title_to_id(title: str, created_issues: list[dict]) -> str | None:
    """Find the bd issue ID whose title best matches the given task title."""
    title_lower = title.lower().strip()
    for issue in created_issues:
        issue_title = issue.get('title', '').lower().strip()
        if issue_title == title_lower:
            return issue['id']
        if title_lower in issue_title or issue_title in title_lower:
            return issue['id']
    return None


def run_bd_create(plan_path: Path) -> list[dict] | None:
    """Run bd create -f and return list of created issues, or None on failure."""
    result = subprocess.run(
        ['bd', 'create', '-f', str(plan_path), '--json'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f'  bd create -f failed: {result.stderr.strip()[:200]}')
        return None

    try:
        data = json.loads(result.stdout)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and 'issues' in data:
            return data['issues']
        if isinstance(data, dict) and 'id' in data:
            return [data]
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        lines = result.stdout.strip().splitlines()
        issues = []
        for line in lines:
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and 'id' in obj:
                    issues.append(obj)
            except json.JSONDecodeError:
                continue
        return issues if issues else None


def run_bd_deps(deps: list[dict], created_issues: list[dict]) -> tuple[int, int]:
    """Wire dependencies via bd dep commands. Returns (success_count, fail_count)."""
    success = 0
    fail = 0

    for dep in deps:
        if dep['type'] == 'blocks':
            blocker_id = match_title_to_id(dep['blocker'], created_issues)
            blocked_id = match_title_to_id(dep['blocked'], created_issues)
            if not blocker_id or not blocked_id:
                label = f'"{dep["blocker"]}" blocks "{dep["blocked"]}"'
                missing = 'blocker' if not blocker_id else 'blocked'
                print(f'  SKIP {label} — {missing} not found in created issues')
                fail += 1
                continue
            result = subprocess.run(
                ['bd', 'dep', blocker_id, '--blocks', blocked_id, '--json'],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f'  FAIL bd dep {blocker_id} --blocks {blocked_id}: '
                      f'{result.stderr.strip()[:100]}')
                fail += 1
            else:
                success += 1

        elif dep['type'] == 'child-of':
            child_id = match_title_to_id(dep['child'], created_issues)
            parent_id = match_title_to_id(dep['parent'], created_issues)
            if not child_id or not parent_id:
                label = f'"{dep["child"]}" child-of "{dep["parent"]}"'
                missing = 'child' if not child_id else 'parent'
                print(f'  SKIP {label} — {missing} not found in created issues')
                fail += 1
                continue
            result = subprocess.run(
                ['bd', 'dep', 'add', child_id, parent_id,
                 '-t', 'parent-child', '--json'],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f'  FAIL bd dep add {child_id} {parent_id} -t parent-child: '
                      f'{result.stderr.strip()[:100]}')
                fail += 1
            else:
                success += 1

    return success, fail


def find_epic_id(created_issues: list[dict]) -> str | None:
    """Find the epic issue from the created issues list."""
    for issue in created_issues:
        title = issue.get('title', '')
        if title.startswith('Goal:') or issue.get('type') == 'epic':
            return issue['id']
    return created_issues[0]['id'] if created_issues else None


def run_bd_parent_child(created_issues: list[dict]) -> tuple[int, int]:
    """Wire parent-child: all non-epic issues become children of the epic."""
    epic_id = find_epic_id(created_issues)
    if not epic_id:
        return 0, 0

    success = 0
    fail = 0
    for issue in created_issues:
        if issue.get('id') == epic_id:
            continue
        result = subprocess.run(
            ['bd', 'dep', 'add', issue['id'], epic_id,
             '-t', 'parent-child', '--json'],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            fail += 1
        else:
            success += 1

    return success, fail


def load_issues_from_bd() -> list[dict] | None:
    """Load current open issues from bd list --json."""
    result = subprocess.run(
        ['bd', 'list', '--limit', '500', '--json'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f'  bd list failed: {result.stderr.strip()[:200]}')
        return None

    try:
        data = json.loads(result.stdout)
        if isinstance(data, list):
            return data
        return []
    except json.JSONDecodeError:
        return None


def cmd_wire_deps(args: list[str]) -> int:
    from_bd = '--from-bd' in args
    id_map_path = None
    if '--id-map' in args:
        idx = args.index('--id-map')
        if idx + 1 < len(args):
            id_map_path = Path(args[idx + 1])

    filtered_args = [
        a for a in args
        if a not in ('--from-bd',) and a != '--id-map'
        and not (id_map_path and a == str(id_map_path))
    ]

    if not filtered_args:
        print('Usage: sculptor wire-deps <deps.txt> --from-bd')
        print('       sculptor wire-deps <deps.txt> --id-map <map.json>')
        return 1

    deps_path = Path(filtered_args[0])
    if not deps_path.exists():
        print(f'File not found: {deps_path}')
        return 1

    if not from_bd and not id_map_path:
        print('Specify --from-bd or --id-map <map.json>')
        return 1

    # Load issues
    if from_bd:
        print('Loading issues from bd...')
        issues = load_issues_from_bd()
        if issues is None:
            return 1
        print(f'  Found {len(issues)} issues')
    else:
        assert id_map_path is not None
        if not id_map_path.exists():
            print(f'File not found: {id_map_path}')
            return 1
        mapping = json.loads(id_map_path.read_text())
        if isinstance(mapping, list):
            issues = [{'id': d['id'], 'title': d['title']} for d in mapping if 'id' in d]
        elif isinstance(mapping, dict):
            issues = [{'id': v, 'title': k} for k, v in mapping.items()]
        else:
            print(f'Unexpected id-map format: expected list or dict, got {type(mapping).__name__}')
            return 1

    # Parse and wire deps
    deps_text = deps_path.read_text()
    parsed_deps = parse_deps_file(deps_text)

    if parsed_deps:
        print(f'\nWiring {len(parsed_deps)} dependencies...')
        success, fail = run_bd_deps(parsed_deps, issues)
        print(f'  Done: {success} wired, {fail} failed/skipped')
    else:
        print('No dependencies found in deps.txt')
        success, fail = 0, 0

    # Wire parent-child
    print(f'\nWiring parent-child relationships...')
    pc_success, pc_fail = run_bd_parent_child(issues)
    print(f'  Done: {pc_success} wired, {pc_fail} failed/skipped')

    total_fail = fail + pc_fail
    return 1 if total_fail > 0 else 0


def cmd_export_beads(args: list[str]) -> int:
    run_mode = '--run' in args
    dry_run = '--dry-run' in args
    filtered_args = [a for a in args if a not in ('--run', '--dry-run')]

    if not filtered_args:
        print('Usage: sculptor export-beads <idea-dir> [--run] [--dry-run]')
        return 1

    idea_dir = Path(filtered_args[0])
    plan_path = idea_dir / 'plan.md'

    if not plan_path.exists():
        print(f'plan.md not found in {idea_dir}')
        return 1

    plan = parse_plan(plan_path.read_text())
    if not plan['phases']:
        print('No phases found in plan.md — nothing to export')
        return 1

    epic_desc = read_idea_description(idea_dir)
    beads_plan = generate_beads_plan(plan, epic_desc)
    deps_text = generate_deps(plan)

    # Write output files
    beads_dir = idea_dir / '.beads'
    beads_dir.mkdir(exist_ok=True)

    plan_out = beads_dir / 'plan.md'
    plan_out.write_text(beads_plan)

    deps_out = beads_dir / 'deps.txt'
    deps_out.write_text(deps_text)

    if plan['invariants']:
        inv_out = beads_dir / 'invariants.md'
        inv_out.write_text(f'# Cross-worker Invariants\n\n{plan["invariants"]}\n')

    # Stats
    task_count = sum(len(ph['tasks']) for ph in plan['phases'])
    subtask_count = sum(
        len(t['subtasks']) for ph in plan['phases'] for t in ph['tasks']
    )
    phase_count = len(plan['phases'])

    print(f'Exported {task_count} tasks ({subtask_count} sub-tasks) '
          f'across {phase_count} phases:\n')
    print(f'  {plan_out.relative_to(idea_dir)}  — bd create -f input')
    print(f'  {deps_out.relative_to(idea_dir)} — dependency graph')
    if plan['invariants']:
        print(f'  .beads/invariants.md — cross-worker invariants')

    if not run_mode:
        print(f'\nNext steps:')
        print(f'  sculptor export-beads {idea_dir} --run')
        print(f'  # or manually:')
        print(f'  bd create -f {plan_out} --json')
        print(f'  # Then wire dependencies using {deps_out}')
        return 0

    # --run mode: execute bd commands
    if dry_run:
        print(f'\n--dry-run: validating plan format...')
        result = subprocess.run(
            ['bd', 'create', '-f', str(plan_out), '--dry-run', '--json'],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f'  Validation failed: {result.stderr.strip()[:200]}')
            return 1
        print(f'  Plan format is valid')
        return 0

    print(f'\nCreating beads...')
    created_issues = run_bd_create(plan_out)
    if created_issues is None:
        print('  Failed to create issues. Fix errors and retry.')
        return 1

    print(f'  Created {len(created_issues)} issues')

    # Save title→ID mapping for reference
    mapping_out = beads_dir / 'id-map.json'
    mapping_out.write_text(json.dumps(
        {issue.get('title', f'issue-{i}'): issue['id']
         for i, issue in enumerate(created_issues) if 'id' in issue},
        indent=2,
    ) + '\n')
    print(f'  ID mapping saved to {mapping_out.relative_to(idea_dir)}')

    # Wire dependencies
    parsed_deps = parse_deps_file(deps_text)
    fail = 0
    if parsed_deps:
        print(f'\nWiring {len(parsed_deps)} dependencies...')
        success, dep_fail = run_bd_deps(parsed_deps, created_issues)
        print(f'  Done: {success} wired, {dep_fail} failed/skipped')
        fail += dep_fail
    else:
        print(f'\nNo dependencies to wire.')

    # Wire parent-child relationships
    print(f'\nWiring parent-child relationships...')
    pc_success, pc_fail = run_bd_parent_child(created_issues)
    print(f'  Done: {pc_success} wired, {pc_fail} failed/skipped')
    fail += pc_fail

    if fail > 0:
        print(f'\n  Check {mapping_out.relative_to(idea_dir)} for ID mappings '
              f'and wire remaining deps manually.')
        return 1

    return 0


COMMANDS = {
    'annotations': cmd_annotations,
    'verify-clean': cmd_verify_clean,
    'phase': cmd_phase,
    'lint-spec': cmd_lint_spec,
    'lint-plan': cmd_lint_plan,
    'lint-cross': cmd_lint_cross,
    'export-beads': cmd_export_beads,
    'wire-deps': cmd_wire_deps,
}

USAGE = """sculptor — validation tool for sculptor sessions

Usage: sculptor <command> [args]

Commands:
  annotations <file.md>              Extract and parse >> annotations
  verify-clean <file.md>             Verify all annotations were removed
  phase <idea-dir>                   Detect current session phase
  lint-spec <spec.md>                Lint spec for dead types, path issues, TODOs
  lint-plan <plan.md> [--spec X]     Lint plan for missing AC, sections, spec refs
  lint-cross <idea-dir>              Cross-document lint (appendix links, types, refs)
  export-beads <idea-dir>            Generate .beads/ files (plan, deps, invariants)
  export-beads <idea-dir> --run      Generate files AND run bd create -f + bd dep
  export-beads <idea-dir> --dry-run  Validate plan format without creating issues
  wire-deps <deps.txt> --from-bd     Wire deps from deps.txt using bd issue list
  wire-deps <deps.txt> --id-map X    Wire deps from deps.txt using ID mapping file
"""


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help', 'help'):
        print(USAGE)
        return 0

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f'Unknown command: {cmd}')
        print(USAGE)
        return 1

    return COMMANDS[cmd](sys.argv[2:])


if __name__ == '__main__':
    sys.exit(main())
