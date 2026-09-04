# session-viewer: Determinism & Token-Efficiency Plan

**Goal:** make `session-viewer` (a) *highly deterministic* — same input always yields byte-identical output, output size is bounded and predictable, file resolution is unambiguous — and (b) *extremely token-efficient* — an agent can pull exactly the slice it needs at the smallest possible token cost.

**Constraints:** stay stdlib-only, single-file CLI, JSON-out for machine consumption (per CLAUDE.md conventions). Python 3.9 compat.

---

## 1. Current state (measured)

On a real 4.4 MB session (`923a986e…`, 399 assistant / 264 user / 248 tool calls):

| Mode | Output | ~tokens |
|------|--------|---------|
| `--json` | 63,306 B | ~15,800 |
| `--compact` | 37,523 B | ~9,400 |
| `--files` | 7,347 B | ~1,840 |
| `--errors` | 5,936 B | ~1,480 |
| `--summary` | 4,420 B | ~1,100 |
| (full) | 337,976 B | ~84,500 |

Within-run determinism holds (`--json` run twice = identical). No test suite exists. Findings below are the gaps between "works" and "highly deterministic + extremely token-efficient".

**What the skill already does well** (confirmed vs. reference `~/code/claude-session-viewer`): handles far more entry types (`system`, `last-prompt`, `ai-title`, `custom-title`, `task-summary`, `pr-link`, `mode`); has stdout+JSON design; per-line JSON-decode isolation; persisted-output resolution; subagent + remote-agent walking; redaction. Keep all of this.

---

## 2. Determinism findings

| # | Issue | Where | Impact |
|---|-------|-------|--------|
| D1 | **Ambiguous session resolution.** Fallback does substring match `sid in f` across all projects in `os.listdir` order → same ID can resolve to different files on different machines, silently picks first. | `find_session_file` L158-172 | Non-reproducible; wrong-session risk. |
| D2 | **No test suite / golden outputs.** Nothing guards output stability across refactors. | (missing) | Determinism is unverifiable. |
| D3 | **`os.walk` subagent order.** Files sorted within a dir, but directory traversal order from `os.walk` is filesystem-dependent. | `parse_subagents` L94 | Subagent list order varies. |
| D4 | **Local-time in `--list`.** `datetime.fromtimestamp(getmtime)` renders machine-local time. | `list_sessions` L189 | Same session, different displayed time per TZ. |
| D5 | **Unbounded fields in structured modes.** `--json` dumps all 248 tool calls + full `errors[].input`; `--summary` lists every user message. Output size scales with session, not a budget. | `print_json`, `print_summary` | Token cost unpredictable. |
| D6 | **No stable schema version marker** on JSON output; consumers can't detect format changes. | `print_json` | Brittle downstream parsing. |

## 3. Token-efficiency findings

| # | Issue | Where | Est. saving |
|---|-------|-------|-------------|
| T1 | **`--json` uses `indent=2`.** Pretty-printing wastes ~20% (63.3 KB → 50.9 KB compact). | `print_json` L832 | ~20% on every JSON call |
| T2 | **`tool_calls` list dominates** (33 KB of 51 KB = 65%). No way to get counts/summary without the full list, or to filter by tool/error/range. | `print_json` | Up to ~65% when not needed |
| T3 | **No slicing.** Can't request "last N turns", a turn range, or a single tool result by ID → agents fetch the whole transcript to read one section. | (missing) | Avoids 84 K-token full dumps |
| T4 | **No in-session search.** No way to grep text/tool-input and get only matching entries. | (missing) | Avoids full-transcript scans |
| T5 | **Fixed, non-tunable caps** (`TOOL_RESULT_MAX=3000`, `TEXT_MAX=5000`, input 1000). Agent can't trade detail for budget. | module constants | Tunable ceilings |
| T6 | **Per-line timestamp prefixes** in transcript/compact add tokens even when irrelevant. | `print_transcript`, `print_compact` | Small, opt-out |
| T7 | **No token/size estimate** to help an agent choose a mode before paying for it. | (missing) | Enables cheap pre-flight |

## 4. Correctness / coverage findings

| # | Issue | Where | Impact |
|---|-------|-------|--------|
| C1 | **`compact_boundary` not surfaced.** Context compaction points are invisible; an agent can't tell earlier turns were summarized away. | `parse_session` L252 | Misreads session history |
| C2 | **`api_error` / `informational` system entries dropped.** `--errors` shows only *tool* errors, not API failures / budget warnings. | `parse_session`, `print_errors` | Incomplete failure debugging |
| C3 | **Meta / injected user turns not distinguished.** `isMeta`/system-reminder/command-output user blocks are counted as real "user messages", polluting `--summary`/`--json`. | `extract_user_messages` L448 | "What did the user ask" is noisy |
| C4 | **New/unknown types silently ignored** (`queue-operation` seen in real data). Fine to skip, but no visibility. | `parse_session` fallthrough | Silent drift as schema evolves |
| C5 | **`sidechain` entries** in the main JSONL aren't flagged as such when interleaved. | `parse_session` | Subagent turns mixed into main flow |

