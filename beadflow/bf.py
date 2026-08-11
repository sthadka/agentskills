#!/usr/bin/env python3
"""bf.py — BeadFlow quality layer. Thin wrappers over bd for close validation,
filtered ready, conflict-check, smoke-test, and import-graph.

Beads is the state. This script adds quality checks that bd doesn't provide.
All output is compact single-line JSON via _out().
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

BD_LIST_LIMIT = "500"

_BD_SEARCH_PATHS = [
    Path.home() / ".local" / "bin" / "bd",
    Path("/usr/local/bin/bd"),
    Path.home() / "go" / "bin" / "bd",
    Path.home() / ".cargo" / "bin" / "bd",
]


def _resolve_bd() -> str:
    found = shutil.which("bd")
    if found:
        return found
    for p in _BD_SEARCH_PATHS:
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    return "bd"


_bd_path_cache: Optional[str] = None


def _bd() -> str:
    global _bd_path_cache
    if _bd_path_cache:
        return _bd_path_cache

    bd_dir = _beads_dir()
    bd_path_file = bd_dir / "bf-bd-path"
    if bd_path_file.exists():
        stored = bd_path_file.read_text().strip()
        if stored:
            _bd_path_cache = stored
            return stored

    resolved = _resolve_bd()
    _bd_path_cache = resolved
    return resolved


def _beads_dir() -> Path:
    d = Path.cwd()
    while d != d.parent:
        bd = d / ".beads"
        if bd.is_dir():
            return bd
        d = d.parent
    return Path.cwd() / ".beads"


def _run(cmd: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def _out(obj: dict | list) -> None:
    print(json.dumps(obj, separators=(",", ":")))


def _fetch_bead(bd: str, bid: str) -> Optional[dict]:
    r = _run(f"{bd} show {bid} --json")
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
        bead = data[0] if isinstance(data, list) else data
        return bead if isinstance(bead, dict) else None
    except (json.JSONDecodeError, IndexError):
        return None


def _parse_bd_json(stdout: str) -> list:
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


# ── File extraction from bead descriptions ──────────────────

_FILES_HEADER_RE = re.compile(
    r"^(?:\*\*)?"
    r"(?:"
    r"(?:Modified|Changed|Target)\s+files"
    r"|Files(?:\s+to\s+\S+(?:/\S+)?)?"
    r")"
    r"(?:\s*\(([^)]*)\))?"
    r":?\*?\*?\s*",
    re.IGNORECASE,
)

_SECTION_RE = re.compile(r"\[([^\]]+)\]")


def _parse_file_entry(entry: str) -> tuple[str, str]:
    entry = entry.strip().strip("`")
    m = _SECTION_RE.search(entry)
    if m:
        section = m.group(1).strip()
        path = entry[:m.start()].strip().strip("`")
        return path, section
    return entry, ""


def _extract_files_from_description(desc: str) -> list[str]:
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
    paths: list[str] = []
    for m in re.finditer(r"`([a-zA-Z0-9_./-]+\.[a-zA-Z0-9]+)`", desc):
        p = m.group(1)
        if "/" in p and not p.startswith("http"):
            paths.append(p)
    for m in re.finditer(r"\bin\s+`?([a-zA-Z0-9_/-]+\.[a-zA-Z]{1,10})`?", desc):
        p = m.group(1)
        if not p.startswith("http"):
            paths.append(p)
    if not paths:
        for m in re.finditer(r"(?<!\w)([a-zA-Z0-9_]+(?:/[a-zA-Z0-9_.]+)+\.[a-zA-Z]{1,10})(?!\w)", desc):
            paths.append(m.group(1))
    return list(dict.fromkeys(paths))


# ── Close validation checks ─────────────────────────────────

_DEAD_CODE_RE = re.compile(
    r"#\[allow\(dead_code\)\]|# noqa: F841|// @ts-ignore|\bTODO\b|\bFIXME\b|\bHACK\b"
)


def _check_uncommitted(files: list[str]) -> list[str]:
    """Check for uncommitted changes in the working tree, excluding .beads/."""
    errors: list[str] = []
    if files:
        for f in files:
            r = _run(f"git diff --name-only -- {shlex.quote(f)}")
            if r.stdout.strip():
                errors.append(f"uncommitted changes: {f}")
            r2 = _run(f"git diff --cached --name-only -- {shlex.quote(f)}")
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
    return errors


def _check_dead_code(files: list[str]) -> list[str]:
    """Scan files for dead-code markers. Returns warnings (not errors)."""
    warnings: list[str] = []
    if not files:
        return warnings
    file_args = " ".join(shlex.quote(f) for f in files)
    r = _run(f"grep -nE '{_DEAD_CODE_RE.pattern}' {file_args}")
    if r.stdout.strip():
        for line in r.stdout.strip().split("\n")[:10]:
            warnings.append(f"dead-code marker: {line.strip()}")
    return warnings


def _check_commit_message() -> list[str]:
    """Check last commit message for task/bead number anti-pattern."""
    r = _run("git log -1 --pretty=%s")
    msg = r.stdout.strip()
    if msg and re.search(r'\bTask\s+\d+', msg, re.IGNORECASE):
        return [f"commit message contains task number: '{msg}'. Amend to remove 'Task N:' prefix"]
    return []


# ── Commands ─────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> None:
    """Minimal init: copy bf.py to .beads/ and store bd path."""
    bd_dir = _beads_dir()
    if not bd_dir.exists():
        _out({"ok": False, "error": ".beads/ directory not found. Run bd init first."})
        return

    bd_path = args.bd_path or _resolve_bd()
    bd_path_file = bd_dir / "bf-bd-path"
    bd_path_file.write_text(bd_path)

    src = Path(__file__).resolve()
    dest = bd_dir / "bf.py"
    if src != dest:
        shutil.copy2(src, dest)

    _out({"ok": True, "bd_path": bd_path, "bf_py": str(dest)})


def cmd_ready(args: argparse.Namespace) -> None:
    """Filtered bd ready: no epics, supplements capped results from bd list."""
    bd = _bd()

    r = _run(f"{bd} ready --json")
    ready_beads = _parse_bd_json(r.stdout)

    ready_beads = [b for b in ready_beads if b.get("type", "").lower() != "epic"]
    ready_ids = {b.get("id", "") for b in ready_beads}

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

    _READY_KEYS = ("id", "title", "type", "priority", "status", "description")
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


def cmd_verify(args: argparse.Namespace) -> None:
    """Run close-validation checks without actually closing. Use mid-task to catch issues early."""
    files = [f.strip() for f in args.files.split(",")] if args.files else []
    errors = _check_uncommitted(files)
    warnings = _check_dead_code(files)
    commit_errors = _check_commit_message()

    ok = not errors and not commit_errors
    result: dict = {"ok": ok}
    if errors:
        result["errors"] = errors
    if commit_errors:
        result["commit_errors"] = commit_errors
    if warnings:
        result["warnings"] = warnings
    _out(result)


def cmd_close(args: argparse.Namespace) -> None:
    """Validate quality checks, then close bead via bd close."""
    bd = _bd()
    files = [f.strip() for f in args.files.split(",")] if args.files else []
    errors: list[str] = []
    warnings: list[str] = []

    if not args.force:
        errors.extend(_check_uncommitted(files))
        if errors:
            _out({"ok": False, "errors": errors})
            return

        warnings.extend(_check_dead_code(files))

        commit_errors = _check_commit_message()
        if commit_errors:
            _out({"ok": False, "errors": commit_errors})
            return

    if args.summary and "AC:" not in args.summary and "acceptance" not in args.summary.lower():
        warnings.append("summary missing AC status — include 'AC: <status>' to confirm all criteria met")

    bead = _fetch_bead(bd, args.bead_id)
    if bead is None:
        _out({"ok": False, "errors": [f"bd show {args.bead_id} failed"]})
        return

    status = bead.get("status", "")
    if status == "closed":
        result: dict = {"ok": True, "id": args.bead_id, "status": "closed", "already": True}
        if warnings:
            result["warnings"] = warnings
        _out(result)
        return
    if status not in ("in_progress", "open"):
        _out({"ok": False, "errors": [f"status is '{status}', expected 'in_progress' or 'open'"]})
        return

    summary = args.summary or "completed"
    files_str = args.files or ""
    reason = f"SUMMARY: {summary}. FILES: {files_str}" if files_str else f"SUMMARY: {summary}"

    r = _run(f'{bd} close {args.bead_id} --reason {shlex.quote(reason)} --json')
    if r.returncode != 0:
        _out({"ok": False, "errors": [f"bd close failed: {r.stderr.strip()[:200]}"]})
        return

    bead = _fetch_bead(bd, args.bead_id)
    final_status = bead.get("status", "unknown") if bead else "unknown"

    if final_status != "closed":
        _run(f'{bd} close {args.bead_id} --reason {shlex.quote(reason)} --json')
        bead = _fetch_bead(bd, args.bead_id)
        final_status = bead.get("status", "unknown") if bead else "unknown"
        if final_status != "closed":
            _out({"ok": False, "errors": [f"still not closed after retry, status: {final_status}"]})
            return

    result = {"ok": True, "id": args.bead_id, "status": "closed"}
    if warnings:
        result["warnings"] = warnings
    _out(result)


def cmd_smoke_test(args: argparse.Namespace) -> None:
    """Run build/test command and report pass/fail. Optionally verify files from beads exist."""
    bd = _bd()
    result: dict = {"build": "skip", "wiring": []}

    if args.build_cmd:
        r = _run(args.build_cmd)
        lines = r.stdout.strip().split("\n")
        tail = lines[-20:] if len(lines) > 20 else lines
        result["build"] = "pass" if r.returncode == 0 else "fail"
        if r.returncode != 0:
            result["build_output"] = "\n".join(tail)

    if args.beads:
        bead_ids = [b.strip() for b in args.beads.split(",")]
        for bid in bead_ids:
            bead = _fetch_bead(bd, bid)
            if bead is None:
                result["wiring"].append({"bead": bid, "error": "cannot read bead"})
                continue

            desc = bead.get("description", "")
            close_reason = bead.get("close_reason", bead.get("reason", ""))
            files_section = ""
            for text in [close_reason, desc]:
                if "FILES:" in text:
                    start = text.index("FILES:") + 6
                    rest = text[start:]
                    # Find next section marker or end of string
                    for marker in [". SUMMARY:", ". CONTEXT:", ".\n", "\n"]:
                        pos = rest.find(marker)
                        if pos != -1:
                            files_section = rest[:pos].strip()
                            break
                    else:
                        files_section = rest.strip().rstrip(".")
                    break

            if files_section:
                flist = [f.strip() for f in files_section.split(",")]
                for fp in flist:
                    if not fp:
                        continue
                    exists = Path(fp).exists()
                    result["wiring"].append({"bead": bid, "file": fp, "exists": exists})

    _out(result)


def cmd_conflict_check(args: argparse.Namespace) -> None:
    """Check file conflicts between beads for parallelism safety."""
    bd = _bd()
    bead_ids = [b.strip() for b in args.beads.split(",") if b.strip()]

    bead_files: dict[str, list[str]] = {}
    bead_modifies: dict[str, list[str]] = {}
    bead_sections: dict[str, dict[str, set[str]]] = {}
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
        deps = [m.group(1).strip() for m in re.finditer(r"depends_on:\s*(.+?)(?:,|$)", desc, re.IGNORECASE)]
        if deps:
            bead_soft_deps[bid] = deps

    file_map: dict[str, list[str]] = {}
    for bid, files in bead_files.items():
        for f in files:
            file_map.setdefault(f, []).append(bid)

    all_conflicts = {f: bids for f, bids in file_map.items() if len(bids) > 1}

    hard_conflicts: dict[str, list[str]] = {}
    low_risk: dict[str, dict] = {}
    for f, bids in all_conflicts.items():
        sections_per_bead = []
        all_have_sections = True
        for bid in bids:
            secs = bead_sections.get(bid, {}).get(f, set())
            if not secs:
                all_have_sections = False
                break
            sections_per_bead.append((bid, secs))

        if all_have_sections and len(sections_per_bead) >= 2:
            all_sections = [s for _, secs in sections_per_bead for s in secs]
            has_overlap = len(all_sections) != len(set(all_sections))
            if not has_overlap:
                low_risk[f] = {
                    "beads": bids,
                    "sections": {bid: sorted(secs) for bid, secs in sections_per_bead},
                }
                continue
        hard_conflicts[f] = bids

    modify_conflicts: dict[str, list[str]] = {}
    for f, bids in hard_conflicts.items():
        modifiers = [bid for bid in bids if f in bead_modifies.get(bid, [])]
        if len(modifiers) > 1:
            modify_conflicts[f] = modifiers

    conflicting_beads = set()
    for bids in hard_conflicts.values():
        conflicting_beads.update(bids)
    safe = [bid for bid in bead_ids if bid not in conflicting_beads and bid in bead_files]

    result: dict = {"conflicts": hard_conflicts, "safe": safe}
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


def cmd_dep(args: argparse.Namespace) -> None:
    """Add a dependency idempotently — UNIQUE constraint errors treated as success."""
    bd = _bd()
    r = _run(f"{bd} dep {args.blocker} --blocks {args.blocked}")
    if r.returncode == 0:
        _out({"ok": True, "blocker": args.blocker, "blocked": args.blocked, "already_existed": False})
        return
    combined = (r.stderr + r.stdout).lower()
    if "unique" in combined or "duplicate" in combined or "already exists" in combined:
        _out({"ok": True, "blocker": args.blocker, "blocked": args.blocked, "already_existed": True})
        return
    _out({"ok": False, "error": f"bd dep failed: {r.stderr.strip()[:200]}"})


def cmd_import_graph(args: argparse.Namespace) -> None:
    """Import a beads-graph.jsonl file via bd create --graph."""
    graph_path = Path(args.file)
    if not graph_path.exists():
        _out({"ok": False, "error": f"file not found: {args.file}"})
        return
    if graph_path.suffix == '.md':
        _out({"ok": False, "error": "Markdown plans must be converted first. Run: /sculptor export-beads <idea-dir>"})
        return

    bd = _bd()
    r = _run(f"{bd} create --graph {shlex.quote(str(graph_path))} --json")
    if r.returncode != 0:
        _out({"ok": False, "error": f"bd create --graph failed: {r.stderr.strip()[:300]}"})
        return

    stdout = r.stdout.strip()
    json_start = next((i for i, ch in enumerate(stdout) if ch in ("{", "[")), -1)
    if json_start < 0:
        _out({"ok": False, "error": "no JSON in bd create --graph output"})
        return
    try:
        result = json.loads(stdout[json_start:])
    except json.JSONDecodeError as e:
        _out({"ok": False, "error": f"JSON parse error: {e}"})
        return

    ids = result.get("ids", {})
    epic_id = ids.get("epic", "")
    _out({"ok": True, "epic_id": epic_id, "created": len(ids), "ids": ids})


# ── CLI ──────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(prog="bf", description="BeadFlow quality layer")
    sub = p.add_subparsers(dest="cmd")

    # init
    s = sub.add_parser("init")
    s.add_argument("--bd-path", default="", dest="bd_path")

    # ready
    sub.add_parser("ready")

    # verify
    s = sub.add_parser("verify")
    s.add_argument("--files", default="")

    # close
    s = sub.add_parser("close")
    s.add_argument("bead_id")
    s.add_argument("--files", default="")
    s.add_argument("--summary", default="")
    s.add_argument("--force", action="store_true", default=False)

    # smoke-test
    s = sub.add_parser("smoke-test")
    s.add_argument("--build-cmd", default="", dest="build_cmd")
    s.add_argument("--beads", default="")

    # conflict-check
    s = sub.add_parser("conflict-check")
    s.add_argument("--beads", required=True)

    # dep
    s = sub.add_parser("dep")
    s.add_argument("blocker")
    s.add_argument("blocked")

    # import-graph
    s = sub.add_parser("import-graph")
    s.add_argument("file")

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        sys.exit(1)

    cmds = {
        "init": cmd_init,
        "ready": cmd_ready,
        "verify": cmd_verify,
        "close": cmd_close,
        "smoke-test": cmd_smoke_test,
        "conflict-check": cmd_conflict_check,
        "dep": cmd_dep,
        "import-graph": cmd_import_graph,
    }
    cmds[args.cmd](args)


if __name__ == "__main__":
    main()
