#!/usr/bin/env python3
"""tf.py — TreeFlow state manager. Deterministic coordination for the treeflow orchestrator.

All output is compact single-line JSON for token efficiency.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

REGISTRY_FILE = "registry.json"
BD_LIST_LIMIT = "500"

# Worker status constants
STATUS_ACTIVE = "active"
STATUS_IDLE = "idle"
STATUS_RETIRED = "retired"

# Notification status constants
NOTIF_PENDING = "pending"
NOTIF_RECEIVED = "received"
NOTIF_RECONCILED = "reconciled"

# Context threshold constants
CTX_HIGH = 90
CTX_LOW = 40

# Common locations where bd might be installed
_BD_SEARCH_PATHS = [
    Path.home() / ".local" / "bin" / "bd",
    Path("/usr/local/bin/bd"),
    Path.home() / "go" / "bin" / "bd",
    Path.home() / ".cargo" / "bin" / "bd",
]


def _resolve_bd() -> str:
    """Resolve bd binary path. Tries shutil.which, then common install locations."""
    found = shutil.which("bd")
    if found:
        return found
    for p in _BD_SEARCH_PATHS:
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return "bd"


def _bd(registry_path: Optional[Path] = None) -> str:
    """Get bd binary path from registry, falling back to _resolve_bd().

    IMPORTANT: Do NOT validate bd_path with Path.exists() or shutil.which().
    The orchestrator's sandbox PATH differs from the worker's. macOS sandbox
    blocks stat() on paths like ~/.local/bin/ even though execve works fine.
    Trust the stored path — the worker will fail fast on first invocation if wrong.
    """
    if registry_path:
        try:
            with open(registry_path) as f:
                reg = json.load(f)
            bd_path = reg.get("bd_path", "")
            if bd_path:
                return bd_path
        except (json.JSONDecodeError, OSError) as e:
            sys.stderr.write(f"tf.py warning: reading bd_path from registry: {e}\n")
    return _resolve_bd()


def _bd_cmd(subcmd: str, registry_path: Optional[Path] = None) -> str:
    """Build a bd command using the resolved binary path."""
    return f"{_bd(registry_path)} {subcmd}"


def _beads_dir() -> Path:
    """Find .beads/ directory by walking up from cwd."""
    d = Path.cwd()
    while d != d.parent:
        bd = d / ".beads"
        if bd.is_dir():
            return bd
        d = d.parent
    return Path.cwd() / ".beads"


def _registry_path() -> Path:
    bd = _beads_dir()
    active_plan_file = bd / "active-plan"
    if active_plan_file.exists():
        plan_name = active_plan_file.read_text().strip()
        if plan_name:
            rp = bd / f"context-{plan_name}" / REGISTRY_FILE
            if rp.exists():
                return rp
            ctx = bd / f"context-{plan_name}"
            if ctx.is_dir():
                return ctx / REGISTRY_FILE
            sys.exit(f'{{"error":"active-plan points to \'{plan_name}\' but context-{plan_name}/ does not exist. Run tf.py init {plan_name}."}}')
    sys.exit('{"error":"no .beads/active-plan found. Run tf.py init <plan-name> first."}')


def _load_registry(path: Optional[Path] = None) -> tuple:
    p = path or _registry_path()
    if not p.exists():
        sys.exit(f'{{"error":"registry not found at {p}"}}')
    with open(p) as f:
        return json.load(f), p


def _save_registry(data: dict, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(s: str) -> datetime:
    """Parse an ISO timestamp string (as produced by _now()) into a tz-aware datetime."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _run(cmd: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def _out(obj: dict) -> None:
    print(json.dumps(obj, separators=(",", ":")))


def _fetch_bead(bd: str, bid: str) -> Optional[dict]:
    """Run `bd show {bid} --json`, parse JSON, unwrap list-vs-dict. Return bead dict or None."""
    r = _run(f"{bd} show {bid} --json")
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
        bead = data[0] if isinstance(data, list) else data
        return bead if isinstance(bead, dict) else None
    except (json.JSONDecodeError, IndexError):
        return None


def _write_context_update(bd: str, ctx: Path, bead: Optional[dict], bead_id: str, worker: str, files: str, summary: str, gotcha: str = "") -> None:
    """Write task completion context to epic file, task-summaries.md, and optionally gotcha to worker-context.md."""
    title = bead.get("title", bead.get("name", bead_id)) if bead else bead_id
    epic_slug = ""
    parent_id = bead.get("parent", "") if bead else ""
    if parent_id:
        parent = _fetch_bead(bd, parent_id)
        if parent:
            epic_slug = _slugify(parent.get("title", parent.get("name", parent_id)))
        else:
            epic_slug = _slugify(parent_id)

    summary_block = f"### BD-{bead_id}: {title}\n**Worker**: {worker} | **Files**: {files}\n{summary}"
    if epic_slug:
        epic_file = ctx / f"epic-{epic_slug}.md"
        _append_to_section(epic_file, "## Completed Tasks", summary_block)
    summaries_file = ctx / "task-summaries.md"
    _append_to_section(summaries_file, "## Completed Tasks", summary_block)

    if gotcha:
        wc = ctx / "worker-context.md"
        _append_to_section(wc, "## Known Gotchas", f"- {gotcha}")


def _update_heartbeat(reg: dict, rp: Path, note: str, worker_name: Optional[str] = None) -> None:
    """Update heartbeat for a worker. Called implicitly by worker commands."""
    if not worker_name:
        worker_name = os.environ.get("CLAUDE_AGENT_NAME", "")
    if not worker_name or worker_name not in reg.get("workers", {}):
        return
    w = reg["workers"][worker_name]
    now = _now()
    w["last_heartbeat"] = now
    w["last_heartbeat_note"] = note
    if "heartbeat_history" not in w:
        w["heartbeat_history"] = []
    w["heartbeat_history"].append({"ts": now, "note": note})
    if len(w["heartbeat_history"]) > 20:
        w["heartbeat_history"] = w["heartbeat_history"][-20:]
    _save_registry(reg, rp)


def _is_stalled(worker: dict, threshold_mins: int = 20) -> bool:
    """Check if an active worker has gone silent beyond the threshold."""
    if worker.get("status") != STATUS_ACTIVE:
        return False
    last_hb = worker.get("last_heartbeat") or worker.get("dispatched_at")
    if not last_hb:
        return False
    now = datetime.now(timezone.utc)
    # If worker has a deadline, check both deadline AND silence — a worker past deadline
    # but still heartbeating is slow, not stalled.
    last_dt = _parse_ts(last_hb)
    elapsed = (now - last_dt).total_seconds() / 60
    expected = worker.get("expected_completion_at")
    if expected and now > _parse_ts(expected):
        return elapsed > threshold_mins
    elif expected:
        # Deadline not yet passed, but flag if silent for 2x threshold
        return elapsed > threshold_mins * 2
    return elapsed > threshold_mins


def _idle_minutes(worker: dict) -> Optional[float]:
    """Minutes since a worker became idle. None if not idle."""
    idle_since = worker.get("idle_since")
    if not idle_since or worker.get("status") != STATUS_IDLE:
        return None
    return (datetime.now(timezone.utc) - _parse_ts(idle_since)).total_seconds() / 60


def _stalled_info(wname: str, w: dict) -> dict:
    """Build a compact stalled-worker dict for output."""
    last_hb = w.get("last_heartbeat", w.get("dispatched_at", "?"))
    info: dict = {
        "worker": wname,
        "bead": w.get("bead", "?"),
        "last_hb": last_hb,
        "note": w.get("last_heartbeat_note", ""),
    }
    if last_hb and last_hb != "?":
        try:
            elapsed = (datetime.now(timezone.utc) - _parse_ts(last_hb)).total_seconds() / 60
            info["silent_mins"] = round(elapsed)
        except (ValueError, TypeError, KeyError) as e:
            sys.stderr.write(f"tf.py warning: stalled_info timestamp parse: {e}\n")
    return info


# ── Subcommands ──────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize context directory and registry."""
    bd = _beads_dir()
    ctx = bd / f"context-{args.plan_name}"
    ctx.mkdir(parents=True, exist_ok=True)

    # Write active-plan pointer so all subsequent commands resolve deterministically
    (bd / "active-plan").write_text(args.plan_name)

    reg = ctx / REGISTRY_FILE
    if reg.exists():
        _out({"ok": True, "msg": "already exists", "path": str(ctx)})
        return

    bd_path = getattr(args, "bd_path", "") or _resolve_bd()
    worker_model = getattr(args, "worker_model", "") or ""
    data = {
        "plan_name": args.plan_name,
        "bd_path": bd_path,
        "worker_model": worker_model,
        "session_id": _now(),
        "settings": {
            "stall_threshold_mins": 20,
            "idle_timeout_mins": getattr(args, "idle_timeout", 0) or 8,
        },
        "workers": {},
        "routing": {},
        "phases": {},
    }
    _save_registry(data, reg)

    # Write worker-context.md stub from template if not already present
    wc_path = ctx / "worker-context.md"
    if not wc_path.exists():
        template_path = Path(__file__).resolve().parent / "WORKER-CONTEXT-TEMPLATE.md"
        if template_path.exists():
            wc_path.write_text(template_path.read_text())
        else:
            wc_path.write_text("# Worker Context\n\n## Overview\n\n## Tech Stack\n\n## Known Gotchas\n")

    # Copy tf.py to .beads/ for project-local access
    src = Path(__file__).resolve()
    dst = bd / "tf.py"
    if src != dst:
        shutil.copy2(src, dst)

    # Ensure .beads/ is gitignored to prevent git stash -u from stashing context
    repo_root = bd.parent
    gitignore = repo_root / ".gitignore"
    marker = ".beads/"
    if gitignore.exists():
        content = gitignore.read_text()
        if marker not in content.splitlines():
            with open(gitignore, "a") as f:
                f.write(f"\n# TreeFlow context (auto-added)\n{marker}\n")
    else:
        gitignore.write_text(f"# TreeFlow context (auto-added)\n{marker}\n")

    _out({"ok": True, "path": str(ctx), "bd_path": bd_path, "worker_model": worker_model})


def cmd_dispatch(args: argparse.Namespace) -> None:
    """Record worker dispatch — sets active + notification pending."""
    reg, rp = _load_registry()
    now = _now()

    r_ut = _run("git ls-files --others --exclude-standard")
    pre_untracked = [f for f in r_ut.stdout.strip().split("\n") if f]

    # Support comma-separated bead IDs
    bead_ids = [b.strip() for b in args.bead_id.split(",") if b.strip()]
    bead_value = bead_ids if len(bead_ids) > 1 else bead_ids[0] if bead_ids else args.bead_id

    reg["workers"][args.worker] = {
        "status": STATUS_ACTIVE,
        "skill": args.skill,
        "context_pct": reg.get("workers", {}).get(args.worker, {}).get("context_pct", 0),
        "bead": bead_value,
        "notification": NOTIF_PENDING,
        "dispatched_at": now,
        "dispatch_sha": _run("git rev-parse HEAD").stdout.strip(),
        "dispatch_untracked": pre_untracked,
        "last_heartbeat": now,
        "last_heartbeat_note": None,
        "heartbeat_history": [],
        "output_file": getattr(args, "output_file", "") or "",
        "agent_id": getattr(args, "agent_id", "") or "",
        "spawned_session": reg.get("session_id", ""),
    }
    _save_registry(reg, rp)
    _out({"ok": True, "worker": args.worker, "bead": bead_value})


