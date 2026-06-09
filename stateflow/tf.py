#!/usr/bin/env python3
"""tf.py — StateFlow state manager. Deterministic coordination for the stateflow orchestrator.

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

REGISTRY_FILE = "registry.json"
BD_LIST_LIMIT = "500"

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


def _bd(registry_path: Path | None = None) -> str:
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
        except (json.JSONDecodeError, OSError):
            pass
    return _resolve_bd()


def _bd_cmd(subcmd: str, registry_path: Path | None = None) -> str:
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


def _load_registry(path: Path | None = None) -> tuple[dict, Path]:
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


def _update_heartbeat(reg: dict, rp: Path, note: str, worker_name: str | None = None) -> None:
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
    if worker.get("status") != "active":
        return False
    last_hb = worker.get("last_heartbeat") or worker.get("dispatched_at")
    if not last_hb:
        return False
    now = datetime.now(timezone.utc)
    # If worker has a deadline, check both deadline AND silence — a worker past deadline
    # but still heartbeating is slow, not stalled.
    expected = worker.get("expected_completion_at")
    if expected and now > _parse_ts(expected):
        last_dt = _parse_ts(last_hb)
        elapsed = (now - last_dt).total_seconds() / 60
        return elapsed > threshold_mins
    elif expected:
        return False
    last_dt = _parse_ts(last_hb)
    elapsed = (now - last_dt).total_seconds() / 60
    return elapsed > threshold_mins


def _idle_minutes(worker: dict) -> float | None:
    """Minutes since a worker became idle. None if not idle."""
    idle_since = worker.get("idle_since")
    if not idle_since or worker.get("status") != "idle":
        return None
    return (datetime.now(timezone.utc) - _parse_ts(idle_since)).total_seconds() / 60


def _stalled_info(wname: str, w: dict) -> dict:
    """Build a compact stalled-worker dict for output."""
    return {
        "worker": wname,
        "bead": w.get("bead", "?"),
        "last_hb": w.get("last_heartbeat", w.get("dispatched_at", "?")),
        "note": w.get("last_heartbeat_note", ""),
    }


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
        "settings": {"stall_threshold_mins": 20},
        "workers": {},
        "routing": {},
        "phases": {},
    }
    _save_registry(data, reg)

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
                f.write(f"\n# StateFlow context (auto-added)\n{marker}\n")
    else:
        gitignore.write_text(f"# StateFlow context (auto-added)\n{marker}\n")

    _out({"ok": True, "path": str(ctx), "bd_path": bd_path, "worker_model": worker_model})


def cmd_dispatch(args: argparse.Namespace) -> None:
    """Record worker dispatch — sets active + notification pending."""
    reg, rp = _load_registry()
    now = _now()

    r_ut = _run("git ls-files --others --exclude-standard")
    pre_untracked = [f for f in r_ut.stdout.strip().split("\n") if f]

    reg["workers"][args.worker] = {
        "status": "active",
        "skill": args.skill,
        "context_pct": reg.get("workers", {}).get(args.worker, {}).get("context_pct", 0),
        "bead": args.bead_id,
        "notification": "pending",
        "dispatched_at": now,
        "dispatch_sha": _run("git rev-parse HEAD").stdout.strip(),
        "dispatch_untracked": pre_untracked,
        "last_heartbeat": now,
        "last_heartbeat_note": None,
        "heartbeat_history": [],
        "output_file": getattr(args, "output_file", "") or "",
        "spawned_session": reg.get("session_id", ""),
    }
    _save_registry(reg, rp)
    _out({"ok": True, "worker": args.worker, "bead": args.bead_id})


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
    except Exception:
        pass

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
    elif args.files:
        files = [f.strip() for f in args.files.split(",")]
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
        _out({"ok": False, "errors": errors, "hint": "commit your changes first"})
        return

    # Scan for dead-code markers (warnings only — does not block close)
    if args.files:
        pattern = r"#\[allow\(dead_code\)\]|# noqa: F841|// @ts-ignore|\bTODO\b|\bFIXME\b|\bHACK\b"
        file_args = " ".join(shlex.quote(f) for f in files)
        r = _run(f"grep -nE '{pattern}' {file_args}")
        if r.stdout.strip():
            for line in r.stdout.strip().split("\n")[:10]:
                warnings.append(f"dead-code marker: {line.strip()}")

    # 2. Check last commit message for task/bead number anti-pattern
    r = _run("git log -1 --pretty=%s")
    msg = r.stdout.strip()
    if msg and re.search(r'\bTask\s+\d+', msg, re.IGNORECASE):
        _out({"ok": False, "errors": [f"commit message contains task number: '{msg}'. Amend to remove 'Task N:' prefix"], "hint": "git commit --amend -m 'feat: <description without task number>'"})
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
        r = _run(f"{bd} show {bid} --json")
        if r.returncode != 0:
            close_errors.append(f"{bid}: bd show failed: {r.stderr.strip()[:100]}")
            continue

        try:
            data = json.loads(r.stdout)
            bead = data[0] if isinstance(data, list) else data
        except (json.JSONDecodeError, IndexError):
            close_errors.append(f"{bid}: bd show returned invalid JSON")
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
        r = _run(f'{bd} close {bid} --reason "{reason}" --json')
        if r.returncode != 0:
            close_errors.append(f"{bid}: bd close failed: {r.stderr.strip()[:100]}")
            continue

        # 6. Verify close
        r = _run(f"{bd} show {bid} --json")
        try:
            data = json.loads(r.stdout)
            bead = data[0] if isinstance(data, list) else data
        except (json.JSONDecodeError, IndexError):
            bead = {}

        if bead.get("status") != "closed":
            _run(f'{bd} close {bid} --reason "{reason}" --json')
            r = _run(f"{bd} show {bid} --json")
            try:
                data = json.loads(r.stdout)
                bead = data[0] if isinstance(data, list) else data
            except (json.JSONDecodeError, IndexError):
                bead = {}
            if bead.get("status") != "closed":
                close_errors.append(f"{bid}: still not closed after retry")
                continue

        closed_beads.append({"id": bid})

    try:
        reg, reg_path = _load_registry(rp)
        _update_heartbeat(reg, reg_path, f"closed {','.join(b['id'] for b in closed_beads)}")
    except Exception:
        pass

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
    except Exception:
        pass  # Best-effort — claim still succeeded

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
    question = args.question.replace('"', '\\"')
    context = args.context.replace('"', '\\"') if args.context else question
    r = _run(f'{bd} create "Question: {question}" -t task -p 1 --deps "{args.bead_id}" -d "{context}" --json')
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
    except Exception:
        pass

    _out({"ok": True, "bead": args.bead_id, "status": "blocked", "question_bead": q_id})


def cmd_discover(args: argparse.Namespace) -> None:
    """Worker creates a discovered-work bead."""
    rp = _registry_path()
    bd = _bd(rp)
    title = args.title.replace('"', '\\"')
    desc = args.description.replace('"', '\\"') if args.description else title
    r = _run(f'{bd} create "Found: {title}" -t task -p 2 --deps "discovered-from:{args.bead_id}" -d "{desc}" --json')
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
    except Exception:
        pass

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
    print(_bd(rp))


def cmd_notify(args: argparse.Namespace) -> None:
    """Orchestrator calls on task-notification. Updates registry atomically."""
    reg, rp = _load_registry()
    now = _now()

    worker = reg["workers"].get(args.worker)
    if not worker:
        # Worker not in registry — add it
        reg["workers"][args.worker] = {
            "status": "idle",
            "skill": args.skill or "unknown",
            "context_pct": args.context_pct,
            "bead": args.bead_id,
            "notification": "received",
            "idle_since": now,
            "summary": (args.summary or "")[:200],
        }
        _save_registry(reg, rp)
        _out({"ok": True, "late": False, "worker": args.worker})
        return

    late = worker.get("notification") == "received"

    # Auto-retire workers at 90%+ context — they can never be meaningfully reused
    auto_retired = args.context_pct >= 90
    worker["status"] = "retired" if auto_retired else "idle"
    worker["context_pct"] = args.context_pct
    worker["notification"] = "reconciled" if late else "received"
    if auto_retired:
        worker["retired_at"] = now
    else:
        worker["idle_since"] = now
    worker["bead"] = args.bead_id
    if args.summary:
        worker["summary"] = args.summary[:200]

    _save_registry(reg, rp)
    result = {"ok": True, "late": late, "worker": args.worker, "ctx": args.context_pct}
    if auto_retired:
        result["auto_retired"] = True

    # Include bead status so orchestrator doesn't need a separate bd show call
    result["bead_status"] = "unknown"
    bd = _bd(rp)
    r = _run(f"{bd} show {args.bead_id} --json")
    try:
        data = json.loads(r.stdout)
        bead = data[0] if isinstance(data, list) else data
        result["bead_status"] = bead.get("status", "unknown")
    except (json.JSONDecodeError, IndexError):
        bead = {}

    # Auto-close bead if worker didn't call worker-close
    if result["bead_status"] == "in_progress":
        cr = _run(f'{bd} close {args.bead_id} --reason "auto-closed on worker notification" --force --json')
        if cr.returncode == 0:
            result["bead_status"] = "closed"
            result["auto_closed"] = True

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
            except (json.JSONDecodeError, IndexError):
                pass

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
                "status": "idle",
                "skill": "unknown",
                "context_pct": ctx_pct,
                "bead": bead_id,
                "notification": "received",
                "idle_since": now,
            }
            results.append({"worker": worker_name, "bead": bead_id, "ok": True})
            continue

        auto_retired = ctx_pct >= 90
        worker["status"] = "retired" if auto_retired else "idle"
        worker["context_pct"] = ctx_pct
        worker["notification"] = "received"
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

    # Check all workers have notification=received
    for wname, w in reg["workers"].items():
        notif = w.get("notification", "")
        if notif == "pending":
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
            r = _run(f"{bd} show {bid} --json")
            if r.returncode != 0:
                result["wiring"].append({"bead": bid, "error": "cannot read bead"})
                continue
            try:
                bead = json.loads(r.stdout)
            except json.JSONDecodeError:
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


def _extract_files_from_description(desc: str) -> list[str]:
    """Extract file paths from a bead description's Files: line."""
    for line in desc.split("\n"):
        stripped = line.strip()
        # Match "Files:" at line start (with optional markdown bold/backticks)
        if re.match(r"^(?:\*\*)?Files:?\*?\*?\s*", stripped, re.IGNORECASE):
            # Everything after "Files:" on that line
            after = re.sub(r"^(?:\*\*)?Files:?\*?\*?\s*", "", stripped, flags=re.IGNORECASE)
            # Strip backticks and split on commas
            return [f.strip().strip("`") for f in after.split(",") if f.strip().strip("`")]
    return []


