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
        names.add(match.group(1))
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

    # 6. Spec coverage — check if plan references spec sections
    if spec_path and spec_path.exists():
        spec_text = spec_path.read_text()
        spec_sections = re.findall(r'^## (.+)$', spec_text, re.MULTILINE)
        plan_mentions_spec = 'spec.md' in text.lower() or 'Spec:' in text or 'spec §' in text
        if spec_sections and not plan_mentions_spec:
            issues.append(
                'Plan does not reference spec.md — consider adding '
                '`Spec: spec.md §N` citations to tasks'
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


## ── Plan parser (shared by lint-plan and export-beads) ──────────────────────


TASK_RE = re.compile(r'^- \[ \] (.+)')
SUBTASK_RE = re.compile(r'^  - \[ \] (.+)')
AC_RE = re.compile(r'^\s+- AC:\s*(.*)')
PHASE_RE = re.compile(r'^## (Phase \d+:\s*.+|Setup)(\s+\[.*\])?$')


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
    """Extract problem + solution from idea.md for the epic description."""
    idea_path = idea_dir / 'idea.md'
    if not idea_path.exists():
        return ''

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

    return '\n'.join(sections).strip()


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

            # Description: task desc + sub-tasks + extra lines
            out.append('### Description')
            out.append(task['description'])
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

    # Setup → Phase 1
    setup_slugs = [slug for pi, ti, slug, ph in all_tasks if ph['is_setup']]
    first_non_setup = [
        (pi, ti, slug, ph) for pi, ti, slug, ph in all_tasks if not ph['is_setup']
    ]
    if setup_slugs and first_non_setup:
        first_phase_idx = first_non_setup[0][0]
        first_phase_tasks = [
            slug for pi, ti, slug, ph in all_tasks
            if pi == first_phase_idx and not ph['is_setup']
        ]
        out.append('# Setup blocks all Phase 1 tasks')
        for ss in setup_slugs:
            for fpt in first_phase_tasks:
                out.append(f'"{ss}" blocks "{fpt}"')
        out.append('')

    # Within sequential phases: chain tasks
    # Between phases: last of N blocks first of N+1
    prev_phase_idx = -1
    prev_phase_last_slugs: list[str] = []

    for pi, phase in enumerate(plan['phases']):
        if phase['is_setup']:
            continue

        phase_tasks = [(ti, slug) for ppi, ti, slug, ph in all_tasks if ppi == pi]
        if not phase_tasks:
            continue

        # Cross-phase deps
        if prev_phase_last_slugs and phase_tasks:
            phase_label = phase['name']
            out.append(f'# {phase_label} — cross-phase dependencies')
            first_slugs = (
                [slug for _, slug in phase_tasks]
                if plan['phases'][prev_phase_idx]['is_parallel']
                else [phase_tasks[0][1]]
            )
            for ps in prev_phase_last_slugs:
                for fs in first_slugs:
                    out.append(f'"{ps}" blocks "{fs}"')
            out.append('')

        # Within-phase deps
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
    if not parsed_deps:
        print(f'\nNo dependencies to wire.')
        return 0

    print(f'\nWiring {len(parsed_deps)} dependencies...')
    success, fail = run_bd_deps(parsed_deps, created_issues)
    print(f'  Done: {success} wired, {fail} failed/skipped')

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
  export-beads <idea-dir>            Generate .beads/ files (plan, deps, invariants)
  export-beads <idea-dir> --run      Generate files AND run bd create -f + bd dep
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
