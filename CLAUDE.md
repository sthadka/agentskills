# Agent Skills — Project Instructions

A collection of Claude Code skills: reusable workflow packages (sculptor, treeflow, beadflow, reviewer, session-viewer, stateflow) that orchestrate multi-agent development.

## Build & Test

```bash
# Run all tests (Python 3.9+, no install needed)
python3 -m pytest sculptor/test_sculptor.py -x -q
python3 -m pytest treeflow/test_tf.py -x -q
python3 -m pytest stateflow/test_tf.py -x -q

# Run a specific test class
python3 -m pytest treeflow/test_tf.py::TestWorkerPrompt -x -q
```

No build step, no dependencies beyond pytest. Scripts are standalone Python files invoked as CLIs (`python3 sculptor/sculptor.py`, `python3 treeflow/tf.py`).

## Architecture

```
sculptor/          # Idea → spec → plan → beads graph
  sculptor.py      # CLI: annotate, lint-spec, lint-plan, export-beads
  test_sculptor.py

treeflow/          # Parallel orchestration via named workers + tf.py state manager
  tf.py            # CLI: init, dispatch, notify, worker-prompt, phase-complete, ...
  test_tf.py
  SKILL.md + COMMANDS.md + templates

stateflow/         # Earlier orchestration variant (separate tf.py, not shared)
  tf.py, test_tf.py, SKILL.md

beadflow/          # Lightweight single-agent task management via beads
reviewer/          # Code review with tech-stack checklists
session-viewer/    # Claude Code session JSONL parser
```

### Key data flow

1. **sculptor** parses `plan.md` into structured tasks, exports `beads-graph.jsonl` with dependency edges
2. **treeflow** imports the graph via `bd create --graph`, then orchestrates workers using `tf.py` for dispatch/notify/phase-gate/sync
3. **tf.py** manages a JSON registry (worker state, heartbeats, context%) and generates worker prompts from templates + bead descriptions

### sculptor internals

- `parse_plan()` → structured dict of phases/tasks/subtasks/AC
- `generate_graph_plan()` → nodes + edges JSON for `bd create --graph`
- `parse_deps_txt()` → reads `deps.txt` ("X blocks Y" lines) for explicit dependencies
- `export-beads` CLI ties it all together: plan.md + idea.md + deps.txt → `.beads/beads-graph.jsonl`

### treeflow/tf.py internals

- All commands output JSON (`_out()`) for machine consumption by the orchestrator
- Worker lifecycle: `dispatch` → `claim` → `heartbeat` → `worker-close` → `notify`
- Registry tracks: worker status (active/idle/retired), context%, bead assignment, heartbeat history
- `worker-prompt` assembles: template + project context + dependency summaries + phase interfaces + AC + build verification
- `phase-complete` gates phases: all beads closed + all notifications received + optional build command
- `notify` auto-closes parent epic when all children close, auto-runs build verification

## Conventions

- **Test pattern**: Tests use `subprocess.run()` to invoke the CLI and parse JSON output. Internal helpers are tested via `importlib` dynamic import. Both sculptor and treeflow tests use `tmp_path` fixtures for isolated workspaces.
- **No packages**: Scripts are standalone `.py` files, not installable packages. Tests import via `importlib.util.spec_from_file_location`.
- **Python 3.9 compat**: Use `from __future__ import annotations` for `X | Y` type syntax. The runtime is Python 3.9.
- **CLI output**: All tf.py commands return JSON via `_out()`. Errors go to stderr. Human-readable output is never mixed with JSON.
- **Bead descriptions**: Use `Files (new):` / `Files (modifies):` annotations for `conflict-check`. Use `AC:` lines for acceptance criteria. Both are parsed automatically.

## Beads Issue Tracker

This project uses `bd` (beads) for issue tracking. See the system prompt for full `bd` command reference.

- Use `bd` for ALL task tracking — not TodoWrite, TaskCreate, or markdown TODO lists
- Use `bd remember` for persistent knowledge — not MEMORY.md files