def cmd_worker_close(args: argparse.Namespace) -> None:
    """Worker calls this to validate and close a bead. Returns ok/errors."""
    rp = _registry_path()
    bd = _bd(rp)
    errors = []
    warnings = []

    # 1. Check uncommitted changes — diff against dispatch SHA if available
    dispatch_sha = ""
    pre_untracked: set[str] = set()
    worker_name = os.environ.get("CLAUDE_AGENT_NAME", "")
    try:
        reg, _ = _load_registry(rp)
        if worker_name and worker_name in reg.get("workers", {}):
            w_entry = reg["workers"][worker_name]
            dispatch_sha = w_entry.get("dispatch_sha", "")
            pre_untracked = set(w_entry.get("dispatch_untracked", []))
    except (OSError, json.JSONDecodeError, KeyError) as e:
        sys.stderr.write(f"tf.py warning: registry read in worker-close: {e}\n")

    files = [f.strip() for f in args.files.split(",")] if args.files else []

    if dispatch_sha:
        r_wt = _run(f"git diff {dispatch_sha} --name-only")
        r_ci = _run(f"git diff {dispatch_sha} HEAD --name-only")
        r_ut = _run("git ls-files --others --exclude-standard")

        wt_changed = {f for f in r_wt.stdout.strip().split("\n") if f and not f.startswith(".beads/")}
        committed = {f for f in r_ci.stdout.strip().split("\n") if f}
        untracked = {f for f in r_ut.stdout.strip().split("\n") if f and not f.startswith(".beads/")}
        new_untracked = untracked - pre_untracked

        uncommitted = (wt_changed - committed) | new_untracked
        if uncommitted:
            errors.append(f"uncommitted: {','.join(sorted(uncommitted)[:5])}")
    elif files:
        for f in files:
            r = _run(f"git diff --name-only -- {f}")
            if r.stdout.strip():
                errors.append(f"uncommitted changes: {f}")
            r2 = _run(f"git diff --cached --name-only -- {f}")
            if r2.stdout.strip():
                errors.append(f"staged but uncommitted: {f}")
    else:
        r = _run("git status --porcelain")
        modified = [
            line[3:] for line in r.stdout.strip().split("\n")
            if line and line[0] in ("M", "A", "?") and not line[3:].startswith(".beads/")
        ]
        if modified:
            errors.append(f"uncommitted: {','.join(modified[:5])}")

    if errors:
        _out({"ok": False, "errors": errors})
        return

    # Check that target files were actually modified (unless --force)
    force = getattr(args, "force", False)
    if files and dispatch_sha and not force:
        r_diff = _run(f"git diff {dispatch_sha} HEAD --name-only")
        changed_files = {f.strip() for f in r_diff.stdout.strip().split("\n") if f.strip()}
        unmodified = [f for f in files if f not in changed_files]
        if unmodified and len(unmodified) == len(files):
            warnings.append(f"no target files were modified: {','.join(unmodified[:5])}. If intentional, re-run with --force")

    # Scan for dead-code markers (warnings only — does not block close)
    if files:
        pattern = r"#\[allow\(dead_code\)\]|# noqa: F841|// @ts-ignore|\bTODO\b|\bFIXME\b|\bHACK\b"
        file_args = " ".join(shlex.quote(f) for f in files)
        r = _run(f"grep -nE '{pattern}' {file_args}")
        if r.stdout.strip():
            for line in r.stdout.strip().split("\n")[:10]:
                warnings.append(f"dead-code marker: {line.strip()}")

    # Warn if summary doesn't mention acceptance criteria status
    if args.summary and "AC:" not in args.summary and "acceptance" not in args.summary.lower():
        warnings.append("summary missing AC status — include 'AC: <status>' to confirm all criteria met")

    # 2. Check last commit message for task/bead number anti-pattern
    r = _run("git log -1 --pretty=%s")
    msg = r.stdout.strip()
    if msg and re.search(r'\bTask\s+\d+', msg, re.IGNORECASE):
        _out({"ok": False, "errors": [f"commit message contains task number: '{msg}'. Amend to remove 'Task N:' prefix"]})
        return

    # Support --beads for batch close (multiple bead IDs)
    extra_beads = getattr(args, "beads", "")
    if extra_beads:
        bead_ids = [b.strip() for b in extra_beads.split(",") if b.strip()]
    else:
        bead_ids = [args.bead_id] if args.bead_id else []

    if not bead_ids:
        _out({"ok": False, "errors": ["no bead IDs provided"]})
        return

    # 3–6. Close each bead
    closed_beads = []
    close_errors = []
    for bid in bead_ids:
        # 3. Check bead is in_progress
        bead = _fetch_bead(bd, bid)
        if bead is None:
            close_errors.append(f"{bid}: bd show failed or returned invalid JSON")
            continue

        status = bead.get("status", "")
        if status == "closed":
            closed_beads.append({"id": bid, "already": True})
            continue
        if status != "in_progress":
            close_errors.append(f"{bid}: status is '{status}', expected 'in_progress'")
            continue

        # 4. Build close reason
        summary = args.summary or "completed"
        files_str = args.files or ""
        reason = f"SUMMARY: {summary}. FILES: {files_str}. CONTEXT: {args.context_pct}%"

        # 5. Close bead
        r = _run(f'{bd} close {bid} --reason {shlex.quote(reason)} --json')
        if r.returncode != 0:
            close_errors.append(f"{bid}: bd close failed: {r.stderr.strip()[:100]}")
            continue

        # 6. Verify close
        bead = _fetch_bead(bd, bid) or {}

        if bead.get("status") != "closed":
            _run(f'{bd} close {bid} --reason {shlex.quote(reason)} --json')
            bead = _fetch_bead(bd, bid) or {}
            if bead.get("status") != "closed":
                close_errors.append(f"{bid}: still not closed after retry")
                continue

        closed_beads.append({"id": bid})

    try:
        reg, reg_path = _load_registry(rp)
        _update_heartbeat(reg, reg_path, f"closed {','.join(b['id'] for b in closed_beads)}")
        if worker_name and worker_name in reg.get("workers", {}):
            reg["workers"][worker_name]["closed_self"] = True
            _save_registry(reg, reg_path)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        sys.stderr.write(f"tf.py warning: registry update in worker-close: {e}\n")

    if close_errors and not closed_beads:
        _out({"ok": False, "errors": close_errors})
        return

    result: dict = {"ok": True, "status": "closed", "context_pct": args.context_pct, "closed": [b["id"] for b in closed_beads]}
    if len(bead_ids) == 1 and closed_beads and closed_beads[0].get("already"):
        result["already"] = True
    if close_errors:
        result["errors"] = close_errors
    if warnings:
        result["warnings"] = warnings
    _out(result)


def cmd_claim(args: argparse.Namespace) -> None:
    """Worker claims a bead — wraps bd update --status in_progress.
    Also updates the registry bead reference so the orchestrator sees the current active bead
    (important for batch workers handling multiple sequential tasks).
    """
    rp = _registry_path()
    bd = _bd(rp)
    r = _run(f"{bd} update {args.bead_id} --status in_progress --json")
    if r.returncode != 0:
        _out({"ok": False, "error": f"bd update failed: {r.stderr.strip()[:100]}"})
        return

    # Update registry: bead reference + heartbeat
    try:
        reg, reg_path = _load_registry(rp)
        worker_name = os.environ.get("CLAUDE_AGENT_NAME", "")
        if worker_name and worker_name in reg.get("workers", {}):
            reg["workers"][worker_name]["bead"] = args.bead_id
            expected_mins = getattr(args, "expected_mins", 0)
            if expected_mins:
                deadline = datetime.now(timezone.utc) + timedelta(minutes=expected_mins)
                reg["workers"][worker_name]["expected_completion_at"] = deadline.strftime("%Y-%m-%dT%H:%M:%SZ")
            _update_heartbeat(reg, reg_path, f"claimed {args.bead_id}", worker_name)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        sys.stderr.write(f"tf.py warning: registry update in claim: {e}\n")

    _out({"ok": True, "bead": args.bead_id, "status": "in_progress"})


def cmd_block(args: argparse.Namespace) -> None:
    """Worker marks bead blocked and creates a question task."""
    rp = _registry_path()
    bd = _bd(rp)

    # Mark bead blocked
    r = _run(f"{bd} update {args.bead_id} --status blocked --json")
    if r.returncode != 0:
        _out({"ok": False, "error": f"bd update failed: {r.stderr.strip()[:100]}"})
        return

    # Create question task
    q_title = shlex.quote(f"Question: {args.question}")
    q_context = shlex.quote(args.context if args.context else args.question)
    r = _run(f'{bd} create {q_title} -t task -p 1 --deps {shlex.quote(args.bead_id)} -d {q_context} --json')
    if r.returncode != 0:
        _out({"ok": False, "error": f"bd create failed: {r.stderr.strip()[:100]}", "bead_blocked": True})
        return

    try:
        created = json.loads(r.stdout)
        q_id = created.get("id", "?")
    except json.JSONDecodeError:
        q_id = "?"

    try:
        reg, reg_path = _load_registry(rp)
        _update_heartbeat(reg, reg_path, f"blocked — {args.question[:50]}")
    except (OSError, json.JSONDecodeError, KeyError) as e:
        sys.stderr.write(f"tf.py warning: registry update in block: {e}\n")

    _out({"ok": True, "bead": args.bead_id, "status": "blocked", "question_bead": q_id})


def cmd_discover(args: argparse.Namespace) -> None:
    """Worker creates a discovered-work bead."""
    rp = _registry_path()
    bd = _bd(rp)
    d_title = shlex.quote(f"Found: {args.title}")
    d_desc = shlex.quote(args.description if args.description else args.title)
    r = _run(f'{bd} create {d_title} -t task -p 2 --deps {shlex.quote(f"discovered-from:{args.bead_id}")} -d {d_desc} --json')
    if r.returncode != 0:
        _out({"ok": False, "error": f"bd create failed: {r.stderr.strip()[:100]}"})
        return
    try:
        created = json.loads(r.stdout)
        new_id = created.get("id", "?")
    except json.JSONDecodeError:
        new_id = "?"

    try:
        reg, reg_path = _load_registry(rp)
        _update_heartbeat(reg, reg_path, "discovered follow-up")
    except (OSError, json.JSONDecodeError, KeyError) as e:
        sys.stderr.write(f"tf.py warning: registry update in discover: {e}\n")

    _out({"ok": True, "bead": new_id, "source": args.bead_id})


def cmd_heartbeat(args: argparse.Namespace) -> None:
    """Explicit heartbeat for long-running operations."""
    reg, rp = _load_registry()
    worker_name = os.environ.get("CLAUDE_AGENT_NAME", "")
    if not worker_name:
        _out({"ok": False, "error": "CLAUDE_AGENT_NAME not set"})
        return
    if worker_name not in reg.get("workers", {}):
        _out({"ok": False, "error": f"worker '{worker_name}' not in registry"})
        return
    note = args.note or f"heartbeat on {args.bead_id}"
    _update_heartbeat(reg, rp, note, worker_name)
    _out({"ok": True, "worker": worker_name, "note": note})


def cmd_stalled(args: argparse.Namespace) -> None:
    """Return list of stalled active workers."""
    reg, _ = _load_registry()
    threshold = args.threshold_mins
    if not threshold:
        threshold = reg.get("settings", {}).get("stall_threshold_mins", 20)
    stalled = []
    for wname, w in reg.get("workers", {}).items():
        if _is_stalled(w, threshold):
            info = _stalled_info(wname, w)
            info["skill"] = w.get("skill", "?")
            stalled.append(info)
    _out({"stalled": stalled, "threshold_mins": threshold})


def cmd_bd_path(args: argparse.Namespace) -> None:
    """Print resolved bd binary path for diagnostics."""
    rp = _registry_path()
    _out({"bd_path": _bd(rp)})


