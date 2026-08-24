#!/usr/bin/env python3
"""Sculptor validation tool — deterministic checks for sculptor sessions."""
from __future__ import annotations

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

    # 7. Contract validation — producer tasks that cross task boundaries should have Contract: sections
    plan_data = None
    if 'Contract:' in text or 'Consumed by:' in text or 'consumed by' in text.lower():
        plan_data = parse_plan(text)
    if plan_data is None:
        plan_data = parse_plan(text)

    _consumer_re = re.compile(r'consumed\s+by|used\s+by\s+task|called\s+from\s+task', re.IGNORECASE)
    for phase in plan_data['phases']:
        for task in phase['tasks']:
            full_desc = '\n'.join([task['description']] + task.get('body_lines', []) + task.get('extra_lines', []))
            if _consumer_re.search(full_desc) and 'Contract:' not in full_desc and 'contract:' not in full_desc.lower():
                slug = make_task_slug(task['description'])[:60]
                issues.append(f'Task `{slug}` has consumer reference but no Contract: section')

    # 8. Cross-cutting requirement detection
    if spec_path and spec_path.exists():
        spec_text_for_cc = spec_path.read_text()
        _quantifier_re = re.compile(
            r'\b(all\s+(?:commands?|endpoints?|handlers?|modules?|queries|routes?))\s+'
            r'(?:must|should|shall|need\s+to|have\s+to)\s+(.{10,80})',
            re.IGNORECASE,
        )
        for m in _quantifier_re.finditer(spec_text_for_cc):
            requirement = m.group(0).strip()[:80]
            scope_word = m.group(1).strip().lower()  # e.g. "all commands"
            action_text = m.group(2).strip().lower()
            # Extract key terms (identifiers like --verbose, flag names, etc.)
            key_terms = re.findall(r'--[\w-]+|`[^`]+`|\b\w{4,}\b', action_text)
            key_terms = [t.strip('`').lower() for t in key_terms[:5]]

            has_dedicated = False
            for phase in plan_data['phases']:
                for task in phase['tasks']:
                    task_text = (task['description'] + ' ' + ' '.join(task.get('body_lines', []))).lower()
                    # Match if task references the scope ("all commands", "every") AND
                    # mentions at least one key term from the requirement
                    scope_match = any(w in task_text for w in ('all ', 'every ', 'cross-cutting', 'each '))
                    term_match = any(t in task_text for t in key_terms) if key_terms else False
                    if scope_match and term_match:
                        has_dedicated = True
                        break
                if has_dedicated:
                    break
            if not has_dedicated:
                issues.append(f'Cross-cutting requirement has no dedicated task: "{requirement}"')

    # 9. Remaining annotations
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
                'is_parallel': '[parallel]' in desc.lower(),
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
    """Readable slug from a task description, used as title in plan.md and graph nodes."""
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


_SUBTASK_FILE_RE = re.compile(r'`([a-zA-Z0-9_/.=-]+\.[a-zA-Z0-9]{1,10})`')


def _extract_file_paths_from_subtasks(task: dict) -> list[str]:
    """Extract file paths from subtask descriptions for Files: annotation.

    Looks for backtick-wrapped paths like `config.go`, `engine.go`,
    `internal/config/config.go` in subtask description text.
    Also checks the task description for a directory prefix like
    `internal/config/` to qualify bare filenames.
    """
    dir_prefix = ''
    desc = task.get('description', '')
    m = re.search(r'`((?:[a-zA-Z0-9_-]+/)+)`', desc)
    if m:
        dir_prefix = m.group(1)

    paths: list[str] = []
    seen: set[str] = set()
    for st in task.get('subtasks', []):
        for fm in _SUBTASK_FILE_RE.finditer(st['description']):
            p = fm.group(1)
            if '/' in p:
                qualified = p
            elif dir_prefix:
                qualified = dir_prefix + p
            else:
                qualified = p
            if qualified in seen:
                continue
            if re.match(r'^[a-z0-9-]+\.[a-z]{2,}/', qualified):
                continue
            seen.add(qualified)
            paths.append(qualified)
    return paths


