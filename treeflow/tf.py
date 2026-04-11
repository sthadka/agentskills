#!/usr/bin/env python3
"""tf.py — TreeFlow state manager. Deterministic coordination for the treeflow orchestrator.

All output is compact single-line JSON for token efficiency.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_FILE = "registry.json"

# Common locations where bd might be installed
_BD_SEARCH_PATHS = [
    Path.home() / ".local" / "bin" / "bd",
    Path("/usr/local/bin/bd"),
    Path.home() / "go" / "bin" / "bd",
    Path.home() / ".cargo" / "bin" / "bd",
]


def _resolve_bd() -> str:
    """Resolve bd binary path. Tries shutil.which, then common locations, then bare 'bd'."""
    found = shutil.which("bd")
    if found:
        return found
    for p in _BD_SEARCH_PATHS:
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return "bd"


def _bd(registry_path: Path | None = None) -> str:
    """Get bd binary path from registry, falling back to _resolve_bd()."""
    if registry_path:
        try:
            with open(registry_path) as f:
                reg = json.load(f)
            bd_path = reg.get("bd_path", "")
            if bd_path and (Path(bd_path).exists() or shutil.which(bd_path)):
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
    # Find the context dir that contains registry.json
    for child in bd.iterdir():
        if child.is_dir() and child.name.startswith("context-"):
            rp = child / REGISTRY_FILE
            if rp.exists():
                return rp
    # Fallback: return first context dir
    for child in bd.iterdir():
        if child.is_dir() and child.name.startswith("context-"):
            return child / REGISTRY_FILE
    sys.exit('{"error":"no context directory found in .beads/"}')


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


def _run(cmd: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def _out(obj: dict) -> None:
    print(json.dumps(obj, separators=(",", ":")))


# ── Subcommands ──────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize context directory and registry."""
    bd = _beads_dir()
    ctx = bd / f"context-{args.plan_name}"
    ctx.mkdir(parents=True, exist_ok=True)

    reg = ctx / REGISTRY_FILE
    if reg.exists():
        _out({"ok": True, "msg": "already exists", "path": str(ctx)})
        return

    bd_path = _resolve_bd()
    worker_model = getattr(args, "worker_model", "") or ""
    data = {
        "plan_name": args.plan_name,
        "bd_path": bd_path,
        "worker_model": worker_model,
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
                f.write(f"\n# TreeFlow context (auto-added)\n{marker}\n")
    else:
        gitignore.write_text(f"# TreeFlow context (auto-added)\n{marker}\n")

    _out({"ok": True, "path": str(ctx), "bd_path": bd_path, "worker_model": worker_model})


def cmd_dispatch(args: argparse.Namespace) -> None:
    """Record worker dispatch — sets active + notification pending."""
    reg, rp = _load_registry()
    now = _now()

    reg["workers"][args.worker] = {
        "status": "active",
        "skill": args.skill,
        "context_pct": reg.get("workers", {}).get(args.worker, {}).get("context_pct", 0),
        "bead": args.bead_id,
        "notification": "pending",
        "dispatched_at": now,
    }
    _save_registry(reg, rp)
    _out({"ok": True, "worker": args.worker, "bead": args.bead_id})


def cmd_worker_close(args: argparse.Namespace) -> None:
    """Worker calls this to validate and close a bead. Returns ok/errors."""
    rp = _registry_path()
    bd = _bd(rp)
    errors = []

    # 1. Check uncommitted changes in target files
    if args.files:
        files = [f.strip() for f in args.files.split(",")]
        for f in files:
            r = _run(f"git diff --name-only -- {f}")
            if r.stdout.strip():
                errors.append(f"uncommitted changes: {f}")
            r2 = _run(f"git diff --cached --name-only -- {f}")
            if r2.stdout.strip():
                errors.append(f"staged but uncommitted: {f}")
    else:
        # Check if there are any uncommitted changes at all
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

    # 2. Check last commit message for task/bead number anti-pattern
    r = _run("git log -1 --pretty=%s")
    msg = r.stdout.strip()
    if msg and re.search(r'\bTask\s+\d+', msg, re.IGNORECASE):
        _out({"ok": False, "errors": [f"commit message contains task number: '{msg}'. Amend to remove 'Task N:' prefix"], "hint": "git commit --amend -m 'feat: <description without task number>'"})
        return

    # 3. Check bead is in_progress (was step 2)
    r = _run(f"{bd} show {args.bead_id} --json")
    if r.returncode != 0:
        _out({"ok": False, "errors": [f"bd show failed: {r.stderr.strip()[:100]}"]})
        return

    try:
        bead = json.loads(r.stdout)
    except json.JSONDecodeError:
        _out({"ok": False, "errors": ["bd show returned invalid JSON"]})
        return

    status = bead.get("status", "")
    if status == "closed":
        _out({"ok": True, "status": "closed", "already": True})
        return
    if status != "in_progress":
        _out({"ok": False, "errors": [f"bead status is '{status}', expected 'in_progress'"]})
        return

    # 4. Build close reason
    summary = args.summary or "completed"
    files_str = args.files or ""
    reason = f"SUMMARY: {summary}. FILES: {files_str}. CONTEXT: {args.context_pct}%"

    # 5. Close bead
    r = _run(f'{bd} close {args.bead_id} --reason "{reason}" --json')
    if r.returncode != 0:
        _out({"ok": False, "errors": [f"bd close failed: {r.stderr.strip()[:100]}"]})
        return

    # 6. Verify close
    r = _run(f"{bd} show {args.bead_id} --json")
    try:
        bead = json.loads(r.stdout)
    except json.JSONDecodeError:
        bead = {}

    if bead.get("status") != "closed":
        # Retry once
        _run(f'{bd} close {args.bead_id} --reason "{reason}" --json')
        r = _run(f"{bd} show {args.bead_id} --json")
        try:
            bead = json.loads(r.stdout)
        except json.JSONDecodeError:
            bead = {}
        if bead.get("status") != "closed":
            _out({"ok": False, "errors": ["bead still not closed after retry"]})
            return

    _out({"ok": True, "status": "closed", "context_pct": args.context_pct})


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

    # Update registry bead reference for this worker (if worker is registered)
    try:
        reg, reg_path = _load_registry(rp)
        # Find this worker by matching active workers — use the worker name from env if available
        worker_name = os.environ.get("CLAUDE_AGENT_NAME", "")
        if worker_name and worker_name in reg.get("workers", {}):
            reg["workers"][worker_name]["bead"] = args.bead_id
            _save_registry(reg, reg_path)
    except (SystemExit, Exception):
        pass  # Registry update is best-effort — claim still succeeded

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
    _out({"ok": True, "bead": new_id, "source": args.bead_id})


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

    worker["status"] = "idle"
    worker["context_pct"] = args.context_pct
    worker["notification"] = "reconciled" if late else "received"
    worker["idle_since"] = now
    worker["bead"] = args.bead_id
    if args.summary:
        worker["summary"] = args.summary[:200]

    _save_registry(reg, rp)
    _out({"ok": True, "late": late, "worker": args.worker, "ctx": args.context_pct})


def cmd_phase_gate(args: argparse.Namespace) -> None:
    """Check if a phase/epic is fully complete: all beads closed + all notifications received."""
    reg, rp = _load_registry()
    bd = _bd(rp)
    blocking = []

    # Get all child beads of the epic
    r = _run(f"{bd} list --parent {args.epic_id} --json")
    if r.returncode != 0:
        # Fallback: list all open
        r = _run(f"{bd} list --json")

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


def cmd_status(args: argparse.Namespace) -> None:
    """Status overview for orchestrator — designed as recovery surface after context compression."""
    reg, rp = _load_registry()
    bd = _bd(rp)
    workers = reg.get("workers", {})

    counts = {"active": 0, "idle": 0, "retired": 0, "failed": 0}
    pending = 0
    active_workers = []
    for wname, w in workers.items():
        s = w.get("status", "")
        counts[s] = counts.get(s, 0) + 1
        if w.get("notification") == "pending":
            pending += 1
        if s == "active":
            active_workers.append({
                "name": wname,
                "bead": w.get("bead", "?"),
                "skill": w.get("skill", "?"),
                "ctx": w.get("context_pct", 0),
            })

    # Get bead counts
    r = _run(f"{bd} list --json 2>/dev/null")
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
        "pending_notif": pending,
        "beads": {"open": open_beads, "blocked": blocked, "closed": closed},
    }
    if active_workers:
        result["active"] = active_workers
    _out(result)