def cmd_notify(args: argparse.Namespace) -> None:
    """Orchestrator calls on task-notification. Updates registry atomically."""
    reg, rp = _load_registry()
    now = _now()

    # Resolve bead_id: use provided value, or look up from registry
    if not args.bead_id:
        w_entry = reg["workers"].get(args.worker)
        if not w_entry or not w_entry.get("bead"):
            _out({"ok": False, "error": f"no bead recorded for worker '{args.worker}'"})
            return
        args.bead_id = w_entry["bead"]

    worker = reg["workers"].get(args.worker)
    agent_id = getattr(args, "agent_id", "") or ""
    if not worker:
        # Worker not in registry — add it
        entry: dict = {
            "status": STATUS_IDLE,
            "skill": args.skill or "unknown",
            "context_pct": args.context_pct,
            "bead": args.bead_id,
            "notification": NOTIF_RECEIVED,
            "idle_since": now,
            "summary": (args.summary or "")[:200],
        }
        if agent_id:
            entry["agent_id"] = agent_id
        reg["workers"][args.worker] = entry
        _save_registry(reg, rp)
        _out({"ok": True, "worker": args.worker})
        return

    late = worker.get("notification") == NOTIF_RECEIVED

    # Auto-retire workers at 90%+ context — they can never be meaningfully reused
    auto_retired = args.context_pct >= CTX_HIGH
    worker["status"] = STATUS_RETIRED if auto_retired else STATUS_IDLE
    worker["context_pct"] = args.context_pct
    worker["notification"] = NOTIF_RECONCILED if late else NOTIF_RECEIVED
    if auto_retired:
        worker["retired_at"] = now
    else:
        worker["idle_since"] = now
    worker["bead"] = args.bead_id
    if agent_id:
        worker["agent_id"] = agent_id
    if args.summary:
        worker["summary"] = args.summary[:200]

    _save_registry(reg, rp)
    result: dict = {"ok": True, "worker": args.worker, "ctx": args.context_pct}
    if late:
        result["late"] = True
    if auto_retired:
        result["auto_retired"] = True

    # Include bead status so orchestrator doesn't need a separate bd show call
    result["bead_status"] = "unknown"
    bd = _bd(rp)
    bead = _fetch_bead(bd, args.bead_id) or {}
    result["bead_status"] = bead.get("status", "unknown")

    # Auto-generate summary from bead title if --summary was omitted
    if not args.summary and isinstance(bead, dict):
        title = bead.get("title", bead.get("name", ""))
        if title:
            args.summary = f"Completed: {title}"
            worker["summary"] = args.summary[:200]
            _save_registry(reg, rp)

    # Auto-close only if worker called worker-close (closed_self flag).
    # If worker finished without calling worker-close, signal the orchestrator
    # to inspect rather than silently masking a possible failure.
    if result["bead_status"] == "in_progress":
        closed_self = worker.get("closed_self", False) if worker else False
        if closed_self:
            cr = _run(f'{bd} close {args.bead_id} --reason "auto-closed on worker notification" --force --json')
            if cr.returncode == 0:
                result["bead_status"] = "closed"
                result["auto_closed"] = True
        else:
            result["auto_closed"] = False
            result["action_needed"] = "worker did not call worker-close — inspect git diff and either close manually or redispatch"

    # Auto-close parent epic if all siblings are closed
    if result["bead_status"] == "closed" and isinstance(bead, dict):
        parent_id = bead.get("parent", "")
        if parent_id:
            r_children = _run(f"{bd} list --parent {parent_id} --limit {BD_LIST_LIMIT} --json")
            try:
                children = json.loads(r_children.stdout)
                if isinstance(children, dict):
                    children = children.get("issues", [])
                all_closed = all(c.get("status") == "closed" for c in children) if children else False
                if all_closed:
                    _run(f'{bd} close {parent_id} --reason "all children closed" --json')
                    result["parent_auto_closed"] = parent_id
            except (json.JSONDecodeError, IndexError) as e:
                sys.stderr.write(f"tf.py warning: auto-close parent epic: {e}\n")

    # Inline context update when --files provided
    files_str = getattr(args, "files", "") or ""
    gotcha_str = getattr(args, "gotcha", "") or ""
    if files_str:
        _write_context_update(bd, _context_dir(), bead, args.bead_id, args.worker, files_str, (args.summary or "")[:200], gotcha_str)

    _out(result)


def cmd_batch_notify(args: argparse.Namespace) -> None:
    """Process multiple worker:bead completion notifications in one call."""
    reg, rp = _load_registry()
    now = _now()
    bd_bin = _bd(rp)
    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    ctx_pct = args.context_pct
    results = []

    for pair in pairs:
        if ":" not in pair:
            results.append({"pair": pair, "ok": False, "error": "expected worker:bead_id format"})
            continue
        worker_name, bead_id = pair.split(":", 1)
        worker = reg["workers"].get(worker_name)
        if not worker:
            reg["workers"][worker_name] = {
                "status": STATUS_IDLE,
                "skill": "unknown",
                "context_pct": ctx_pct,
                "bead": bead_id,
                "notification": NOTIF_RECEIVED,
                "idle_since": now,
            }
            results.append({"worker": worker_name, "bead": bead_id, "ok": True})
            continue

        auto_retired = ctx_pct >= CTX_HIGH
        worker["status"] = STATUS_RETIRED if auto_retired else STATUS_IDLE
        worker["context_pct"] = ctx_pct
        worker["notification"] = NOTIF_RECEIVED
        if auto_retired:
            worker["retired_at"] = now
        else:
            worker["idle_since"] = now
        worker["bead"] = bead_id
        entry: dict = {"worker": worker_name, "bead": bead_id, "ok": True, "ctx": ctx_pct}
        if auto_retired:
            entry["auto_retired"] = True
        results.append(entry)

    _save_registry(reg, rp)

    # Inline context update for last bead when --files provided
    files_str = getattr(args, "files", "") or ""
    gotcha_str = getattr(args, "gotcha", "") or ""
    if files_str and pairs:
        last_pair = pairs[-1]
        if ":" in last_pair:
            last_worker, last_bead = last_pair.split(":", 1)
            bead_obj = _fetch_bead(bd_bin, last_bead)
            summary_text = getattr(args, "summary", "") or ""
            _write_context_update(bd_bin, _context_dir(), bead_obj, last_bead, last_worker, files_str, summary_text, gotcha_str)

    _out({"ok": True, "results": results})


def cmd_phase_gate(args: argparse.Namespace) -> None:
    """Check if a phase/epic is fully complete: all beads closed + all notifications received."""
    reg, rp = _load_registry()
    bd = _bd(rp)
    blocking = []

    # Get all child beads of the epic
    r = _run(f"{bd} list --parent {args.epic_id} --limit {BD_LIST_LIMIT} --json")
    if r.returncode != 0:
        # Fallback: list all open
        r = _run(f"{bd} list --limit {BD_LIST_LIMIT} --json")

    stdout = r.stdout.strip()
    if not stdout:
        _out({"pass": False, "error": "bd list returned empty output"})
        return

    # Skip non-JSON prefix (bd may print status lines before JSON)
    json_start = next((i for i, ch in enumerate(stdout) if ch in ("[", "{")), -1)
    if json_start < 0:
        _out({"pass": False, "error": "no JSON found in bd list output"})
        return

    try:
        beads = json.loads(stdout[json_start:])
    except json.JSONDecodeError:
        _out({"pass": False, "error": "failed to parse bd list"})
        return

    if isinstance(beads, dict):
        beads = beads.get("issues", [])

    # Check each bead is closed
    for b in beads:
        bid = b.get("id", "")
        st = b.get("status", "")
        if st != "closed":
            blocking.append({"bead": bid, "reason": f"status={st}"})

    # Check workers assigned to this phase's beads have notification=received
    phase_bead_ids = {b.get("id", "") for b in beads}
    for wname, w in reg["workers"].items():
        notif = w.get("notification", "")
        if notif == NOTIF_PENDING and w.get("bead", "") in phase_bead_ids:
            blocking.append({"worker": wname, "bead": w.get("bead", "?"), "reason": "notification pending"})

    if blocking:
        _out({"pass": False, "blocking": blocking})
    else:
        _out({"pass": True})


def cmd_smoke_test(args: argparse.Namespace) -> None:
    """Run build + wiring verification for completed beads."""
    rp = _registry_path()
    bd = _bd(rp)
    result: dict = {"build": "skip", "wiring": []}

    # Build check
    if args.build_cmd:
        r = _run(args.build_cmd)
        lines = r.stdout.strip().split("\n")
        tail = lines[-20:] if len(lines) > 20 else lines
        result["build"] = "pass" if r.returncode == 0 else "fail"
        if r.returncode != 0:
            result["build_output"] = "\n".join(tail)

    # Wiring verification
    if args.beads:
        bead_ids = [b.strip() for b in args.beads.split(",")]
        for bid in bead_ids:
            bead = _fetch_bead(bd, bid)
            if bead is None:
                result["wiring"].append({"bead": bid, "error": "cannot read bead"})
                continue

            desc = bead.get("description", "")
            # Extract FILES: section from close reason or description
            close_reason = bead.get("close_reason", bead.get("reason", ""))
            files_section = ""
            for text in [close_reason, desc]:
                if "FILES:" in text:
                    start = text.index("FILES:") + 6
                    end = text.find(".", start)
                    if end == -1:
                        end = len(text)
                    files_section = text[start:end].strip()
                    break

            if files_section:
                files = [f.strip() for f in files_section.split(",")]
                for fp in files:
                    if not fp:
                        continue
                    exists = Path(fp).exists()
                    result["wiring"].append({"bead": bid, "file": fp, "exists": exists})

    _out(result)


_FILES_HEADER_RE = re.compile(
    r"^(?:\*\*)?"
    r"(?:"
    r"(?:Modified|Changed|Target)\s+files"          # "Modified files:", etc.
    r"|Files(?:\s+to\s+\S+(?:/\S+)?)?"              # "Files:", "Files to create/modify:", etc.
    r")"
    r"(?:\s*\(([^)]*)\))?"                           # optional "(new)" / "(modifies)" — group 1
    r":?\*?\*?\s*",                                  # trailing colon/bold
    re.IGNORECASE,
)


_SECTION_RE = re.compile(r"\[([^\]]+)\]")


def _parse_file_entry(entry: str) -> tuple[str, str]:
    """Parse a file entry like 'src/config.rs [StorageConfig]' into (path, section).

    Returns (path, section) where section is empty if no [section] annotation.
    """
    entry = entry.strip().strip("`")
    m = _SECTION_RE.search(entry)
    if m:
        section = m.group(1).strip()
        path = entry[:m.start()].strip().strip("`")
        return path, section
    return entry, ""


def _extract_files_from_description(desc: str) -> list[str]:
    """Extract file paths from a bead description's Files: line.

    Also recognizes 'Files (new):' and 'Files (modifies):' variants,
    'Files to create/modify:' patterns, and synonym headers like
    'Modified files:', 'Changed files:', 'Target files:'.
    Returns a flat list of all file paths regardless of category.
    """
    files: list[str] = []
    for line in desc.split("\n"):
        stripped = line.strip()
        m = _FILES_HEADER_RE.match(stripped)
        if m:
            after = stripped[m.end():]
            for f in after.split(","):
                path, _ = _parse_file_entry(f)
                if path:
                    files.append(path)
    return files


def _extract_files_with_sections(desc: str) -> list[tuple[str, str]]:
    """Extract file paths with section annotations from a bead description.

    Returns list of (path, section) tuples. Section is "" if not annotated.
    E.g., 'Files (modifies): src/config.rs [StorageConfig]' → [("src/config.rs", "StorageConfig")]
    """
    result: list[tuple[str, str]] = []
    for line in desc.split("\n"):
        stripped = line.strip()
        m = _FILES_HEADER_RE.match(stripped)
        if m:
            after = stripped[m.end():]
            for f in after.split(","):
                path, section = _parse_file_entry(f)
                if path:
                    result.append((path, section))
    return result


def _extract_files_detailed(desc: str) -> dict[str, list[str]]:
    """Extract file paths with category: new, modifies, or all.

    Returns {"new": [...], "modifies": [...], "all": [...]}.
    Plain 'Files:' entries go into 'all'.
    Also recognizes synonym headers (Modified/Changed/Target files)
    and 'Files to <verb>:' patterns.
    """
    result: dict[str, list[str]] = {"new": [], "modifies": [], "all": []}
    for line in desc.split("\n"):
        stripped = line.strip()
        m = _FILES_HEADER_RE.match(stripped)
        if not m:
            continue
        category = (m.group(1) or "").strip().lower()
        after = stripped[m.end():]
        paths = [f.strip().strip("`") for f in after.split(",") if f.strip().strip("`")]
        if category == "new":
            result["new"].extend(paths)
        elif category in ("modifies", "modify"):
            result["modifies"].extend(paths)
        else:
            result["all"].extend(paths)
    return result


def _infer_files_from_description(desc: str) -> list[str]:
    """Fallback: infer file paths from description text when no Files: line exists."""
    paths: list[str] = []
    # Match backtick-wrapped paths with extensions (e.g., `src/foo/bar.rs`)
    for m in re.finditer(r"`([a-zA-Z0-9_./-]+\.[a-zA-Z0-9]+)`", desc):
        p = m.group(1)
        if "/" in p and not p.startswith("http"):
            paths.append(p)
    # Match "in <filename>" references (e.g., "in listen.rs", "in store/mod.rs")
    for m in re.finditer(r"\bin\s+`?([a-zA-Z0-9_/-]+\.[a-zA-Z]{1,10})`?", desc):
        p = m.group(1)
        if not p.startswith("http"):
            paths.append(p)
    # Match bare paths with slashes and extensions
    if not paths:
        for m in re.finditer(r"(?<!\w)([a-zA-Z0-9_]+(?:/[a-zA-Z0-9_.]+)+\.[a-zA-Z]{1,10})(?!\w)", desc):
            paths.append(m.group(1))
    return list(dict.fromkeys(paths))