def _extract_plan_section(plan_path: str, title: str) -> str:
    """Extract the full section for a task from a plan file, matched by title."""
    p = Path(plan_path)
    if not p.exists():
        return ""
    content = p.read_text()
    lines = content.split("\n")
    norm_title = re.sub(r'\s+', ' ', title.strip().lower())

    collecting = False
    section_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            if collecting:
                break
            heading = stripped[3:].strip()
            if re.sub(r'\s+', ' ', heading.lower()) == norm_title:
                collecting = True
                continue
        elif collecting:
            section_lines.append(line)

    return "\n".join(section_lines).strip()


def cmd_conflict_check(args: argparse.Namespace) -> None:
    """Check file conflicts between ready beads for parallelism safety."""
    rp = _registry_path()
    bd = _bd(rp)
    bead_ids = [b.strip() for b in args.beads.split(",") if b.strip()]

    # Fetch descriptions and extract file lists
    bead_files: dict[str, list[str]] = {}
    unparseable: list[str] = []
    for bid in bead_ids:
        r = _run(f"{bd} show {bid} --json")
        if r.returncode != 0:
            continue
        try:
            data = json.loads(r.stdout)
            bead = data[0] if isinstance(data, list) else data
        except (json.JSONDecodeError, IndexError):
            continue
        desc = bead.get("description", "")
        files = _extract_files_from_description(desc)
        if files:
            bead_files[bid] = files
        else:
            unparseable.append(bid)

    # Build file → [bead_ids] map
    file_map: dict[str, list[str]] = {}
    for bid, files in bead_files.items():
        for f in files:
            file_map.setdefault(f, []).append(bid)

    # Identify conflicts (file touched by 2+ beads)
    conflicts = {f: bids for f, bids in file_map.items() if len(bids) > 1}

    # Compute parallel groups: beads with no file overlap can run together
    conflicting_beads = set()
    for bids in conflicts.values():
        conflicting_beads.update(bids)
    safe = [bid for bid in bead_ids if bid not in conflicting_beads and bid in bead_files]

    result: dict = {
        "bead_files": bead_files,
        "conflicts": conflicts,
        "safe_parallel": safe,
    }
    if unparseable:
        result["unparseable"] = unparseable
    if conflicts:
        # Build serial groups: sets of beads that share files
        serial: list[list[str]] = []
        seen: set[str] = set()
        for bids in conflicts.values():
            group = sorted(set(bids) - seen)
            if group:
                serial.append(sorted(set(bids)))
                seen.update(bids)
        result["serial_groups"] = serial
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


