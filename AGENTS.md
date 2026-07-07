# Agent Skill Catalog

**Purpose**: This file provides AI agents with structured guidance for discovering, selecting, and activating skills from this collection.

**Usage**: Scan the catalog below to identify relevant skills when processing user requests. Activate skills by loading their SKILL.md file when task characteristics match the activation triggers.

## Skill Discovery Protocol

### When to Activate a Skill

Activate a skill when ANY of these conditions are met:

1. **Explicit mention**: User references the skill by name
2. **Task pattern match**: User's request matches the skill's "When to Use" triggers
3. **Workflow requirement**: The task requires a structured approach that a skill provides
4. **Domain alignment**: The task involves a domain covered by a technical skill

### Selection Priority

When multiple skills could apply:

1. **User-specified skills**: If the user names a skill, use it
2. **Workflow skills first**: Process/methodology skills take precedence over technical skills
3. **Most specific match**: Choose the skill with the most specific relevance to the task
4. **Combine when appropriate**: Some skills work together (e.g., workflow + domain skills)

### Beadflow vs Treeflow

Both use Beads and support parallel sub-agents. The key difference: beadflow's main agent writes code + coordinates; treeflow's orchestrator never touches code and delegates everything to named, reusable workers with deterministic state management via `tf.py`.

**Use beadflow** for most projects (5-15 tasks, fits in one context window). **Use treeflow** when the project exceeds one context window, workers benefit from reuse across phases, or you need coordination guarantees (phase gates, smoke tests). See [ref/beadflow-vs-treeflow.md](ref/beadflow-vs-treeflow.md) for the full decision guide.

### Activation Method

1. Read the skill's SKILL.md file completely
2. Follow the instructions exactly as specified
3. Load referenced files (scripts/, references/, assets/) only when the skill instructions indicate
4. Do not modify or interpret skill instructions creatively—execute as written

## Complete Skill Catalog

### Workflow & Process Skills

| Skill Name | One-Line Description | Activation Triggers |
|------------|----------------------|---------------------|
| **beadflow** | Autonomous task management using Beads issue tracker for multi-step project orchestration | Multi-step projects, PRD breakdown, complex implementations, dependency tracking, task management, autonomous planning |
| **sculptor** | Collaborative idea polishing through structured dialogue and file-based annotation cycles | Idea exploration, brainstorming, concept refinement, PRD creation, spec writing, "I have an idea", "help me design this", "let's think through this" |
| **reviewer** | Comprehensive code review with tech-stack-specific checklists, spec deviation tracking, and structured report | Code review, codebase audit, spec compliance check, production readiness assessment, "review this code", "audit the codebase", "check against the spec" |
| **treeflow** | Orchestrates parallel execution using Beads and background AI workers | Large projects, parallel implementation, "use workers", "dispatch tasks", orchestrator mode, projects too large for single context |

### Technical & Domain Skills

*No technical skills currently available — reviewer is categorized as a Workflow skill*

## Detailed Skill Information

### beadflow

**Category**: Workflow
**Full Name**: beadflow
**Description**: Autonomous task management using Beads. Use when working on multi-step projects, breaking down PRDs, or managing complex implementations. Tracks all work in Beads issue graph.

**When to Activate**:
- User provides a PRD or project specification to implement
- Task involves multiple dependent steps
- Project requires tracking work status and dependencies
- Need to break down a large feature into manageable units
- Managing implementation with blocked/unblocked task states
- Autonomous execution of planned work

**Key Capabilities**:
- Converts PRDs into structured issue graphs (epics > features > tasks)
- Manages task dependencies and blocking relationships
- Provides autonomous work execution loop (find ready work, execute, track progress)
- Supports batch operations for efficient planning
- Maintains durable state across sessions
- Generates visual dependency graphs

**Entry Point**: Run `bd ready --json` to check for actionable work or `bd init` to start a new project

**File Location**: `beadflow/SKILL.md`

### sculptor

**Category**: Workflow
**Full Name**: sculptor
**Description**: Collaborative idea polishing through structured dialogue and file-based annotation cycles. Use when exploring, refining, or formalizing ideas into specs, PRDs, or implementation plans.

**When to Activate**:
- User has a vague idea they want to explore or refine
- User says "I have an idea", "let's brainstorm", "help me think through this", "help me design this"
- User wants to create a PRD, spec, or implementation plan from scratch
- User needs to formalize a concept before implementation
- User wants structured feedback on an idea through iterative annotation

**Key Capabilities**:
- Flexible research from any source (codebase, web, docs, dialogue)
- Iterative file-based annotation cycles (user annotates in editor, agent addresses notes)
- Scales document complexity to match idea complexity
- Proposes multiple approaches with trade-offs
- Optional escalation to PRD, technical spec, or implementation plan
- Session continuity through persistent file artifacts