def cmd_conflict_check(args: argparse.Namespace) -> None:
    """Check file conflicts between ready beads for parallelism safety."""
    rp = _registry_path()
    bd = _bd(rp)
    bead_ids = [b.strip() for b in args.beads.split(",") if b.strip()]

    # Fetch descriptions and extract file lists + soft deps
    bead_files: dict[str, list[str]] = {}
    bead_modifies: dict[str, list[str]] = {}
    bead_sections: dict[str, dict[str, set[str]]] = {}  # bid → {file → {sections}}
    bead_soft_deps: dict[str, list[str]] = {}
    inferred: list[str] = []
    unparseable: list[str] = []
    for bid in bead_ids:
        bead = _fetch_bead(bd, bid)
        if bead is None:
            continue
        desc = bead.get("description", "")
        files = _extract_files_from_description(desc)
        if files:
            bead_files[bid] = files
            detailed = _extract_files_detailed(desc)
            if detailed["modifies"]:
                bead_modifies[bid] = detailed["modifies"]
            # Extract section annotations
            file_secs = _extract_files_with_sections(desc)
            sec_map: dict[str, set[str]] = {}
            for path, section in file_secs:
                if section:
                    sec_map.setdefault(path, set()).add(section)
            if sec_map:
                bead_sections[bid] = sec_map
        else:
            files = _infer_files_from_description(desc)
            if files:
                bead_files[bid] = files
                inferred.append(bid)
            else:
                unparseable.append(bid)
        # Extract depends_on references
        deps = [m.group(1).strip() for m in re.finditer(r"depends_on:\s*(.+?)(?:,|$)", desc, re.IGNORECASE)]
        if deps:
            bead_soft_deps[bid] = deps

    # Build file → [bead_ids] map
    file_map: dict[str, list[str]] = {}
    for bid, files in bead_files.items():
        for f in files:
            file_map.setdefault(f, []).append(bid)

    # Identify conflicts (file touched by 2+ beads)
    all_conflicts = {f: bids for f, bids in file_map.items() if len(bids) > 1}

    # Separate low_risk (same file, different sections) from hard conflicts
    hard_conflicts: dict[str, list[str]] = {}
    low_risk: dict[str, dict] = {}
    for f, bids in all_conflicts.items():
        # Check if ALL beads touching this file have section annotations with no overlap
        sections_per_bead = []
        all_have_sections = True
        for bid in bids:
            secs = bead_sections.get(bid, {}).get(f, set())
            if not secs:
                all_have_sections = False
                break
            sections_per_bead.append((bid, secs))

        if all_have_sections and len(sections_per_bead) >= 2:
            # Check for section overlap between any pair
            all_sections = [s for _, secs in sections_per_bead for s in secs]
            has_overlap = len(all_sections) != len(set(all_sections))
            if not has_overlap:
                low_risk[f] = {
                    "beads": bids,
                    "sections": {bid: sorted(secs) for bid, secs in sections_per_bead},
                }
                continue
        hard_conflicts[f] = bids

    # Flag modify-modify conflicts (higher severity)
    modify_conflicts: dict[str, list[str]] = {}
    for f, bids in hard_conflicts.items():
        modifiers = [bid for bid in bids if f in bead_modifies.get(bid, [])]
        if len(modifiers) > 1:
            modify_conflicts[f] = modifiers

    # Compute parallel groups: beads with no HARD file overlap can run together
    # low_risk conflicts are safe for parallel dispatch
    conflicting_beads = set()
    for bids in hard_conflicts.values():
        conflicting_beads.update(bids)
    safe = [bid for bid in bead_ids if bid not in conflicting_beads and bid in bead_files]

    result: dict = {
        "conflicts": hard_conflicts,
        "safe": safe,
    }
    if low_risk:
        result["low_risk"] = low_risk
    if modify_conflicts:
        result["modify_conflicts"] = modify_conflicts
    if bead_soft_deps:
        result["soft_deps"] = bead_soft_deps
    if inferred:
        result["inferred"] = inferred
    if unparseable:
        result["unparseable"] = unparseable
    if hard_conflicts:
        serial_groups: list[list[str]] = []
        seen: set[str] = set()
        for bids in hard_conflicts.values():
            group = sorted(set(bids) - seen)
            if group:
                serial_groups.append(sorted(set(bids)))
                seen.update(bids)
        result["serial"] = serial_groups
    _out(result)


def cmd_wave_plan(args: argparse.Namespace) -> None:
    """Compute dispatch waves from ready beads using file conflicts and active workers.

    Groups beads into waves where all beads in a wave can run in parallel.
    Considers file conflicts between beads AND files currently owned by active workers.
    """
    rp = _registry_path()
    bd = _bd(rp)
    reg, _ = _load_registry(rp)
    bead_ids = [b.strip() for b in args.beads.split(",") if b.strip()]

    # Collect files owned by active workers
    active_files: dict[str, str] = {}  # file → worker_name
    for wname, w in reg.get("workers", {}).items():
        if w.get("status") != STATUS_ACTIVE:
            continue
        wbead = w.get("bead", "")
        if not wbead:
            continue
        bead = _fetch_bead(bd, wbead)
        if bead is None:
            continue
        desc = bead.get("description", "")
        for f in _extract_files_from_description(desc) or _infer_files_from_description(desc):
            active_files[f] = wname

    # Fetch file lists for each bead (reuse conflict-check helpers)
    bead_files: dict[str, list[str]] = {}
    bead_sections: dict[str, dict[str, set[str]]] = {}
    for bid in bead_ids:
        bead = _fetch_bead(bd, bid)
        if bead is None:
            continue
        desc = bead.get("description", "")
        files = _extract_files_from_description(desc) or _infer_files_from_description(desc)
        if files:
            bead_files[bid] = files
        file_secs = _extract_files_with_sections(desc)
        sec_map: dict[str, set[str]] = {}
        for path, section in file_secs:
            if section:
                sec_map.setdefault(path, set()).add(section)
        if sec_map:
            bead_sections[bid] = sec_map

    def _has_hard_conflict(bid_a: str, bid_b: str) -> bool:
        """Check if two beads have a hard file conflict (same file, overlapping sections)."""
        files_a = set(bead_files.get(bid_a, []))
        files_b = set(bead_files.get(bid_b, []))
        shared = files_a & files_b
        for f in shared:
            secs_a = bead_sections.get(bid_a, {}).get(f, set())
            secs_b = bead_sections.get(bid_b, {}).get(f, set())
            if not secs_a or not secs_b or secs_a & secs_b:
                return True
        return False

    # Graph coloring: assign each bead to the earliest wave where it has no conflicts
    waves: list[list[str]] = []
    assigned: dict[str, int] = {}  # bid → wave index
    blocked_by_active: list[dict] = []

    for bid in bead_ids:
        if bid not in bead_files:
            continue

        # Check conflict with active workers
        bid_files = set(bead_files[bid])
        active_conflict = None
        for f in bid_files:
            if f in active_files:
                # Check section-level — if both have sections and no overlap, it's safe
                bid_secs = bead_sections.get(bid, {}).get(f, set())
                if not bid_secs:
                    active_conflict = active_files[f]
                    break
        if active_conflict:
            blocked_by_active.append({"bead": bid, "blocked_by_worker": active_conflict})
            continue

        # Find earliest wave with no conflicts
        placed = False
        for wi, wave in enumerate(waves):
            has_conflict = False
            for existing_bid in wave:
                if _has_hard_conflict(bid, existing_bid):
                    has_conflict = True
                    break
            if not has_conflict:
                wave.append(bid)
                assigned[bid] = wi
                placed = True
                break
        if not placed:
            waves.append([bid])
            assigned[bid] = len(waves) - 1

    result: dict = {
        "ok": True,
        "waves": [{"group": i + 1, "beads": w} for i, w in enumerate(waves)],
        "total_waves": len(waves),
        "total_beads": len(assigned),
    }
    if blocked_by_active:
        result["blocked_by_active"] = blocked_by_active
    unplanned = [bid for bid in bead_ids if bid not in assigned and bid not in [b["bead"] for b in blocked_by_active]]
    if unplanned:
        result["unparseable"] = unplanned
    _out(result)


def cmd_dep(args: argparse.Namespace) -> None:
    """Add a dependency idempotently — UNIQUE constraint errors are treated as success."""
    rp = _registry_path()
    bd = _bd(rp)
    r = _run(f"{bd} dep {args.blocker} --blocks {args.blocked}")
    if r.returncode == 0:
        _out({"ok": True, "blocker": args.blocker, "blocked": args.blocked, "already_existed": False})
        return
    combined = (r.stderr + r.stdout).lower()
    if "unique" in combined or "duplicate" in combined or "already exists" in combined:
        _out({"ok": True, "blocker": args.blocker, "blocked": args.blocked, "already_existed": True})
        return
    _out({"ok": False, "error": f"bd dep failed: {r.stderr.strip()[:200]}"})


def cmd_import_deps(args: argparse.Namespace) -> None:
    """Bulk-import deps from a file using "Title A" blocks "Title B" format."""
    dep_path = Path(args.file)
    if not dep_path.exists():
        _out({"ok": False, "error": f"file not found: {args.file}"})
        return

    # Parse dep lines
    pairs: list[tuple[str, str]] = []
    for line in dep_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'"([^"]+)"\s+blocks\s+"([^"]+)"', line)
        if m:
            pairs.append((m.group(1), m.group(2)))

    if not pairs:
        _out({"ok": True, "applied": 0, "already_existed": 0, "errors": [], "unresolved": []})
        return

    # Build title→ID mapping via bd list
    rp = _registry_path()
    bd = _bd(rp)
    r = _run(f"{bd} list --limit {BD_LIST_LIMIT} --json")
    stdout = r.stdout.strip()
    json_start = next((i for i, ch in enumerate(stdout) if ch in ("[", "{")), -1)
    all_beads = []
    if json_start >= 0:
        try:
            all_beads = json.loads(stdout[json_start:])
        except json.JSONDecodeError as e:
            sys.stderr.write(f"tf.py warning: parsing bd list JSON in import-deps: {e}\n")
    if isinstance(all_beads, dict):
        all_beads = all_beads.get("issues", [])

    title_to_id: dict[str, str] = {}
    for b in all_beads:
        title = b.get("title", b.get("name", ""))
        bid = b.get("id", "")
        if title and bid:
            title_to_id[_norm(title)] = bid

    def _resolve(title: str) -> str:
        norm_t = _norm(title)
        if norm_t in title_to_id:
            return title_to_id[norm_t]
        for t, tid in title_to_id.items():
            if t.startswith(norm_t) or norm_t.startswith(t):
                return tid
        return ""

    unresolved = []
    applied = 0
    already_existed = 0
    errors = []

    if args.validate:
        for blocker_title, blocked_title in pairs:
            if not _resolve(blocker_title):
                unresolved.append(blocker_title)
            if not _resolve(blocked_title):
                unresolved.append(blocked_title)
        unresolved = sorted(set(unresolved))
        _out({"ok": len(unresolved) == 0, "total": len(pairs), "resolved": len(pairs) * 2 - len(unresolved), "unresolved": unresolved})
        return

    for blocker_title, blocked_title in pairs:
        blocker_id = _resolve(blocker_title)
        blocked_id = _resolve(blocked_title)
        if not blocker_id:
            unresolved.append(blocker_title)
            continue
        if not blocked_id:
            unresolved.append(blocked_title)
            continue

        r = _run(f"{bd} dep {blocker_id} --blocks {blocked_id}")
        if r.returncode == 0:
            applied += 1
        else:
            combined = (r.stderr + r.stdout).lower()
            if "unique" in combined or "duplicate" in combined or "already exists" in combined:
                already_existed += 1
            else:
                errors.append(f"{blocker_title} -> {blocked_title}: {r.stderr.strip()[:100]}")

    unresolved = sorted(set(unresolved))
    result: dict = {"ok": len(errors) == 0 and len(unresolved) == 0, "applied": applied, "already_existed": already_existed}
    if errors:
        result["errors"] = errors
    if unresolved:
        result["unresolved"] = unresolved
    _out(result)


def cmd_close(args: argparse.Namespace) -> None:
    """Close a bead via bd close, normalizing output to a simple JSON object."""
    rp = _registry_path()
    bd = _bd(rp)
    reason = args.reason or "completed"
    r = _run(f'{bd} close {args.bead_id} --reason {shlex.quote(reason)} --json')
    if r.returncode != 0:
        _out({"ok": False, "error": f"bd close failed: {r.stderr.strip()[:200]}"})
        return
    # Verify via bd show
    bead = _fetch_bead(bd, args.bead_id)
    status = bead.get("status", "unknown") if bead else "unknown"
    _out({"ok": True, "id": args.bead_id, "status": status})


