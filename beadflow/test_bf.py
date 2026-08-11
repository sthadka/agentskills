"""Tests for bf.py — BeadFlow quality layer.

Runs against the real bf.py CLI via subprocess, using isolated temp directories
with mock .beads/ structures. Tests that don't need `bd` use a stub script.

Run: python3 -m pytest beadflow/test_bf.py -x -q
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

BF_PY = Path(__file__).parent / "bf.py"

_bf_module = None


def _load_bf():
    global _bf_module
    if _bf_module is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("bf", BF_PY)
        _bf_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_bf_module)
    return _bf_module


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path):
    """Create a minimal workspace with .beads/ and git init."""
    repo = tmp_path / "repo"
    repo.mkdir()
    beads = repo / ".beads"
    beads.mkdir()

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    # Initial commit so git log/diff work
    (repo / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=repo, check=True)

    import shutil
    shutil.copy2(BF_PY, beads / "bf.py")

    return repo


@pytest.fixture
def bd_stub(workspace):
    """Create a stub bd binary that returns configurable JSON responses.

    Routes by subcommand: set BD_STUB_READY, BD_STUB_LIST, BD_STUB_SHOW,
    BD_STUB_CLOSE, BD_STUB_DEP env vars for per-command responses.
    Falls back to BD_STUB_RESPONSE for unrouted commands.
    """
    stub = workspace / ".beads" / "bd-stub"
    stub.write_text(textwrap.dedent("""\
        #!/bin/bash
        # Route responses by subcommand
        CMD=""
        for arg in "$@"; do
            case "$arg" in
                ready) CMD="READY" ;;
                list) CMD="LIST" ;;
                show) CMD="SHOW" ;;
                close) CMD="CLOSE" ;;
                dep) CMD="DEP" ;;
                create) CMD="CREATE" ;;
            esac
            [ -n "$CMD" ] && break
        done

        VAR_NAME="BD_STUB_${CMD}"
        DEFAULT_RESP='{}'
        RESP="${!VAR_NAME:-${BD_STUB_RESPONSE:-$DEFAULT_RESP}}"
        echo "$RESP"
        if [ -n "$BD_STUB_STDERR" ]; then echo "$BD_STUB_STDERR" >&2; fi
        exit "${BD_STUB_EXIT:-0}"
    """))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return str(stub)


def bf(workspace, args: list[str], env=None) -> dict:
    """Run bf.py in the workspace and return parsed JSON output."""
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)
    r = subprocess.run(
        ["python3", str(BF_PY)] + args,
        cwd=workspace,
        capture_output=True,
        text=True,
        env=cmd_env,
    )
    stdout = r.stdout.strip()
    if not stdout:
        return {"_exit": r.returncode, "_stderr": r.stderr.strip()}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"_raw": stdout, "_exit": r.returncode, "_stderr": r.stderr.strip()}


def bf_raw(workspace, args: list[str], env=None) -> subprocess.CompletedProcess:
    """Run bf.py and return the raw CompletedProcess."""
    cmd_env = os.environ.copy()
    if env:
        cmd_env.update(env)
    return subprocess.run(
        ["python3", str(BF_PY)] + args,
        cwd=workspace,
        capture_output=True,
        text=True,
        env=cmd_env,
    )


# ── Init ──────────────────────────────────────────────────────


class TestInit:
    def test_init_copies_bf_py(self, workspace, bd_stub):
        result = bf(workspace, ["init", "--bd-path", bd_stub])
        assert result["ok"] is True
        assert (workspace / ".beads" / "bf.py").exists()

    def test_init_stores_bd_path(self, workspace, bd_stub):
        bf(workspace, ["init", "--bd-path", bd_stub])
        stored = (workspace / ".beads" / "bf-bd-path").read_text().strip()
        assert stored == bd_stub

    def test_init_no_beads_dir(self, tmp_path):
        repo = tmp_path / "empty"
        repo.mkdir()
        r = subprocess.run(
            ["python3", str(BF_PY), "init"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        result = json.loads(r.stdout.strip())
        assert result["ok"] is False
        assert ".beads/" in result["error"]


# ── Ready ─────────────────────────────────────────────────────


class TestReady:
    def test_ready_filters_epics(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        beads_json = json.dumps([
            {"id": "BF-001", "title": "Epic", "type": "epic", "status": "open"},
            {"id": "BF-002", "title": "Task A", "type": "task", "status": "open"},
        ])
        result = bf(workspace, ["ready"], env={
            "BD_STUB_READY": beads_json,
            "BD_STUB_LIST": "[]",
        })
        assert result["ok"] is True
        ids = [b["id"] for b in result["ready"]]
        assert "BF-001" not in ids
        assert "BF-002" in ids

    def test_ready_supplements_from_list(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        # bd ready returns nothing, but bd list has an open unblocked task
        result = bf(workspace, ["ready"], env={
            "BD_STUB_READY": "[]",
            "BD_STUB_LIST": json.dumps([
                {"id": "BF-003", "title": "Missed", "type": "task", "status": "open"},
            ]),
            "BD_STUB_SHOW": json.dumps([{"id": "BF-003", "dependencies": []}]),
        })
        assert result["ok"] is True
        assert result["supplemented"] == 1
        assert any(b["id"] == "BF-003" for b in result["ready"])

    def test_ready_includes_description(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        beads_json = json.dumps([
            {"id": "BF-010", "title": "Task", "type": "task", "status": "open",
             "description": "Do the thing"},
        ])
        result = bf(workspace, ["ready"], env={
            "BD_STUB_READY": beads_json,
            "BD_STUB_LIST": "[]",
        })
        assert result["ready"][0].get("description") == "Do the thing"


# ── Verify ────────────────────────────────────────────────────


class TestVerify:
    def test_verify_clean(self, workspace):
        result = bf(workspace, ["verify"])
        assert result["ok"] is True

    def test_verify_detects_uncommitted(self, workspace):
        f = workspace / "dirty.py"
        f.write_text("x = 1")
        result = bf(workspace, ["verify"])
        assert result["ok"] is False
        assert any("uncommitted" in e for e in result.get("errors", []))

    def test_verify_detects_uncommitted_specific_file(self, workspace):
        f = workspace / "dirty.py"
        f.write_text("x = 1")
        subprocess.run(["git", "add", "dirty.py"], cwd=workspace, check=True)
        result = bf(workspace, ["verify", "--files", "dirty.py"])
        assert result["ok"] is False
        assert any("staged but uncommitted" in e for e in result.get("errors", []))

    def test_verify_ignores_beads_dir(self, workspace):
        f = workspace / ".beads" / "notes.md"
        f.write_text("some notes")
        result = bf(workspace, ["verify"])
        assert result["ok"] is True

    def test_verify_detects_dead_code_markers(self, workspace):
        f = workspace / "code.py"
        f.write_text("# TODO fix this\nx = 1\n")
        subprocess.run(["git", "add", "code.py"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "add code", "-q"], cwd=workspace, check=True)
        result = bf(workspace, ["verify", "--files", "code.py"])
        assert result["ok"] is True  # dead code is warning, not error
        assert any("dead-code marker" in w for w in result.get("warnings", []))

    def test_verify_detects_task_number_in_commit(self, workspace):
        f = workspace / "code.py"
        f.write_text("x = 1\n")
        subprocess.run(["git", "add", "code.py"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "Task 5: do thing", "-q"], cwd=workspace, check=True)
        result = bf(workspace, ["verify"])
        assert result["ok"] is False
        assert any("task number" in e for e in result.get("commit_errors", []))


# ── Close ─────────────────────────────────────────────────────


class TestClose:
    def test_close_success(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        result = bf(workspace, ["close", "BF-001", "--summary", "AC: all pass", "--files", "a.py"], env={
            "BD_STUB_SHOW": json.dumps([{"id": "BF-001", "status": "closed"}]),
        })
        assert result["ok"] is True
        assert result["status"] == "closed"

    def test_close_blocks_on_uncommitted(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        f = workspace / "dirty.py"
        f.write_text("x = 1")
        result = bf(workspace, ["close", "BF-001", "--summary", "done"], env={
            "BD_STUB_SHOW": json.dumps([{"id": "BF-001", "status": "in_progress"}]),
        })
        assert result["ok"] is False
        assert any("uncommitted" in e for e in result.get("errors", []))

    def test_close_force_skips_checks(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        f = workspace / "dirty.py"
        f.write_text("x = 1")
        result = bf(workspace, ["close", "BF-001", "--summary", "done", "--force"], env={
            "BD_STUB_SHOW": json.dumps([{"id": "BF-001", "status": "closed"}]),
        })
        assert result["ok"] is True

    def test_close_warns_missing_ac(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        result = bf(workspace, ["close", "BF-001", "--summary", "just did stuff"], env={
            "BD_STUB_SHOW": json.dumps([{"id": "BF-001", "status": "closed"}]),
        })
        assert result["ok"] is True
        assert any("AC status" in w for w in result.get("warnings", []))

    def test_close_already_closed(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        result = bf(workspace, ["close", "BF-001"], env={
            "BD_STUB_SHOW": json.dumps([{"id": "BF-001", "status": "closed"}]),
        })
        assert result["ok"] is True
        assert result.get("already") is True

    def test_close_bad_status(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        result = bf(workspace, ["close", "BF-001"], env={
            "BD_STUB_SHOW": json.dumps([{"id": "BF-001", "status": "blocked"}]),
        })
        assert result["ok"] is False
        assert any("blocked" in e for e in result.get("errors", []))

    def test_close_blocks_on_task_number_commit(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        f = workspace / "code.py"
        f.write_text("x = 1\n")
        subprocess.run(["git", "add", "code.py"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "Task 3: implement", "-q"], cwd=workspace, check=True)
        result = bf(workspace, ["close", "BF-001"], env={
            "BD_STUB_SHOW": json.dumps([{"id": "BF-001", "status": "in_progress"}]),
        })
        assert result["ok"] is False
        assert any("task number" in e for e in result.get("errors", []))


# ── Smoke Test ────────────────────────────────────────────────


class TestSmokeTest:
    def test_smoke_test_build_pass(self, workspace):
        result = bf(workspace, ["smoke-test", "--build-cmd", "true"])
        assert result["build"] == "pass"

    def test_smoke_test_build_fail(self, workspace):
        result = bf(workspace, ["smoke-test", "--build-cmd", "false"])
        assert result["build"] == "fail"

    def test_smoke_test_skip(self, workspace):
        result = bf(workspace, ["smoke-test"])
        assert result["build"] == "skip"

    def test_smoke_test_wiring_check(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        (workspace / "a.py").write_text("x = 1")
        result = bf(workspace, ["smoke-test", "--beads", "BF-001"], env={
            "BD_STUB_SHOW": json.dumps([{
                "id": "BF-001",
                "description": "",
                "close_reason": "SUMMARY: done. FILES: a.py",
            }]),
        })
        wiring = result["wiring"]
        assert len(wiring) == 1
        assert wiring[0]["file"] == "a.py"
        assert wiring[0]["exists"] is True

    def test_smoke_test_missing_file(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        result = bf(workspace, ["smoke-test", "--beads", "BF-001"], env={
            "BD_STUB_SHOW": json.dumps([{
                "id": "BF-001",
                "description": "",
                "close_reason": "SUMMARY: done. FILES: nonexistent.py",
            }]),
        })
        wiring = result["wiring"]
        assert len(wiring) == 1
        assert wiring[0]["exists"] is False


# ── Conflict Check ────────────────────────────────────────────


class TestConflictCheck:
    def test_no_conflicts(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        # Use different show responses for different beads — stub returns same for all,
        # so we test the parsing logic with a single description format
        show_resp = json.dumps([{
            "id": "BF-001",
            "description": "Files (new): src/a.py",
        }])
        result = bf(workspace, ["conflict-check", "--beads", "BF-001"], env={
            "BD_STUB_SHOW": show_resp,
        })
        assert result["conflicts"] == {}
        assert "BF-001" in result["safe"]

    def test_detects_conflicts(self, workspace, bd_stub):
        """Two beads touching the same file should be flagged as conflicting."""
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        # Stub returns same response for both bd show calls — simulates both beads
        # claiming the same file. We test the conflict detection logic here.
        show_resp = json.dumps([{
            "id": "BF-001",
            "description": "Files (modifies): src/shared.py",
        }])
        result = bf(workspace, ["conflict-check", "--beads", "BF-001,BF-002"], env={
            "BD_STUB_SHOW": show_resp,
        })
        # Both beads get the same files from stub, so there should be a conflict
        assert "src/shared.py" in result["conflicts"]

    def test_section_aware_low_risk(self, workspace, bd_stub):
        """Same file but different [section] annotations should be low_risk."""
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        show_resp = json.dumps([{
            "id": "BF-001",
            "description": "Files (modifies): src/config.py [DatabaseConfig]",
        }])
        # Same stub response means both beads claim same section — that's a conflict.
        # To test low_risk we need different sections, which requires a smarter stub.
        # For now, verify the structure parses correctly.
        result = bf(workspace, ["conflict-check", "--beads", "BF-001"], env={
            "BD_STUB_SHOW": show_resp,
        })
        assert result["safe"] == ["BF-001"]

    def test_inferred_files(self, workspace, bd_stub):
        """When no Files: line exists, paths should be inferred from backtick paths."""
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        show_resp = json.dumps([{
            "id": "BF-001",
            "description": "Update the handler in `src/handlers/auth.py` to add validation",
        }])
        result = bf(workspace, ["conflict-check", "--beads", "BF-001"], env={
            "BD_STUB_SHOW": show_resp,
        })
        assert "BF-001" in result.get("inferred", [])
        assert "BF-001" in result["safe"]

    def test_unparseable_files(self, workspace, bd_stub):
        """Beads with no file references should be marked unparseable."""
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        show_resp = json.dumps([{
            "id": "BF-001",
            "description": "Do some refactoring",
        }])
        result = bf(workspace, ["conflict-check", "--beads", "BF-001"], env={
            "BD_STUB_SHOW": show_resp,
        })
        assert "BF-001" in result.get("unparseable", [])

    def test_soft_deps_detected(self, workspace, bd_stub):
        """depends_on: references should be captured."""
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        show_resp = json.dumps([{
            "id": "BF-001",
            "description": "Files (new): src/b.py\ndepends_on: Task A",
        }])
        result = bf(workspace, ["conflict-check", "--beads", "BF-001"], env={
            "BD_STUB_SHOW": show_resp,
        })
        assert "BF-001" in result.get("soft_deps", {})


# ── Dep ───────────────────────────────────────────────────────


class TestDep:
    def test_dep_success(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        result = bf(workspace, ["dep", "BF-001", "BF-002"], env={})
        assert result["ok"] is True
        assert result["blocker"] == "BF-001"
        assert result["blocked"] == "BF-002"

    def test_dep_idempotent(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        result = bf(workspace, ["dep", "BF-001", "BF-002"], env={
            "BD_STUB_EXIT": "1",
            "BD_STUB_STDERR": "UNIQUE constraint failed",
        })
        assert result["ok"] is True
        assert result["already_existed"] is True

    def test_dep_real_error(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        result = bf(workspace, ["dep", "BF-001", "BF-002"], env={
            "BD_STUB_EXIT": "1",
            "BD_STUB_STDERR": "some real error",
        })
        assert result["ok"] is False


# ── Import Graph ──────────────────────────────────────────────


class TestImportGraph:
    def test_import_graph_file_not_found(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        result = bf(workspace, ["import-graph", "nonexistent.jsonl"])
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_import_graph_rejects_markdown(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        plan = workspace / "plan.md"
        plan.write_text("# Plan")
        result = bf(workspace, ["import-graph", "plan.md"])
        assert result["ok"] is False
        assert "Markdown" in result["error"]

    def test_import_graph_success(self, workspace, bd_stub):
        (workspace / ".beads" / "bf-bd-path").write_text(bd_stub)
        graph = workspace / "graph.jsonl"
        graph.write_text('{"nodes": [], "edges": []}')
        result = bf(workspace, ["import-graph", "graph.jsonl"], env={
            "BD_STUB_CREATE": json.dumps({"ids": {"epic": "BF-E01", "task1": "BF-001"}}),
        })
        assert result["ok"] is True
        assert result["created"] == 2


# ── Internal helpers (unit tests via importlib) ───────────────


class TestFileExtraction:
    def test_extract_files_basic(self):
        bf_mod = _load_bf()
        desc = "Files (new): src/a.py, src/b.py"
        assert bf_mod._extract_files_from_description(desc) == ["src/a.py", "src/b.py"]

    def test_extract_files_modifies(self):
        bf_mod = _load_bf()
        desc = "Files (modifies): src/config.rs"
        assert bf_mod._extract_files_from_description(desc) == ["src/config.rs"]

    def test_extract_files_with_sections(self):
        bf_mod = _load_bf()
        desc = "Files (modifies): src/config.rs [StorageConfig], src/main.rs"
        result = bf_mod._extract_files_with_sections(desc)
        assert ("src/config.rs", "StorageConfig") in result
        assert ("src/main.rs", "") in result

    def test_extract_files_detailed(self):
        bf_mod = _load_bf()
        desc = "Files (new): src/a.py\nFiles (modifies): src/b.py"
        result = bf_mod._extract_files_detailed(desc)
        assert result["new"] == ["src/a.py"]
        assert result["modifies"] == ["src/b.py"]

    def test_infer_files_backtick(self):
        bf_mod = _load_bf()
        desc = "Update `src/handlers/auth.py` to add validation"
        result = bf_mod._infer_files_from_description(desc)
        assert "src/handlers/auth.py" in result

    def test_infer_files_bare_path(self):
        bf_mod = _load_bf()
        desc = "Modify the config in config/settings.yaml"
        result = bf_mod._infer_files_from_description(desc)
        assert "config/settings.yaml" in result

    def test_infer_files_no_paths(self):
        bf_mod = _load_bf()
        desc = "Do some general cleanup"
        assert bf_mod._infer_files_from_description(desc) == []

    def test_extract_files_synonym_headers(self):
        bf_mod = _load_bf()
        desc = "Modified files: src/a.py, src/b.py"
        assert bf_mod._extract_files_from_description(desc) == ["src/a.py", "src/b.py"]

    def test_extract_files_target_header(self):
        bf_mod = _load_bf()
        desc = "Target files: src/main.go"
        assert bf_mod._extract_files_from_description(desc) == ["src/main.go"]

    def test_parse_file_entry_with_section(self):
        bf_mod = _load_bf()
        path, section = bf_mod._parse_file_entry("`src/config.rs [StorageConfig]`")
        assert path == "src/config.rs"
        assert section == "StorageConfig"

    def test_parse_file_entry_plain(self):
        bf_mod = _load_bf()
        path, section = bf_mod._parse_file_entry("src/main.py")
        assert path == "src/main.py"
        assert section == ""


class TestParseBdJson:
    def test_parse_list(self):
        bf_mod = _load_bf()
        result = bf_mod._parse_bd_json('[{"id": "BF-001"}]')
        assert len(result) == 1
        assert result[0]["id"] == "BF-001"

    def test_parse_dict_with_issues(self):
        bf_mod = _load_bf()
        result = bf_mod._parse_bd_json('{"issues": [{"id": "BF-001"}]}')
        assert len(result) == 1

    def test_parse_with_prefix(self):
        bf_mod = _load_bf()
        result = bf_mod._parse_bd_json('warning: something\n[{"id": "BF-001"}]')
        assert len(result) == 1

    def test_parse_empty(self):
        bf_mod = _load_bf()
        assert bf_mod._parse_bd_json("") == []

    def test_parse_invalid_json(self):
        bf_mod = _load_bf()
        assert bf_mod._parse_bd_json("not json at all") == []


class TestBeadsDir:
    def test_finds_beads_in_cwd(self, workspace):
        bf_mod = _load_bf()
        original_cwd = os.getcwd()
        try:
            os.chdir(workspace)
            bd = bf_mod._beads_dir()
            assert bd == workspace / ".beads"
        finally:
            os.chdir(original_cwd)

    def test_walks_up_to_find_beads(self, workspace):
        bf_mod = _load_bf()
        subdir = workspace / "src" / "deep"
        subdir.mkdir(parents=True)
        original_cwd = os.getcwd()
        try:
            os.chdir(subdir)
            bd = bf_mod._beads_dir()
            assert bd == workspace / ".beads"
        finally:
            os.chdir(original_cwd)


# ── CLI help ──────────────────────────────────────────────────


class TestCLI:
    def test_no_args_shows_help(self, workspace):
        r = bf_raw(workspace, [])
        assert r.returncode == 1

    def test_unknown_command(self, workspace):
        r = bf_raw(workspace, ["bogus"])
        assert r.returncode != 0