# ── CLI ──────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(prog="tf", description="TreeFlow state manager")
    sub = p.add_subparsers(dest="cmd")

    # init
    s = sub.add_parser("init")
    s.add_argument("plan_name")
    s.add_argument("--worker-model", default="", dest="worker_model")

    # dispatch
    s = sub.add_parser("dispatch")
    s.add_argument("worker")
    s.add_argument("bead_id")
    s.add_argument("--skill", required=True)

    # worker-close
    s = sub.add_parser("worker-close")
    s.add_argument("bead_id")
    s.add_argument("--context-pct", type=int, default=0, dest="context_pct")
    s.add_argument("--files", default="")
    s.add_argument("--summary", default="")

    # claim
    s = sub.add_parser("claim")
    s.add_argument("bead_id")

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

    # bd-path
    sub.add_parser("bd-path")

    # notify
    s = sub.add_parser("notify")
    s.add_argument("worker")
    s.add_argument("bead_id")
    s.add_argument("--context-pct", type=int, default=0, dest="context_pct")
    s.add_argument("--summary", default="")
    s.add_argument("--skill", default="")

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

    # status
    sub.add_parser("status")

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
        "bd-path": cmd_bd_path,
        "notify": cmd_notify,
        "phase-gate": cmd_phase_gate,
        "smoke-test": cmd_smoke_test,
        "registry": cmd_registry,
        "retire": cmd_retire,
        "routing": cmd_routing,
        "status": cmd_status,
    }
    cmds[args.cmd](args)


if __name__ == "__main__":
    main()