def cmd_create(args: argparse.Namespace) -> None:
    """Wrapper for bd create -f with clean JSON output.

    Handles the common issue where bd create --json output is truncated or
    contains non-JSON prefixes that break jq parsing. Falls back to bd list
    to verify creation count if JSON parsing fails.
    """
    plan_path = Path(args.file)
    if not plan_path.exists():
        _out({"ok": False, "error": f"file not found: {args.file}"})
        return

    rp = _registry_path()
    bd = _bd(rp)

    # Count expected issues from plan file (## headers)
    content = plan_path.read_text()
    expected = len(re.findall(r"^## .+", content, re.MULTILINE))

    # Run bd create -f
    file_escaped = shlex.quote(str(plan_path))
    r = _run(f"{bd} create -f {file_escaped} --json")
    if r.returncode != 0:
        _out({"ok": False, "error": f"bd create failed: {r.stderr.strip()[:300]}"})
        return

    # Try to parse the JSON output
    created = _parse_bd_json(r.stdout)
    if not created:
        # Fallback: bd create JSON was unparseable — verify via bd list
        r2 = _run(f"{bd} list --status=open --limit {BD_LIST_LIMIT} --json")
        created = _parse_bd_json(r2.stdout) or []

    ids = [c.get("id", c.get("name", "")) for c in created if isinstance(c, dict)]
    result: dict = {"ok": True, "created": len(ids), "expected": expected, "ids": ids}
    if not _parse_bd_json(r.stdout):
        result["fallback"] = True
        result["note"] = "bd create JSON was unparseable — count from bd list"

    # Persist created issues for wire-plan to consume
    ids_file = ""
    try:
        ctx = _context_dir()
        ids_path = ctx / "created.json"
        ids_path.write_text(json.dumps(created, indent=2))
        ids_file = str(ids_path)
    except (OSError, KeyError):
        pass
    if ids_file:
        result["ids_file"] = ids_file

    _out(result)


def _parse_bd_json(stdout: str) -> list:
    """Parse JSON from bd output, skipping any non-JSON prefix."""
    stdout = stdout.strip()
    json_start = next((i for i, ch in enumerate(stdout) if ch in ("[", "{")), -1)
    if json_start < 0:
        return []
    try:
        data = json.loads(stdout[json_start:])
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return data.get("issues", [])
    return data if isinstance(data, list) else []


def cmd_ready(args: argparse.Namespace) -> None:
    """Return dispatchable tasks: bd ready filtered (no epics) + supplemented from bd list."""
    rp = _registry_path()
    bd = _bd(rp)

    # Primary: bd ready
    r = _run(f"{bd} ready --json")
    ready_beads = _parse_bd_json(r.stdout)

    # Filter out epics
    ready_beads = [b for b in ready_beads if b.get("type", "").lower() != "epic"]
    ready_ids = {b.get("id", "") for b in ready_beads}

    # Supplement: open beads with no blockers that bd ready missed.
    # bd list may not populate blocked_by reliably, so we fetch full
    # details via bd show and check actual dependencies for open blockers.
    r2 = _run(f"{bd} list --status=open --limit {BD_LIST_LIMIT} --json")
    all_open = _parse_bd_json(r2.stdout)
    supplemented = 0
    for b in all_open:
        bid = b.get("id", "")
        if bid in ready_ids:
            continue
        if b.get("type", "").lower() == "epic":
            continue
        blocked_by = b.get("blocked_by") or b.get("blockedBy") or []
        if blocked_by:
            continue
        full = _fetch_bead(bd, bid)
        if full:
            deps = full.get("dependencies") or []
            has_open_blocker = any(
                d.get("dependency_type") == "blocks" and d.get("status") != "closed"
                for d in deps if isinstance(d, dict)
            )
            if has_open_blocker:
                continue
        ready_beads.append(b)
        ready_ids.add(bid)
        supplemented += 1

    # Trim each bead to essential fields only (token efficiency)
    _READY_KEYS = ("id", "title", "type", "priority", "parent", "status")
    trimmed: list[dict] = []
    for b in ready_beads:
        t: dict = {}
        for k in _READY_KEYS:
            if k == "type":
                t[k] = b.get("type") or b.get("issue_type", "")
            elif k in b:
                t[k] = b[k]
        trimmed.append(t)

    _out({"ok": True, "ready": trimmed, "supplemented": supplemented})


def cmd_recover(args: argparse.Namespace) -> None:
    """Scan for orphaned beads (in_progress but no active worker) after context compaction."""
    reg, rp = _load_registry()
    bd = _bd(rp)

    # Get all in-progress beads
    r = _run(f"{bd} list --status=in_progress --limit {BD_LIST_LIMIT} --json")
    in_progress = _parse_bd_json(r.stdout)

    # Build worker bead mapping
    active_beads: set[str] = set()
    worker_history: dict[str, str] = {}  # bead_id -> last worker name
    for wname, w in reg.get("workers", {}).items():
        bead = w.get("bead", "")
        if w.get("status") == STATUS_ACTIVE:
            active_beads.add(bead)
        if bead:
            worker_history[bead] = wname

    orphaned = []
    for b in in_progress:
        bid = b.get("id", "")
        if bid not in active_beads:
            entry: dict = {"id": bid, "title": b.get("title", b.get("name", ""))}
            if bid in worker_history:
                entry["last_worker"] = worker_history[bid]
            orphaned.append(entry)

    _out({"ok": True, "orphaned": orphaned})


def cmd_dedup(args: argparse.Namespace) -> None:
    """Detect and optionally close duplicate bead titles."""
    rp = _registry_path()
    bd = _bd(rp)

    r = _run(f"{bd} list --limit {BD_LIST_LIMIT} --json")
    all_beads = _parse_bd_json(r.stdout)

    groups: dict[str, list[dict]] = {}
    for b in all_beads:
        title = b.get("title", b.get("name", ""))
        key = _norm(title)
        if key:
            groups.setdefault(key, []).append(b)

    duplicates = []
    closed = 0
    keep = getattr(args, "keep", "newest")
    apply_mode = getattr(args, "apply", False)

    for key, beads in groups.items():
        if len(beads) < 2:
            continue
        beads.sort(key=lambda b: b.get("id", ""))
        if keep == "oldest":
            keep_bead = beads[0]
            close_beads = beads[1:]
        else:
            keep_bead = beads[-1]
            close_beads = beads[:-1]

        entry: dict = {
            "title": beads[0].get("title", beads[0].get("name", "")),
            "count": len(beads),
            "keep": keep_bead.get("id", ""),
            "close": [b.get("id", "") for b in close_beads],
        }

        if apply_mode:
            for b in close_beads:
                bid = b.get("id", "")
                cr = _run(f"{bd} close {bid} --reason duplicate")
                if cr.returncode == 0:
                    closed += 1
                else:
                    entry.setdefault("errors", []).append(f"{bid}: {cr.stderr.strip()[:100]}")

        duplicates.append(entry)

    result: dict = {"ok": True, "duplicates": duplicates, "dry_run": not apply_mode}
    if apply_mode:
        result["closed"] = closed
    _out(result)


def cmd_ad_hoc(args: argparse.Namespace) -> None:
    """Register an informal task in the registry for stall detection without a bead."""
    reg, rp = _load_registry()
    now = _now()
    worker = args.worker
    reg["workers"][worker] = {
        "status": STATUS_ACTIVE,
        "skill": args.skill or "general",
        "context_pct": 0,
        "bead": f"ad-hoc:{args.name}",
        "notification": NOTIF_PENDING,
        "dispatched_at": now,
        "last_heartbeat": now,
        "last_heartbeat_note": f"ad-hoc task: {args.name}",
    }
    _save_registry(reg, rp)
    _out({"ok": True, "worker": worker, "bead": f"ad-hoc:{args.name}"})


def cmd_validate_plan(args: argparse.Namespace) -> None:
    """Validate a plan markdown file for bd create -f compatibility."""
    plan_path = Path(args.file)
    if not plan_path.exists():
        _out({"ok": False, "error": f"file not found: {args.file}"})
        return

    content = plan_path.read_text()
    lines = content.split("\n")
    errors = []
    warnings = []
    issues = []

    active_plan = _beads_dir() / "active-plan"
    if not active_plan.exists():
        warnings.append("tf.py init has not been run — tf.py create will fail without it")

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r'^-{3,}\s*$', stripped):
            errors.append(f"line {i}: '---' horizontal rule breaks bd parser — remove it")
        elif stripped.startswith("## "):
            title = stripped[3:].strip()
            issues.append({"line": i, "title": title})

    # Determine types and extract bodies for each issue
    epics = 0
    epic_indices: set[int] = set()
    for idx, iss in enumerate(issues):
        start = iss["line"] - 1  # 0-based
        end = issues[idx + 1]["line"] - 1 if idx + 1 < len(issues) else len(lines)
        body = "\n".join(lines[start:end])
        iss["body"] = body
        # Check if this issue is an epic
        for i in range(start, end):
            if re.match(r'^###\s+[Tt]ype\s*$', lines[i].strip()):
                for j in range(i + 1, min(i + 4, end)):
                    type_line = lines[j].strip()
                    if type_line and not type_line.startswith("#"):
                        if type_line.lower() == "epic":
                            epics += 1
                            epic_indices.add(idx)
                        break
                break

    # Check for unrecognized ### headers that bd may misparse as metadata
    recognized_h3 = {
        "type", "priority", "description", "design", "acceptance criteria",
        "assignee", "labels", "dependencies", "soft dependencies", "files",
    }
    rogue_headers = []
    for idx, iss in enumerate(issues):
        start = iss["line"] - 1
        end = issues[idx + 1]["line"] - 1 if idx + 1 < len(issues) else len(lines)
        for li in range(start + 1, end):
            stripped = lines[li].strip()
            h3_match = re.match(r'^###\s+(.+)$', stripped)
            if h3_match:
                header_text = h3_match.group(1).strip().lower()
                if header_text not in recognized_h3:
                    rogue_headers.append(
                        f"line {li + 1}: unrecognized H3: '{stripped}'"
                    )

    # Check that non-epic issues have a Files: line
    missing_files = []
    no_files_check = getattr(args, "no_files_check", False)
    if not no_files_check:
        for idx, iss in enumerate(issues):
            if idx in epic_indices:
                continue
            files = _extract_files_from_description(iss.get("body", ""))
            if not files:
                missing_files.append(iss["title"])

    # Check that producer tasks name their consumer
    orphan_packages = []
    task_titles = [iss["title"] for idx, iss in enumerate(issues) if idx not in epic_indices]
    all_bodies = " ".join(iss.get("body", "") for iss in issues)
    for idx, iss in enumerate(issues):
        if idx in epic_indices:
            continue
        title_lower = iss["title"].lower()
        if any(kw in title_lower for kw in ("implement", "create")) and any(kw in title_lower for kw in ("package", "module", "library", "pkg/")):
            # Extract a package-like name from the title
            pkg_match = re.search(r'`?(?:pkg/|internal/)?([a-zA-Z0-9_/-]+)`?', iss["title"])
            if pkg_match:
                pkg_name = pkg_match.group(1).split("/")[-1]
                # Check if any other task references this package
                other_bodies = " ".join(
                    other.get("body", "") for jdx, other in enumerate(issues)
                    if jdx != idx and jdx not in epic_indices
                )
                if pkg_name not in other_bodies:
                    orphan_packages.append(iss["title"])

    # Parallelism analysis: scan ### Dependencies and ### Soft Dependencies
    tasks_count = len(issues) - epics
    dep_count = 0
    has_blocks = 0
    has_depends_on = 0
    in_deps = False
    for line in lines:
        stripped = line.strip()
        if re.match(r'^###\s+(?:[Dd]ependencies|[Ss]oft [Dd]ependencies)\s*$', stripped):
            in_deps = True
            dep_count += 1
            continue
        if stripped.startswith("##"):
            in_deps = False
            continue
        if in_deps and stripped:
            if "blocks:" in stripped.lower():
                has_blocks += 1
            if "depends_on:" in stripped.lower():
                has_depends_on += 1

    if tasks_count > 3:
        if dep_count == 0:
            warnings.append("No ### Dependencies sections found — remember to add parallel groups after creation")
        elif has_blocks > 0 and has_blocks >= tasks_count - 1:
            roots = tasks_count - has_blocks
            if roots <= 1:
                warnings.append("fully sequential — no parallelism")

    if rogue_headers:
        errors.extend(rogue_headers)
    if missing_files:
        for title in missing_files:
            errors.append(f"'{title}' missing Files:")
    if orphan_packages:
        for title in orphan_packages:
            warnings.append(f"Task '{title}' has no consumer task — add a 'wire into' task or reference it from a downstream task")

    check_parallelism = getattr(args, "check_parallelism", False)

    result: dict = {
        "ok": len(errors) == 0 and not (check_parallelism and warnings),
        "file": args.file,
        "issues": len(issues),
        "epics": epics,
        "tasks": tasks_count,
    }
    if errors:
        result["errors"] = errors
    if warnings:
        result["warnings"] = warnings
    if has_depends_on:
        result["soft_deps"] = has_depends_on
    _out(result)


