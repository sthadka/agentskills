# awty Feedback — Ideas from Building the StateFlow Workflow

Observations from converting a real orchestration skill (treeflow → stateflow) into an awty-enforced workflow. These are features/changes that would make workflows more powerful, effective, or simpler.

## 1. Auto-transition when parser fires an event with a matching transition

**Problem:** Parser fires an event (e.g., `LATE_NOTIFICATION` from tf_notify), but the agent still has to explicitly call `awty transition LATE_NOTIFICATION`. The parser already did the work — the agent just parrots it.

**Proposal:** If a parser fires an event AND the current state has a transition for that event AND all guards pass, auto-transition without agent intervention. Save 1 LLM turn per parser-fired event.

**Flag:** `auto_transition: true` on the pattern, or a global parser-level flag, so opt-in per pattern.

**Impact:** In stateflow, this would eliminate ~3-5 LLM turns per orchestration loop (LATE_NOTIFICATION self-loops, WORKER_BLOCKED routing, PHASE_GATE_* routing).

## 2. Multi-command on_enter

**Problem:** `on_enter` supports exactly one command. In stateflow's `orchestration_ready`, the agent needs both `tf.py status` (for counts) and `bd ready --json | jq -c` (for work detection) before it can do anything. Only one can be auto-run.

**Proposal:** Allow `on_enter.commands` as an array:
```yaml
on_enter:
  commands:
    - command: "python3 .beads/tf.py status"
      parser: tf_status
    - command: "bd ready --json | jq -c"
      parser: bd_ready
```

Execute sequentially, each parsed independently. This pre-populates all context the agent needs, eliminating 1-2 LLM turns of "run this command to orient myself."

## 3. Context field TTL / auto-clear

**Problem:** `test_result` gets set to `"pass"` in `tdd_verification`, then carries forward forever. Next time we enter that state for a different bead, the stale `test_result: "pass"` could let the `tests_passing` guard pass without actually running tests.

**Proposal:** Context field metadata with TTL:
```yaml
context:
  test_result:
    default: ""
    clear_on_enter: [tdd_verification, phase_gating]
```

Or simpler — `on_enter` supports a `clear_context` list:
```yaml
on_enter:
  clear_context: [test_result, last_bead_status, dispatch_plan]
  command: "python3 .beads/tf.py status"
  parser: tf_status
```

This prevents stale context from affecting guard evaluation in loop states.

## 4. on_exit hooks

**Problem:** When leaving a state, the agent often needs to clean up (archive context, clear dispatch_plan, write a summary). Currently this is in instructions and relies on the LLM remembering.

**Proposal:**
```yaml
on_exit:
  command: "python3 .beads/tf.py archive-context"
  clear_context: [dispatch_plan, has_ready_work]
```

Deterministic cleanup, not LLM-dependent.

## 5. Inline guards on transitions

**Problem:** One-off guards require a named definition in the top-level `guards:` section, even if they're only used once. This adds boilerplate and forces the workflow author to name trivial predicates.

**Proposal:** Allow inline guard objects directly in transitions:
```yaml
on:
  ASSESS_COMPLETE:
    - target: session_end
      guard:
        field: open_bead_count
        op: eq
        value: "0"
    - target: awaiting_completions
```

Named guards still work for reuse. Inline guards for one-offs.

## 6. Parser absence detection (negative match)

**Problem:** Want to fire an event when a pattern does NOT match. Example: if test runner output doesn't contain "ok" or "PASSED", it probably failed — but the failure format varies across runners.

**Proposal:** `negate: true` on a pattern:
```yaml
- type: json
  jq: ".ok"
  when_value: true
  negate: true          # fires when .ok is NOT true
  event: COMMAND_FAILED
```

Or a `when_absent: true` flag that fires the event/extract when the jq expression returns null/false.

## 7. Conditional on_enter

**Problem:** `on_enter` always runs. In `orchestration_ready`, running `tf.py status` is useful on first entry but wasteful when looping back from `processing_completions` (where status was just checked). The command costs ~200ms + 1 parser eval every re-entry.

**Proposal:**
```yaml
on_enter:
  command: "python3 .beads/tf.py status"
  parser: tf_status
  guard: status_stale     # only run if guard passes
```

Or a simpler `skip_on_reentry: true` flag that skips on_enter if the previous state was the same as the source of the transition.

## 8. State-scoped parser activation

**Problem:** All parsers are global — they match against every command in every state. The `test_runner` parser shouldn't fire in `entry_check` (where tests aren't relevant), and `tf_notify` shouldn't fire in `planning` (where notifications don't happen).

**Proposal:** Optional `states` field on parser:
```yaml
- name: test_runner
  match_command: "^(go test|pytest|npm test|cargo test|jest)"
  states: [tdd_verification, phase_gating]
  patterns: [...]
```

Omit `states` for global parsers. Reduces false-positive matches and makes parser intent clearer.

## 9. Transition data auto-merge from parser context

**Problem:** When the agent fires `awty transition EVENT --data '{...}'`, it manually copies context fields into the data payload. But parsers already populated those fields in context. The agent is doing redundant serialization.

**Proposal:** If `output_schema.required_fields` are already set in context (by parsers or prior transitions), the engine could auto-satisfy them without requiring `--data`. The agent just fires `awty transition PLAN_COMPLETE` and the engine checks that `plan_file` and `beads_created` exist in context.

If this already works, the docs should make it clearer — current examples always show `--data`.

## 10. Workflow composition without sub_machine overhead

**Problem:** `sub_machine` is powerful but heavy — it creates a child state file, maps context in/out, and requires a separate workflow YAML. For simple "run this 3-state verification sequence inline", it's overkill.

**Proposal:** Lightweight inline state groups:
```yaml
tdd_verification:
  type: group
  states:
    check_summary: ...
    run_tests: ...
    assess: ...
  on_complete: orchestration_ready
```

States within the group are scoped — they don't appear in the top-level state list. The group behaves as a single state from the parent's perspective. No separate file, no context mapping.

## 11. Diagram improvements

**Proposal:** `awty diagram` could annotate edges with guard names and mark `safe_next` transitions with a dashed line. Currently all transitions look the same in the Mermaid output, which hides the guard-vs-unguarded distinction.

## Summary — Priority ranking by impact

| # | Feature | LLM turns saved | Complexity |
|---|---------|-----------------|------------|
| 1 | Auto-transition on parser event | 3-5/loop | Medium |
| 2 | Multi-command on_enter | 1-2/state entry | Low |
| 3 | Context TTL / clear_on_enter | Prevents bugs | Low |
| 9 | Auto-merge from context | 1/transition | Low |
| 4 | on_exit hooks | 1/transition | Low |
| 8 | State-scoped parsers | Correctness | Low |
| 5 | Inline guards | Authoring convenience | Low |
| 7 | Conditional on_enter | 0.5/reentry | Medium |
| 6 | Negative match | Niche | Low |
| 10 | Inline state groups | Authoring convenience | High |
| 11 | Diagram annotations | Readability | Low |
