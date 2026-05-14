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
| `### Dependencies` | `blocks:id, discovered-from:id, parent-child:id` | -- |

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

**Validate before creating:**
```bash
python3 .beads/tf.py validate-plan plan.md
```
This detects `---` separators, counts epics/tasks, and prints a dry-run preview.

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

For manual dep wiring, `tf.py dep` remains available (idempotent — handles duplicates gracefully):

```bash
python3 .beads/tf.py dep <blocker-id> <blocked-id>
```