def cmd_wire_plan(args: argparse.Namespace) -> None:
    """Auto-wire parent-child and blocking deps from a plan file + bd create output."""
    plan_path = Path(args.file)
    if not plan_path.exists():
        _out({"ok": False, "error": f"file not found: {args.file}"})
        return

    # Load bd create -f --json output: list of created issues with titles + IDs.
    # If --ids not provided, look for created.json from tf.py create.
    ids_arg = args.ids
    if not ids_arg:
        try:
            ctx = _context_dir()
            default_ids = ctx / "created.json"
            if default_ids.exists():
                ids_arg = str(default_ids)
        except (OSError, KeyError):
            pass
    if not ids_arg:
        _out({"ok": False, "error": "no --ids file provided and no created.json found — run tf.py create first"})
        return
    ids_path = Path(ids_arg)
    if not ids_path.exists():
        _out({"ok": False, "error": f"ids file not found: {ids_arg}"})
        return
    try:
        created = json.loads(ids_path.read_text())
        if isinstance(created, dict):
            created = created.get("issues", created.get("created", []))
    except json.JSONDecodeError:
        _out({"ok": False, "error": "ids file is not valid JSON"})
        return

    # Build title→ID mapping (normalize titles for fuzzy matching)
    title_to_id: dict[str, str] = {}
    for item in created:
        title = item.get("title", item.get("name", ""))
        iid = item.get("id", "")
        if title and iid:
            title_to_id[_norm(title)] = iid

    # Parse plan structure: extract issues, their types, parent epics, and deps
    content = plan_path.read_text()
    lines = content.split("\n")

    issues: list[dict] = []
    current = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            if current:
                issues.append(current)
            title = stripped[3:].strip()
            current = {"title": title, "type": "task", "deps": [], "in_section": ""}
        elif current and stripped.startswith("### "):
            current["in_section"] = stripped[4:].strip().lower()
        elif current:
            sec = current["in_section"]
            if sec == "type" and stripped and not stripped.startswith("#"):
                current["type"] = stripped.lower()
            elif sec == "dependencies" and stripped:
                current["deps"].append(stripped)

    if current:
        issues.append(current)

    # Resolve parent-child: tasks belong to the most recent preceding epic
    rp = _registry_path()
    bd = _bd(rp)
    parent_child_wired = 0
    blocking_wired = 0
    errors = []
    current_epic_id = ""

    for iss in issues:
        norm_title = _norm(iss["title"])
        iss_id = title_to_id.get(norm_title, "")
        if not iss_id:
            errors.append(f"no ID found for: {iss['title']}")
            continue

        if iss["type"] == "epic":
            current_epic_id = iss_id
            continue

        # Wire parent-child
        if current_epic_id:
            r = _run(f"{bd} dep add {iss_id} {current_epic_id} -t parent-child")
            combined = (r.stderr + r.stdout).lower()
            if r.returncode == 0 or "unique" in combined or "duplicate" in combined or "already" in combined:
                parent_child_wired += 1
            else:
                errors.append(f"parent-child failed for {iss_id}: {r.stderr.strip()[:100]}")

        # Wire blocking deps from ### Dependencies section
        for dep_line in iss.get("deps", []):
            for part in re.split(r'[,;]\s*', dep_line):
                part = part.strip()
                match = re.match(r'(?:blocks?:)\s*(.+)', part, re.IGNORECASE)
                if match:
                    ref = match.group(1).strip().strip('"').strip("'")
                    ref_norm = _norm(ref)
                    # Try exact match, then prefix match
                    blocker_id = title_to_id.get(ref_norm, "")
                    if not blocker_id:
                        for t, tid in title_to_id.items():
                            if t.startswith(ref_norm) or ref_norm.startswith(t):
                                blocker_id = tid
                                break
                    if blocker_id:
                        r = _run(f"{bd} dep {blocker_id} --blocks {iss_id}")
                        combined = (r.stderr + r.stdout).lower()
                        if r.returncode == 0 or "unique" in combined or "duplicate" in combined or "already" in combined:
                            blocking_wired += 1
                        else:
                            errors.append(f"dep failed {blocker_id}→{iss_id}: {r.stderr.strip()[:100]}")
                    else:
                        errors.append(f"dep ref not found: '{ref}' in task '{iss['title']}'")

    result: dict = {
        "ok": len(errors) == 0,
        "parent_child": parent_child_wired,
        "blocking": blocking_wired,
        "total_issues": len(issues),
    }
    if errors:
        result["errors"] = errors[:20]
    _out(result)


def _norm(t: str) -> str:
    return re.sub(r'\s+', ' ', t.strip().lower())


def _slugify(text: str) -> str:
    """Convert text to a slug for filenames."""
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:50]


def _context_dir() -> Path:
    """Return the active context directory."""
    bd = _beads_dir()
    active_plan_file = bd / "active-plan"
    if active_plan_file.exists():
        plan_name = active_plan_file.read_text().strip()
        if plan_name:
            return bd / f"context-{plan_name}"
    return bd


def _append_to_section(filepath: Path, section_heading: str, content: str) -> None:
    """Append content under a markdown section heading, creating it if absent."""
    if filepath.exists():
        text = filepath.read_text()
    else:
        text = ""
    if section_heading in text:
        idx = text.index(section_heading) + len(section_heading)
        end_of_line = text.find("\n", idx)
        if end_of_line == -1:
            end_of_line = len(text)
        text = text[:end_of_line] + "\n\n" + content + text[end_of_line:]
    else:
        text = text.rstrip() + f"\n\n{section_heading}\n\n{content}\n"
    filepath.write_text(text)


def cmd_update_context(args: argparse.Namespace) -> None:
    """Append completion summary to epic context files and optional gotcha to worker-context."""
    rp = _registry_path()
    bd_bin = _bd(rp)
    ctx = _context_dir()
    updated = []

    # Get bead info for title and parent
    title = args.bead_id
    epic_slug = ""
    bead = _fetch_bead(bd_bin, args.bead_id)
    if bead:
        title = bead.get("title", bead.get("name", args.bead_id))
        parent_id = bead.get("parent", "")
        if parent_id:
            parent = _fetch_bead(bd_bin, parent_id)
            if parent:
                epic_slug = _slugify(parent.get("title", parent.get("name", parent_id)))
            else:
                epic_slug = _slugify(parent_id)

    # Format summary block
    files_str = args.files or ""
    summary_block = f"### BD-{args.bead_id}: {title}\n**Worker**: {args.worker} | **Files**: {files_str}\n{args.summary}"

    # Append to epic context file
    if epic_slug:
        epic_file = ctx / f"epic-{epic_slug}.md"
        _append_to_section(epic_file, "## Completed Tasks", summary_block)
        updated.append(f"epic-{epic_slug}.md")

    # Always write to task-summaries.md as a catch-all
    summaries_file = ctx / "task-summaries.md"
    _append_to_section(summaries_file, "## Completed Tasks", summary_block)
    updated.append("task-summaries.md")

    # Append gotcha if provided
    gotcha_added = False
    if args.gotcha:
        wc = ctx / "worker-context.md"
        _append_to_section(wc, "## Known Gotchas", f"- {args.gotcha}")
        gotcha_added = True
        updated.append("worker-context.md")

    _out({"ok": True, "updated": updated, "gotcha_added": gotcha_added})


def cmd_phase_summary(args: argparse.Namespace) -> None:
    """Show bead status per epic/phase — lighter than phase-complete, no gate logic."""
    rp = _registry_path()
    bd = _bd(rp)
    epic_ids = [e.strip() for e in args.epics.split(",") if e.strip()]

    phases: list[dict] = []
    for epic_id in epic_ids:
        epic_title = epic_id
        epic = _fetch_bead(bd, epic_id)
        if epic:
            epic_title = epic.get("title", epic.get("name", epic_id))

        r = _run(f"{bd} list --parent {epic_id} --limit {BD_LIST_LIMIT} --json")
        children = []
        if r.returncode == 0 and r.stdout.strip():
            json_start = next((i for i, ch in enumerate(r.stdout.strip()) if ch in ("[", "{")), -1)
            if json_start >= 0:
                try:
                    children = json.loads(r.stdout.strip()[json_start:])
                    if isinstance(children, dict):
                        children = children.get("issues", [])
                except json.JSONDecodeError as e:
                    sys.stderr.write(f"tf.py warning: parsing bd list children in phase-summary: {e}\n")

        closed = sum(1 for c in children if c.get("status") == "closed")
        total = len(children)
        status = "done" if total > 0 and closed == total else "in_progress" if closed > 0 else "pending"

        phases.append({
            "epic": epic_id,
            "title": epic_title,
            "closed": closed,
            "total": total,
            "status": status,
        })

    _out({"ok": True, "phases": phases})


def cmd_phase_complete(args: argparse.Namespace) -> None:
    """Combined phase gate + smoke test + phase summary writer."""
    rp = _registry_path()
    bd_bin = _bd(rp)
    reg, _ = _load_registry(rp)
    ctx = _context_dir()

    # 1. Phase gate check
    blocking = []
    r = _run(f"{bd_bin} list --parent {args.epic_id} --limit {BD_LIST_LIMIT} --json")
    if r.returncode != 0:
        r = _run(f"{bd_bin} list --limit {BD_LIST_LIMIT} --json")
    stdout = r.stdout.strip()
    json_start = next((i for i, ch in enumerate(stdout) if ch in ("[", "{")), -1)
    beads = []
    if json_start >= 0:
        try:
            beads = json.loads(stdout[json_start:])
        except json.JSONDecodeError as e:
            sys.stderr.write(f"tf.py warning: parsing bd list JSON in phase-complete: {e}\n")
    if isinstance(beads, dict):
        beads = beads.get("issues", [])

    all_files = []
    for b in beads:
        bid = b.get("id", "")
        st = b.get("status", "")
        if st != "closed":
            blocking.append({"bead": bid, "reason": f"status={st}"})
        else:
            reason = b.get("close_reason", b.get("reason", ""))
            if "FILES:" in reason:
                start = reason.index("FILES:") + 6
                rest = reason[start:]
                m = re.search(r'\.\s*$|\.\s', rest)
                end = start + m.start() if m else len(reason)
                files = [f.strip() for f in reason[start:end].split(",") if f.strip()]
                all_files.extend(files)

    # Also aggregate files from task-summaries.md (written by update-context)
    summaries_path = ctx / "task-summaries.md"
    if summaries_path.exists():
        summaries_text = summaries_path.read_text()
        closed_ids = {b.get("id", "") for b in beads if b.get("status") == "closed"}
        for bid in closed_ids:
            pattern = rf"### BD-{re.escape(bid)}:.*?\n\*\*Worker\*\*.*?\*\*Files\*\*:\s*(.*?)(?:\n|$)"
            m = re.search(pattern, summaries_text)
            if m:
                files_from_summary = [f.strip() for f in m.group(1).split(",") if f.strip()]
                all_files.extend(files_from_summary)

    phase_bead_ids = {b.get("id", "") for b in beads}
    for wname, w in reg.get("workers", {}).items():
        if w.get("notification") == NOTIF_PENDING and w.get("bead", "") in phase_bead_ids:
            bead_title = next((b.get("title", "") for b in beads if b.get("id") == w.get("bead")), "")
            blocking.append({"worker": wname, "bead": w.get("bead", "?"), "bead_title": bead_title, "reason": "notification pending"})

    if blocking:
        _out({"pass": False, "blocking": blocking})
        return

    # 2. Smoke test (if build cmd provided)
    build_status = "skip"
    build_output = ""
    if args.build_cmd:
        r = _run(args.build_cmd)
        build_status = "pass" if r.returncode == 0 else "fail"
        if r.returncode != 0:
            lines = r.stdout.strip().split("\n")
            build_output = "\n".join(lines[-20:])

    # 3. Write phase summary
    phase_num = args.phase_num or "1"
    phase_file = f"phase-{phase_num}.md"
    files_list = "\n".join(f"- `{f}`" for f in sorted(set(all_files))) if all_files else "- (none extracted)"
    phase_content = f"""# Phase {phase_num} Summary

**Status:** Complete | **Build:** {build_status.title()}

## Files Created/Modified
{files_list}

## Beads Closed
{len([b for b in beads if b.get('status') == 'closed'])} beads
"""
    if build_output:
        phase_content += f"\n## Build Output\n```\n{build_output}\n```\n"

    # 4. Parallelism analysis from worker timestamps
    closed_bead_ids = {b.get("id", "") for b in beads if b.get("status") == "closed"}
    intervals = []
    for wname, w in reg.get("workers", {}).items():
        dispatched = w.get("dispatched_at", "")
        idle_since = w.get("idle_since", w.get("retired_at", ""))
        if dispatched and idle_since:
            try:
                t0 = _parse_ts(dispatched)
                t1 = _parse_ts(idle_since)
                if t1 > t0:
                    intervals.append((t0, t1))
            except (ValueError, TypeError) as e:
                sys.stderr.write(f"tf.py warning: parsing timestamp in sync: {e}\n")

    parallelism: dict = {"total_beads": len(closed_bead_ids)}
    if len(intervals) >= 2:
        intervals.sort()
        overlap_seconds = 0
        for i in range(len(intervals)):
            for j in range(i + 1, len(intervals)):
                overlap_start = max(intervals[i][0], intervals[j][0])
                overlap_end = min(intervals[i][1], intervals[j][1])
                if overlap_end > overlap_start:
                    overlap_seconds += (overlap_end - overlap_start).total_seconds()
        total_seconds = sum((t1 - t0).total_seconds() for t0, t1 in intervals)
        parallelism["sequential_pct"] = round(100 * (1 - overlap_seconds / total_seconds)) if total_seconds > 0 else 100
    else:
        parallelism["sequential_pct"] = 100

    phase_content += f"\n## Parallelism\n- **Sequential:** {parallelism['sequential_pct']}%\n- **Beads:** {parallelism['total_beads']}\n"

    (ctx / phase_file).write_text(phase_content)

    result: dict = {
        "pass": True,
        "build": build_status,
        "phase_file": phase_file,
        "beads_closed": len([b for b in beads if b.get("status") == "closed"]),
        "files": sorted(set(all_files)),
        "parallelism": parallelism,
    }
    _out(result)


