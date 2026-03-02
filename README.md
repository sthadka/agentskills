# Agent Skills Collection

A curated collection of skills for AI coding agents (Claude Code, Cursor, OpenAI Codex, etc.) following the [agentskills.io specification](https://agentskills.io/specification). Skills define specialized workflows, processes, and capabilities that enhance how AI agents approach development tasks.

## Quick Start

### For Claude Code

1. Clone this repository:
   ```bash
   git clone <your-repo-url> ~/.claude/skills
   ```

2. Skills are automatically discovered from `~/.claude/skills/` on startup

3. Use skills by referencing them in conversation:
   ```
   "Let's use beadflow to manage this project"
   ```

### For Cursor

1. Clone this repository to your preferred location:
   ```bash
   git clone <your-repo-url> ~/agent-skills
   ```

2. Configure Cursor to load skills from this directory (see Cursor documentation for current setup method)

3. Skills will be available in your Cursor AI assistant sessions

### For Other Agents

Skills following the agentskills.io specification can be used with any compatible AI coding agent. Refer to your agent's documentation for how to configure custom skill directories.

## Available Skills

### Workflow Skills

| Name | Description | When to Use |
|------|-------------|-------------|
| [beadflow](./beadflow/SKILL.md) | Autonomous task management using Beads issue tracker | Multi-step projects, breaking down PRDs, managing complex implementations with dependency tracking |
| [sculptor](./sculptor/SKILL.md) | Collaborative idea polishing through dialogue and annotation cycles | Exploring vague ideas, refining concepts, creating PRDs, specs, or implementation plans |

### Technical Skills

*Coming soon - contributions welcome!*

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
   - Updated AGENTS.md (add to the catalog)

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
├── README.md           # This file (for humans)
├── AGENTS.md           # Agent guidance (for AI)
├── beadflow/           # Example skill
│   └── SKILL.md
└── your-skill/         # Your skills here
    ├── SKILL.md
    ├── scripts/        # Optional: executable scripts
    ├── references/     # Optional: detailed documentation
    └── assets/         # Optional: templates, diagrams
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

**For AI Agents**: See [AGENTS.md](./AGENTS.md) for the structured skill catalog and usage guidelines.
