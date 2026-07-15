# {Plan Name} — Worker Context

> Sent to all workers. Do not include Worker Registry or Skill Routing — those are orchestrator-only (registry.json).
> Remove this header line before writing the file.

## Overview

{1-2 sentence description of what is being built and why}

## Tech Stack

- **Language/Runtime**: {e.g., TypeScript, Go, Python}
- **Framework**: {e.g., WXT, Next.js, Gin}
- **Key libraries**: {e.g., React, Fuse.js, xterm.js}
- **Testing**: {e.g., Vitest, Go test}
- **Build**: {e.g., Vite, esbuild}

## Repo Structure

```
{paste the relevant directory tree — focus on where workers will be writing}
```

## Conventions

- **Commit messages**: conventional format (`feat:`, `fix:`, `chore:`) — enforced by `tf.py worker-close`. Never include task/bead numbers (e.g., ❌ "feat: Task 9: ...")
- **Logging**: {e.g., use `slog` for structured logging; stderr for diagnostics, stdout for user-facing output}
- **Error handling**: {e.g., wrap errors with `fmt.Errorf("context: %w", err)` or raise with context}
- **Test files**: every production source file must have a corresponding `_test` file. Integration tests that require infrastructure use `t.Skip()` but must still exist. Table-driven tests for normalization/conversion helpers are mandatory.
- **Input validation**: {e.g., validate dates match YYYY-MM-DD, validate names against allowlist, validate paths contain no shell metacharacters}
- {e.g., Each command implements the Command interface}
- {e.g., All state in chrome.storage — no module-level globals}

## Security

- Any value interpolated into a shell command, SQL string, or file path must be validated first
- {e.g., CLI flags: validate dates match `YYYY-MM-DD`, scanner names against allowlist, paths contain no shell metacharacters}
- {e.g., Never pass user input directly to `fmt.Sprintf` in SQL or shell strings — use parameterized queries or `shlex.quote`}
- {e.g., Secrets and API keys must come from env vars, never hardcoded}

## Key Specs

- Full spec: `{path/to/spec.md}`
- {Other relevant docs with paths}

## Known Gotchas

<!-- Orchestrator: add project-specific entries discovered during codebase reading. -->

{Add project-specific gotchas here}

### Build Failures

If the build command fails due to platform-specific issues, use the most targeted verification command available for your language/framework, and note the limitation in your worker-close summary.

### Transient Diagnostics

LSP diagnostics during active worker runs (unresolved imports, undefined symbols in partially-written files) are almost always transient. Use the build tool as ground truth, not LSP. Do not act on these until the responsible worker completes.

- Workers never call `bd` directly — all bead operations go through `tf.py` subcommands (`claim`, `block`, `discover`, `worker-close`)
- **Never run `git stash -u` or `git stash --include-untracked`** — this stashes `.beads/context-*/` files and breaks orchestration state. Use `git stash` (tracked files only) or `git stash push <specific-files>` instead.

{Add project-specific gotchas here. Orchestrator appends more as workers discover recurring issues.}