_EDGE_CASE_ACS = [
    'Command/query returns valid output when the store/database is empty (no crash, no null where array expected)',
    'Returns appropriate error when required parameters are missing',
    'Returns empty collection (not null) when no data matches',
]

_COMMAND_INDICATORS = re.compile(
    r'\b(command|CLI|endpoint|handler|route|query|api)\b', re.IGNORECASE
)


def generate_graph_plan(
    plan: dict,
    epic_description: str,
    deps_txt: str | None = None,
    spec_coverage: list[dict] | None = None,
) -> dict:
    """Generate a bd create --graph JSON plan with symbolic keys and edges."""
    nodes: list[dict] = []
    edges: list[dict] = []

    epic_node: dict = {
        'key': 'epic',
        'title': f'Goal: {plan["title"]}',
        'type': 'epic',
        'priority': 0,
    }
    if epic_description:
        epic_node['description'] = epic_description
    if plan['risks']:
        epic_node['design'] = f'**Risks:**\n{plan["risks"]}'
    if spec_coverage:
        epic_node['notes'] = 'Spec Coverage:\n' + '\n'.join(
            f'- {row["spec_section"]} → {row["task_ref"]}' for row in spec_coverage
        )
    nodes.append(epic_node)

    slug_to_key: dict[str, str] = {}
    task_parallel: dict[str, bool] = {}
    all_tasks: list[tuple[int, int, str, dict]] = []

    # Map phase list index → user-facing phase number (non-setup phases count from 1)
    phase_num: dict[int, int] = {}
    non_setup_count = 0
    for pi, phase in enumerate(plan['phases']):
        if phase['is_setup']:
            phase_num[pi] = 0
        else:
            non_setup_count += 1
            phase_num[pi] = non_setup_count

    for pi, phase in enumerate(plan['phases']):
        for ti, task in enumerate(phase['tasks']):
            key = f'setup.{ti + 1}' if phase['is_setup'] else f'{phase_num[pi]}.{ti + 1}'
            slug = make_task_slug(task['description'])
            slug_to_key[slug] = key
            task_parallel[slug] = task.get('is_parallel', False)
            all_tasks.append((pi, ti, slug, phase))

            desc_parts = [task['description']]
            if task.get('body_lines'):
                desc_parts.append('')
                desc_parts.extend(task['body_lines'])
            if task['extra_lines']:
                desc_parts.append('')
                desc_parts.extend(task['extra_lines'])
            if task['subtasks']:
                desc_parts.append('')
                desc_parts.append('Sub-tasks:')
                for st in task['subtasks']:
                    desc_parts.append(f'- {st["description"]}')
                    for el in st.get('extra_lines', []):
                        desc_parts.append(f'  {el}')
                    for bl in st.get('body_lines', []):
                        desc_parts.append(f'  {bl.strip()}')

            # Synthesize Files: annotation from subtask paths
            file_paths = _extract_file_paths_from_subtasks(task)
            if file_paths:
                desc_parts.append('')
                desc_parts.append(f'Files (new): {", ".join(file_paths)}')

            ac_lines = list(task['ac'])
            for st in task['subtasks']:
                ac_lines.extend(st['ac'])

            # Edge-case AC injection for command/API/query tasks
            full_task_text = task['description'] + ' ' + ' '.join(task.get('body_lines', []))
            is_integration = 'integration test' in task['description'].lower() or '[integration]' in task['description'].lower()
            if _COMMAND_INDICATORS.search(full_task_text) and not is_integration:
                existing_ac_text = ' '.join(ac_lines).lower()
                for edge_ac in _EDGE_CASE_ACS:
                    if edge_ac.split('(')[0].lower().strip() not in existing_ac_text:
                        ac_lines.append(edge_ac)

            labels = []
            if phase['is_parallel']:
                labels.append('parallel')
            if task['is_tdd']:
                labels.append('tdd')
            if phase['is_setup']:
                labels.append('setup')

            node: dict = {
                'key': key,
                'title': slug,
                'type': 'task',
                'priority': 1 if phase['is_setup'] else 2,
                'parent_key': 'epic',
                'description': '\n'.join(desc_parts),
            }
            if ac_lines:
                node['acceptance_criteria'] = '\n'.join(f'- {ac}' for ac in ac_lines)
            if labels:
                node['labels'] = labels
            nodes.append(node)

    # Auto-generate integration test beads at phase boundaries
    phase_task_keys: dict[int, list[str]] = {}
    for pi, phase in enumerate(plan['phases']):
        if phase['is_setup']:
            continue
        keys_in_phase = []
        for ti, task in enumerate(phase['tasks']):
            key = f'{phase_num[pi]}.{ti + 1}'
            keys_in_phase.append(key)
        phase_task_keys[pi] = keys_in_phase

    for pi, phase in enumerate(plan['phases']):
        if phase['is_setup']:
            continue
        # Skip if phase already has an integration test task
        has_integration = any(
            'integration test' in t['description'].lower() or '[integration]' in t['description'].lower()
            for t in phase['tasks']
        )
        if has_integration:
            continue
        keys_in_phase = phase_task_keys.get(pi, [])
        if len(keys_in_phase) < 2:
            continue

        pnum = phase_num[pi]
        integ_key = f'{pnum}.integ'
        task_titles = [make_task_slug(t['description']) for t in phase['tasks']]
        integ_desc = (
            f'Integration test for {phase["name"]}.\n'
            f'Verify that tasks in this phase compose correctly:\n'
            + '\n'.join(f'- {t}' for t in task_titles)
        )
        integ_ac = (
            f'- Integration test exercises cross-task interactions from {phase["name"]}\n'
            f'- Test against live APIs/DBs/services — do not mock'
        )
        integ_node = {
            'key': integ_key,
            'title': f'Integration test — {phase["name"]}',
            'type': 'task',
            'priority': 2,
            'parent_key': 'epic',
            'description': integ_desc,
            'acceptance_criteria': integ_ac,
            'labels': ['integration', 'optional'],
        }
        nodes.append(integ_node)
        slug_to_key[f'Integration test — {phase["name"]}'] = integ_key
        # Integration test depends on all tasks in its phase
        for dep_key in keys_in_phase:
            edges.append({'from_key': integ_key, 'to_key': dep_key, 'type': 'blocks'})

    # Build edges — use deps_txt when available, otherwise fall back to phase-level inference
    if deps_txt is not None:
        edges = parse_deps_txt(deps_txt, slug_to_key)
    else:
        phase_task_map: dict[int, list[tuple[int, str]]] = {}
        for pi, phase in enumerate(plan['phases']):
            phase_task_map[pi] = []
            for ti, task in enumerate(phase['tasks']):
                phase_task_map[pi].append((ti, make_task_slug(task['description'])))

        explicit_deps = parse_dependency_section(plan.get('dependencies', ''), plan['phases'])

        setup_slugs = [slug for _, _, slug, ph in all_tasks if ph['is_setup']]
        first_non_setup = [(pi, ti, slug, ph) for pi, ti, slug, ph in all_tasks if not ph['is_setup']]
        if setup_slugs and first_non_setup:
            first_phase_idx = first_non_setup[0][0]
            setup_target_indices = {first_phase_idx}
            if explicit_deps is not None:
                for pi_key, dep_list in explicit_deps.items():
                    if dep_list == []:
                        setup_target_indices.add(pi_key)
            for ss in setup_slugs:
                for stt_slug in [s for p, _, s, ph in all_tasks if p in setup_target_indices and not ph['is_setup']]:
                    if ss in slug_to_key and stt_slug in slug_to_key:
                        edges.append({'from_key': slug_to_key[stt_slug], 'to_key': slug_to_key[ss], 'type': 'blocks'})

        prev_phase_idx = -1
        prev_phase_last_slugs: list[str] = []

        for pi, phase in enumerate(plan['phases']):
            if phase['is_setup']:
                continue
            phase_tasks = phase_task_map.get(pi, [])
            if not phase_tasks:
                continue

            def _add_cross_edges(from_slugs: list[str], to_slugs: list[str]) -> None:
                for fs in from_slugs:
                    for ts in to_slugs:
                        if fs in slug_to_key and ts in slug_to_key:
                            edges.append({'from_key': slug_to_key[ts], 'to_key': slug_to_key[fs], 'type': 'blocks'})

            def _first_slugs_for_phase(pt: list[tuple[int, str]], par: bool) -> list[str]:
                return [s for _, s in pt] if par else [pt[0][1]]

            if explicit_deps is not None:
                dep_phase_indices = explicit_deps.get(pi)
                if dep_phase_indices is None:
                    if prev_phase_last_slugs:
                        is_par = phase['is_parallel'] or plan['phases'][prev_phase_idx]['is_parallel']
                        _add_cross_edges(prev_phase_last_slugs, _first_slugs_for_phase(phase_tasks, is_par))
                elif dep_phase_indices:
                    for dep_pi in dep_phase_indices:
                        dep_tasks = phase_task_map.get(dep_pi, [])
                        if not dep_tasks:
                            continue
                        dep_phase = plan['phases'][dep_pi]
                        dep_last = [s for _, s in dep_tasks] if dep_phase['is_parallel'] else [dep_tasks[-1][1]]
                        _add_cross_edges(dep_last, _first_slugs_for_phase(phase_tasks, phase['is_parallel']))
            else:
                if prev_phase_last_slugs and phase_tasks:
                    is_par = phase['is_parallel'] or plan['phases'][prev_phase_idx]['is_parallel']
                    _add_cross_edges(prev_phase_last_slugs, _first_slugs_for_phase(phase_tasks, is_par))

            if not phase['is_parallel'] and len(phase_tasks) > 1:
                last_seq_slug = None
                pending_parallel: list[str] = []
                for i in range(len(phase_tasks)):
                    slug = phase_tasks[i][1]
                    if task_parallel.get(slug, False):
                        dep = last_seq_slug or (phase_tasks[0][1] if i > 0 else None)
                        if dep and dep in slug_to_key and slug in slug_to_key:
                            edges.append({'from_key': slug_to_key[slug], 'to_key': slug_to_key[dep], 'type': 'blocks'})
                        pending_parallel.append(slug)
                    else:
                        if pending_parallel:
                            for par_slug in pending_parallel:
                                if par_slug in slug_to_key and slug in slug_to_key:
                                    edges.append({'from_key': slug_to_key[slug], 'to_key': slug_to_key[par_slug], 'type': 'blocks'})
                            pending_parallel = []
                        elif last_seq_slug and last_seq_slug in slug_to_key and slug in slug_to_key:
                            edges.append({'from_key': slug_to_key[slug], 'to_key': slug_to_key[last_seq_slug], 'type': 'blocks'})
                        last_seq_slug = slug

            prev_phase_idx = pi
            if phase['is_parallel']:
                prev_phase_last_slugs = [s for _, s in phase_tasks]
            else:
                prev_phase_last_slugs = [phase_tasks[-1][1]] if phase_tasks else []

    return {'nodes': nodes, 'edges': edges}


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