def cmd_close(args: argparse.Namespace) -> None:
    """Close a bead via bd close, normalizing output to a simple JSON object."""
    rp = _registry_path()
    bd = _bd(rp)
    reason = args.reason or "completed"
    reason_escaped = reason.replace('"', '\\"')
    r = _run(f'{bd} close {args.bead_id} --reason "{reason_escaped}" --json')
    if r.returncode != 0:
        _out({"ok": False, "error": f"bd close failed: {r.stderr.strip()[:200]}"})
        return
    # Verify via bd show
    r2 = _run(f"{bd} show {args.bead_id} --json")
    status = "unknown"
    try:
        data = json.loads(r2.stdout)
        bead = data[0] if isinstance(data, list) else data
        status = bead.get("status", "unknown") if isinstance(bead, dict) else "unknown"
    except (json.JSONDecodeError, IndexError):
        pass
    _out({"ok": True, "id": args.bead_id, "status": status})


def cmd_validate_plan(args: argparse.Namespace) -> None:
    """Validate a plan markdown file for bd create -f compatibility."""
    plan_path = Path(args.file)
    if not plan_path.exists():
        _out({"ok": False, "error": f"file not found: {args.file}"})
        return

    content = plan_path.read_text()
    lines = content.split("\n")
    errors = []
    issues = []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r'^-{3,}\s*$', stripped):
            errors.append(f"line {i}: '---' horizontal rule breaks bd parser — remove it")
        elif stripped.startswith("## "):
            title = stripped[3:].strip()
            issues.append({"line": i, "title": title})

    # Build per-issue bodies and detect epics
    epic_indices: set[int] = set()
    for idx, iss in enumerate(issues):
        start = iss["line"] - 1
        end = issues[idx + 1]["line"] - 1 if idx + 1 < len(issues) else len(lines)
        iss["body"] = "\n".join(lines[start:end])
        for i in range(start, end):
            if re.match(r'^###\s+[Tt]ype\s*$', lines[i].strip()):
                for j in range(i + 1, min(i + 4, end)):
                    type_line = lines[j].strip()
                    if type_line and not type_line.startswith("#"):
                        if type_line.lower() == "epic":
                            epic_indices.add(idx)
                        break
                break
    epics = len(epic_indices)

    # Check for unrecognized ### headers that bd may misparse as metadata
    recognized_h3 = {
        "type", "priority", "description", "design", "acceptance criteria",
        "assignee", "labels", "dependencies", "soft dependencies", "files",
    }
    for idx, iss in enumerate(issues):
        start = iss["line"] - 1
        end = issues[idx + 1]["line"] - 1 if idx + 1 < len(issues) else len(lines)
        for li in range(start + 1, end):
            stripped = lines[li].strip()
            h3_match = re.match(r'^###\s+(.+)$', stripped)
            if h3_match:
                header_text = h3_match.group(1).strip().lower()
                if header_text not in recognized_h3:
                    errors.append(
                        f"line {li + 1}: '{stripped}' looks like a metadata header but is not recognized by bd — remove or convert to bold text"
                    )

    # Check that non-epic issues have a Files: line
    missing_files = []
    for idx, iss in enumerate(issues):
        if idx in epic_indices:
            continue
        files = _extract_files_from_description(iss.get("body", ""))
        if not files:
            missing_files.append(iss["title"])
    if missing_files:
        for title in missing_files:
            errors.append(f"Task '{title}' missing Files: section — required for conflict detection")

    # Parallelism analysis: scan ### Dependencies sections
    warnings = []
    tasks_count = len(issues) - epics
    dep_count = 0
    has_blocks = 0
    in_deps = False
    for line in lines:
        stripped = line.strip()
        if re.match(r'^###\s+[Dd]ependencies\s*$', stripped):
            in_deps = True
            dep_count += 1
            continue
        if stripped.startswith("##"):
            in_deps = False
            continue
        if in_deps and stripped:
            if "blocks:" in stripped.lower():
                has_blocks += 1

    if tasks_count > 3:
        if dep_count == 0:
            warnings.append("No ### Dependencies sections found — remember to add parallel groups after creation")
        elif has_blocks > 0 and has_blocks >= tasks_count - 1:
            roots = tasks_count - has_blocks
            if roots <= 1:
                warnings.append("Plan is fully sequential — no parallel execution possible. Consider splitting into parallel groups.")

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
    if issues:
        result["dry_run"] = [{"title": iss["title"]} for iss in issues]
    _out(result)


