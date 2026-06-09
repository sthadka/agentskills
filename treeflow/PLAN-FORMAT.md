# Markdown File Format for `bd create -f`

Write a `.md` file using this structure:

- `## Title` (H2) starts each issue
- `### Section` (H3) sets metadata within an issue
- Lines after `## Title` before any `###` become the description

## Recognized Sections

| Section | Content | Default |
|---------|---------|---------|
| `### Priority` | `0`-`4` or `P0`-`P4` | `2` |
| `### Type` | `bug`, `feature`, `task`, `epic`, `chore` | `task` |
| `### Description` | Multi-line text (overrides auto-description) | -- |
| `### Design` | Implementation approach, architecture notes | -- |
| `### Acceptance Criteria` | Definition of done, success criteria | -- |
| `### Assignee` | Username | -- |
| `### Labels` | Comma or space-separated | -- |
| `### Dependencies` | `blocks:id, depends_on:title, discovered-from:id, parent-child:id` | -- |

## Example Plan File

```markdown
## Goal: Build Authentication System

### Type
epic

### Priority
0

### Description
End-to-end auth system with JWT tokens, login/logout, and password reset.

### Acceptance Criteria
- Users can register, login, and logout
- JWT tokens with refresh rotation
- Password reset via email

## Create User model with email, password_hash, created_at fields

### Type
task

### Priority
2

### Description
Create the User model in models/user.py with all required fields and migrations.

## Add POST /api/auth/login endpoint

### Type
task

### Priority
2

### Description
Login endpoint in routes/auth.py. Validates credentials, returns JWT access + refresh tokens.

## Add POST /api/auth/logout endpoint

### Type
task

### Priority
2

### Description
Logout endpoint that invalidates the refresh token.

## Write unit tests for authentication

### Type
task

### Priority
2

### Description
Tests for register, login, logout, and token refresh in tests/test_auth.py.
```

## Format Restrictions

**Never use `---` horizontal rules in plan files.** The `---` sequence breaks `bd`'s markdown parser — epics parse but child tasks are silently dropped. Use blank lines or headings to separate sections.

**Every task must include a Files: line.** Use `Files (new):` for files the task creates and `Files (modifies):` for files it changes. Plain `Files:` is also accepted. `validate-plan` warns on tasks missing this section, and `conflict-check` uses it to detect file-level parallelism conflicts. Two tasks both modifying the same file are flagged as `modify_conflicts` (higher severity).

```markdown
## Implement auth middleware

Files (new): `middleware/auth.go`, `middleware/auth_test.go`
Files (modifies): `cmd/server/main.go`
```

**Validate before creating:**
```bash
python3 .beads/tf.py validate-plan plan.md
```
This detects `---` separators, missing Files: sections, counts epics/tasks, and prints a dry-run preview.

## Post-Creation Wiring

After creating issues, use `wire-plan` to auto-wire parent-child hierarchy and blocking dependencies in one command:

```bash
bd create -f plan.md --json > created.json
python3 .beads/tf.py wire-plan plan.md --ids created.json
```

`wire-plan` automatically:
- **Parent-child:** tasks under an epic heading become children of that epic
- **Blocking deps:** parses `### Dependencies` sections for `blocks:<title>` references and wires them

The `### Dependencies` section uses title-based references (matched by prefix against created issue titles):

```markdown
## Add POST /api/auth/login endpoint

### Dependencies
blocks:Add POST /api/auth/logout endpoint, blocks:Write unit tests for authentication
```

### Soft Dependencies (depends_on)

Use `depends_on:` for internal ordering that doesn't block readiness but prevents true parallelism. If task A creates types/interfaces that task B imports, task B should declare `depends_on:Task A title`.

```markdown
## Implement snapshot package

### Dependencies
depends_on:Implement scanner interface
```

Unlike `blocks:`, `depends_on:` does not prevent a task from appearing in `bd ready`. It signals the orchestrator to avoid batching these tasks into the same parallel group. `conflict-check` includes `depends_on` relationships in its `soft_deps` output.

For manual dep wiring, `tf.py dep` remains available (idempotent — handles duplicates gracefully):

```bash
python3 .beads/tf.py dep <blocker-id> <blocked-id>
```