def parse_deps_txt(deps_text: str, slug_to_key: dict[str, str]) -> list[dict]:
    """Parse deps.txt into explicit edge list.

    Format: "blocker title" blocks "blocked title"
    Returns [{'from_key': ..., 'to_key': ..., 'type': 'blocks'}]
    """
    edges: list[dict] = []
    line_re = re.compile(r'"(.+?)"\s+blocks\s+"(.+?)"')

    def _resolve(title: str) -> str | None:
        slug = make_task_slug(title)
        if slug in slug_to_key:
            return slug_to_key[slug]
        for s, k in slug_to_key.items():
            if slug in s or s in slug:
                return k
        return None

    for line in deps_text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = line_re.match(line)
        if not m:
            continue
        from_title, to_title = m.group(1), m.group(2)
        from_key = _resolve(from_title)
        to_key = _resolve(to_title)
        if from_key and to_key:
            edges.append({'from_key': to_key, 'to_key': from_key, 'type': 'blocks'})
        else:
            parts = []
            if not from_key:
                parts.append(f'from="{from_title[:60]}"')
            if not to_key:
                parts.append(f'to="{to_title[:60]}"')
            print(f'  warning: unresolved dep — {", ".join(parts)}', file=sys.stderr)

    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for e in edges:
        pair = (e['from_key'], e['to_key'])
        if pair not in seen:
            seen.add(pair)
            deduped.append(e)
    return deduped


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

    deps_txt = None
    for deps_candidate in [idea_dir / 'deps.txt', idea_dir / '.beads' / 'deps.txt']:
        if deps_candidate.exists():
            deps_txt = deps_candidate.read_text()
            print(f'Using deps from {deps_candidate.relative_to(idea_dir)}')
            break

    # Parse spec coverage matrix if spec.md exists
    spec_cov = None
    spec_path = idea_dir / 'spec.md'
    if spec_path.exists():
        plan_text = plan_path.read_text()
        spec_cov = parse_spec_coverage_table(plan_text) or None

    graph = generate_graph_plan(plan, epic_desc, deps_txt=deps_txt, spec_coverage=spec_cov)

    beads_dir = idea_dir / '.beads'
    beads_dir.mkdir(exist_ok=True)

    graph_out = beads_dir / 'beads-graph.jsonl'
    graph_out.write_text(json.dumps(graph, indent=2) + '\n')

    if plan['invariants']:
        inv_out = beads_dir / 'invariants.md'
        inv_out.write_text(f'# Cross-worker Invariants\n\n{plan["invariants"]}\n')

    task_count = sum(len(ph['tasks']) for ph in plan['phases'])
    subtask_count = sum(
        len(t['subtasks']) for ph in plan['phases'] for t in ph['tasks']
    )
    phase_count = len(plan['phases'])
    edge_count = len(graph['edges'])

    print(f'Exported {task_count} tasks ({subtask_count} sub-tasks) '
          f'across {phase_count} phases, {edge_count} dependency edges:\n')
    print(f'  {graph_out.relative_to(idea_dir)}  — bd create --graph input')
    if plan['invariants']:
        print(f'  .beads/invariants.md — cross-worker invariants')

    if not run_mode:
        print(f'\nNext steps:')
        print(f'  bd create --graph {graph_out} --json')
        print(f'  # Or use treeflow:')
        print(f'  python3 .beads/tf.py import-graph {graph_out}')
        return 0

    if dry_run:
        print(f'\n--dry-run: validating graph...')
        result = subprocess.run(
            ['bd', 'create', '--graph', str(graph_out), '--dry-run', '--json'],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f'  Validation failed: {result.stderr.strip()[:200]}')
            return 1
        print(f'  Graph is valid')
        return 0

    print(f'\nCreating beads via bd create --graph...')
    result = subprocess.run(
        ['bd', 'create', '--graph', str(graph_out), '--json'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f'  Failed: {result.stderr.strip()[:200]}')
        return 1

    try:
        created = json.loads(result.stdout)
        ids = created.get('ids', {})
    except (json.JSONDecodeError, AttributeError):
        ids = {}

    print(f'  Created {len(ids)} issues (epic + {len(ids) - 1} tasks)')

    mapping_out = beads_dir / 'id-map.json'
    mapping_out.write_text(json.dumps(ids, indent=2) + '\n')
    print(f'  ID mapping saved to {mapping_out.relative_to(idea_dir)}')

    epic_id = ids.get('epic', '')
    if epic_id:
        print(f'\n  Epic: {epic_id}')

    return 0


COMMANDS = {
    'annotations': cmd_annotations,
    'verify-clean': cmd_verify_clean,
    'phase': cmd_phase,
    'lint-spec': cmd_lint_spec,
    'lint-plan': cmd_lint_plan,
    'lint-cross': cmd_lint_cross,
    'export-beads': cmd_export_beads,
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
  export-beads <idea-dir>            Generate .beads/ files (graph, invariants)
  export-beads <idea-dir> --run      Generate files AND run bd create --graph
  export-beads <idea-dir> --dry-run  Validate plan format without creating issues
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