def cmd_wire_plan(args: argparse.Namespace) -> None:
    """Auto-wire parent-child and blocking deps from a plan file + bd create output."""
    plan_path = Path(args.file)
    if not plan_path.exists():
        _out({"ok": False, "error": f"file not found: {args.file}"})
        return

    # Load bd create -f --json output: list of created issues with titles + IDs
    ids_path = Path(args.ids)
    if not ids_path.exists():
        _out({"ok": False, "error": f"ids file not found: {args.ids}"})
        return
    try:
        created = json.loads(ids_path.read_text())
        if isinstance(created, dict):
            created = created.get("issues", created.get("created", []))
    except json.JSONDecodeError:
        _out({"ok": False, "error": "ids file is not valid JSON"})
        return

    # Build title→ID mapping (normalize titles for fuzzy matching)
    def _norm(t: str) -> str:
        return re.sub(r'\s+', ' ', t.strip().lower())

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
    current: dict | None = None

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

    # Store plan file path in registry for worker-prompt fallback
    reg, reg_path = _load_registry(rp)
    reg["plan_file"] = str(plan_path.resolve())
    _save_registry(reg, reg_path)

    result: dict = {
        "ok": len(errors) == 0,
        "parent_child": parent_child_wired,
        "blocking": blocking_wired,
        "total_issues": len(issues),
    }
    if errors:
        result["errors"] = errors[:20]
    _out(result)


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
    r = _run(f"{bd_bin} show {args.bead_id} --json")
    try:
        data = json.loads(r.stdout)
        bead = data[0] if isinstance(data, list) else data
        title = bead.get("title", bead.get("name", args.bead_id))
        parent_id = bead.get("parent", "")
        if parent_id:
            r2 = _run(f"{bd_bin} show {parent_id} --json")
            try:
                pdata = json.loads(r2.stdout)
                parent = pdata[0] if isinstance(pdata, list) else pdata
                epic_slug = _slugify(parent.get("title", parent.get("name", parent_id)))
            except (json.JSONDecodeError, IndexError):
                epic_slug = _slugify(parent_id)
    except (json.JSONDecodeError, IndexError):
        pass

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
        r = _run(f"{bd} show {epic_id} --json")
        epic_title = epic_id
        try:
            data = json.loads(r.stdout)
            epic = data[0] if isinstance(data, list) else data
            epic_title = epic.get("title", epic.get("name", epic_id))
        except (json.JSONDecodeError, IndexError):
            pass

        r = _run(f"{bd} list --parent {epic_id} --limit {BD_LIST_LIMIT} --json")
        children = []
        if r.returncode == 0 and r.stdout.strip():
            json_start = next((i for i, ch in enumerate(r.stdout.strip()) if ch in ("[", "{")), -1)
            if json_start >= 0:
                try:
                    children = json.loads(r.stdout.strip()[json_start:])
                    if isinstance(children, dict):
                        children = children.get("issues", [])
                except json.JSONDecodeError:
                    pass

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
        except json.JSONDecodeError:
            pass
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

    for wname, w in reg.get("workers", {}).items():
        if w.get("notification") == "pending":
            blocking.append({"worker": wname, "bead": w.get("bead", "?"), "reason": "notification pending"})

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
            except (ValueError, TypeError):
                pass

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

    # Read context layers
    project_context = ""
    wc_path = ctx / "worker-context.md"
    if wc_path.exists():
        project_context = wc_path.read_text()

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
        r = _run(f"{bd_bin} show {bid} --json")
        try:
            data = json.loads(r.stdout)
            bead = data[0] if isinstance(data, list) else data
        except (json.JSONDecodeError, IndexError):
            bead = {"id": bid, "title": bid, "description": ""}
        bead.setdefault("id", bid)
        bead.setdefault("title", bead.get("name", bid))
        bead.setdefault("description", "")
        # Fallback: if description is empty, extract from plan file
        if not bead["description"].strip():
            plan_file = reg.get("plan_file", "")
            if plan_file:
                bead["description"] = _extract_plan_section(plan_file, bead["title"])
        bead["target_files"] = _extract_files_from_description(bead["description"])
        bead_data.append(bead)

    # Determine epic context
    epic_context = "N/A"
    feature_context = "N/A"
    if bead_data:
        parent_id = bead_data[0].get("parent", "")
        if parent_id:
            r = _run(f"{bd_bin} show {parent_id} --json")
            try:
                pdata = json.loads(r.stdout)
                parent = pdata[0] if isinstance(pdata, list) else pdata
                slug = _slugify(parent.get("title", parent.get("name", parent_id)))
            except (json.JSONDecodeError, IndexError):
                slug = _slugify(parent_id)
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

    if getattr(args, "prompt_only", False):
        print(prompt)
        return

    model = reg.get("worker_model", "")
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
        if v.get("notification") == "pending":
            out[k]["notif"] = "pending"
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
    w["status"] = "retired"
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

        if s == "active":
            total_active += 1
            if _is_stalled(w, threshold):
                stalled.append(_stalled_info(wname, w))
            continue

        if s != "idle":
            continue

        ctx = w.get("context_pct", 0)

        # Auto-retire: too high or too low context
        if ctx >= 90 or ctx < 40:
            w["status"] = "retired"
            w["retired_at"] = now
            retired.append({"worker": wname, "ctx": ctx, "reason": "high_ctx" if ctx >= 90 else "low_ctx"})
            continue

        # Auto-retire cross-session workers — they're not addressable
        current_session = reg.get("session_id", "")
        if current_session and w.get("spawned_session") and w["spawned_session"] != current_session:
            w["status"] = "retired"
            w["retired_at"] = now
            retired.append({"worker": wname, "ctx": ctx, "reason": "cross_session"})
            continue

        # Skip workers that never called back — they're likely session-ended
        notif = w.get("notification", "")
        if notif == "pending":
            w["status"] = "retired"
            w["retired_at"] = now
            retired.append({"worker": wname, "ctx": ctx, "reason": "never_notified"})
            continue

        # Available for reuse — flag stale idle workers
        skill = w.get("skill", "unknown")
        if skill not in available:
            available[skill] = []
        entry: dict = {"name": wname, "ctx": ctx, "bead": w.get("bead", "")}
        idle_min = _idle_minutes(w)
        if idle_min is not None and idle_min > 30:
            entry["stale"] = True
            entry["idle_min"] = round(idle_min)
        available[skill].append(entry)

    if retired:
        _save_registry(reg, rp)

    idle_count = sum(len(v) for v in available.values())
    addressable_count = sum(
        1 for workers in available.values() for w in workers if not w.get("stale")
    )
    result: dict = {
        "available": available,
        "retired_now": retired,
        "counts": {"total": total_spawned, "active": total_active, "idle": idle_count, "addressable": addressable_count, "retired": len(retired)},
    }
    if stalled:
        result["stalled"] = stalled
    ready_count = getattr(args, "ready_count", 0)
    if ready_count and addressable_count > ready_count * 0.5:
        result["reuse_enforced"] = True
    _out(result)