(Already safe, verified: `image` blocks are never base64-dumped; `tool_result.content` list/str both handled; timestamps parse Z-suffix.)

---

## 5. Progressive disclosure — the centerpiece (tilth model)

The single biggest change. Today the entry points are already heavy — `--json` ≈ 119 lines, `--summary` ≈ 43 lines — and the *default* (no flag) is the full 84 K-token transcript. That is backwards: the agent pays maximum tokens before it knows what it's looking for.

tilth's principle: **the first call returns a bounded skeleton with addressable coordinates; the agent drills into exactly what it needs.** "Small files come back whole; large files get an outline; drill in with `--section`." We adopt the same ladder, with the *session* as the file and *user-prompt turns* as the sections.

### The disclosure ladder

| Level | Command | Returns | Cost |
|-------|---------|---------|------|
| **0. Map** *(new default)* | `session <id>` | Session outline: header + **one line per user-prompt turn** — headline, activity rollup (`Read×3 Edit×2 Bash [ERR]`), msg range, ~token estimate. Compaction markers inline. | ~1 line/turn, bounded |
| 1. Section | `--turn N` / `--section A:B` | Full detail for **one** section: that user prompt + the assistant turns/tools/results under it. | one work unit |
| 2. Summary | `--summary` | Aggregate stats (existing). | ~1 K |
| 3. Structured | `--json` | Tiered/compact structured data (see P1). | tunable |
| 4. Drill | `--grep`, `--tool-result <id>`, `--errors`, `--files` | Only matching entries / one result / just failures / just paths. | targeted |
| 5. Full | `--full` | Old full transcript. **Explicit opt-in.** | up to ~84 K |

### Key design rules (from tilth)