**Entry Point**: Present an idea in any form — the skill will guide from there

**File Location**: `sculptor/SKILL.md`

### reviewer

**Category**: Workflow
**Full Name**: reviewer
**Description**: Comprehensive code review that explores the codebase, selects tech-stack-specific checklist modules, and produces a structured report with spec deviations and code quality findings in separate sections.

**When to Activate**:
- User asks to review a codebase or specific files
- User wants to audit code quality or check production readiness
- User wants to verify implementation against a spec, PRD, or requirements document
- User says "review this code", "audit the codebase", "check against the spec", "assess code quality"

**Key Capabilities**:
- Auto-detects tech stack and selects relevant checklist modules (Go, TypeScript, Python, Rust, Java/Kotlin, Temporal, React, DB, API)
- Builds spec traceability matrix when a spec is found
- Separates spec deviations from code quality findings in the report
- Produces severity-ranked findings with file:line references
- Generates test coverage map and weighted scorecard

**Entry Point**: Point the skill at a codebase and optionally a spec — it discovers both automatically

**File Location**: `reviewer/SKILL.md`

### treeflow

**Category**: Workflow
**Full Name**: treeflow
**Description**: Orchestrates parallel execution using Beads issue graph and background AI workers. Dispatches tasks to named workers, tracks progress, reuses workers by skill affinity, and maintains layered project context. The orchestrator never writes code.

**When to Activate**:
- User invokes `/treeflow` (always activate)
- User explicitly asks for parallel/distributed execution
- User says "use workers", "dispatch tasks", "orchestrator mode"
- Project has many independent tasks benefiting from parallelism
- User wants to scale beyond single-agent sequential execution

**Key Capabilities**:
- Pure orchestrator — never reads/writes project source code
- Deterministic state management via `tf.py` (Python) — registry, notifications, phase gates, smoke tests
- Spawns named background workers with fresh context windows
- Reuses workers when context allows for cache efficiency
- Routes tasks by skill (Go, React, Python, architecture, tests, etc.)
- Layered context: project > epic > feature > task
- Beads dependency graph for intelligent scheduling
- File-conflict analysis for parallelism safety
- Worker-to-user question bubbling
- Token-efficient: discards verbose notification content, stores compact summaries

**Entry Point**: `bd ready --json | jq -c` or provide a PRD/goal

**File Location**: `treeflow/SKILL.md` (with tf.py, COMMANDS.md, CONTEXT-MANAGEMENT.md, PLAN-FORMAT.md, SCULPTOR-IMPORT.md, WORKER-PROMPT.md, WORKER-CONTEXT-TEMPLATE.md, WORKER-REGISTRY-TEMPLATE.md)

## Usage Guidelines for Agents

### Skill Activation Pattern

```
1. Identify matching skill from catalog
2. Announce activation to user: "Using [skill-name] to [purpose]"
3. Load SKILL.md completely
4. Follow skill instructions exactly
5. Load additional files only when skill directs you to
```

### Multi-Skill Workflows

Some tasks benefit from combining skills:

- **Workflow + Technical**: Use a process skill (like beadflow) to structure the work, and technical skills for domain-specific implementation
- **Sequential activation**: Complete one skill's workflow before activating another
- **Nested skills**: Some skills may recommend activating other skills as part of their process

### Handling Skill Conflicts

If multiple skills could apply and it's unclear which to use:

1. Ask the user which approach they prefer
2. Default to workflow/process skills over technical skills
3. Choose the most specific skill for the task at hand

### After Skill Completion

When a skill's workflow is complete:

1. Report completion status to the user
2. Provide summary of what was accomplished
3. Offer to activate related skills if additional work remains
4. Do not remain "locked" into the skill—return to normal operation

## Skill File Structure Reference

Each skill directory follows this structure:

```
skill-name/
├── SKILL.md              # Primary instructions (always load first)
├── scripts/              # Optional: executable scripts
│   └── *.py, *.sh, etc.
├── references/           # Optional: detailed documentation
│   └── *.md
└── assets/               # Optional: templates and resources
    └── templates/, diagrams/, etc.
```

**Loading Priority**:
1. Always read SKILL.md first and completely
2. Load scripts/ files only when skill instructs you to execute them
3. Load references/ files only when skill references them or you need specific details
4. Load assets/ files only when skill directs you to use templates or resources

## Catalog Maintenance

This catalog is maintained manually. When new skills are added:

1. Update the appropriate category table with skill name, description, and triggers
2. Add detailed skill information in the "Detailed Skill Information" section
3. Ensure consistency between README.md (human-focused) and AGENTS.md (agent-focused)

---

**Last Updated**: 2026-04-05
**Total Skills**: 4 (4 workflow, 0 technical)

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
