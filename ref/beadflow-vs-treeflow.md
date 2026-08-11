# Beadflow vs Treeflow — Decision Guide

Both skills use Beads (`bd`) for issue tracking and both support parallel execution via sub-agents. The difference is in **who writes code** and **how much coordination machinery you need**.

## At a Glance

| | Beadflow | Treeflow |
|--|---------|----------|
| **Who writes code** | Main agent + sub-agents for `[parallel]` | Named workers only, orchestrator never touches code |
| **State management** | Beads only (bd is the state) | Beads + `registry.json` + `tf.py` |
| **Quality gates** | `bf.py close` (validation), `bf.py smoke-test` | `tf.py worker-close` (validation), `tf.py phase-gate`, `tf.py phase-complete` |
| **Context strategy** | Main agent accumulates, recovers via `bd ready` | Layered files — workers get only what they need |
| **Worker lifecycle** | Ephemeral sub-agents, no reuse | Named, reusable via SendMessage, domain-specialized |
| **Phase transitions** | `bf.py smoke-test` between phases | `tf.py phase-complete` + spec-trace verification |
| **Context pressure** | Grows over time (mitigated by compaction) | Orchestrator stays lean, workers are disposable |
| **Overhead** | Low — `bf.py` is ~400 lines, no registry | Higher — `tf.py` is ~2700 lines, registry, context files, worker prompts |
| **Recovery** | `bd ready` + `bd list --status=in_progress` | `tf.py status` + `tf.py recover` + `tf.py registry` |
| **Cost** | 1 agent + ephemeral sub-agents | 1 orchestrator + N named workers |

## Decision Flowchart

```
Does the project fit in one context window?
├─ Yes → beadflow
└─ No or unsure
   │
   Does the main agent need to see all code to make decisions?
   ├─ Yes → beadflow (accept compaction risk)
   └─ No — tasks are independent enough to scope per-worker
      │
      Are there 3+ phases where the same domain repeats?
      ├─ Yes → treeflow (worker reuse pays off)
      └─ No
         │
         Is the project 15+ tasks across multiple phases?
         ├─ Yes → treeflow
         └─ No → beadflow
```

## Use Beadflow When

- **Project fits in one context window** — the main agent can hold everything it needs
- **Tasks are interconnected** — the agent benefits from seeing prior implementations directly
- **Parallel groups are small** — 2-4 tasks per `[parallel]` group
- **You want minimal overhead** — beads is the state, `bf.py` is a thin quality layer
- **Project is 5-15 tasks** — one session, maybe two
- **You need quality gates but not orchestration** — close validation, smoke-tests, conflict-check

## Use Treeflow When

- **Project exceeds one context window** — treeflow keeps the orchestrator lean by delegating all code work
- **Workers benefit from reuse** — domain-specialized workers retain context across tasks
- **You need coordination guarantees** — phase gates, notification tracking, stall detection
- **Worker specialization matters** — skill routing ensures the right worker gets the right task
- **Project is multi-phase (15-50+ tasks)** — the orchestrator coordinates without accumulating implementation details
- **You want deterministic state management** — registry.json tracks everything atomically

## How They Compose

Both skills share the same Beads commands, plan format, and sculptor import. The progression is natural:

1. **Start with beadflow** for most projects
2. **Switch to treeflow** when you notice:
   - Context getting full and you're not halfway done
   - Same types of tasks repeating across phases
   - Parallel groups getting large (5+)
   - Need for phase gates or notification tracking

A beadflow session can be "promoted" to treeflow by having the orchestrator stop writing code and start dispatching workers instead.

## Cost Comparison

For a 20-task project with 3 phases:

**Beadflow**: 1 main agent context. Sub-agents for `[parallel]` groups. Close validation and smoke-tests catch quality issues. Risk: compaction around task 15-20.

**Treeflow**: 1 orchestrator (~20-30% context throughout) + ~5-8 named workers. More total tokens, but no compaction risk and faster wall-clock time due to parallelism.