def cmd_worker_prompt(args: argparse.Namespace) -> None:
    """Assemble a complete worker prompt from template + context files + bead data."""
    rp = _registry_path()
    bd_bin = _bd(rp)
    reg, _ = _load_registry(rp)
    ctx = _context_dir()
    bead_ids = [b.strip() for b in args.beads.split(",") if b.strip()]

    # Build context reference instead of embedding full worker-context.md
    wc_path = ctx / "worker-context.md"
    wc_relative = f".beads/context-{ctx.name.removeprefix('context-')}/worker-context.md"
    if wc_path.exists():
        project_context = f"**Read `{wc_relative}` for project conventions, tech stack, and known gotchas before starting work.**"
    else:
        project_context = ""

    # Include latest phase file, capped to avoid bloating worker context
    phase_files = sorted(ctx.glob("phase-*.md"))
    if phase_files:
        phase_content = phase_files[-1].read_text()
        lines = phase_content.split("\n")
        if len(lines) > 60:
            lines = lines[:20] + ["\n... (earlier summaries trimmed) ...\n"] + lines[-40:]
            phase_content = "\n".join(lines)
        project_context += "\n\n" + phase_content

    # Fetch bead data
    bead_data = []
    for bid in bead_ids:
        bead = _fetch_bead(bd_bin, bid) or {"id": bid, "title": bid, "description": ""}
        bead.setdefault("id", bid)
        bead.setdefault("title", bead.get("name", bid))
        bead.setdefault("description", "")
        bead["target_files"] = _extract_files_from_description(bead["description"])
        bead_data.append(bead)

    # Determine epic context
    epic_context = "N/A"
    feature_context = "N/A"
    if bead_data:
        parent_id = bead_data[0].get("parent", "")
        if parent_id:
            parent = _fetch_bead(bd_bin, parent_id)
            slug = _slugify(parent.get("title", parent.get("name", parent_id))) if parent else _slugify(parent_id)
            epic_file = ctx / f"epic-{slug}.md"
            if epic_file.exists():
                epic_context = epic_file.read_text()
            feat_file = ctx / f"feature-{slug}.md"
            if feat_file.exists():
                feature_context = feat_file.read_text()

    # Build prompt
    if args.reuse and args.prior_bead:
        # Reuse prompt (shorter)
        bd0 = bead_data[0]
        files_str = ", ".join(f"`{f}`" for f in bd0["target_files"]) or "See description"
        prompt = f"""## Prior Task — COMPLETE AND CLOSED
Bead {args.prior_bead} is ALREADY CLOSED. Do NOT re-close it, retry worker-close on it, or reference it.
Your new task begins below.

## New Task
**{bd0['title']}** (Bead ID: {bd0['id']})

{bd0['description']}

## Target Files
{files_str}

## Updated Context
{epic_context if epic_context != 'N/A' else '(no updates)'}

Same execution rules apply. Claim the NEW bead first, execute, commit, then run:
python3 .beads/tf.py claim {bd0['id']}
... do the work ...
python3 .beads/tf.py worker-close {bd0['id']} --context-pct <N> --files <file1>,<file2> --summary "<what you did>"
Fix any errors it reports. Done when it returns ok:true."""
    else:
        # Full prompt — read template
        template_path = Path(__file__).parent / "WORKER-PROMPT.md"
        template = ""
        if template_path.exists():
            content = template_path.read_text()
            # Extract the template section between first ``` and second ```
            blocks = content.split("```")
            if len(blocks) >= 3:
                template = blocks[1].strip()

        if len(bead_data) == 1:
            bd0 = bead_data[0]
            files_str = ", ".join(f"`{f}`" for f in bd0["target_files"]) or "See description"
            if template:
                prompt = template.replace("{bead_id}", bd0["id"])
                prompt = prompt.replace("{bead_title}", bd0["title"])
                prompt = prompt.replace("{bead_description}", bd0["description"])
                prompt = prompt.replace("{target_files}", files_str)
                prompt = prompt.replace("{project_context}", project_context)
                prompt = prompt.replace("{epic_context}", epic_context)
                prompt = prompt.replace("{feature_context}", feature_context)
            else:
                prompt = f"""You are a worker agent executing a specific task.

## Project Context
{project_context}

## Epic Context
{epic_context}

## Task
**{bd0['title']}** (Bead ID: {bd0['id']})

{bd0['description']}

## Target Files
{files_str}"""
        else:
            # Multi-bead: sequential sub-tasks
            sub_tasks = []
            for i, bd_item in enumerate(bead_data, 1):
                files_str = ", ".join(f"`{f}`" for f in bd_item["target_files"]) or "See description"
                sub_tasks.append(f"""## Sub-Task {i}
**{bd_item['title']}** (Bead ID: {bd_item['id']})

{bd_item['description']}

**Target Files**: {files_str}

Claim before starting: `python3 .beads/tf.py claim {bd_item['id']}`
Close when done: `python3 .beads/tf.py worker-close {bd_item['id']} --context-pct <N> --files <files> --summary "..."`""")

            all_ids = ", ".join(bd["id"] for bd in bead_data)
            if template:
                # Use template but replace Task section with sub-tasks
                task_section_marker = "## Task"
                if task_section_marker in template:
                    pre_task = template[:template.index(task_section_marker)]
                else:
                    pre_task = template
                pre_task = pre_task.replace("{bead_id}", bead_data[0]["id"])
                pre_task = pre_task.replace("{bead_title}", f"Batch: {len(bead_data)} tasks")
                pre_task = pre_task.replace("{bead_description}", f"Sequential batch of {len(bead_data)} tasks. Claim and close each one individually.")
                pre_task = pre_task.replace("{target_files}", all_ids)
                pre_task = pre_task.replace("{project_context}", project_context)
                pre_task = pre_task.replace("{epic_context}", epic_context)
                pre_task = pre_task.replace("{feature_context}", feature_context)
                prompt = pre_task + "\n\n## Batch Tasks\n\nYou have {n} sequential sub-tasks. Complete each in order — claim, implement, commit, close — before starting the next.\n\n".format(n=len(bead_data)) + "\n\n".join(sub_tasks)
            else:
                prompt = f"""You are a worker agent executing {len(bead_data)} sequential tasks.

## Project Context
{project_context}

## Epic Context
{epic_context}

## Batch Tasks

Complete each in order — claim, implement, commit, close — before starting the next.

""" + "\n\n".join(sub_tasks)

    # Append integration task guidance if any bead is marked [integration]
    for bd_item in bead_data:
        if "[integration]" in bd_item.get("title", "").lower():
            prompt += ("\n\n## Integration Task — Extended Operations\n"
                       "This is an integration/E2E task. Long-running operations are expected.\n"
                       "- Claim with a time estimate: `python3 .beads/tf.py claim {id} --expected-mins N`\n"
                       "- Heartbeat BEFORE and AFTER every operation >2 minutes: "
                       "`tf.py heartbeat {id} --note \"downloading feed 2/5\"`\n"
                       "- If an external service times out, call `tf.py block` rather than retrying indefinitely\n"
                       "- When the pipeline succeeds and produces output, record results and close immediately "
                       "— do not run secondary verification loops")
            break

    # Append parallel worker warning if --parallel-with is provided
    parallel_with = getattr(args, "parallel_with", "") or ""
    if parallel_with:
        parallel_bead_ids = [b.strip() for b in parallel_with.split(",") if b.strip()]
        parallel_files = []
        for pbid in parallel_bead_ids:
            pbead = _fetch_bead(bd_bin, pbid) or {}
            desc = pbead.get("description", "")
            pfiles = _extract_files_from_description(desc)
            for pf in pfiles:
                parallel_files.append(f"- `{pf}` (bead {pbid})")
        if parallel_files:
            prompt += "\n\n## Parallel Worker Warning\n"
            prompt += "The following files are owned by parallel workers. Do NOT modify them.\n"
            prompt += "If you need any of these files, call `tf.py block` instead.\n\n"
            prompt += "\n".join(parallel_files)

    if getattr(args, "prompt_only", False):
        print(prompt)
        return

    model = reg.get("worker_model", "")

    is_reuse = getattr(args, "reuse", False)
    if getattr(args, "write_file", False) and not is_reuse:
        import tempfile
        fd, path = tempfile.mkstemp(prefix="tf-prompt-", suffix=".md")
        with os.fdopen(fd, "w") as f:
            f.write(prompt)
        _out({"ok": True, "prompt_file": path, "model": model, "beads": bead_ids})
        return

    _out({"ok": True, "prompt": prompt, "model": model, "beads": bead_ids})


def cmd_registry(args: argparse.Namespace) -> None:
    """Query worker registry. Compact output."""
    reg, _ = _load_registry()

    # If --worker-model flag, just print the configured model
    if getattr(args, "worker_model", False):
        print(reg.get("worker_model", ""))
        return

    workers = reg.get("workers", {})

    if args.status:
        workers = {k: v for k, v in workers.items() if v.get("status") == args.status}
    if args.skill:
        workers = {k: v for k, v in workers.items() if v.get("skill") == args.skill}

    # Compact: only essential fields
    out = {}
    for k, v in workers.items():
        out[k] = {
            "s": v.get("status", "?")[0],  # a/i/r/f
            "ctx": v.get("context_pct", 0),
            "bead": v.get("bead", ""),
            "skill": v.get("skill", ""),
        }
        if v.get("notification") == NOTIF_PENDING:
            out[k]["notif"] = NOTIF_PENDING
        if v.get("last_heartbeat"):
            out[k]["hb"] = v["last_heartbeat"]
        if v.get("last_heartbeat_note"):
            out[k]["hb_note"] = v["last_heartbeat_note"]
        if v.get("output_file"):
            out[k]["out"] = v["output_file"]

    _out(out)


def cmd_retire(args: argparse.Namespace) -> None:
    """Mark worker as retired."""
    reg, rp = _load_registry()
    w = reg["workers"].get(args.worker)
    if not w:
        _out({"ok": False, "error": f"worker '{args.worker}' not found"})
        return
    w["status"] = STATUS_RETIRED
    w["retired_at"] = _now()
    _save_registry(reg, rp)
    _out({"ok": True, "worker": args.worker})


def cmd_routing(args: argparse.Namespace) -> None:
    """Add or query skill routing entries."""
    reg, rp = _load_registry()

    if args.add:
        # --add "pattern:domain:prefix"
        parts = args.add.split(":")
        if len(parts) < 3:
            _out({"error": "format: pattern:domain:prefix"})
            return
        pattern, domain, prefix = parts[0], parts[1], parts[2]
        reg["routing"][pattern] = {"domain": domain, "prefix": prefix}
        _save_registry(reg, rp)
        _out({"ok": True})
    else:
        _out(reg.get("routing", {}))