- **Map is the default** *(decided)*. No flag → the map, never the full transcript. `--full` is now required for the whole thing. This is an intentional breaking change to the no-flag behavior; SKILL.md and the golden tests are updated to match.
- **Token-based auto-whole.** If the entire session is under a threshold (~6 K tokens, mirroring tilth's file rule), the default prints it whole — outlining a tiny session wastes a round-trip. Only large sessions get the map.
- **Sections keyed by real user prompts.** Each genuine user prompt starts a section; assistant turns + tool activity nest under it and are addressable as one range (`--turn N` or `--section 10:14`). User-prompt turns are far fewer than raw messages (~17 real prompts vs. 264 user entries in the sample), so the map stays small even for huge sessions. Depends on C3 (meta-turn filtering) to keep the section list clean.
- **Every level prints its own cost.** Map header and each section carry `(~Xk tokens)` like tilth's `[outline]` header, so the agent can decide whether to drill further without a separate `--estimate` call.
- **Coordinates are stable and printed.** The map emits the exact `--turn`/`--section` argument next to each row, so drilling is copy-paste with no guessing.

### Example map output (sketch)

```
# session ba25023a (~4.4 MB, 47 turns, ~84k tokens, 3 errors, 1 compaction) [map]
branch: main  |  cwd: …/agentskills  |  duration: 12m

[T1]  user  "refactor the auth module to use the new client"   Read×3 Edit×2 Bash        ~1.2k  (msgs 1-9)
[T2]  user  "tests are failing"                                 Bash(pytest)[ERR] Edit    ~0.8k  (msgs 10-14)
--- CONTEXT COMPACTED ---
[T3]  user  "now update the docs"                               Read Write               ~0.5k  (msgs 15-22)
…
drill: --turn T2   |   full: --full   |   errors: --errors
```

This is the flow the user asked for: start with the basics, let the agent progressively request what it needs instead of loading everything into context.

---

## 6. Proposed changes (prioritized)

### P0 — Determinism foundation (do first)

1. **Golden-file test suite** (`test_session_viewer.py`) — addresses D2.
   - Ship 2–3 small synthetic fixture `.jsonl` files under `session-viewer/fixtures/` covering: normal turns, tool errors, persisted output, subagents, compact boundary, meta turns, corrupt line, image block.
   - Tests invoke the CLI via `subprocess` (matching project test pattern), assert **byte-identical** golden output per mode, and assert JSON parses + has expected keys.
   - Add a "run twice → identical" determinism assertion.
   - Wire into CLAUDE.md build/test section.

2. **Unambiguous file resolution** (D1): drop the fuzzy `sid in f` fallback. Match exact `<sid>.jsonl` only; if a bare prefix could match multiple, sort candidates and **error listing all matches** rather than silently picking one. Add `--project <name>` to disambiguate.

3. **Deterministic ordering** (D3): replace `os.walk` with a sorted recursive walk (sort dirs + files) so subagent order is stable.

4. **UTC in `--list`** (D4): render mtime as UTC ISO (`…Z`), not local time.

5. **Schema version marker** (D6): add `"_schema": "session-viewer/1"` as the first JSON key.

### P1 — Progressive disclosure + token efficiency (highest leverage)

6. **`--map` as the new default** (Section 5): section-outline keyed by user prompts, with rollups, ranges, coordinates, and per-row token estimates. Token-based auto-whole for small sessions. Old default (full transcript) moves behind `--full`.

7. **`--turn N` / `--section A:B`** (Section 5 / T3): expand exactly one section (or message range) to full detail — the primary drill-down after the map.

8. **Compact JSON by default** (T1): `json.dumps(..., separators=(",",":"))`; add `--pretty` for human debugging. ~20% off every JSON call.

9. **Tiered `--json`** (T2): default to a *lean* object (metadata, usage, tool_counts, file lists, error summaries, user messages); gate the full `tool_calls` array behind `--full` / `--include tool_calls`. Lean overview ≈ 1 K tokens instead of 15 K. (Consistent with map-first: the cheap thing is the default at every level.)

10. **Slicing / drill flags** (T3/T4), combinable with any mode:
   - `--last N` — last N turns only.
   - `--tool-result <id>` — print exactly one tool result (expanded), nothing else.
   - `--grep <regex>` — emit only entries whose text / tool-input / result matches; deterministic, line-bounded.

11. **Tunable caps** (T5): `--max-result N`, `--max-text N` override the constants; document defaults. Keep defaults fixed for golden-test stability.

12. **`--estimate`** (T7): print per-mode byte + approx-token counts (bytes/4) without emitting content. (Secondary to per-row estimates in the map, but useful for choosing between whole-session modes.)

13. **`--no-timestamps`** (T6) to drop `[HH:MM:SS]` prefixes.

*(P2 renumbering: items below keep their intent; C-numbers unchanged.)*

### P2 — Coverage / correctness

12. **Surface `compact_boundary`** (C1): record boundaries in metadata and print a `--- CONTEXT COMPACTED (N) ---` marker inline in transcript/compact; include in JSON `compaction_points`.

13. **Include API-level failures in `--errors`** (C2): collect `system.api_error` (+ optionally `informational` budget warnings) alongside tool errors.

14. **Filter meta/injected user turns** (C3): detect `isMeta` / synthetic user blocks (system-reminder, command output, tool_result-only) and exclude from `user_messages`; add `--include-meta` to opt back in.

15. **Unknown-type visibility** (C4): in `--summary`/`--json`, emit a small `unknown_types: {type: count}` map so schema drift is observable without changing behavior.

16. **Flag sidechain entries** (C5): mark `isSidechain` turns in output rather than silently interleaving.

### P3 — Docs

17. Update `SKILL.md` (new flags, "pick the cheapest mode" workflow, estimate-first guidance) and `SCHEMA.md` (`compact_boundary`, `isMeta`, `queue-operation`, sidechain notes). Keep the mode-selection table token-budget–oriented.

---

## 7. Suggested sequencing

1. **P0 golden tests + fixtures** — lock current behavior before touching anything.
2. **P0 determinism fixes** (resolution, ordering, UTC, schema marker) — update goldens.
3. **C3 meta-turn filtering** — prerequisite for a clean section list.
4. **`--map` + `--turn`/`--section`** — the progressive-disclosure core and biggest UX/token win. Make map the default last, once drill-downs exist so nothing is unreachable.
5. **Compact + tiered `--json`** — biggest structured-output token win.
6. **Slicing, caps, estimate, timestamp toggle.**
7. **Remaining P2** coverage (compaction markers, API errors, unknown-types, sidechain).
8. **P3 docs** — rewrite SKILL.md around the ladder ("start with the map, drill with `--turn`").

Each step keeps the suite green; every output-shape change updates a golden in the same commit, so determinism stays enforced throughout.

## 8. Non-goals

- No TUI, no external deps, no file-writing output mode (reference project does these; not needed for an agent-facing tool).
- No change to redaction semantics beyond keeping it working across new modes.