def cmd_status(args: argparse.Namespace) -> None:
    """Status overview for orchestrator — designed as recovery surface after context compression."""
    reg, rp = _load_registry()
    bd = _bd(rp)
    workers = reg.get("workers", {})

    threshold = reg.get("settings", {}).get("stall_threshold_mins", 20)
    counts = {"active": 0, "idle": 0, "retired": 0, "failed": 0}
    pending_from = []
    active_workers = []
    stalled_workers = []
    for wname, w in workers.items():
        s = w.get("status", "")
        counts[s] = counts.get(s, 0) + 1
        if w.get("notification") == "pending":
            pending_from.append({"worker": wname, "bead": w.get("bead", "?")})
        if s == "active":
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
    except (json.JSONDecodeError, TypeError):
        pass

    result: dict = {
        "w": counts,
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
    p = argparse.ArgumentParser(prog="tf", description="StateFlow state manager")
    sub = p.add_subparsers(dest="cmd")

    # init
    s = sub.add_parser("init")
    s.add_argument("plan_name")
    s.add_argument("--bd-path", default="", dest="bd_path")
    s.add_argument("--worker-model", default="", dest="worker_model")

    # dispatch
    s = sub.add_parser("dispatch")
    s.add_argument("worker")
    s.add_argument("bead_id")
    s.add_argument("--skill", required=True)
    s.add_argument("--output-file", default="", dest="output_file")

    # worker-close
    s = sub.add_parser("worker-close")
    s.add_argument("bead_id", nargs="?", default="")
    s.add_argument("--beads", default="")
    s.add_argument("--context-pct", type=int, default=0, dest="context_pct")
    s.add_argument("--files", default="")
    s.add_argument("--summary", default="")

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
    s.add_argument("bead_id")
    s.add_argument("--context-pct", type=int, default=0, dest="context_pct")
    s.add_argument("--summary", default="")
    s.add_argument("--skill", default="")

    # batch-notify
    s = sub.add_parser("batch-notify")
    s.add_argument("--pairs", required=True)
    s.add_argument("--context-pct", type=int, default=0, dest="context_pct")

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

    # sync
    s = sub.add_parser("sync")
    s.add_argument("--ready-count", type=int, default=0, dest="ready_count")

    # status
    sub.add_parser("status")

    # dep (idempotent bd dep wrapper)
    s = sub.add_parser("dep")
    s.add_argument("blocker")
    s.add_argument("blocked")

    # close (normalized bd close wrapper)
    s = sub.add_parser("close")
    s.add_argument("bead_id")
    s.add_argument("--reason", default="completed")

    # validate-plan
    s = sub.add_parser("validate-plan")
    s.add_argument("file")
    s.add_argument("--check-parallelism", action="store_true", dest="check_parallelism")

    # wire-plan
    s = sub.add_parser("wire-plan")
    s.add_argument("file")
    s.add_argument("--ids", required=True)

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
        "registry": cmd_registry,
        "retire": cmd_retire,
        "routing": cmd_routing,
        "sync": cmd_sync,
        "status": cmd_status,
        "dep": cmd_dep,
        "close": cmd_close,
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