def cmd_sync(args: argparse.Namespace) -> None:
    """Pre-dispatch sync: retire stale workers, return reusable idle workers by skill.

    Call this BEFORE every dispatch decision. It handles all housekeeping:
    1. Auto-retire workers at >=90% context (can't reuse meaningfully)
    2. Auto-retire workers at <40% context (not worth the reuse overhead)
    3. Return available workers grouped by skill domain for reuse matching
    """
    reg, rp = _load_registry()
    now = _now()
    retired = []
    stalled = []
    available = {}  # skill -> [{name, ctx, bead}]
    total_spawned = 0
    total_active = 0
    threshold = reg.get("settings", {}).get("stall_threshold_mins", 20)

    for wname, w in reg["workers"].items():
        total_spawned += 1
        s = w.get("status", "")

        if s == STATUS_ACTIVE:
            total_active += 1
            if _is_stalled(w, threshold):
                stalled.append(_stalled_info(wname, w))
            continue

        if s != STATUS_IDLE:
            continue

        ctx = w.get("context_pct", 0)

        # Auto-retire: too high or too low context
        if ctx >= CTX_HIGH or ctx < CTX_LOW:
            w["status"] = STATUS_RETIRED
            w["retired_at"] = now
            retired.append(wname)
            continue

        # Auto-retire cross-session workers — they're not addressable
        current_session = reg.get("session_id", "")
        if current_session and w.get("spawned_session") and w["spawned_session"] != current_session:
            w["status"] = STATUS_RETIRED
            w["retired_at"] = now
            retired.append(wname)
            continue

        # Skip workers that never called back — they're likely session-ended
        notif = w.get("notification", "")
        if notif == NOTIF_PENDING:
            w["status"] = STATUS_RETIRED
            w["retired_at"] = now
            retired.append(wname)
            continue

        # Skip workers that called worker-close — they've terminated
        if w.get("closed_self"):
            w["status"] = STATUS_RETIRED
            w["retired_at"] = now
            retired.append(wname)
            continue

        # Auto-retire workers idle too long — agent runtime likely ended
        idle_min = _idle_minutes(w)
        idle_timeout = reg.get("settings", {}).get("idle_timeout_mins", 8)
        if idle_min is not None and idle_min > idle_timeout:
            w["status"] = STATUS_RETIRED
            w["retired_at"] = now
            retired.append(wname)
            continue

        # Available for reuse
        skill = w.get("skill", "unknown")
        if skill not in available:
            available[skill] = []
        entry: dict = {"name": wname, "ctx": ctx, "bead": w.get("bead", "")}
        if w.get("agent_id"):
            entry["agent_id"] = w["agent_id"]
        if idle_min is not None:
            entry["idle_min"] = round(idle_min)
        available[skill].append(entry)

    if retired:
        _save_registry(reg, rp)

    idle_count = sum(len(v) for v in available.values())
    # Only workers idle <= 6 min are likely still addressable by the runtime
    fresh_count = sum(
        1 for workers in available.values()
        for w in workers if w.get("idle_min", 0) <= 6
    )
    result: dict = {
        "available": available,
        "retired_now": retired,
        "counts": {"total": total_spawned, STATUS_ACTIVE: total_active, STATUS_IDLE: idle_count, STATUS_RETIRED: len(retired)},
    }
    if stalled:
        result["stalled"] = stalled
    ready_count = getattr(args, "ready_count", 0)
    if ready_count and fresh_count > ready_count * 0.5:
        result["reuse_enforced"] = True
    _out(result)


def cmd_status(args: argparse.Namespace) -> None:
    """Status overview for orchestrator — designed as recovery surface after context compression."""
    reg, rp = _load_registry()
    bd = _bd(rp)
    workers = reg.get("workers", {})

    threshold = reg.get("settings", {}).get("stall_threshold_mins", 20)
    counts: dict = {STATUS_ACTIVE: 0, STATUS_IDLE: 0, STATUS_RETIRED: 0, "failed": 0}
    pending_from = []
    active_workers = []
    stalled_workers = []
    for wname, w in workers.items():
        s = w.get("status", "")
        counts[s] = counts.get(s, 0) + 1
        if w.get("notification") == NOTIF_PENDING:
            pending_from.append({"worker": wname, "bead": w.get("bead", "?")})
        if s == STATUS_ACTIVE:
            active_workers.append({
                "name": wname,
                "bead": w.get("bead", "?"),
                "skill": w.get("skill", "?"),
                "ctx": w.get("context_pct", 0),
            })
            if _is_stalled(w, threshold):
                stalled_workers.append(_stalled_info(wname, w))

    # Get bead counts
    r = _run(f"{bd} list --limit {BD_LIST_LIMIT} --json 2>/dev/null")
    open_beads = blocked = closed = 0
    try:
        beads = json.loads(r.stdout)
        if isinstance(beads, dict):
            beads = beads.get("issues", [])
        for b in beads:
            st = b.get("status", "")
            if st == "closed":
                closed += 1
            elif st == "blocked":
                blocked += 1
            else:
                open_beads += 1
    except (json.JSONDecodeError, TypeError) as e:
        sys.stderr.write(f"tf.py warning: parsing bd list JSON in status: {e}\n")

    # Remove zero counts for non-essential statuses
    w_counts = {k: v for k, v in counts.items() if v > 0 or k in (STATUS_ACTIVE, STATUS_IDLE)}
    result: dict = {
        "w": w_counts,
        "beads": {"open": open_beads, "blocked": blocked, "closed": closed},
    }
    if active_workers:
        result["active"] = active_workers
    if stalled_workers:
        result["stalled"] = stalled_workers
    if pending_from:
        result["pending_notif"] = pending_from
    _out(result)


# ── CLI ──────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(prog="tf", description="TreeFlow state manager")
    sub = p.add_subparsers(dest="cmd")

    # init
    s = sub.add_parser("init")
    s.add_argument("plan_name")
    s.add_argument("--bd-path", default="", dest="bd_path")
    s.add_argument("--worker-model", default="", dest="worker_model")
    s.add_argument("--idle-timeout", type=int, default=0, dest="idle_timeout")

    # dispatch
    s = sub.add_parser("dispatch")
    s.add_argument("worker")
    s.add_argument("bead_id")
    s.add_argument("--skill", required=True)
    s.add_argument("--output-file", default="", dest="output_file")
    s.add_argument("--agent-id", default="", dest="agent_id")

    # worker-close
    s = sub.add_parser("worker-close")
    s.add_argument("bead_id", nargs="?", default="")
    s.add_argument("--beads", default="")
    s.add_argument("--context-pct", type=int, default=0, dest="context_pct")
    s.add_argument("--files", default="")
    s.add_argument("--summary", default="")
    s.add_argument("--force", action="store_true", default=False)

    # claim
    s = sub.add_parser("claim")
    s.add_argument("bead_id")
    s.add_argument("--expected-mins", type=int, default=0, dest="expected_mins")

    # block
    s = sub.add_parser("block")
    s.add_argument("bead_id")
    s.add_argument("--question", required=True)
    s.add_argument("--context", default="")

    # discover
    s = sub.add_parser("discover")
    s.add_argument("bead_id")
    s.add_argument("--title", required=True)
    s.add_argument("--description", default="")

    # heartbeat
    s = sub.add_parser("heartbeat")
    s.add_argument("bead_id")
    s.add_argument("--note", default="")

    # stalled
    s = sub.add_parser("stalled")
    s.add_argument("--threshold-mins", type=int, default=0, dest="threshold_mins")

    # bd-path
    sub.add_parser("bd-path")

    # notify
    s = sub.add_parser("notify")
    s.add_argument("worker")
    s.add_argument("bead_id", nargs="?", default=None)
    s.add_argument("--context-pct", type=int, default=0, dest="context_pct")
    s.add_argument("--summary", default="")
    s.add_argument("--skill", default="")
    s.add_argument("--agent-id", default="", dest="agent_id")
    s.add_argument("--files", default="")
    s.add_argument("--gotcha", default="")

    # batch-notify
    s = sub.add_parser("batch-notify")
    s.add_argument("--pairs", required=True)
    s.add_argument("--context-pct", type=int, default=0, dest="context_pct")
    s.add_argument("--summary", default="")
    s.add_argument("--files", default="")
    s.add_argument("--gotcha", default="")

    # phase-gate
    s = sub.add_parser("phase-gate")
    s.add_argument("epic_id")

    # smoke-test
    s = sub.add_parser("smoke-test")
    s.add_argument("--build-cmd", default="", dest="build_cmd")
    s.add_argument("--beads", default="")

    # registry
    s = sub.add_parser("registry")
    s.add_argument("--status", default="")
    s.add_argument("--skill", default="")
    s.add_argument("--worker-model", action="store_true", dest="worker_model")

    # retire
    s = sub.add_parser("retire")
    s.add_argument("worker")

    # routing
    s = sub.add_parser("routing")
    s.add_argument("--add", default="")

    # conflict-check
    s = sub.add_parser("conflict-check")
    s.add_argument("--beads", required=True)

    # wave-plan
    s = sub.add_parser("wave-plan")
    s.add_argument("--beads", required=True)

    # sync
    s = sub.add_parser("sync")
    s.add_argument("--ready-count", type=int, default=0, dest="ready_count")

    # status
    sub.add_parser("status")

    # ready (filtered bd ready + supplement)
    sub.add_parser("ready")

    # recover (find orphaned in-progress beads)
    sub.add_parser("recover")

    # dedup (detect/close duplicate bead titles)
    s = sub.add_parser("dedup")
    s.add_argument("--apply", action="store_true")
    s.add_argument("--keep", choices=["newest", "oldest"], default="newest")

    # ad-hoc (register informal task without bead)
    s = sub.add_parser("ad-hoc")
    s.add_argument("--name", required=True)
    s.add_argument("--worker", required=True)
    s.add_argument("--skill", default="general")

    # dep (idempotent bd dep wrapper)
    s = sub.add_parser("dep")
    s.add_argument("blocker")
    s.add_argument("blocked")

    # import-deps (bulk dep import from deps.txt)
    s = sub.add_parser("import-deps")
    s.add_argument("file")
    s.add_argument("--validate", action="store_true")

    # close (normalized bd close wrapper)
    s = sub.add_parser("close")
    s.add_argument("bead_id")
    s.add_argument("--reason", default="completed")

    # create (bd create -f wrapper with clean JSON)
    s = sub.add_parser("create")
    s.add_argument("file")

    # validate-plan
    s = sub.add_parser("validate-plan")
    s.add_argument("file")
    s.add_argument("--check-parallelism", action="store_true", dest="check_parallelism")
    s.add_argument("--no-files-check", action="store_true", dest="no_files_check")

    # wire-plan
    s = sub.add_parser("wire-plan")
    s.add_argument("file")
    s.add_argument("--ids", default="")

    # update-context
    s = sub.add_parser("update-context")
    s.add_argument("--bead", required=True, dest="bead_id")
    s.add_argument("--worker", required=True)
    s.add_argument("--summary", required=True)
    s.add_argument("--files", default="")
    s.add_argument("--gotcha", default="")

    # phase-summary
    s = sub.add_parser("phase-summary")
    s.add_argument("--epics", required=True)

    # phase-complete
    s = sub.add_parser("phase-complete")
    s.add_argument("--epic", required=True, dest="epic_id")
    s.add_argument("--build-cmd", default="", dest="build_cmd")
    s.add_argument("--phase-num", default="", dest="phase_num")

    # worker-prompt
    s = sub.add_parser("worker-prompt")
    s.add_argument("--beads", required=True)
    s.add_argument("--reuse", action="store_true")
    s.add_argument("--prior-bead", default="", dest="prior_bead")
    s.add_argument("--prompt-only", action="store_true", dest="prompt_only")
    s.add_argument("--write-file", action="store_true", dest="write_file")
    s.add_argument("--parallel-with", default="", dest="parallel_with")

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        sys.exit(1)

    cmds = {
        "init": cmd_init,
        "dispatch": cmd_dispatch,
        "worker-close": cmd_worker_close,
        "claim": cmd_claim,
        "block": cmd_block,
        "discover": cmd_discover,
        "heartbeat": cmd_heartbeat,
        "stalled": cmd_stalled,
        "bd-path": cmd_bd_path,
        "notify": cmd_notify,
        "batch-notify": cmd_batch_notify,
        "phase-gate": cmd_phase_gate,
        "smoke-test": cmd_smoke_test,
        "conflict-check": cmd_conflict_check,
        "wave-plan": cmd_wave_plan,
        "registry": cmd_registry,
        "retire": cmd_retire,
        "routing": cmd_routing,
        "sync": cmd_sync,
        "status": cmd_status,
        "ready": cmd_ready,
        "recover": cmd_recover,
        "dedup": cmd_dedup,
        "ad-hoc": cmd_ad_hoc,
        "dep": cmd_dep,
        "import-deps": cmd_import_deps,
        "close": cmd_close,
        "create": cmd_create,
        "validate-plan": cmd_validate_plan,
        "wire-plan": cmd_wire_plan,
        "update-context": cmd_update_context,
        "phase-summary": cmd_phase_summary,
        "phase-complete": cmd_phase_complete,
        "worker-prompt": cmd_worker_prompt,
    }
    cmds[args.cmd](args)


if __name__ == "__main__":
    main()
