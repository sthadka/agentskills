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

### Technical & Domain Skills

*No technical skills currently available*

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

**Last Updated**: 2026-02-27
**Total Skills**: 2 (2 workflow, 0 technical)
