#!/usr/bin/env python3
"""Parse Claude Code session JSONL files and render them in token-efficient formats.

Usage:
    claude_session.py <session-id|path> [mode] [flags]
    claude_session.py --list [project-filter]

Progressive disclosure — start cheap, drill in:
    (default)      Session map: one line per user-prompt turn (see --map)
    --map          Force the map even for small sessions
    --turn N       Expand one section (e.g. --turn T2 or --turn 2)
    --section A:B   Expand a message range (e.g. --section 10:14)
    --full         Full transcript (the most expensive view)

Whole-session modes (mutually exclusive):
    --summary      Overview: turns, tokens, tool counts, file ops
    --json         Structured JSON (compact by default; add --full for tool_calls)
    --compact      One line per event
    --errors       Failed tool calls + API errors
    --files        File operations only (Read, Write, Edit paths)
    --tools-only   Tool calls and results only
    --estimate     Per-mode size / token estimates (no content)
    --grep REGEX   Only entries matching REGEX

Flags (combinable):
    --redact          Strip potential secrets (tokens, passwords, URLs with creds)
    --thinking        Include thinking blocks
    --no-results      Omit tool results (show only tool calls)
    --no-timestamps   Drop [HH:MM:SS] prefixes
    --expand          Resolve persisted tool results (large outputs saved to disk)
    --subagents       Include subagent sessions
    --include-meta    Keep meta / injected user turns (default: filtered)
    --pretty          Pretty-print JSON (default is compact)
    --last N          Restrict to the last N sections
    --max-result N    Cap tool-result chars (default 3000)
    --max-text N      Cap message-text chars (default 5000)
    --project NAME    Disambiguate session-id lookup to one project

Searches ~/.claude/projects/*/ for session files.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import glob
import re
import sys
from collections import Counter, OrderedDict
from contextlib import redirect_stdout
from datetime import datetime, timezone

CLAUDE_DIR = os.path.expanduser("~/.claude/projects")
SCHEMA_VERSION = "session-viewer/1"

DEFAULT_MAX_RESULT = 3000
DEFAULT_MAX_TEXT = 5000
INPUT_MAX = 1000
# Sessions estimated under this many tokens print whole by default (tilth rule).
AUTO_WHOLE_TOKEN_THRESHOLD = 6000
HEADLINE_MAX = 60

PERSISTED_RE = re.compile(
    r'<persisted-output>\s*Output too large \(([^)]+)\)\. '
    r'Full output saved to: ([^\n]+?)\s*'
    r'Preview \(first [^)]+\):\s*(.*?)\s*(?:</persisted-output>|\.\.\.)',
    re.DOTALL
)

# Entry types stored in the JSONL but not relevant for viewing.
SKIP_TYPES = frozenset((
    "file-history-snapshot", "attribution-snapshot", "content-replacement",
    "marble-origami-commit", "marble-origami-snapshot", "speculation-accept",
    "worktree-state", "agent-name", "agent-color", "agent-setting", "tag",
    "queue-operation",
))

# Injected/meta user-text wrappers that are not genuine user prompts.
META_TEXT_PREFIXES = (
    "<system-reminder>", "<command-name>", "<command-message>",
    "<local-command-stdout>", "<local-command-stderr>",
    "Caveat: The messages below", "[Request interrupted",
    "<user-prompt-submit-hook>",
)

# Patterns for secret redaction
REDACT_PATTERNS = [
    (re.compile(r'(oauth2:)[^\s@]+(@)'), r'\1***\2'),
    (re.compile(r'(token["\s:=]+)["\']?[A-Za-z0-9_\-.]{20,}', re.I), r'\1***'),
    (re.compile(r'(password["\s:=]+)["\']?[^\s"\']+', re.I), r'\1***'),
    (re.compile(r'(secret["\s:=]+)["\']?[^\s"\']+', re.I), r'\1***'),
    (re.compile(r'(api[_-]?key["\s:=]+)["\']?[^\s"\']+', re.I), r'\1***'),
    (re.compile(r'(Bearer\s+)[A-Za-z0-9_\-.]+', re.I), r'\1***'),
    (re.compile(r'(https?://)[^/\s]*:[^/\s]*@'), r'\1***:***@'),
]


def redact(text: str) -> str:
    for pattern, replacement in REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def est_tokens(n_chars: int) -> int:
    """Deterministic rough token estimate: 4 chars/token."""
    return n_chars // 4


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def resolve_persisted(text: str, session_dir: str | None) -> str:
    """Replace <persisted-output> references with full file contents."""
    if not session_dir or "<persisted-output>" not in text:
        return text

    def _replace(m):
        size_str, file_path, preview = m.group(1), m.group(2), m.group(3)
        file_path = file_path.strip()
        try:
            with open(file_path) as f:
                return f.read()
        except OSError:
            return f"[persisted: {file_path} ({size_str}, unreadable)]\n{preview}"

    return PERSISTED_RE.sub(_replace, text)


# -- Discovery -----------------------------------------------------------------

def find_session_dir(session_path: str) -> str | None:
    """Find the companion directory for a session JSONL file."""
    base = session_path.replace(".jsonl", "")
    if os.path.isdir(base):
        return base
    return None


def find_session_file(session_id: str, project: str | None = None) -> str:
    """Resolve a session id or path to exactly one JSONL file.

    Deterministic: matches `<id>.jsonl` exactly. If a bare prefix matches
    several files it raises with the full ambiguous list rather than silently
    picking one. Raises FileNotFoundError when nothing matches.
    """
    if os.path.isfile(session_id):
        return session_id

    sid = session_id[:-6] if session_id.endswith(".jsonl") else session_id

    project_dirs = sorted(
        d for d in glob.glob(os.path.join(CLAUDE_DIR, "*")) if os.path.isdir(d)
    )
    if project:
        project_dirs = [
            d for d in project_dirs if project.lower() in os.path.basename(d).lower()
        ]

    # 1) Exact filename match.
    exact = []
    for project_dir in project_dirs:
        candidate = os.path.join(project_dir, f"{sid}.jsonl")
        if os.path.isfile(candidate):
            exact.append(candidate)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        listing = "\n  ".join(exact)
        raise ValueError(
            f"Session id '{sid}' is ambiguous across projects:\n  {listing}\n"
            f"Disambiguate with --project <name>."
        )

    # 2) Prefix match (deterministic, ambiguity is an error).
    prefix = []
    for project_dir in project_dirs:
        for f in sorted(os.listdir(project_dir)):
            if f.endswith(".jsonl") and f[:-6].startswith(sid):
                prefix.append(os.path.join(project_dir, f))
    if len(prefix) == 1:
        return prefix[0]
    if len(prefix) > 1:
        listing = "\n  ".join(prefix)
        raise ValueError(
            f"Session prefix '{sid}' matches multiple sessions:\n  {listing}\n"
            f"Use a longer id or --project <name>."
        )

    raise FileNotFoundError(f"Session not found: {session_id}")


def list_sessions(project_filter: str = "") -> None:
    found = []
    for project_dir in sorted(glob.glob(os.path.join(CLAUDE_DIR, "*"))):
        if not os.path.isdir(project_dir):
            continue
        project_name = os.path.basename(project_dir)
        if project_filter and project_filter.lower() not in project_name.lower():
            continue
        for f in sorted(os.listdir(project_dir)):
            if not f.endswith(".jsonl") or f == "history.jsonl":
                continue
            if f.startswith("agent-"):
                continue
            path = os.path.join(project_dir, f)
            sid = f[:-6]
            size = os.path.getsize(path)
            mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
            label = _first_label(path)
            found.append((mtime, sid, project_name, size, label))

    found.sort(key=lambda x: (x[0], x[1]), reverse=True)
    print(f"{'DATE (UTC)':<18} {'SIZE':>8}  {'SESSION ID':<38} {'FIRST MESSAGE'}")
    print("-" * 110)
    for mtime, sid, _project, size, msg in found:
        size_str = f"{size // 1024}k" if size >= 1024 else f"{size}B"
        print(f"{mtime.strftime('%Y-%m-%d %H:%MZ'):<18} {size_str:>8}  {sid:<38} {msg}")


def _first_label(path: str) -> str:
    label = ""
    first_msg = ""
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = entry.get("type", "")
                if etype in ("ai-title", "custom-title"):
                    label = entry.get("title", "")[:80]
                elif etype == "user" and not first_msg:
                    text = _user_text(entry.get("message", {}).get("content", ""))
                    if text:
                        first_msg = text.strip()[:80]
    except OSError:
        return ""
    return label or first_msg


def _user_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                return b.get("text", "")
    return ""


# -- Parsing -------------------------------------------------------------------

def parse_session(path: str, expand: bool = False) -> dict:
    messages = []
    metadata = {}
    turns = []
    compaction_points = []
    api_errors = []
    unknown_types: Counter = Counter()
    usage_totals = {"input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0}
    session_dir = find_session_dir(path) if expand else None

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type in SKIP_TYPES:
                continue

            if msg_type == "system":
                subtype = msg.get("subtype")
                if subtype == "turn_duration":
                    turns.append({
                        "duration_ms": msg.get("durationMs", 0),
                        "timestamp": msg.get("timestamp", ""),
                        "git_branch": msg.get("gitBranch", ""),
                        "slug": msg.get("slug", ""),
                        "budget_tokens": msg.get("budgetTokens"),
                        "budget_limit": msg.get("budgetLimit"),
                    })
                    metadata["version"] = msg.get("version", "")
                    metadata["cwd"] = msg.get("cwd", "")
                    metadata["session_id"] = msg.get("sessionId", "")
                    metadata["entrypoint"] = msg.get("entrypoint", "")
                elif subtype == "compact_boundary":
                    compaction_points.append({
                        "after_message": len(messages),
                        "timestamp": msg.get("timestamp", ""),
                    })
                elif subtype == "api_error":
                    api_errors.append({
                        "timestamp": msg.get("timestamp", ""),
                        "text": (msg.get("text") or msg.get("error") or "")[:1000],
                    })
                continue

            if msg_type == "last-prompt":
                metadata["last_prompt"] = msg.get("lastPrompt", "")
                metadata["session_id"] = msg.get("sessionId", "")
                continue
            if msg_type in ("ai-title", "custom-title"):
                metadata["title"] = msg.get("title", "")
                continue
            if msg_type == "task-summary":
                metadata["task_summary"] = msg.get("summary", "")
                continue
            if msg_type == "pr-link":
                metadata["pr_url"] = msg.get("prUrl", "")
                metadata["pr_number"] = msg.get("prNumber")
                continue
            if msg_type == "mode":
                metadata["mode"] = msg.get("mode", "normal")
                continue

            if msg_type in ("user", "assistant"):
                inner = msg.get("message", {})
                ts = msg.get("timestamp", "")
                role = inner.get("role", msg_type)
                content = inner.get("content", "")

                usage = inner.get("usage", {})
                if usage:
                    usage_totals["input_tokens"] += usage.get("input_tokens", 0)
                    usage_totals["output_tokens"] += usage.get("output_tokens", 0)
                    usage_totals["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0)
                    usage_totals["cache_write_tokens"] += usage.get("cache_creation_input_tokens", 0)

                if msg_type == "user" and "permission_mode" not in metadata:
                    metadata["session_id"] = msg.get("sessionId", metadata.get("session_id", ""))
                    metadata["version"] = msg.get("version", "")
                    metadata["cwd"] = msg.get("cwd", "")
                    metadata["git_branch"] = msg.get("gitBranch", "")
                    metadata["permission_mode"] = msg.get("permissionMode", "")
                    metadata["entrypoint"] = msg.get("entrypoint", "")

                if session_dir and isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            rc = block.get("content", "")
                            if isinstance(rc, list):
                                for sub in rc:
                                    if isinstance(sub, dict) and sub.get("type") == "text":
                                        sub["text"] = resolve_persisted(
                                            sub.get("text", ""), session_dir)
                            elif isinstance(rc, str):
                                block["content"] = resolve_persisted(rc, session_dir)

                messages.append({
                    "role": role,
                    "content": content,
                    "timestamp": ts,
                    "meta": bool(msg.get("isMeta", False)),
                    "sidechain": bool(msg.get("isSidechain", False)),
                })
                continue

            # Anything left is an unrecognized type — track for drift visibility.
            unknown_types[msg_type] += 1

    metadata["turns"] = turns
    metadata["usage"] = usage_totals
    metadata["compaction_points"] = compaction_points
    metadata["api_errors"] = api_errors
    metadata["unknown_types"] = dict(sorted(unknown_types.items()))
    if turns:
        metadata["total_duration_ms"] = sum(t["duration_ms"] for t in turns)
        metadata["turn_count"] = len(turns)

    return {"metadata": metadata, "messages": messages}


def parse_subagents(session_dir: str) -> list:
    """Parse subagent sessions (deterministic sorted walk), incl. remote agents."""
    agents = []
    subagents_dir = os.path.join(session_dir, "subagents")
    if os.path.isdir(subagents_dir):
        for root, dirs, files in os.walk(subagents_dir):
            dirs.sort()
            for f in sorted(files):
                if not f.endswith(".meta.json"):
                    continue
                agent_id = f[:-len(".meta.json")]
                meta_path = os.path.join(root, f)
                jsonl_path = os.path.join(root, f"{agent_id}.jsonl")
                try:
                    with open(meta_path) as mf:
                        meta = json.load(mf)
                except (json.JSONDecodeError, OSError):
                    meta = {}

                tool_count, tool_names, errors = 0, [], 0
                if os.path.isfile(jsonl_path):
                    try:
                        sub = parse_session(jsonl_path)
                        sub_calls = extract_tool_calls(sub["messages"])
                        tool_count = len(sub_calls)
                        tool_names = [c["name"] for c in sub_calls]
                        errors = sum(1 for c in sub_calls if c["is_error"])
                    except Exception:
                        pass

                agents.append({
                    "id": agent_id,
                    "type": meta.get("agentType", "?"),
                    "description": meta.get("description", "?"),
                    "worktree": meta.get("worktreePath", ""),
                    "tool_count": tool_count,
                    "tool_names": tool_names,
                    "errors": errors,
                    "jsonl_path": jsonl_path if os.path.isfile(jsonl_path) else None,
                })

    remote_dir = os.path.join(session_dir, "remote-agents")
    if os.path.isdir(remote_dir):
        for f in sorted(os.listdir(remote_dir)):
            if not f.endswith(".meta.json"):
                continue
            try:
                with open(os.path.join(remote_dir, f)) as mf:
                    meta = json.load(mf)
            except (json.JSONDecodeError, OSError):
                meta = {}
            agents.append({
                "id": meta.get("taskId", f[:-len(".meta.json")]),
                "type": f"remote:{meta.get('remoteTaskType', '?')}",
                "description": meta.get("title", meta.get("command", "?")),
                "worktree": "",
                "tool_count": 0,
                "tool_names": [],
                "errors": 0,
                "jsonl_path": None,
            })

    return agents


# -- Formatting helpers --------------------------------------------------------

def format_timestamp(ts: str) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return ts[:19]


def truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"\n... [{len(s)} total chars]"


def headline(text: str, width: int = HEADLINE_MAX) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= width:
        return one_line
    return one_line[:width - 1] + "…"


def print_metadata(meta: dict) -> None:
    print("=" * 70)
    title = meta.get("title", "")
    if title:
        print(f"Title:   {title}")
    print(f"Session: {meta.get('session_id', 'unknown')}")
    print(f"Branch:  {meta.get('git_branch', '?')}  |  CWD: {meta.get('cwd', '?')}")
    mode = meta.get("mode", "")
    mode_str = f"  |  Mode: {mode}" if mode and mode != "normal" else ""
    print(f"Version: {meta.get('version', '?')}  |  Permissions: "
          f"{meta.get('permission_mode', '?')}{mode_str}")
    tc = meta.get("turn_count", 0)
    if tc:
        total_s = meta.get("total_duration_ms", 0) / 1000
        print(f"Turns:   {tc}  |  Total duration: {total_s:.1f}s")
    usage = meta.get("usage", {})
    if any(usage.values()):
        print(f"Tokens:  in={format_tokens(usage['input_tokens'])}  "
              f"out={format_tokens(usage['output_tokens'])}  "
              f"cache_read={format_tokens(usage['cache_read_tokens'])}  "
              f"cache_write={format_tokens(usage['cache_write_tokens'])}")
    if meta.get("compaction_points"):
        print(f"Compactions: {len(meta['compaction_points'])}")
    if meta.get("api_errors"):
        print(f"API errors:  {len(meta['api_errors'])}")
    pr = meta.get("pr_url", "")
    if pr:
        print(f"PR:      {pr}")
    print("=" * 70)


# -- Extractors ----------------------------------------------------------------

def extract_tool_calls(messages: list) -> list:
    pending = {}
    results = []
    for msg in messages:
        content = msg["content"]
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type", "")
            if bt == "tool_use":
                call = {
                    "name": block.get("name", "?"),
                    "id": block.get("id", ""),
                    "input": block.get("input", {}),
                    "timestamp": msg.get("timestamp", ""),
                    "result_text": "",
                    "is_error": False,
                }
                pending[call["id"]] = call
                results.append(call)
            elif bt == "tool_result":
                tool_id = block.get("tool_use_id", "")
                result_text = _extract_result_text(block.get("content", ""))
                if tool_id in pending:
                    pending[tool_id]["result_text"] = result_text
                    pending[tool_id]["is_error"] = block.get("is_error", False)
    return results


def extract_file_ops(tool_calls: list) -> list:
    ops = []
    for call in tool_calls:
        name = call["name"]
        inp = call["input"]
        ts = format_timestamp(call["timestamp"])
        if name in ("Read", "Write", "Edit"):
            ops.append({"op": name.lower(), "path": inp.get("file_path", "?"),
                        "timestamp": ts, "error": call["is_error"]})
        elif name == "Glob":
            ops.append({"op": "glob", "path": inp.get("pattern", "?"),
                        "timestamp": ts, "error": call["is_error"]})
        elif name == "Grep":
            ops.append({"op": "grep", "path": inp.get("pattern", "?"),
                        "timestamp": ts, "error": call["is_error"]})
    return ops


def is_real_prompt(msg: dict) -> bool:
    """True when a user message is a genuine prompt (not injected/meta/tool-only)."""
    if msg["role"] != "user":
        return False
    if msg.get("meta"):
        return False
    content = msg["content"]
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        text = ""
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    break
    else:
        return False
    if not text:
        return False
    return not text.startswith(META_TEXT_PREFIXES)


def extract_user_messages(messages: list, include_meta: bool = False) -> list:
    out = []
    for msg in messages:
        if msg["role"] != "user":
            continue
        if not include_meta and not is_real_prompt(msg):
            continue
        content = msg["content"]
        if isinstance(content, str) and content.strip():
            out.append({"text": content.strip(), "timestamp": msg.get("timestamp", "")})
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" \
                        and block.get("text", "").strip():
                    out.append({"text": block["text"].strip(),
                                "timestamp": msg.get("timestamp", "")})
    return out


def _extract_result_text(result_content) -> str:
    if isinstance(result_content, list):
        return "".join(
            sub.get("text", "") for sub in result_content
            if isinstance(sub, dict) and sub.get("type") == "text"
        )
    if isinstance(result_content, str):
        return result_content
    return ""


def _content_chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        return 0
    total = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        bt = block.get("type", "")
        if bt == "text":
            total += len(block.get("text", ""))
        elif bt == "thinking":
            total += len(block.get("thinking", ""))
        elif bt == "tool_use":
            total += len(json.dumps(block.get("input", {})))
        elif bt == "tool_result":
            total += len(_extract_result_text(block.get("content", "")))
    return total


def _tool_input_summary(name: str, inp: dict) -> str:
    if name in ("Read", "Write", "Edit"):
        return inp.get("file_path", "?")
    if name == "Glob":
        p, d = inp.get("pattern", "?"), inp.get("path", "")
        return f"{p} in {d}" if d else p
    if name == "Grep":
        return f"/{inp.get('pattern', '?')}/ in {inp.get('path', '.')}"
    if name == "Bash":
        return inp.get("command", "?").replace("\n", " ")[:100]
    if name == "LSP":
        return f"{inp.get('command', '?')} {inp.get('file_path', '')}"
    if name == "Agent":
        return inp.get("description", inp.get("prompt", "?"))[:100]
    if name == "ToolSearch":
        return inp.get("query", "?")
    if name == "Skill":
        return inp.get("name", "?")
    s = json.dumps(inp)
    return s[:100] + "..." if len(s) > 100 else s


# -- Sections (progressive disclosure) -----------------------------------------

def build_sections(messages: list) -> list:
    """Group messages into sections keyed by genuine user prompts.

    Each section is addressable via --turn (T-index) or --section (msg range).
    Message indices in the returned ranges are 1-based and inclusive.
    """
    sections = []
    cur = None
    prompt_no = 0  # counts genuine prompts so T-numbers stay 1..N

    def _new(prompt: str, start_idx: int, n: int) -> dict:
        return {
            "id": f"T{n}",
            "n": n,
            "prompt": prompt,
            "msg_start": start_idx,
            "msg_end": start_idx,
            "tools": OrderedDict(),
            "error_count": 0,
            "chars": 0,
        }

    for i, msg in enumerate(messages, 1):
        if is_real_prompt(msg):
            if cur:
                sections.append(cur)
            prompt_no += 1
            cur = _new(_user_text(msg["content"]).strip(), i, prompt_no)
        elif cur is None:
            # Preamble before the first genuine prompt.
            cur = _new("(session start)", i, 0)

        cur["msg_end"] = i
        cur["chars"] += _content_chars(msg["content"])
        if isinstance(msg["content"], list):
            for block in msg["content"]:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    cur["tools"][name] = cur["tools"].get(name, 0) + 1
                elif block.get("type") == "tool_result" and block.get("is_error"):
                    cur["error_count"] += 1

    if cur:
        sections.append(cur)
    return sections


def _rollup(section: dict) -> str:
    parts = [f"{name}×{n}" if n > 1 else name for name, n in section["tools"].items()]
    rollup = " ".join(parts)
    if section["error_count"]:
        rollup = (rollup + " " if rollup else "") + f"[ERR×{section['error_count']}]"
    return rollup


# -- Output modes --------------------------------------------------------------

def print_map(session: dict, sections: list, last: int | None = None,
              do_redact: bool = False) -> None:
    meta = session["metadata"]
    total_chars = sum(_content_chars(m["content"]) for m in session["messages"])
    n_err = sum(1 for c in extract_tool_calls(session["messages"]) if c["is_error"])
    n_err += len(meta.get("api_errors", []))

    header = (f"# session {meta.get('session_id', '?')[:8]} "
              f"({len(sections)} turns, ~{format_tokens(est_tokens(total_chars))} tokens")
    if n_err:
        header += f", {n_err} errors"
    if meta.get("compaction_points"):
        header += f", {len(meta['compaction_points'])} compaction"
    header += ") [map]"
    print(header)
    print(f"branch: {meta.get('git_branch', '?')}  |  cwd: {meta.get('cwd', '?')}")
    if meta.get("title"):
        print(f"title: {meta['title']}")
    print()

    shown = sections[-last:] if last else sections
    compaction_after = {c["after_message"] for c in meta.get("compaction_points", [])}

    for sec in shown:
        line = _user_text_line(sec, do_redact)
        print(line)
        if any(sec["msg_start"] <= a <= sec["msg_end"] for a in compaction_after):
            print("    --- CONTEXT COMPACTED ---")
    print()
    print("drill: --turn <id>  |  range: --section A:B  |  full: --full  |  errors: --errors")


def _user_text_line(sec: dict, do_redact: bool) -> str:
    head = headline(sec["prompt"])
    if do_redact:
        head = redact(head)
    rollup = _rollup(sec)
    toks = f"~{format_tokens(est_tokens(sec['chars']))}"
    rng = f"msgs {sec['msg_start']}-{sec['msg_end']}"
    return (f"[{sec['id']}]  \"{head}\"".ljust(74)
            + f"{rollup}".ljust(30) + f"{toks:>7}  {rng}")


def print_transcript(session: dict, tools_only: bool = False,
                     show_thinking: bool = False, no_results: bool = False,
                     do_redact: bool = False, show_ts: bool = True,
                     max_text: int = DEFAULT_MAX_TEXT,
                     max_result: int = DEFAULT_MAX_RESULT,
                     msg_range: tuple | None = None,
                     header: bool = True) -> None:
    meta = session["metadata"]
    if header:
        print_metadata(meta)
        print()

    tool_calls = {}
    turn = 0
    lo, hi = msg_range if msg_range else (1, len(session["messages"]))

    for idx, msg in enumerate(session["messages"], 1):
        if idx < lo or idx > hi:
            continue
        role = msg["role"]
        content = msg["content"]
        ts = format_timestamp(msg.get("timestamp", "")) if show_ts else ""
        ts_prefix = f"[{ts}] " if ts else ""

        if isinstance(content, str):
            if content.strip() and not (tools_only and role == "assistant"):
                text = truncate(content, max_text)
                if do_redact:
                    text = redact(text)
                print(f"{ts_prefix}{role.upper()}: {text}\n")
            continue

        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type", "")

            if bt == "text":
                text = block.get("text", "")
                if text.strip() and not (tools_only and role == "assistant"):
                    text = truncate(text, max_text)
                    if do_redact:
                        text = redact(text)
                    print(f"{ts_prefix}{role.upper()}: {text}\n")

            elif bt == "thinking" and show_thinking:
                thinking = block.get("thinking", "")
                if thinking.strip():
                    print(f"{ts_prefix}THINKING: {truncate(thinking, max_text)}\n")

            elif bt == "tool_use":
                name = block.get("name", "?")
                tool_calls[block.get("id", "")] = name
                turn += 1
                inp_str = json.dumps(block.get("input", {}), indent=2) or "{}"
                if len(inp_str) > INPUT_MAX:
                    inp_str = inp_str[:INPUT_MAX] + "\n  ... [truncated]"
                if do_redact:
                    inp_str = redact(inp_str)
                print(f"{ts_prefix}TOOL [{turn}]: {name}")
                print(f"  Input: {inp_str}\n")

            elif bt == "tool_result" and not no_results:
                tool_name = tool_calls.get(block.get("tool_use_id", ""), "?")
                is_error = block.get("is_error", False)
                result_text = _extract_result_text(block.get("content", ""))
                if do_redact:
                    result_text = redact(result_text)
                status = "ERROR" if is_error else "OK"
                print(f"  Result ({tool_name}) [{status}]: "
                      f"{truncate(result_text, max_result)}\n")


def print_compact(session: dict, do_redact: bool = False, show_ts: bool = True) -> None:
    print_metadata(session["metadata"])
    print()
    turn = 0
    for msg in session["messages"]:
        role = msg["role"]
        content = msg["content"]
        ts = format_timestamp(msg.get("timestamp", "")) if show_ts else ""
        prefix = f"[{ts}] " if ts else ""

        if isinstance(content, str) and content.strip():
            line = content.strip().replace("\n", " ")[:120]
            if do_redact:
                line = redact(line)
            print(f"{prefix}{role.upper()}: {line}")
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type", "")
            if bt == "text":
                text = block.get("text", "").strip().replace("\n", " ")[:120]
                if text:
                    if do_redact:
                        text = redact(text)
                    print(f"{prefix}{role.upper()}: {text}")
            elif bt == "tool_use":
                turn += 1
                name = block.get("name", "?")
                summary = _tool_input_summary(name, block.get("input", {}))
                if do_redact:
                    summary = redact(summary)
                print(f"{prefix}TOOL[{turn}] {name}: {summary}")
            elif bt == "tool_result" and block.get("is_error", False):
                result_text = _extract_result_text(block.get("content", ""))[:100]
                if do_redact:
                    result_text = redact(result_text)
                print(f"         ERROR: {result_text}")


def print_errors(session: dict, do_redact: bool = False) -> None:
    meta = session["metadata"]
    print_metadata(meta)
    print()
    calls = extract_tool_calls(session["messages"])
    errors = [c for c in calls if c["is_error"]]
    api_errors = meta.get("api_errors", [])

    if not errors and not api_errors:
        print("No errors found.")
        return

    print(f"ERRORS: {len(errors)} tool, {len(api_errors)} API")
    print()
    for i, call in enumerate(errors, 1):
        ts = format_timestamp(call["timestamp"])
        result = call["result_text"][:500]
        inp_str = json.dumps(call["input"], indent=2)[:500]
        if do_redact:
            result, inp_str = redact(result), redact(inp_str)
        print(f"[{ts}] TOOL ERROR {i}: {call['name']}")
        print(f"  Input: {inp_str}")
        print(f"  Result: {result}\n")
    for i, err in enumerate(api_errors, 1):
        ts = format_timestamp(err["timestamp"])
        text = redact(err["text"]) if do_redact else err["text"]
        print(f"[{ts}] API ERROR {i}: {text}\n")


def print_files(session: dict, do_redact: bool = False) -> None:
    print_metadata(session["metadata"])
    print()
    ops = extract_file_ops(extract_tool_calls(session["messages"]))
    if not ops:
        print("No file operations found.")
        return
    by_op = {}
    for op in ops:
        by_op.setdefault(op["op"], []).append(op)
    for op_type in ("write", "edit", "read", "glob", "grep"):
        items = by_op.get(op_type, [])
        if not items:
            continue
        print(f"{op_type.upper()} ({len(items)}):")
        seen = set()
        for item in items:
            path = redact(item["path"]) if do_redact else item["path"]
            err = " [ERROR]" if item["error"] else ""
            key = (path, err)
            if key in seen and op_type == "read":
                continue
            seen.add(key)
            print(f"  [{item['timestamp']}] {path}{err}")
        print()


def print_summary(session: dict, do_redact: bool = False,
                  subagents: list | None = None, include_meta: bool = False) -> None:
    meta = session["metadata"]
    print_metadata(meta)
    print()
    calls = extract_tool_calls(session["messages"])
    user_msgs = extract_user_messages(session["messages"], include_meta)
    file_ops = extract_file_ops(calls)
    errors = [c for c in calls if c["is_error"]]

    print("USER MESSAGES:")
    for i, m in enumerate(user_msgs, 1):
        text = redact(m["text"]) if do_redact else m["text"]
        print(f"  {i}. {truncate(text, 200)}")
    print()

    print(f"TOOLS CALLED: {len(calls)}")
    for name, count in Counter(c["name"] for c in calls).most_common():
        print(f"  {name}: {count}x")
    if errors:
        print(f"  ERRORS: {len(errors)}")
    if meta.get("api_errors"):
        print(f"  API ERRORS: {len(meta['api_errors'])}")
    print()

    writes = [o for o in file_ops if o["op"] in ("write", "edit")]
    reads = [o for o in file_ops if o["op"] == "read"]
    if writes or reads:
        print("FILE OPERATIONS:")
        if writes:
            uniq = sorted(set(o["path"] for o in writes))
            print(f"  Modified ({len(uniq)}):")
            for p in uniq:
                print(f"    {redact(p) if do_redact else p}")
        if reads:
            uniq = sorted(set(o["path"] for o in reads))
            print(f"  Read ({len(uniq)}):")
            for p in uniq:
                print(f"    {redact(p) if do_redact else p}")
        print()

    if meta.get("compaction_points"):
        print(f"COMPACTIONS: {len(meta['compaction_points'])}")
        for c in meta["compaction_points"]:
            print(f"  after msg {c['after_message']} [{format_timestamp(c['timestamp'])}]")
        print()

    if meta.get("unknown_types"):
        print("UNKNOWN ENTRY TYPES:")
        for t, n in meta["unknown_types"].items():
            print(f"  {t}: {n}")
        print()

    turns = meta.get("turns", [])
    if turns:
        print(f"TURNS: {len(turns)}")
        for i, t in enumerate(turns, 1):
            print(f"  {i}. [{format_timestamp(t.get('timestamp', ''))}] "
                  f"{t['duration_ms'] / 1000:.1f}s  branch={t.get('git_branch', '')}")
        print()

    assistant_chars = sum(
        _content_chars(m["content"]) for m in session["messages"]
        if m["role"] == "assistant"
    )
    print(f"ASSISTANT OUTPUT: {assistant_chars} chars (~{format_tokens(est_tokens(assistant_chars))} tokens)")
    print()

    if subagents:
        print(f"SUBAGENTS: {len(subagents)}")
        for sa in subagents:
            errs = f", {sa['errors']} errors" if sa["errors"] else ""
            print(f"  {sa['type']}: {sa['description']} ({sa['tool_count']} tools{errs})")
        print()


def print_grep(session: dict, pattern: str, do_redact: bool = False,
               show_ts: bool = True) -> None:
    try:
        rx = re.compile(pattern)
    except re.error as e:
        print(f"Invalid regex: {e}")
        return
    n = 0
    for msg in session["messages"]:
        role = msg["role"]
        ts = format_timestamp(msg.get("timestamp", "")) if show_ts else ""
        prefix = f"[{ts}] " if ts else ""
        content = msg["content"]
        blocks = content if isinstance(content, list) else [
            {"type": "text", "text": content}] if isinstance(content, str) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            bt = block.get("type", "")
            if bt == "text":
                hay, tag = block.get("text", ""), role.upper()
            elif bt == "thinking":
                hay, tag = block.get("thinking", ""), "THINKING"
            elif bt == "tool_use":
                hay = _tool_input_summary(block.get("name", "?"), block.get("input", {}))
                tag = f"TOOL:{block.get('name', '?')}"
            elif bt == "tool_result":
                hay, tag = _extract_result_text(block.get("content", "")), "RESULT"
            else:
                continue
            for line in hay.splitlines():
                if rx.search(line):
                    n += 1
                    out = redact(line.strip()) if do_redact else line.strip()
                    print(f"{prefix}{tag}: {out[:200]}")
    if n == 0:
        print(f"No matches for /{pattern}/")


def _json_payload(session: dict, subagents: list | None, include_meta: bool,
                  full: bool) -> dict:
    meta = session["metadata"]
    calls = extract_tool_calls(session["messages"])
    user_msgs = extract_user_messages(session["messages"], include_meta)
    file_ops = extract_file_ops(calls)
    errors = [c for c in calls if c["is_error"]]
    sections = build_sections(session["messages"])

    payload = OrderedDict()
    payload["_schema"] = SCHEMA_VERSION
    payload["session_id"] = meta.get("session_id", "")
    payload["title"] = meta.get("title", "")
    payload["cwd"] = meta.get("cwd", "")
    payload["git_branch"] = meta.get("git_branch", "")
    payload["version"] = meta.get("version", "")
    payload["permission_mode"] = meta.get("permission_mode", "")
    payload["entrypoint"] = meta.get("entrypoint", "")
    payload["mode"] = meta.get("mode", "normal")
    payload["pr_url"] = meta.get("pr_url", "")
    payload["turn_count"] = meta.get("turn_count", 0)
    payload["total_duration_ms"] = meta.get("total_duration_ms", 0)
    payload["usage"] = meta.get("usage", {})
    payload["compaction_points"] = meta.get("compaction_points", [])
    payload["api_errors"] = meta.get("api_errors", [])
    payload["unknown_types"] = meta.get("unknown_types", {})
    payload["user_messages"] = [m["text"][:500] for m in user_msgs]
    payload["tool_counts"] = dict(Counter(c["name"] for c in calls).most_common())
    payload["error_count"] = len(errors)
    payload["errors"] = [
        {"name": c["name"], "input": c["input"], "result": c["result_text"][:500],
         "timestamp": format_timestamp(c["timestamp"])}
        for c in errors
    ]
    payload["files_modified"] = sorted(set(
        o["path"] for o in file_ops if o["op"] in ("write", "edit")))
    payload["files_read"] = sorted(set(
        o["path"] for o in file_ops if o["op"] == "read"))
    payload["sections"] = [
        {"id": s["id"], "prompt": headline(s["prompt"], 120),
         "tools": dict(s["tools"]), "errors": s["error_count"],
         "est_tokens": est_tokens(s["chars"]),
         "msg_range": [s["msg_start"], s["msg_end"]]}
        for s in sections
    ]
    payload["subagents"] = [
        {"type": sa["type"], "description": sa["description"],
         "tool_count": sa["tool_count"], "errors": sa["errors"]}
        for sa in (subagents or [])
    ]
    if full:
        payload["tool_calls"] = [
            {"name": c["name"],
             "input_summary": _tool_input_summary(c["name"], c["input"]),
             "is_error": c["is_error"],
             "timestamp": format_timestamp(c["timestamp"])}
            for c in calls
        ]
    return payload


def print_json(session: dict, do_redact: bool = False, subagents: list | None = None,
               include_meta: bool = False, full: bool = False,
               pretty: bool = False) -> None:
    payload = _json_payload(session, subagents, include_meta, full)
    if pretty:
        text = json.dumps(payload, indent=2)
    else:
        text = json.dumps(payload, separators=(",", ":"))
    if do_redact:
        text = redact(text)
    print(text)


def _capture(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue()


def print_estimate(session: dict, sections: list, subagents: list | None,
                   include_meta: bool) -> None:
    modes = {
        "--map": lambda: print_map(session, sections),
        "--summary": lambda: print_summary(session, subagents=subagents,
                                            include_meta=include_meta),
        "--compact": lambda: print_compact(session),
        "--errors": lambda: print_errors(session),
        "--files": lambda: print_files(session),
        "--json": lambda: print_json(session, include_meta=include_meta),
        "--json --full": lambda: print_json(session, include_meta=include_meta, full=True),
        "--full": lambda: print_transcript(session),
    }
    print(f"# estimate  session {session['metadata'].get('session_id', '?')[:8]}"
          f"  ({len(sections)} turns)")
    print(f"{'MODE':<16} {'BYTES':>10} {'~TOKENS':>10}")
    print("-" * 40)
    for name, fn in modes.items():
        out = _capture(fn)
        b = len(out.encode("utf-8"))
        print(f"{name:<16} {b:>10} {est_tokens(b):>10}")


# -- CLI -----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude_session.py",
        description="Parse Claude Code session JSONL files (progressive disclosure).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("session", nargs="?", help="session id or path to .jsonl")
    p.add_argument("--list", nargs="?", const="", default=None, metavar="FILTER",
                   help="list available sessions (optional project filter)")
    p.add_argument("--project", help="disambiguate session-id lookup to one project")

    # Modes
    p.add_argument("--map", action="store_true", help="session outline (default for large sessions)")
    p.add_argument("--turn", metavar="ID", help="expand one section, e.g. T2 or 2")
    p.add_argument("--section", metavar="A:B", help="expand a message range")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--compact", action="store_true")
    p.add_argument("--errors", action="store_true")
    p.add_argument("--files", action="store_true")
    p.add_argument("--tools-only", action="store_true")
    p.add_argument("--full", action="store_true", help="full transcript / full JSON detail")
    p.add_argument("--estimate", action="store_true")
    p.add_argument("--grep", metavar="REGEX")

    # Flags
    p.add_argument("--redact", action="store_true")
    p.add_argument("--thinking", action="store_true")
    p.add_argument("--no-results", action="store_true")
    p.add_argument("--no-timestamps", action="store_true")
    p.add_argument("--expand", action="store_true")
    p.add_argument("--subagents", action="store_true")
    p.add_argument("--include-meta", action="store_true")
    p.add_argument("--pretty", action="store_true")
    p.add_argument("--last", type=int, metavar="N")
    p.add_argument("--max-result", type=int, default=DEFAULT_MAX_RESULT)
    p.add_argument("--max-text", type=int, default=DEFAULT_MAX_TEXT)
    return p


def _resolve_range(spec: str, sections: list, n_messages: int) -> tuple | None:
    """Turn a --turn id or --section A:B into a 1-based inclusive message range."""
    spec = spec.strip()
    if ":" in spec or "-" in spec:
        sep = ":" if ":" in spec else "-"
        a, _, b = spec.partition(sep)
        try:
            lo = int(a) if a else 1
            hi = int(b) if b else n_messages
        except ValueError:
            return None
        return (max(1, lo), min(n_messages, hi))
    # Section id: "T2" or "2"
    key = spec.upper()
    for sec in sections:
        if sec["id"] == key or str(sec["n"]) == spec or sec["id"] == "T" + spec:
            return (sec["msg_start"], sec["msg_end"])
    return None


def _range_for_last(sections: list, last: int) -> tuple | None:
    if not sections:
        return None
    chosen = sections[-last:]
    return (chosen[0]["msg_start"], chosen[-1]["msg_end"])


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.list is not None:
        list_sessions(args.list)
        return

    if not args.session:
        parser.print_help()
        sys.exit(0)

    try:
        path = find_session_file(args.session, args.project)
    except (FileNotFoundError, ValueError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    session = parse_session(path, expand=args.expand)
    sections = build_sections(session["messages"])
    n_messages = len(session["messages"])

    subagents = None
    if args.subagents:
        session_dir = find_session_dir(path)
        if session_dir:
            subagents = parse_subagents(session_dir)

    show_ts = not args.no_timestamps

    # Drill-downs take precedence.
    if args.turn or args.section:
        spec = args.turn or args.section
        rng = _resolve_range(spec, sections, n_messages)
        if not rng:
            print(f"No such section/range: {spec}", file=sys.stderr)
            sys.exit(1)
        print_transcript(session, tools_only=args.tools_only,
                         show_thinking=args.thinking, no_results=args.no_results,
                         do_redact=args.redact, show_ts=show_ts,
                         max_text=args.max_text, max_result=args.max_result,
                         msg_range=rng)
        return

    if args.estimate:
        print_estimate(session, sections, subagents, args.include_meta)
        return
    if args.grep:
        print_grep(session, args.grep, do_redact=args.redact, show_ts=show_ts)
        return
    if args.json:
        print_json(session, do_redact=args.redact, subagents=subagents,
                   include_meta=args.include_meta, full=args.full, pretty=args.pretty)
        return
    if args.summary:
        print_summary(session, do_redact=args.redact, subagents=subagents,
                      include_meta=args.include_meta)
        return
    if args.errors:
        print_errors(session, do_redact=args.redact)
        return
    if args.files:
        print_files(session, do_redact=args.redact)
        return
    if args.compact:
        print_compact(session, do_redact=args.redact, show_ts=show_ts)
        return

    last_range = _range_for_last(sections, args.last) if args.last else None

    if args.full or args.tools_only:
        print_transcript(session, tools_only=args.tools_only,
                         show_thinking=args.thinking, no_results=args.no_results,
                         do_redact=args.redact, show_ts=show_ts,
                         max_text=args.max_text, max_result=args.max_result,
                         msg_range=last_range)
        return

    # Default: map — unless the whole session is small, then print it whole.
    total_chars = sum(_content_chars(m["content"]) for m in session["messages"])
    if not args.map and est_tokens(total_chars) < AUTO_WHOLE_TOKEN_THRESHOLD:
        print_transcript(session, show_thinking=args.thinking,
                         no_results=args.no_results, do_redact=args.redact,
                         show_ts=show_ts, max_text=args.max_text,
                         max_result=args.max_result, msg_range=last_range)
        return

    print_map(session, sections, last=args.last, do_redact=args.redact)


if __name__ == "__main__":
    main()
