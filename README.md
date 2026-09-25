# Agent Skills Collection

A curated collection of skills for AI coding agents (Claude Code, Cursor, OpenAI Codex, etc.) following the [agentskills.io specification](https://agentskills.io/specification). Skills define specialized workflows, processes, and capabilities that enhance how AI agents approach development tasks.

## Quick Start

### For Claude Code

1. Clone this repository:
   ```bash
   git clone <your-repo-url> ~/agentskills
   cd ~/agentskills
   ```

2. Install skills globally or to a specific project:
   ```bash
   # Install all skills globally
   make install-all-global

   # Install a single skill to a project
   make install SKILL=reviewer TARGET=~/myproject

   # Copy instead of symlink (standalone, won't track updates)
   make copy SKILL=reviewer TARGET=~/myproject
   ```

3. Skills are automatically discovered from `~/.claude/skills/` or `<project>/.claude/skills/` on startup

4. Run `make help` to see all available targets and skills

### For omp (Oh My Pi)

omp discovers skills from its own config roots. There are two ways to enable this collection.

**Option A — install into omp-native skill dirs (mirrors the Claude workflow).** Every
Makefile target accepts `AGENT=omp`, which retargets the install/copy from `.claude/skills`
to omp's native layout: `<project>/.omp/skills` (project) and `~/.omp/agent/skills` (user).

```bash
# All skills, user-level (loaded by omp's native provider in every project)
make install-all-global AGENT=omp

# One skill into a project
make install SKILL=reviewer TARGET=~/myproject AGENT=omp

# Copy instead of symlink
make copy SKILL=reviewer TARGET=~/myproject AGENT=omp
```

**Option B — zero install, point omp at this repo.** Because every skill lives at
`<name>/SKILL.md` in the repo root and each has a `description`, omp can scan the repo
directly. Add to `~/.omp/agent/config.yml` (user-wide) or `<project>/.omp/config.yml`:

```yaml
skills:
  customDirectories:
    - /absolute/path/to/agentskills
```

omp's `claude` provider also reads `.claude/skills` at the project level by default, so a
plain `make install ...` (no `AGENT`) is already visible to omp when run inside that project;
the user-level `~/.claude/skills` path additionally requires enabling the Claude user
provider (`skills.enableClaudeUser`). `AGENT=omp` avoids that opt-in entirely.

### For Other Agents

Skills following the agentskills.io specification can be used with any compatible AI coding agent. Refer to your agent's documentation for how to configure custom skill directories.

## Available Skills

### Workflow Skills

| Name | Description | When to Use |
|------|-------------|-------------|
| [beadflow](./beadflow/SKILL.md) | Autonomous task management using Beads issue tracker | Multi-step projects, breaking down PRDs, managing complex implementations with dependency tracking |
| [sculptor](./sculptor/SKILL.md) | Collaborative idea polishing through dialogue and annotation cycles | Exploring vague ideas, refining concepts, creating PRDs, specs, or implementation plans |
| [reviewer](./reviewer/SKILL.md) | Comprehensive code review with tech-stack-specific checklists and structured report | Codebase audits, spec compliance checks, production readiness assessment, code quality reviews |
| [treeflow](./treeflow/SKILL.md) | Orchestrated parallel execution with background AI workers | Large projects, parallel implementation, distributing work across multiple agents |
| [ai-agent-prays-5-times-a-day](./ai-agent-prays-5-times-a-day/SKILL.md) | Makes an AI agent itself perform the five daily Islamic prayers (salah) on live sun times, posting the full fard ritual in a dedicated chat | A Muslim user wants their agent to observe the five daily prayers |

### Utility Skills

| Name | Description | When to Use |
|------|-------------|-------------|
| [session-viewer](./session-viewer/SKILL.md) | Parse and display Claude Code session JSONL files in multiple formats | Viewing, inspecting, analyzing, or debugging Claude Code sessions |

## How Skills Work

Skills are automatically discovered and activated by AI agents when relevant to the current task:

1. **Discovery**: Agents scan skill descriptions at startup
2. **Activation**: When you mention a skill by name or describe a matching task, the agent loads the full skill instructions
3. **Execution**: The agent follows the skill's defined workflow and best practices
4. **Progressive Loading**: Skills use structured references to load additional documentation only when needed

You can also manually trigger a skill in Claude Code using:
```
/skill-name
```

Or by explicitly mentioning it in conversation:
```
"Use the beadflow skill to plan this feature"
```

## Adding New Skills

Want to contribute a skill? Here's how:

1. **Create a skill directory** with a descriptive name (lowercase, hyphens only):
   ```
   your-skill-name/
   └── SKILL.md
   ```

2. **Write SKILL.md** following the [agentskills.io specification](https://agentskills.io/specification):
   ```yaml
   ---
   name: your-skill-name
   description: What this skill does and when to use it
   ---

   # Your Skill Instructions

   [Step-by-step instructions, examples, guidelines...]
   ```

3. **Test your skill** with an AI agent to verify it works as intended

4. **Submit a pull request** with:
   - Your skill directory
   - Updated README.md (add to the appropriate table above)

### Skill Best Practices

- **Clear activation triggers**: Make it obvious when your skill should be used
- **Focused scope**: One skill, one responsibility
- **Progressive disclosure**: Put core instructions in SKILL.md, detailed references in separate files
- **Examples**: Show concrete usage examples
- **Validation**: Test with real AI agents before submitting

See the [agentskills.io specification](https://agentskills.io/specification) for complete formatting requirements.

## Repository Structure

```
.
├── README.md              # This file (for humans)
├── AGENTS.md              # Agent guidance (for AI)
├── Makefile               # Install/uninstall/copy skills
├── ref/                   # Skill authoring reference docs
├── beadflow/              # Task management skill
│   └── SKILL.md
├── sculptor/              # Idea polishing skill
│   └── SKILL.md
├── treeflow/              # Orchestrated parallel execution skill
│   ├── SKILL.md
│   ├── tf.py              # Python state manager (deterministic coordination)
│   ├── COMMANDS.md
│   ├── CONTEXT-MANAGEMENT.md
│   ├── PLAN-FORMAT.md
│   ├── SCULPTOR-IMPORT.md
│   ├── WORKER-PROMPT.md
│   ├── WORKER-CONTEXT-TEMPLATE.md
│   └── WORKER-REGISTRY-TEMPLATE.md
├── reviewer/              # Code review skill
│   ├── SKILL.md
│   ├── CHECKLIST-CATALOG.md
│   └── REPORT-TEMPLATE.md
├── session-viewer/        # Session viewer skill
│   ├── SKILL.md
│   ├── SCHEMA.md
│   └── claude_session.py
└── your-skill/            # Your skills here
    ├── SKILL.md
    └── ...
```

## Contributing

Contributions are welcome! Please:

1. Follow the agentskills.io specification
2. Test your skill with at least one AI agent
3. Update both README.md and AGENTS.md
4. Keep skills focused and well-documented
5. Include clear activation triggers in the description

## License

See individual skill directories for their specific licenses. Skills without explicit licenses default to the repository license.

---

**For AI Agents**: Skills are auto-discovered from `SKILL.md` files. See [CLAUDE.md](./CLAUDE.md) for project conventions.
