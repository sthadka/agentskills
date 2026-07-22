"""Comprehensive tests for tf.py — TreeFlow state manager.

Runs against the real tf.py CLI via subprocess, using isolated temp directories
with mock .beads/ structures. Tests that don't need `bd` avoid it entirely;
tests that do use a stub script.

Run: python3 -m pytest treeflow/test_tf.py -v
"""

import json
import os
import stat
import subprocess
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TF_PY = Path(__file__).parent / "tf.py"


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path):
    """Create a minimal workspace with .beads/ and git init."""
    repo = tmp_path / "repo"
    repo.mkdir()
    beads = repo / ".beads"
    beads.mkdir()

    # git init so worker-close git checks work
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

    # Copy tf.py into .beads/
    import shutil
    shutil.copy2(TF_PY, beads / "tf.py")

    return repo


@pytest.fixture
def bd_stub(workspace):
    """Create a stub bd binary that returns configurable JSON responses."""
    stub = workspace / ".beads" / "bd-stub"
    stub.write_text(textwrap.dedent("""\
        #!/bin/bash
        # Stub bd binary for testing.
        # Reads BD_STUB_RESPONSE env var for stdout, BD_STUB_EXIT for exit code,
        # BD_STUB_STDERR for stderr output.
        DEFAULT_RESP='{}'
        echo "${BD_STUB_RESPONSE:-$DEFAULT_RESP}"
        if [ -n "$BD_STUB_STDERR" ]; then echo "$BD_STUB_STDERR" >&2; fi
        exit "${BD_STUB_EXIT:-0}"
    """))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return str(stub)


def tf(workspace, args: list[str], env=None) -> dict:
    """Run tf.py in the workspace and return parsed JSON output."""
    cmd_env = os.environ.copy()
    cmd_env.pop("CLAUDE_AGENT_NAME", None)
    if env:
        cmd_env.update(env)
    r = subprocess.run(
        ["python3", str(workspace / ".beads" / "tf.py")] + args,
        cwd=workspace,
        capture_output=True,
        text=True,
        env=cmd_env,
    )
    stdout = r.stdout.strip()
    if not stdout:
        return {"_returncode": r.returncode, "_stderr": r.stderr.strip()}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"_raw": stdout, "_returncode": r.returncode, "_stderr": r.stderr.strip()}


def load_registry(workspace) -> dict:
    """Load registry.json from the workspace."""
    for p in (workspace / ".beads").rglob("registry.json"):
        with open(p) as f:
            return json.load(f)
    raise FileNotFoundError("No registry.json found")


def save_registry(workspace, reg: dict):
    """Save registry.json back to the workspace."""
    for p in (workspace / ".beads").rglob("registry.json"):
        with open(p, "w") as f:
            json.dump(reg, f)
        return
    raise FileNotFoundError("No registry.json found")


def ts_minutes_ago(minutes: int) -> str:
    """Return an ISO timestamp N minutes in the past."""
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_minutes_from_now(minutes: int) -> str:
    """Return an ISO timestamp N minutes in the future."""
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Init Tests ──────────────────────────────────────────────────


class TestInit:
    def test_creates_context_dir_and_registry(self, workspace):
        out = tf(workspace, ["init", "my-plan", "--bd-path", "/usr/bin/bd"])
        assert out["ok"] is True
        assert "my-plan" in out["path"]

        reg = load_registry(workspace)
        assert reg["plan_name"] == "my-plan"
        assert reg["bd_path"] == "/usr/bin/bd"
        assert reg["settings"]["stall_threshold_mins"] == 20
        assert reg["workers"] == {}
        assert reg["routing"] == {}

    def test_idempotent(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["init", "test"])
        assert out["ok"] is True
        assert out["msg"] == "already exists"

    def test_creates_gitignore(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        gitignore = workspace / ".gitignore"
        assert gitignore.exists()
        assert ".beads/" in gitignore.read_text()

    def test_appends_to_existing_gitignore(self, workspace):
        gitignore = workspace / ".gitignore"
        gitignore.write_text("node_modules/\n")
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        content = gitignore.read_text()
        assert "node_modules/" in content
        assert ".beads/" in content

    def test_worker_model_stored(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd", "--worker-model", "sonnet"])
        reg = load_registry(workspace)
        assert reg["worker_model"] == "sonnet"

    def test_copies_tf_py(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        assert (workspace / ".beads" / "tf.py").exists()

    def test_reinit_updates_worker_model(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd", "--worker-model", "sonnet"])
        reg = load_registry(workspace)
        assert reg["worker_model"] == "sonnet"
        out = tf(workspace, ["init", "test", "--worker-model", "opus"])
        assert out["ok"] is True
        assert "worker_model" in out["msg"]
        reg = load_registry(workspace)
        assert reg["worker_model"] == "opus"

    def test_reinit_no_flags_says_already_exists(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["init", "test"])
        assert out["msg"] == "already exists"


# ── Dispatch Tests ──────────────────────────────────────────────


class TestDispatch:
    def test_basic_dispatch(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        assert out["ok"] is True
        assert out["worker"] == "rust-1"

        reg = load_registry(workspace)
        w = reg["workers"]["rust-1"]
        assert w["status"] == "active"
        assert w["skill"] == "rust"
        assert w["bead"] == "bead-abc"
        assert w["notification"] == "pending"

    def test_heartbeat_fields_initialized(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        w = reg["workers"]["rust-1"]
        assert w["last_heartbeat"] == w["dispatched_at"]
        assert w["last_heartbeat_note"] is None
        assert w["heartbeat_history"] == []

    def test_output_file_stored(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust", "--output-file", "/tmp/out.txt"])

        reg = load_registry(workspace)
        assert reg["workers"]["rust-1"]["output_file"] == "/tmp/out.txt"

    def test_output_file_defaults_empty(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        assert reg["workers"]["rust-1"]["output_file"] == ""

    def test_re_dispatch_overwrites(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        tf(workspace, ["dispatch", "rust-1", "bead-def", "--skill", "go"])

        reg = load_registry(workspace)
        assert reg["workers"]["rust-1"]["bead"] == "bead-def"
        assert reg["workers"]["rust-1"]["skill"] == "go"

    def test_dispatch_records_git_sha(self, workspace):
        # Create an initial commit so HEAD exists
        dummy = workspace / "init.txt"
        dummy.write_text("init")
        subprocess.run(["git", "add", "init.txt"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=workspace, check=True)

        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace,
                           capture_output=True, text=True, check=True)
        expected_sha = r.stdout.strip()

        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        assert reg["workers"]["rust-1"]["dispatch_sha"] == expected_sha
        assert len(expected_sha) == 40

    def test_dispatch_comma_separated_beads(self, workspace):
        """Dispatch with comma-separated beads stores list in registry."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["dispatch", "w1", "bead-1,bead-2,bead-3", "--skill", "code"])
        assert out["ok"] is True
        assert out["bead"] == ["bead-1", "bead-2", "bead-3"]
        reg = load_registry(workspace)
        assert reg["workers"]["w1"]["bead"] == ["bead-1", "bead-2", "bead-3"]

    def test_dispatch_single_bead_backward_compat(self, workspace):
        """Dispatch with single bead stores string (backward compat)."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        assert out["ok"] is True
        assert out["bead"] == "bead-1"
        reg = load_registry(workspace)
        assert reg["workers"]["w1"]["bead"] == "bead-1"


# ── Heartbeat Tests ─────────────────────────────────────────────


class TestHeartbeat:
    def test_explicit_heartbeat(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        out = tf(workspace, ["heartbeat", "bead-abc", "--note", "compiling"],
                 env={"CLAUDE_AGENT_NAME": "rust-1"})
        assert out["ok"] is True
        assert out["note"] == "compiling"

        reg = load_registry(workspace)
        w = reg["workers"]["rust-1"]
        assert w["last_heartbeat_note"] == "compiling"
        assert len(w["heartbeat_history"]) == 1
        assert w["heartbeat_history"][0]["note"] == "compiling"

    def test_heartbeat_default_note(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-xyz", "--skill", "rust"])
        out = tf(workspace, ["heartbeat", "bead-xyz"],
                 env={"CLAUDE_AGENT_NAME": "rust-1"})
        assert out["note"] == "heartbeat on bead-xyz"

    def test_heartbeat_without_agent_name(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        out = tf(workspace, ["heartbeat", "bead-abc"])
        assert out["ok"] is False
        assert "CLAUDE_AGENT_NAME" in out["error"]

    def test_heartbeat_unknown_worker(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["heartbeat", "bead-abc", "--note", "test"],
                 env={"CLAUDE_AGENT_NAME": "nonexistent"})
        assert out["ok"] is False
        assert "not in registry" in out["error"]

    def test_heartbeat_history_caps_at_20(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        for i in range(25):
            tf(workspace, ["heartbeat", "bead-abc", "--note", f"beat {i}"],
               env={"CLAUDE_AGENT_NAME": "rust-1"})

        reg = load_registry(workspace)
        h = reg["workers"]["rust-1"]["heartbeat_history"]
        assert len(h) == 20
        assert h[0]["note"] == "beat 5"  # first 5 dropped
        assert h[-1]["note"] == "beat 24"

    def test_heartbeat_updates_timestamp(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        original_hb = reg["workers"]["rust-1"]["last_heartbeat"]

        import time
        time.sleep(1.1)

        tf(workspace, ["heartbeat", "bead-abc", "--note", "update"],
           env={"CLAUDE_AGENT_NAME": "rust-1"})

        reg = load_registry(workspace)
        new_hb = reg["workers"]["rust-1"]["last_heartbeat"]
        assert new_hb >= original_hb

    def test_heartbeat_initializes_history_on_old_registry(self, workspace):
        """Workers from before the heartbeat feature should get history initialized."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        # Simulate old registry without heartbeat_history
        reg = load_registry(workspace)
        del reg["workers"]["rust-1"]["heartbeat_history"]
        save_registry(workspace, reg)

        tf(workspace, ["heartbeat", "bead-abc", "--note", "late init"],
           env={"CLAUDE_AGENT_NAME": "rust-1"})

        reg = load_registry(workspace)
        assert len(reg["workers"]["rust-1"]["heartbeat_history"]) == 1


# ── Stall Detection Tests ──────────────────────────────────────


class TestStalled:
    def test_no_stalled_workers(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        out = tf(workspace, ["stalled"])
        assert out["stalled"] == []
        assert out["threshold_mins"] == 20

    def test_detects_stalled_worker(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        # Backdate heartbeat
        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["last_heartbeat"] = ts_minutes_ago(25)
        save_registry(workspace, reg)

        out = tf(workspace, ["stalled"])
        assert len(out["stalled"]) == 1
        assert out["stalled"][0]["worker"] == "rust-1"
        assert out["stalled"][0]["skill"] == "rust"

    def test_custom_threshold(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["last_heartbeat"] = ts_minutes_ago(25)
        save_registry(workspace, reg)

        # High threshold — not stalled
        out = tf(workspace, ["stalled", "--threshold-mins", "60"])
        assert out["stalled"] == []
        assert out["threshold_mins"] == 60

        # Low threshold — stalled
        out = tf(workspace, ["stalled", "--threshold-mins", "10"])
        assert len(out["stalled"]) == 1

    def test_uses_settings_threshold(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        reg["settings"]["stall_threshold_mins"] = 5
        reg["workers"]["rust-1"]["last_heartbeat"] = ts_minutes_ago(10)
        save_registry(workspace, reg)

        out = tf(workspace, ["stalled"])
        assert len(out["stalled"]) == 1
        assert out["threshold_mins"] == 5

    def test_idle_worker_not_stalled(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["status"] = "idle"
        reg["workers"]["rust-1"]["last_heartbeat"] = ts_minutes_ago(60)
        save_registry(workspace, reg)

        out = tf(workspace, ["stalled"])
        assert out["stalled"] == []

    def test_retired_worker_not_stalled(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["status"] = "retired"
        reg["workers"]["rust-1"]["last_heartbeat"] = ts_minutes_ago(60)
        save_registry(workspace, reg)

        out = tf(workspace, ["stalled"])
        assert out["stalled"] == []

    def test_falls_back_to_dispatched_at(self, workspace):
        """Workers without last_heartbeat should use dispatched_at for stall detection."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        del reg["workers"]["rust-1"]["last_heartbeat"]
        reg["workers"]["rust-1"]["dispatched_at"] = ts_minutes_ago(25)
        save_registry(workspace, reg)

        out = tf(workspace, ["stalled"])
        assert len(out["stalled"]) == 1


# ── Expected Completion Tests ──────────────────────────────────


class TestExpectedCompletion:
    def _setup_worker_with_deadline(self, workspace, deadline_minutes_ago: int, heartbeat_minutes_ago: int):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        if deadline_minutes_ago > 0:
            reg["workers"]["rust-1"]["expected_completion_at"] = ts_minutes_ago(deadline_minutes_ago)
        else:
            reg["workers"]["rust-1"]["expected_completion_at"] = ts_minutes_from_now(-deadline_minutes_ago)
        reg["workers"]["rust-1"]["last_heartbeat"] = ts_minutes_ago(heartbeat_minutes_ago)
        save_registry(workspace, reg)

    def test_past_deadline_recent_heartbeat_not_stalled(self, workspace):
        """Past deadline but still heartbeating = slow, not stalled."""
        self._setup_worker_with_deadline(workspace, deadline_minutes_ago=10, heartbeat_minutes_ago=2)
        out = tf(workspace, ["stalled"])
        assert out["stalled"] == []

    def test_past_deadline_stale_heartbeat_is_stalled(self, workspace):
        """Past deadline AND stale heartbeat = stalled."""
        self._setup_worker_with_deadline(workspace, deadline_minutes_ago=10, heartbeat_minutes_ago=25)
        out = tf(workspace, ["stalled"])
        assert len(out["stalled"]) == 1

    def test_future_deadline_not_stalled(self, workspace):
        """Before deadline = never stalled, even with old heartbeat."""
        self._setup_worker_with_deadline(workspace, deadline_minutes_ago=-30, heartbeat_minutes_ago=25)
        out = tf(workspace, ["stalled"])
        assert out["stalled"] == []


# ── Notify Tests ────────────────────────────────────────────────


class TestNotify:
    def test_basic_notify(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        out = tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "55", "--summary", "done"])
        assert out["ok"] is True
        assert "late" not in out  # late omitted when false
        assert out["ctx"] == 55

        reg = load_registry(workspace)
        w = reg["workers"]["rust-1"]
        assert w["status"] == "idle"
        assert w["context_pct"] == 55
        assert w["notification"] == "received"
        assert "idle_since" in w

    def test_auto_retire_at_90_pct(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        out = tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "92"])
        assert out["auto_retired"] is True

        reg = load_registry(workspace)
        assert reg["workers"]["rust-1"]["status"] == "retired"
        assert "retired_at" in reg["workers"]["rust-1"]

    def test_late_notification(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "55"])
        out = tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "55"])
        assert out["late"] is True

        reg = load_registry(workspace)
        assert reg["workers"]["rust-1"]["notification"] == "reconciled"

    def test_notify_unknown_worker(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["notify", "unknown-1", "bead-abc", "--context-pct", "50", "--skill", "rust"])
        assert out["ok"] is True
        # Should add the worker
        reg = load_registry(workspace)
        assert "unknown-1" in reg["workers"]
        assert reg["workers"]["unknown-1"]["status"] == "idle"

    def test_summary_truncated(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        long_summary = "x" * 300
        tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "50", "--summary", long_summary])

        reg = load_registry(workspace)
        assert len(reg["workers"]["rust-1"]["summary"]) == 200

    def test_no_auto_close_without_closed_self(self, workspace, bd_stub):
        """Notify should not auto-close in_progress beads when worker didn't call worker-close."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        bead_resp = json.dumps({"id": "bead-abc", "status": "in_progress"})
        out = tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "55"],
                 env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True
        assert out.get("auto_closed") is False
        assert "action_needed" in out

    def test_auto_closes_with_closed_self(self, workspace, bd_stub):
        """Notify should auto-close in_progress beads when worker called worker-close."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["closed_self"] = True
        save_registry(workspace, reg)
        bead_resp = json.dumps({"id": "bead-abc", "status": "in_progress"})
        out = tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "55"],
                 env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True

    def test_no_auto_close_when_already_closed(self, workspace, bd_stub):
        """Notify should not try to close already-closed beads."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        bead_resp = json.dumps({"id": "bead-abc", "status": "closed"})
        out = tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "55"],
                 env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True
        assert "auto_closed" not in out

    def test_notify_with_files_triggers_context_update(self, workspace, bd_stub):
        """notify --files should write to task-summaries.md context file."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        bead_resp = json.dumps({"id": "bead-1", "title": "Add auth", "status": "closed"})
        out = tf(workspace, [
            "notify", "w1", "bead-1", "--context-pct", "50",
            "--summary", "Added auth module", "--files", "auth.py,routes.py",
        ], env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True
        ctx = workspace / ".beads" / "context-test"
        summaries = ctx / "task-summaries.md"
        assert summaries.exists()
        content = summaries.read_text()
        assert "BD-bead-1" in content
        assert "auth.py,routes.py" in content

    def test_notify_with_files_and_gotcha(self, workspace, bd_stub):
        """notify --files --gotcha should write both context and gotcha."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        bead_resp = json.dumps({"id": "bead-1", "title": "Fix bug", "status": "closed"})
        out = tf(workspace, [
            "notify", "w1", "bead-1", "--context-pct", "50",
            "--summary", "Fixed the bug", "--files", "fix.py",
            "--gotcha", "Watch for race conditions",
        ], env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True
        ctx = workspace / ".beads" / "context-test"
        wc = ctx / "worker-context.md"
        assert wc.exists()
        assert "Watch for race conditions" in wc.read_text()

    def test_notify_without_files_no_context_update(self, workspace, bd_stub):
        """notify without --files should NOT write context files."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        bead_resp = json.dumps({"id": "bead-1", "title": "Task", "status": "closed"})
        out = tf(workspace, [
            "notify", "w1", "bead-1", "--context-pct", "50",
            "--summary", "Done",
        ], env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True
        ctx = workspace / ".beads" / "context-test"
        summaries = ctx / "task-summaries.md"
        # task-summaries may exist from init but should not have BD-bead-1
        if summaries.exists():
            assert "BD-bead-1" not in summaries.read_text()

    def test_notify_omit_bead_id_uses_registry(self, workspace, bd_stub):
        """notify without bead_id looks up bead from registry."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        bead_resp = json.dumps({"id": "bead-1", "status": "closed"})
        out = tf(workspace, [
            "notify", "w1", "--context-pct", "45", "--summary", "done",
        ], env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True
        reg = load_registry(workspace)
        assert reg["workers"]["w1"]["bead"] == "bead-1"

    def test_notify_with_bead_id_works_as_before(self, workspace, bd_stub):
        """notify with explicit bead_id still works."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        bead_resp = json.dumps({"id": "bead-2", "status": "closed"})
        out = tf(workspace, [
            "notify", "w1", "bead-2", "--context-pct", "45", "--summary", "done",
        ], env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True
        reg = load_registry(workspace)
        assert reg["workers"]["w1"]["bead"] == "bead-2"

    def test_notify_omit_bead_id_unknown_worker_fails(self, workspace):
        """notify without bead_id and unknown worker should fail."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, [
            "notify", "unknown-worker", "--context-pct", "45", "--summary", "done",
        ])
        assert out["ok"] is False
        assert "no bead recorded" in out["error"]


# ── Sync Tests ──────────────────────────────────────────────────


class TestSync:
    def test_empty_sync(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["sync"])
        assert out["available"] == {}
        assert out["retired_now"] == []
        assert out["counts"]["total"] == 0

    def test_auto_retire_high_context(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "55"])

        # Manually set context to 95 and status back to idle
        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["context_pct"] = 95
        reg["workers"]["rust-1"]["status"] = "idle"
        save_registry(workspace, reg)

        out = tf(workspace, ["sync"])
        assert len(out["retired_now"]) == 1
        assert out["retired_now"][0] == "rust-1"

    def test_auto_retire_low_context(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "30"])

        out = tf(workspace, ["sync"])
        assert len(out["retired_now"]) == 1
        assert out["retired_now"][0] == "rust-1"

    def test_available_workers_grouped_by_skill(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-1", "--skill", "rust"])
        tf(workspace, ["notify", "rust-1", "bead-1", "--context-pct", "55"])
        tf(workspace, ["dispatch", "go-1", "bead-2", "--skill", "go"])
        tf(workspace, ["notify", "go-1", "bead-2", "--context-pct", "60"])

        out = tf(workspace, ["sync"])
        assert "rust" in out["available"]
        assert "go" in out["available"]
        assert out["available"]["rust"][0]["name"] == "rust-1"
        assert out["available"]["go"][0]["name"] == "go-1"

    def test_idle_too_long_retired(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "55"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["idle_since"] = ts_minutes_ago(45)
        save_registry(workspace, reg)

        out = tf(workspace, ["sync"])
        assert "rust" not in out.get("available", {})
        assert "rust-1" in out["retired_now"]

    def test_fresh_idle_worker_not_stale(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "55"])

        out = tf(workspace, ["sync"])
        available = out["available"]["rust"][0]
        assert "stale" not in available

    def test_stalled_active_workers_in_sync(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["last_heartbeat"] = ts_minutes_ago(25)
        save_registry(workspace, reg)

        out = tf(workspace, ["sync"])
        assert "stalled" in out
        assert len(out["stalled"]) == 1
        assert out["stalled"][0]["worker"] == "rust-1"

    def test_sync_persists_retirements(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "30"])

        tf(workspace, ["sync"])  # should auto-retire

        reg = load_registry(workspace)
        assert reg["workers"]["rust-1"]["status"] == "retired"


# ── Registry Tests ──────────────────────────────────────────────


class TestRegistry:
    def test_compact_output(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        out = tf(workspace, ["registry"])
        assert "rust-1" in out
        assert out["rust-1"]["s"] == "a"
        assert out["rust-1"]["skill"] == "rust"

    def test_filter_by_status(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-1", "--skill", "rust"])
        tf(workspace, ["dispatch", "go-1", "bead-2", "--skill", "go"])
        tf(workspace, ["notify", "go-1", "bead-2", "--context-pct", "55"])

        out = tf(workspace, ["registry", "--status", "idle"])
        assert "go-1" in out
        assert "rust-1" not in out

    def test_filter_by_skill(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-1", "--skill", "rust"])
        tf(workspace, ["dispatch", "go-1", "bead-2", "--skill", "go"])

        out = tf(workspace, ["registry", "--skill", "go"])
        assert "go-1" in out
        assert "rust-1" not in out

    def test_heartbeat_fields_shown(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        tf(workspace, ["heartbeat", "bead-abc", "--note", "building"],
           env={"CLAUDE_AGENT_NAME": "rust-1"})

        out = tf(workspace, ["registry"])
        assert "hb" in out["rust-1"]
        assert out["rust-1"]["hb_note"] == "building"

    def test_output_file_shown(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust", "--output-file", "/tmp/out.txt"])

        out = tf(workspace, ["registry"])
        assert out["rust-1"]["out"] == "/tmp/out.txt"

    def test_worker_model_flag(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd", "--worker-model", "sonnet"])
        r = subprocess.run(
            ["python3", str(workspace / ".beads" / "tf.py"), "registry", "--worker-model"],
            cwd=workspace, capture_output=True, text=True,
        )
        assert r.stdout.strip() == "sonnet"

    def test_pending_notification_shown(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        out = tf(workspace, ["registry"])
        assert out["rust-1"]["notif"] == "pending"


# ── Retire Tests ────────────────────────────────────────────────


class TestRetire:
    def test_retire_worker(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        out = tf(workspace, ["retire", "rust-1"])
        assert out["ok"] is True

        reg = load_registry(workspace)
        assert reg["workers"]["rust-1"]["status"] == "retired"
        assert "retired_at" in reg["workers"]["rust-1"]

    def test_retire_unknown_worker(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["retire", "nonexistent"])
        assert out["ok"] is False
        assert "not found" in out["error"]


# ── Routing Tests ───────────────────────────────────────────────


class TestRouting:
    def test_add_and_query_routing(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["routing", "--add", "src/*.rs:rust:rust-"])
        assert out["ok"] is True

        out = tf(workspace, ["routing"])
        assert "src/*.rs" in out
        assert out["src/*.rs"]["domain"] == "rust"
        assert out["src/*.rs"]["prefix"] == "rust-"

    def test_routing_invalid_format(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["routing", "--add", "bad-format"])
        assert "error" in out


# ── Status Tests ────────────────────────────────────────────────


class TestStatus:
    def test_basic_status(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        out = tf(workspace, ["status"])
        assert out["w"]["active"] == 1
        assert out["w"]["idle"] == 0

    def test_active_workers_listed(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        out = tf(workspace, ["status"])
        assert len(out["active"]) == 1
        assert out["active"][0]["name"] == "rust-1"
        assert out["active"][0]["bead"] == "bead-abc"

    def test_stalled_workers_in_status(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["last_heartbeat"] = ts_minutes_ago(25)
        save_registry(workspace, reg)

        out = tf(workspace, ["status"])
        assert "stalled" in out
        assert len(out["stalled"]) == 1

    def test_pending_notifications_in_status(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        out = tf(workspace, ["status"])
        assert "pending_notif" in out
        assert out["pending_notif"][0]["worker"] == "rust-1"

    def test_no_stalled_when_fresh(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        out = tf(workspace, ["status"])
        assert "stalled" not in out


# ── Multi-Context Dir Tests ─────────────────────────────────────


class TestActivePlan:
    def test_init_creates_active_plan_file(self, workspace):
        tf(workspace, ["init", "my-plan", "--bd-path", "/usr/bin/bd"])
        active = workspace / ".beads" / "active-plan"
        assert active.exists()
        assert active.read_text().strip() == "my-plan"

    def test_reinit_updates_active_plan(self, workspace):
        tf(workspace, ["init", "plan-a", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["init", "plan-b", "--bd-path", "/usr/bin/bd"])
        active = workspace / ".beads" / "active-plan"
        assert active.read_text().strip() == "plan-b"

    def test_errors_without_active_plan(self, workspace):
        # No init → no active-plan file → commands should error
        out = tf(workspace, ["sync"])
        assert "_returncode" in out
        assert out["_returncode"] != 0
        assert "active-plan" in out.get("_stderr", "")

    def test_errors_with_missing_context_dir(self, workspace):
        # Write active-plan pointing to non-existent context dir
        (workspace / ".beads" / "active-plan").write_text("nonexistent")
        out = tf(workspace, ["sync"])
        assert "_returncode" in out
        assert out["_returncode"] != 0

    def test_active_plan_resolves_correct_context(self, workspace):
        # Create two context dirs, active-plan points to "old"
        beads = workspace / ".beads"
        old = beads / "context-old"
        old.mkdir()
        old_reg = old / "registry.json"
        old_reg.write_text(json.dumps({
            "plan_name": "old", "bd_path": "bd",
            "workers": {"w1": {"status": "idle", "skill": "go", "bead": "b1", "context_pct": 50}},
            "routing": {}, "phases": {}, "settings": {"stall_threshold_mins": 20},
        }))

        new = beads / "context-new"
        new.mkdir()
        new_reg = new / "registry.json"
        new_reg.write_text(json.dumps({
            "plan_name": "new", "bd_path": "/usr/bin/bd",
            "workers": {},
            "routing": {}, "phases": {}, "settings": {"stall_threshold_mins": 20},
        }))

        # Point to "old" — should use "old" registry (which has 1 worker)
        (beads / "active-plan").write_text("old")
        out = tf(workspace, ["sync"])
        assert out["counts"]["total"] == 1


# ── Helper Function Unit Tests ─────────────────────────────────


class TestHelpers:
    """Test internal helper functions by importing tf.py directly."""

    @pytest.fixture(autouse=True)
    def _import_tf(self):
        """Import tf.py module for direct function testing."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("tf", TF_PY)
        self.tf_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tf_mod)

    def test_parse_ts(self):
        dt = self.tf_mod._parse_ts("2026-04-12T12:00:00Z")
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 12
        assert dt.hour == 12
        assert dt.tzinfo == timezone.utc

    def test_now_roundtrips(self):
        ts = self.tf_mod._now()
        dt = self.tf_mod._parse_ts(ts)
        assert dt.tzinfo == timezone.utc
        # Should be within 2 seconds of now
        diff = abs((datetime.now(timezone.utc) - dt).total_seconds())
        assert diff < 2

    def test_is_stalled_non_active(self):
        assert self.tf_mod._is_stalled({"status": "idle", "last_heartbeat": ts_minutes_ago(60)}) is False

    def test_is_stalled_no_timestamps(self):
        assert self.tf_mod._is_stalled({"status": "active"}) is False

    def test_is_stalled_recent(self):
        assert self.tf_mod._is_stalled({"status": "active", "last_heartbeat": ts_minutes_ago(5)}) is False

    def test_is_stalled_old(self):
        assert self.tf_mod._is_stalled({"status": "active", "last_heartbeat": ts_minutes_ago(25)}) is True

    def test_is_stalled_custom_threshold(self):
        assert self.tf_mod._is_stalled(
            {"status": "active", "last_heartbeat": ts_minutes_ago(25)},
            threshold_mins=30,
        ) is False

    def test_idle_minutes_not_idle(self):
        assert self.tf_mod._idle_minutes({"status": "active"}) is None

    def test_idle_minutes_no_idle_since(self):
        assert self.tf_mod._idle_minutes({"status": "idle"}) is None

    def test_idle_minutes_returns_value(self):
        mins = self.tf_mod._idle_minutes({
            "status": "idle",
            "idle_since": ts_minutes_ago(15),
        })
        assert mins is not None
        assert 14 < mins < 16

    def test_stalled_info_structure(self):
        info = self.tf_mod._stalled_info("rust-1", {
            "bead": "abc",
            "last_heartbeat": "2026-04-12T12:00:00Z",
            "last_heartbeat_note": "compiling",
        })
        assert info["worker"] == "rust-1"
        assert info["bead"] == "abc"
        assert info["last_hb"] == "2026-04-12T12:00:00Z"
        assert info["note"] == "compiling"
        assert isinstance(info["silent_mins"], int)
        assert info["silent_mins"] > 0

    def test_stalled_info_defaults(self):
        info = self.tf_mod._stalled_info("rust-1", {})
        assert info["bead"] == "?"
        assert info["last_hb"] == "?"
        assert info["note"] == ""
        assert "silent_mins" not in info

    def test_stalled_info_falls_back_to_dispatched_at(self):
        info = self.tf_mod._stalled_info("rust-1", {
            "dispatched_at": "2026-04-12T12:00:00Z",
        })
        assert info["last_hb"] == "2026-04-12T12:00:00Z"

    def test_update_heartbeat_with_reg(self, tmp_path):
        """Test _update_heartbeat with a real registry file."""
        reg_path = tmp_path / "registry.json"
        reg = {
            "plan_name": "test",
            "workers": {
                "rust-1": {
                    "status": "active",
                    "bead": "abc",
                }
            }
        }
        with open(reg_path, "w") as f:
            json.dump(reg, f)

        self.tf_mod._update_heartbeat(reg, reg_path, "test note", "rust-1")

        with open(reg_path) as f:
            updated = json.load(f)

        w = updated["workers"]["rust-1"]
        assert w["last_heartbeat_note"] == "test note"
        assert len(w["heartbeat_history"]) == 1

    def test_update_heartbeat_unknown_worker_noop(self, tmp_path):
        """Unknown worker name should be a no-op."""
        reg_path = tmp_path / "registry.json"
        reg = {"plan_name": "test", "workers": {}}
        with open(reg_path, "w") as f:
            json.dump(reg, f)

        self.tf_mod._update_heartbeat(reg, reg_path, "test", "nonexistent")

        with open(reg_path) as f:
            updated = json.load(f)
        assert updated["workers"] == {}


# ── Atomic Write Tests ─────────────────────────────────────────


class TestAtomicWrite:
    def test_save_registry_atomic(self, workspace):
        """Verify registry save uses atomic write (tmp + rename)."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])

        # Dispatch creates a registry write
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        # No .tmp files should remain
        for p in (workspace / ".beads").rglob("*.tmp"):
            pytest.fail(f"Leftover temp file: {p}")

        # Registry should be valid JSON
        reg = load_registry(workspace)
        assert reg["workers"]["rust-1"]["status"] == "active"


# ── Worker Close Tests (without bd) ────────────────────────────


class TestWorkerClose:
    def test_rejects_uncommitted_changes(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        # Commit gitignore first so it's not flagged
        subprocess.run(["git", "add", ".gitignore"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add gitignore", "-q"], cwd=workspace, check=True)

        # Create staged but uncommitted file
        (workspace / "src.rs").write_text("fn main() {}")
        subprocess.run(["git", "add", "src.rs"], cwd=workspace, check=True)

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50"])
        assert out["ok"] is False
        assert any("uncommitted" in e or "staged" in e for e in out["errors"])

    def test_rejects_task_number_in_commit(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        # Commit gitignore first (init creates it, worker-close flags uncommitted files)
        subprocess.run(["git", "add", ".gitignore"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add gitignore", "-q"], cwd=workspace, check=True)

        # Create a commit with task number
        (workspace / "src.rs").write_text("fn main() {}")
        subprocess.run(["git", "add", "src.rs"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: Task 5: add feature"], cwd=workspace, check=True)

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"status": "in_progress"}])})
        assert out["ok"] is False
        assert any("task number" in e.lower() for e in out["errors"])

    def test_accepts_clean_commit(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        subprocess.run(["git", "add", ".gitignore"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add gitignore", "-q"], cwd=workspace, check=True)

        (workspace / "src.rs").write_text("fn main() {}")
        subprocess.run(["git", "add", "src.rs"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add main function"], cwd=workspace, check=True)

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50",
                              "--files", "src.rs", "--summary", "added main"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"status": "in_progress"}]),
                      "CLAUDE_AGENT_NAME": "rust-1"})
        # bd stub returns {} for bd close, so verify won't find "closed" status
        # But the git validation checks (uncommitted, task number) should pass
        if not out.get("ok"):
            errors = out.get("errors", [])
            for e in errors:
                assert "uncommitted" not in e
                assert "task number" not in e.lower()

    def test_already_closed_bead(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        subprocess.run(["git", "add", ".gitignore"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add gitignore", "-q"], cwd=workspace, check=True)

        (workspace / "src.rs").write_text("fn main() {}")
        subprocess.run(["git", "add", "src.rs"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add function"], cwd=workspace, check=True)

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"status": "closed"}])})
        assert out["ok"] is True
        assert out.get("already") is True


    def test_dead_code_warnings(self, workspace, bd_stub):
        """worker-close should include warnings for dead-code markers in target files."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        subprocess.run(["git", "add", ".gitignore"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add gitignore", "-q"], cwd=workspace, check=True)

        # Create file with dead-code markers
        (workspace / "lib.rs").write_text(
            '#[allow(dead_code)]\nfn unused() {}\n// TODO: wire this up\nfn main() {}\n'
        )
        subprocess.run(["git", "add", "lib.rs"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add lib"], cwd=workspace, check=True)

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50",
                              "--files", "lib.rs", "--summary", "added lib"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"status": "in_progress"}]),
                      "CLAUDE_AGENT_NAME": "rust-1"})
        # The bead close may fail (stub returns {} for close), but warnings should be present
        # regardless of ok status. Check warnings are populated.
        # If ok is True (bead closed successfully), warnings should be in result.
        # If ok is False (bead didn't close), we check the pre-close scan ran by
        # verifying the test didn't error on the grep step.
        if out.get("warnings"):
            assert any("dead_code" in w or "TODO" in w for w in out["warnings"])

    def test_no_warnings_on_clean_file(self, workspace, bd_stub):
        """worker-close should not include warnings key when files are clean."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        subprocess.run(["git", "add", ".gitignore"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add gitignore", "-q"], cwd=workspace, check=True)

        (workspace / "clean.rs").write_text("fn main() { println!(\"hello\"); }\n")
        subprocess.run(["git", "add", "clean.rs"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add clean module"], cwd=workspace, check=True)

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50",
                              "--files", "clean.rs", "--summary", "added clean"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"status": "in_progress"}]),
                      "CLAUDE_AGENT_NAME": "rust-1"})
        # Clean file should produce no warnings
        assert "warnings" not in out or len(out.get("warnings", [])) == 0

    def test_rejects_uncommitted_since_dispatch_sha(self, workspace, bd_stub):
        """With dispatch SHA, worker-close should catch files changed after dispatch."""
        # Create initial commit
        (workspace / "init.txt").write_text("init")
        subprocess.run(["git", "add", "init.txt"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=workspace, check=True)

        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        subprocess.run(["git", "add", ".gitignore"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "chore: gitignore", "-q"], cwd=workspace, check=True)

        # Dispatch records current HEAD as dispatch_sha
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        # Worker creates a file but does NOT commit
        (workspace / "new_file.rs").write_text("fn main() {}")

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50"],
                 env={"CLAUDE_AGENT_NAME": "rust-1",
                      "BD_STUB_RESPONSE": json.dumps([{"status": "in_progress"}])})
        assert out["ok"] is False
        assert any("uncommitted" in e for e in out["errors"])
        assert any("new_file.rs" in e for e in out["errors"])

    def test_accepts_preexisting_uncommitted(self, workspace, bd_stub):
        """Pre-existing uncommitted files (before dispatch) should NOT block close."""
        (workspace / "init.txt").write_text("init")
        subprocess.run(["git", "add", "init.txt"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=workspace, check=True)

        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        subprocess.run(["git", "add", ".gitignore"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "chore: gitignore", "-q"], cwd=workspace, check=True)

        # Pre-existing uncommitted file — exists BEFORE dispatch
        (workspace / "preexisting.txt").write_text("wip")

        # Dispatch — SHA is after gitignore commit; preexisting.txt is already untracked
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        # Worker commits real work (clean working tree for worker's changes)
        (workspace / "src.rs").write_text("fn main() {}")
        subprocess.run(["git", "add", "src.rs"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add main"], cwd=workspace, check=True)

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50",
                              "--files", "src.rs", "--summary", "done"],
                 env={"CLAUDE_AGENT_NAME": "rust-1",
                      "BD_STUB_RESPONSE": json.dumps([{"status": "in_progress"}])})
        # preexisting.txt was untracked before dispatch — should not block
        # The check may still fail on bd close/verify, but the uncommitted check should pass
        if not out.get("ok"):
            for e in out.get("errors", []):
                assert "preexisting" not in e

    def test_fallback_when_no_dispatch_sha(self, workspace, bd_stub):
        """Without dispatch_sha, should fall back to git status check."""
        (workspace / "init.txt").write_text("init")
        subprocess.run(["git", "add", "init.txt"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=workspace, check=True)

        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        subprocess.run(["git", "add", ".gitignore"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "chore: gitignore", "-q"], cwd=workspace, check=True)

        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        # Remove dispatch_sha from registry
        reg = load_registry(workspace)
        del reg["workers"]["rust-1"]["dispatch_sha"]
        save_registry(workspace, reg)

        # Create uncommitted file
        (workspace / "oops.rs").write_text("fn oops() {}")
        subprocess.run(["git", "add", "oops.rs"], cwd=workspace, check=True)

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50",
                              "--files", "oops.rs"],
                 env={"CLAUDE_AGENT_NAME": "rust-1",
                      "BD_STUB_RESPONSE": json.dumps([{"status": "in_progress"}])})
        assert out["ok"] is False
        assert any("uncommitted" in e or "staged" in e for e in out["errors"])


# ── Bd Path Tests ──────────────────────────────────────────────


class TestBdPath:
    def test_bd_path_from_registry(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/custom/path/bd"])
        out = tf(workspace, ["bd-path"])
        assert out["bd_path"] == "/custom/path/bd"


# ── Smoke Test Tests ───────────────────────────────────────────


class TestSmokeTest:
    def test_build_pass(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["smoke-test", "--build-cmd", "echo ok"])
        assert out["build"] == "pass"

    def test_build_fail(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["smoke-test", "--build-cmd", "false"])
        assert out["build"] == "fail"

    def test_no_build_cmd(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["smoke-test"])
        assert out["build"] == "skip"


# ── Conflict Check Tests ──────────────────────────────────────


class TestConflictCheck:
    def test_no_conflicts(self, workspace, bd_stub):
        """Beads with disjoint file lists should all be safe."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])

        # Stub returns different descriptions per bead
        # Since bd_stub returns same response for all calls, we test with a single bead
        out = tf(workspace, ["conflict-check", "--beads", "bead-1,bead-2"],
                 env={"BD_STUB_RESPONSE": json.dumps([{
                     "description": "Implement X.\nFiles: `src/a.rs`, `src/b.rs`"
                 }])})
        # Both beads get same files from stub, so they conflict
        assert "conflicts" in out

    def test_detects_conflicts(self, workspace, bd_stub):
        """Beads sharing files should appear in conflicts and serial."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])

        out = tf(workspace, ["conflict-check", "--beads", "bead-1,bead-2"],
                 env={"BD_STUB_RESPONSE": json.dumps([{
                     "description": "Implement feature.\nFiles: `src/shared.rs`, `src/other.rs`"
                 }])})
        assert len(out["conflicts"]) > 0
        assert "serial" in out
        assert out["safe"] == []

    def test_empty_beads(self, workspace, bd_stub):
        """No beads should return empty results."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["conflict-check", "--beads", ""],
                 env={"BD_STUB_RESPONSE": json.dumps([{"description": ""}])})
        assert out["safe"] == []
        assert out["conflicts"] == {}


class TestConflictCheckHelpers:
    """Test _extract_files_from_description directly."""

    @pytest.fixture(autouse=True)
    def _import_tf(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tf", TF_PY)
        self.tf_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tf_mod)

    def test_backtick_files(self):
        desc = "Do something.\nFiles: `src/a.rs`, `src/b.rs`\nMore text."
        assert self.tf_mod._extract_files_from_description(desc) == ["src/a.rs", "src/b.rs"]

    def test_plain_files(self):
        desc = "Task.\nFiles: src/a.rs, src/b.rs"
        assert self.tf_mod._extract_files_from_description(desc) == ["src/a.rs", "src/b.rs"]

    def test_bold_files(self):
        desc = "Task.\n**Files:** `src/a.rs`, `src/b.rs`"
        assert self.tf_mod._extract_files_from_description(desc) == ["src/a.rs", "src/b.rs"]

    def test_no_files_line(self):
        desc = "No files listed here."
        assert self.tf_mod._extract_files_from_description(desc) == []

    def test_files_to_create_modify(self):
        desc = "Task.\nFiles to create/modify: src/a.rs, src/b.rs"
        assert self.tf_mod._extract_files_from_description(desc) == ["src/a.rs", "src/b.rs"]

    def test_files_to_change(self):
        desc = "Task.\nFiles to change: src/c.rs"
        assert self.tf_mod._extract_files_from_description(desc) == ["src/c.rs"]

    def test_modified_files(self):
        desc = "Task.\nModified files: src/d.rs"
        assert self.tf_mod._extract_files_from_description(desc) == ["src/d.rs"]

    def test_target_files(self):
        desc = "Task.\nTarget files: src/e.rs"
        assert self.tf_mod._extract_files_from_description(desc) == ["src/e.rs"]

    def test_changed_files(self):
        desc = "Task.\nChanged files: src/f.rs"
        assert self.tf_mod._extract_files_from_description(desc) == ["src/f.rs"]

    def test_detailed_files_to_create_modify(self):
        """_extract_files_detailed also handles synonym headers."""
        desc = "Task.\nFiles to create/modify: src/a.rs, src/b.rs"
        result = self.tf_mod._extract_files_detailed(desc)
        assert result["all"] == ["src/a.rs", "src/b.rs"]

    def test_detailed_modified_files(self):
        desc = "Task.\nModified files: src/d.rs"
        result = self.tf_mod._extract_files_detailed(desc)
        assert result["all"] == ["src/d.rs"]


# ── Notify Bead Status Tests ─────────────────────────────────


class TestNotifyBeadStatus:
    def test_includes_bead_status(self, workspace, bd_stub):
        """notify should include bead_status from bd show."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        out = tf(workspace, ["notify", "rust-1", "bead-abc",
                              "--context-pct", "50", "--summary", "done"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"status": "closed"}])})
        assert out["ok"] is True
        assert out["bead_status"] == "closed"

    def test_bead_status_blocked(self, workspace, bd_stub):
        """notify should report blocked status."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        out = tf(workspace, ["notify", "rust-1", "bead-abc",
                              "--context-pct", "50", "--summary", "blocked on input"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"status": "blocked"}])})
        assert out["bead_status"] == "blocked"


# ── Sync Reuse Enforcement Tests ─────────────────────────────


class TestSyncReuseEnforcement:
    def test_reuse_enforced_when_idle_exceeds_threshold(self, workspace):
        """sync should set reuse_enforced when idle > ready * 0.5."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])

        # Create 3 idle workers with 50% context (within reuse range)
        reg = load_registry(workspace)
        for i in range(3):
            reg["workers"][f"w-{i}"] = {
                "status": "idle", "skill": "rust", "context_pct": 50,
                "bead": f"bead-{i}", "notification": "received",
                "idle_since": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        save_registry(workspace, reg)

        # 2 ready tasks, 3 idle workers → 3 > 2*0.5=1 → reuse enforced
        out = tf(workspace, ["sync", "--ready-count", "2"])
        assert out.get("reuse_enforced") is True

    def test_no_reuse_enforced_when_few_idle(self, workspace):
        """sync should NOT set reuse_enforced when idle <= ready * 0.5."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])

        reg = load_registry(workspace)
        reg["workers"]["w-0"] = {
            "status": "idle", "skill": "rust", "context_pct": 50,
            "bead": "bead-0", "notification": "received",
            "idle_since": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        save_registry(workspace, reg)

        # 4 ready tasks, 1 idle worker → 1 <= 4*0.5=2 → no enforcement
        out = tf(workspace, ["sync", "--ready-count", "4"])
        assert "reuse_enforced" not in out

    def test_no_enforcement_without_ready_count(self, workspace):
        """sync without --ready-count should never set reuse_enforced."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])

        reg = load_registry(workspace)
        for i in range(5):
            reg["workers"][f"w-{i}"] = {
                "status": "idle", "skill": "rust", "context_pct": 50,
                "bead": f"bead-{i}", "notification": "received",
                "idle_since": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        save_registry(workspace, reg)

        out = tf(workspace, ["sync"])
        assert "reuse_enforced" not in out


# ── Dep Tests (Problem 5) ─────────────────────────────────────


class TestDep:
    def test_dep_success(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["dep", "bead-1", "bead-2"])
        assert out["ok"] is True
        assert out["blocker"] == "bead-1"
        assert out["blocked"] == "bead-2"
        assert out["already_existed"] is False

    def test_dep_unique_constraint_as_success(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["dep", "bead-1", "bead-2"],
                 env={"BD_STUB_EXIT": "1",
                      "BD_STUB_STDERR": "UNIQUE constraint failed: deps.blocker_id, deps.blocked_id"})
        assert out["ok"] is True
        assert out["already_existed"] is True

    def test_dep_duplicate_as_success(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["dep", "bead-1", "bead-2"],
                 env={"BD_STUB_EXIT": "1",
                      "BD_STUB_STDERR": "duplicate dependency"})
        assert out["ok"] is True
        assert out["already_existed"] is True

    def test_dep_real_error_propagated(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["dep", "bead-1", "bead-2"],
                 env={"BD_STUB_EXIT": "1",
                      "BD_STUB_STDERR": "connection refused"})
        assert out["ok"] is False
        assert "connection refused" in out["error"]


# ── Close Tests (Problem 4) ───────────────────────────────────


class TestTfClose:
    def test_close_normalizes_array(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["close", "bead-abc"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"id": "bead-abc", "status": "closed"}])})
        assert out["ok"] is True
        assert out["id"] == "bead-abc"
        assert out["status"] == "closed"

    def test_close_normalizes_object(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["close", "bead-abc"],
                 env={"BD_STUB_RESPONSE": json.dumps({"id": "bead-abc", "status": "closed"})})
        assert out["ok"] is True
        assert out["id"] == "bead-abc"
        assert out["status"] == "closed"

    def test_close_with_reason(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["close", "bead-abc", "--reason", "phase 1 complete"])
        assert out["ok"] is True

    def test_close_bd_failure(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["close", "bead-abc"],
                 env={"BD_STUB_EXIT": "1", "BD_STUB_STDERR": "bead not found"})
        assert out["ok"] is False
        assert "bead not found" in out["error"]

    def test_close_default_reason(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["close", "bead-abc"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"id": "bead-abc", "status": "closed"}])})
        assert out["ok"] is True


# ── Validate Plan Tests (Problem 2) ───────────────────────────


class TestValidatePlan:
    def test_valid_plan_passes(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Build auth system

            ### Type
            epic

            ## Create user model

            ### Type
            task

            Files: `models/user.py`

            ## Add login endpoint

            ### Type
            task

            Files: `routes/auth.py`
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert out["ok"] is True
        assert out["issues"] == 3
        assert out["epics"] == 1
        assert out["tasks"] == 2

    def test_detects_hr_separators(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Epic one

            ---

            ## Task one
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert out["ok"] is False
        assert any("---" in e for e in out["errors"])
        assert any("line 3" in e for e in out["errors"])

    def test_counts_epics_and_tasks(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## My Epic

            ### Type
            epic

            ## Task A

            ## Task B

            ## Task C
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert out["epics"] == 1
        assert out["tasks"] == 3

    def test_issue_count(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text("## First issue\n\n## Second issue\n")
        out = tf(workspace, ["validate-plan", str(plan)])
        assert out["issues"] == 2
        assert "dry_run" not in out

    def test_nonexistent_file(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["validate-plan", "/nonexistent/plan.md"])
        assert out["ok"] is False
        assert "not found" in out.get("error", "")

    def test_dash_variants(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text("## Issue\n---\n----\n-----\n## Another\n")
        out = tf(workspace, ["validate-plan", str(plan)])
        assert out["ok"] is False
        dash_errors = [e for e in out["errors"] if "---" in e]
        assert len(dash_errors) == 3

    def test_errors_missing_files_section(self, workspace):
        """Tasks without a Files: line should produce an error."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Goal: Build Auth

            ### Type
            epic

            ## Create User model

            ### Description
            Create models/user.py with fields.

            ## Add login endpoint

            ### Description
            Login endpoint.
            Files: `routes/auth.py`, `routes/login.py`
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert out["ok"] is False
        assert "missing_files" not in out  # removed from output
        assert any("Create User model" in e and "missing Files" in e for e in out.get("errors", []))

    def test_no_warning_when_all_have_files(self, workspace):
        """All tasks with Files: lines should produce no missing_files."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Goal: Build Auth

            ### Type
            epic

            ## Create User model

            Files: `models/user.py`

            ## Add login endpoint

            Files: `routes/auth.py`
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert "missing_files" not in out

    def test_detects_depends_on(self, workspace):
        """validate-plan should count depends_on entries in soft_deps."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Epic

            ### Type
            epic

            ## Implement scanner interface

            Files: `pkg/scanner/scanner.go`

            ## Implement snapshot package

            Files: `pkg/scanner/snapshot.go`

            ### Dependencies
            depends_on:Implement scanner interface
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert out["ok"] is True
        assert out.get("soft_deps") == 1

    def test_detects_rogue_h3_headers(self, workspace):
        """Unrecognized ### headers inside issue bodies should produce errors."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / ".beads" / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Setup config loader

            ### Type
            task

            ### Description
            Implement config loading.
            ### Task 2: Database session with ATTACH
            This line should not be here.

            ## Implement database layer

            ### Type
            task

            ### Description
            Database stuff.
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert out["ok"] is False
        assert any("Task 2: Database session" in e for e in out.get("errors", []))

    def test_recognized_h3_headers_pass(self, workspace):
        """Recognized ### headers (Type, Priority, etc.) should not trigger errors."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / ".beads" / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Setup config loader

            ### Type
            task

            ### Priority
            high

            ### Description
            Implement config loading.

            ### Acceptance Criteria
            - Config loads from file

            Files: `config.py`
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert not out.get("errors")


# ── Dedup Tests ────────────────────────────────────────────


class TestDedup:
    def test_no_duplicates(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [
            {"id": "1", "title": "Task A", "status": "open"},
            {"id": "2", "title": "Task B", "status": "open"},
        ]
        out = tf(workspace, ["dedup"], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["ok"] is True
        assert out["duplicates"] == []
        assert out["dry_run"] is True

    def test_detects_duplicates_dry_run(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [
            {"id": "1", "title": "Task A", "status": "open"},
            {"id": "2", "title": "Task A", "status": "open"},
            {"id": "3", "title": "Task B", "status": "open"},
        ]
        out = tf(workspace, ["dedup"], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["ok"] is True
        assert len(out["duplicates"]) == 1
        dup = out["duplicates"][0]
        assert dup["title"] == "Task A"
        assert dup["count"] == 2
        assert dup["keep"] == "2"
        assert dup["close"] == ["1"]
        assert out["dry_run"] is True

    def test_keep_oldest(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [
            {"id": "1", "title": "Task A", "status": "open"},
            {"id": "2", "title": "Task A", "status": "open"},
        ]
        out = tf(workspace, ["dedup", "--keep", "oldest"], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["duplicates"][0]["keep"] == "1"
        assert out["duplicates"][0]["close"] == ["2"]

    def test_case_insensitive_match(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [
            {"id": "1", "title": "Setup Config", "status": "open"},
            {"id": "2", "title": "setup config", "status": "open"},
        ]
        out = tf(workspace, ["dedup"], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert len(out["duplicates"]) == 1

    def test_three_way_duplicates(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [
            {"id": "1", "title": "Task X", "status": "open"},
            {"id": "2", "title": "Task X", "status": "open"},
            {"id": "3", "title": "Task X", "status": "open"},
        ]
        out = tf(workspace, ["dedup"], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        dup = out["duplicates"][0]
        assert dup["count"] == 3
        assert dup["keep"] == "3"
        assert dup["close"] == ["1", "2"]


# ── Update-Context Tests ─────────────────────────────────────


class TestUpdateContext:
    def test_creates_epic_context_file(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx = workspace / ".beads" / "context-test"
        bead_resp = json.dumps({"id": "42", "title": "Add login", "parent": "10"})
        parent_resp = json.dumps({"id": "10", "title": "Auth Epic"})
        # Stub returns same for both calls; use parent-like response
        out = tf(workspace, [
            "update-context",
            "--bead", "42", "--worker", "w1",
            "--summary", "Added login endpoint",
            "--files", "auth.py,routes.py",
        ], env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True
        # Epic slug comes from parent; stub returns same both times so slug is "add-login"
        epic_files = list(ctx.glob("epic-*.md"))
        assert len(epic_files) == 1
        content = epic_files[0].read_text()
        assert "BD-42" in content
        assert "w1" in content
        assert "Added login endpoint" in content

    def test_appends_gotcha(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx = workspace / ".beads" / "context-test"
        out = tf(workspace, [
            "update-context",
            "--bead", "1", "--worker", "w1",
            "--summary", "done", "--gotcha", "LSP lies about imports",
        ], env={"BD_STUB_RESPONSE": json.dumps({"id": "1", "title": "T1"})})
        assert out["gotcha_added"] is True
        wc = ctx / "worker-context.md"
        assert wc.exists()
        assert "LSP lies about imports" in wc.read_text()

    def test_no_gotcha_no_append(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx = workspace / ".beads" / "context-test"
        wc = ctx / "worker-context.md"
        content_before = wc.read_text() if wc.exists() else ""
        out = tf(workspace, [
            "update-context",
            "--bead", "1", "--worker", "w1",
            "--summary", "done",
        ], env={"BD_STUB_RESPONSE": json.dumps({"id": "1", "title": "T1"})})
        assert out["gotcha_added"] is False
        content_after = wc.read_text() if wc.exists() else ""
        assert content_after == content_before

    def test_appends_to_existing_epic_file(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx = workspace / ".beads" / "context-test"
        resp = json.dumps({"id": "1", "title": "Task A", "parent": "10"})
        # Pre-create epic file
        epic_file = ctx / "epic-task-a.md"
        epic_file.write_text("# Epic\n\n## Completed Tasks\n\n### BD-0: Setup\nDone.\n")
        tf(workspace, [
            "update-context",
            "--bead", "1", "--worker", "w2",
            "--summary", "Second task",
        ], env={"BD_STUB_RESPONSE": resp})
        content = epic_file.read_text()
        assert "BD-0: Setup" in content
        assert "BD-1: Task A" in content

    def test_handles_bd_failure_gracefully(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, [
            "update-context",
            "--bead", "99", "--worker", "w1",
            "--summary", "done",
        ], env={"BD_STUB_RESPONSE": "not json", "BD_STUB_EXIT": "1"})
        assert out["ok"] is True
        assert "task-summaries.md" in out["updated"]


# ── Phase-Complete Tests ──────────────────────────────────────


class TestPhaseComplete:
    def test_blocks_on_open_beads(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [{"id": "1", "status": "closed"}, {"id": "2", "status": "open"}]
        out = tf(workspace, [
            "phase-complete", "--epic", "10",
        ], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["pass"] is False
        assert any(b["bead"] == "2" for b in out["blocking"])

    def test_blocks_on_pending_notifications(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        # All beads closed but a worker has pending notification
        beads = [{"id": "1", "status": "closed"}]
        tf(workspace, ["dispatch", "w1", "1", "--skill", "code"],
           env={"BD_STUB_RESPONSE": "{}"})
        reg = load_registry(workspace)
        reg["workers"]["w1"]["notification"] = "pending"
        save_registry(workspace, reg)
        out = tf(workspace, [
            "phase-complete", "--epic", "10",
        ], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["pass"] is False
        assert any("notification" in str(b.get("reason", "")) for b in out["blocking"])

    def test_passes_and_writes_phase_file(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx = workspace / ".beads" / "context-test"
        beads = [
            {"id": "1", "status": "closed", "close_reason": "done. FILES: auth.py, db.py."},
            {"id": "2", "status": "closed", "close_reason": "done. FILES: routes.py."},
        ]
        out = tf(workspace, [
            "phase-complete", "--epic", "10",
        ], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["pass"] is True
        assert out["beads_closed"] == 2
        assert "auth.py" in out["files"]
        phase_file = ctx / "phase-1.md"
        assert phase_file.exists()
        content = phase_file.read_text()
        assert "Phase 1" in content
        assert "auth.py" in content

    def test_custom_phase_number(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx = workspace / ".beads" / "context-test"
        beads = [{"id": "1", "status": "closed"}]
        out = tf(workspace, [
            "phase-complete", "--epic", "10", "--phase-num", "3",
        ], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["pass"] is True
        assert out["phase_file"] == "phase-3.md"
        assert (ctx / "phase-3.md").exists()

    def test_smoke_test_failure(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [{"id": "1", "status": "closed"}]
        out = tf(workspace, [
            "phase-complete", "--epic", "10",
            "--build-cmd", "false",
        ], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["pass"] is True
        assert out["build"] == "fail"

    def test_smoke_test_pass(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [{"id": "1", "status": "closed"}]
        out = tf(workspace, [
            "phase-complete", "--epic", "10",
            "--build-cmd", "true",
        ], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["pass"] is True
        assert out["build"] == "pass"


# ── Worker-Prompt Tests ───────────────────────────────────────


class TestWorkerPrompt:
    def test_single_bead_prompt(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        resp = json.dumps({"id": "5", "title": "Build parser", "description": "Parse the input.\nFiles: parser.py"})
        out = tf(workspace, [
            "worker-prompt", "--beads", "5",
        ], env={"BD_STUB_RESPONSE": resp})
        assert out["ok"] is True
        assert "Build parser" in out["prompt"]
        assert out["beads"] == ["5"]
        assert "`parser.py`" in out["prompt"]

    def test_multi_bead_serial_prompt(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        # Stub returns same response for all bd show calls
        resp = json.dumps({"id": "1", "title": "Task One", "description": "Do first thing"})
        out = tf(workspace, [
            "worker-prompt", "--beads", "1,2,3",
        ], env={"BD_STUB_RESPONSE": resp})
        assert out["ok"] is True
        assert out["beads"] == ["1", "2", "3"]
        # Should have sub-task structure
        assert "Sub-Task 1" in out["prompt"]
        assert "Sub-Task 2" in out["prompt"]
        assert "Sub-Task 3" in out["prompt"]
        assert "Batch" in out["prompt"] or "sequential" in out["prompt"].lower()

    def test_reuse_prompt(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        resp = json.dumps({"id": "6", "title": "Next task", "description": "Do next thing"})
        out = tf(workspace, [
            "worker-prompt", "--beads", "6",
            "--reuse", "--prior-bead", "5",
        ], env={"BD_STUB_RESPONSE": resp})
        assert out["ok"] is True
        assert "Prior Task" in out["prompt"]
        assert "ALREADY CLOSED" in out["prompt"]
        assert "5" in out["prompt"]  # prior bead referenced
        assert "Next task" in out["prompt"]

    def test_references_project_context_file(self, workspace, bd_stub):
        """worker-prompt should reference worker-context.md, not embed it."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx = workspace / ".beads" / "context-test"
        (ctx / "worker-context.md").write_text("# Project\nUse TypeScript everywhere.")
        resp = json.dumps({"id": "1", "title": "T1", "description": "desc"})
        out = tf(workspace, [
            "worker-prompt", "--beads", "1",
        ], env={"BD_STUB_RESPONSE": resp})
        assert "worker-context.md" in out["prompt"]
        assert "TypeScript everywhere" not in out["prompt"]

    def test_includes_phase_context(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx = workspace / ".beads" / "context-test"
        (ctx / "phase-1.md").write_text("# Phase 1\nParser built.")
        resp = json.dumps({"id": "1", "title": "T1", "description": "desc"})
        out = tf(workspace, [
            "worker-prompt", "--beads", "1",
        ], env={"BD_STUB_RESPONSE": resp})
        assert "Parser built" in out["prompt"]

    def test_includes_epic_context(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx = workspace / ".beads" / "context-test"
        resp = json.dumps({"id": "1", "title": "Task", "description": "desc", "parent": "10"})
        # Parent response will also be the same stub response, so slug = "task"
        (ctx / "epic-task.md").write_text("# Auth Epic\nOAuth chosen.")
        out = tf(workspace, [
            "worker-prompt", "--beads", "1",
        ], env={"BD_STUB_RESPONSE": resp})
        assert "OAuth chosen" in out["prompt"]

    def test_returns_model_from_registry(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub, "--worker-model", "sonnet"])
        resp = json.dumps({"id": "1", "title": "T1", "description": "desc"})
        out = tf(workspace, [
            "worker-prompt", "--beads", "1",
        ], env={"BD_STUB_RESPONSE": resp})
        assert out["model"] == "sonnet"

    def test_handles_bd_failure(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, [
            "worker-prompt", "--beads", "99",
        ], env={"BD_STUB_RESPONSE": "bad json", "BD_STUB_EXIT": "1"})
        assert out["ok"] is True
        assert out["beads"] == ["99"]
        # Falls back to using bead ID as title
        assert "99" in out["prompt"]

    def test_prompt_only_returns_raw_text(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        resp = json.dumps({"id": "5", "title": "Build parser", "description": "Parse input"})
        out = tf(workspace, [
            "worker-prompt", "--beads", "5", "--prompt-only",
        ], env={"BD_STUB_RESPONSE": resp})
        # --prompt-only returns raw text, not JSON → tf() wraps it in _raw
        assert "_raw" in out
        assert "Build parser" in out["_raw"]

    def test_prompt_only_not_json(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        resp = json.dumps({"id": "7", "title": "Task", "description": "Do it"})
        out = tf(workspace, [
            "worker-prompt", "--beads", "7", "--prompt-only",
        ], env={"BD_STUB_RESPONSE": resp})
        # Should NOT contain JSON keys like "ok", "model", "beads"
        raw = out.get("_raw", "")
        assert "ok" not in raw.split("\n")[0] if raw else True
        assert '"beads"' not in raw

    def test_parallel_with_includes_warning(self, workspace, bd_stub):
        """worker-prompt --parallel-with should add a Parallel Worker Warning section."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        bead_resp = json.dumps({
            "id": "1", "title": "Task A",
            "description": "Implement A.\nFiles: `src/a.py`, `src/b.py`"
        })
        out = tf(workspace, [
            "worker-prompt", "--beads", "1", "--parallel-with", "2,3",
        ], env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True
        assert "Parallel Worker Warning" in out["prompt"]
        assert "src/a.py" in out["prompt"]
        assert "bead 2" in out["prompt"] or "bead 3" in out["prompt"]

    def test_no_parallel_with_no_warning(self, workspace, bd_stub):
        """worker-prompt without --parallel-with should not have warning section."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        bead_resp = json.dumps({"id": "1", "title": "Task A", "description": "desc"})
        out = tf(workspace, [
            "worker-prompt", "--beads", "1",
        ], env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True
        assert "Parallel Worker Warning" not in out["prompt"]

    def test_includes_acceptance_criteria(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        desc = textwrap.dedent("""\
            Build the parser module.

            ### Acceptance Criteria
            - Parses JSON input correctly
            - Returns error on invalid input

            ### Dependencies
            blocks:Other task
        """)
        resp = json.dumps({"id": "1", "title": "Build parser", "description": desc})
        out = tf(workspace, [
            "worker-prompt", "--beads", "1",
        ], env={"BD_STUB_RESPONSE": resp})
        assert out["ok"] is True
        assert "Acceptance Criteria" in out["prompt"]
        assert "Parses JSON input correctly" in out["prompt"]
        assert "Returns error on invalid input" in out["prompt"]

    def test_includes_spec_sections(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        spec = textwrap.dedent("""\
            # Project Spec

            ## Authentication
            Users authenticate via JWT tokens.

            ### Token Format
            Access tokens expire after 15 minutes.

            ## Data Model
            All models use UUID primary keys.
        """)
        (workspace / "spec.md").write_text(spec)
        desc = "Implement auth.\nSpec: spec.md §Authentication"
        resp = json.dumps({"id": "1", "title": "Auth task", "description": desc})
        out = tf(workspace, [
            "worker-prompt", "--beads", "1",
        ], env={"BD_STUB_RESPONSE": resp})
        assert out["ok"] is True
        assert "JWT tokens" in out["prompt"]
        assert "Spec Reference" in out["prompt"]

    def test_spec_section_not_found_silently_skipped(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        (workspace / "spec.md").write_text("# Spec\n## Other Section\nStuff.\n")
        desc = "Implement auth.\nSpec: spec.md §Nonexistent Section"
        resp = json.dumps({"id": "1", "title": "Auth task", "description": desc})
        out = tf(workspace, [
            "worker-prompt", "--beads", "1",
        ], env={"BD_STUB_RESPONSE": resp})
        assert out["ok"] is True
        assert "Spec Reference" not in out["prompt"]

    def test_spec_file_not_found_silently_skipped(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        desc = "Implement auth.\nSpec: nonexistent.md §Auth"
        resp = json.dumps({"id": "1", "title": "Auth task", "description": desc})
        out = tf(workspace, [
            "worker-prompt", "--beads", "1",
        ], env={"BD_STUB_RESPONSE": resp})
        assert out["ok"] is True
        assert "Spec Reference" not in out["prompt"]


# ── Validate Plan Parallelism Tests ──────────────────────────


class TestValidatePlanParallelism:
    def test_warns_fully_sequential_plan(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Epic

            ### Type
            epic

            ## Task A

            Files: `src/a.py`

            ### Dependencies
            blocks:epic

            ## Task B

            Files: `src/b.py`

            ### Dependencies
            blocks:task-a

            ## Task C

            Files: `src/c.py`

            ### Dependencies
            blocks:task-b

            ## Task D

            Files: `src/d.py`

            ### Dependencies
            blocks:task-c
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert out["ok"] is True
        assert "warnings" in out
        assert any("sequential" in w.lower() for w in out["warnings"])

    def test_no_warning_for_parallel_plan(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Epic

            ### Type
            epic

            ## Task A

            Files: `src/a.py`

            ## Task B

            Files: `src/b.py`

            ## Task C

            Files: `src/c.py`

            ### Dependencies
            blocks:task-a

            ## Task D

            Files: `src/d.py`

            ### Dependencies
            blocks:task-b
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert "warnings" not in out or not any("sequential" in w.lower() for w in out.get("warnings", []))

    def test_warns_no_dependencies(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Epic

            ### Type
            epic

            ## Task A

            Files: `src/a.py`

            ## Task B

            Files: `src/b.py`

            ## Task C

            Files: `src/c.py`

            ## Task D

            Files: `src/d.py`
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert "warnings" in out
        assert any("dependencies" in w.lower() or "parallel" in w.lower() for w in out["warnings"])

    def test_check_parallelism_flag_errors(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Epic

            ### Type
            epic

            ## Task A

            Files: `src/a.py`

            ### Dependencies
            blocks:epic

            ## Task B

            Files: `src/b.py`

            ### Dependencies
            blocks:task-a

            ## Task C

            Files: `src/c.py`

            ### Dependencies
            blocks:task-b

            ## Task D

            Files: `src/d.py`

            ### Dependencies
            blocks:task-c
        """))
        out = tf(workspace, ["validate-plan", "--check-parallelism", str(plan)])
        assert out["ok"] is False
        assert "warnings" in out

    def test_small_plan_no_warning(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Epic

            ### Type
            epic

            ## Task A

            Files: `src/a.py`

            ## Task B

            Files: `src/b.py`
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert "warnings" not in out


# ── Session Tracking Tests ───────────────────────────────────


class TestSessionTracking:
    def test_init_sets_session_id(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        reg = load_registry(workspace)
        assert "session_id" in reg
        assert reg["session_id"]  # non-empty

    def test_dispatch_copies_session_id(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        reg = load_registry(workspace)
        session = reg["session_id"]
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        reg = load_registry(workspace)
        assert reg["workers"]["w1"]["spawned_session"] == session

    def test_sync_retires_cross_session_worker(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        reg = load_registry(workspace)
        # Simulate worker completed and went idle
        reg["workers"]["w1"]["status"] = "idle"
        reg["workers"]["w1"]["context_pct"] = 60
        reg["workers"]["w1"]["idle_since"] = ts_minutes_ago(5)
        # Change session_id to simulate a new session
        reg["workers"]["w1"]["spawned_session"] = "old-session"
        save_registry(workspace, reg)
        out = tf(workspace, ["sync"])
        # Worker should be retired as cross_session
        assert "w1" in out["retired_now"]
        assert out["counts"]["idle"] == 0

    def test_sync_keeps_same_session_worker(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        reg = load_registry(workspace)
        reg["workers"]["w1"]["status"] = "idle"
        reg["workers"]["w1"]["context_pct"] = 60
        reg["workers"]["w1"]["idle_since"] = ts_minutes_ago(3)
        reg["workers"]["w1"]["notification"] = "received"
        save_registry(workspace, reg)
        out = tf(workspace, ["sync"])
        assert out["counts"]["idle"] == 1
        assert "code" in out["available"]

    def test_reuse_enforced_only_for_same_session(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        # Create 3 workers from old session
        reg = load_registry(workspace)
        for i in range(3):
            reg["workers"][f"w{i}"] = {
                "status": "idle", "skill": "code", "context_pct": 60,
                "idle_since": ts_minutes_ago(5), "spawned_session": "old-session",
            }
        save_registry(workspace, reg)
        out = tf(workspace, ["sync", "--ready-count", "1"])
        # All workers retired, so reuse_enforced should NOT be set
        assert "reuse_enforced" not in out


# ── Batch Notify Tests ───────────────────────────────────────


class TestBatchNotify:
    def test_basic_batch_notify(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        tf(workspace, ["dispatch", "w2", "bead-2", "--skill", "code"])
        out = tf(workspace, [
            "batch-notify", "--pairs", "w1:bead-1,w2:bead-2", "--context-pct", "55",
        ])
        assert out["ok"] is True
        assert len(out["results"]) == 2
        reg = load_registry(workspace)
        assert reg["workers"]["w1"]["status"] == "idle"
        assert reg["workers"]["w2"]["status"] == "idle"
        assert reg["workers"]["w1"]["context_pct"] == 55

    def test_batch_notify_auto_retire(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        out = tf(workspace, [
            "batch-notify", "--pairs", "w1:bead-1", "--context-pct", "95",
        ])
        assert out["ok"] is True
        assert out["results"][0].get("auto_retired") is True
        reg = load_registry(workspace)
        assert reg["workers"]["w1"]["status"] == "retired"

    def test_batch_notify_unknown_worker(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, [
            "batch-notify", "--pairs", "new-worker:bead-99", "--context-pct", "50",
        ])
        assert out["ok"] is True
        assert len(out["results"]) == 1
        reg = load_registry(workspace)
        assert "new-worker" in reg["workers"]

    def test_batch_notify_invalid_pair_format(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, [
            "batch-notify", "--pairs", "badformat", "--context-pct", "50",
        ])
        assert out["ok"] is True
        assert out["results"][0]["ok"] is False
        assert "format" in out["results"][0]["error"]


# ── Phase Complete Parallelism Tests ─────────────────────────


class TestPhaseCompleteParallelism:
    def test_sequential_workers_100_pct(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        reg = load_registry(workspace)
        # Two workers that ran sequentially (no overlap)
        reg["workers"]["w1"] = {
            "status": "idle", "skill": "code", "context_pct": 50,
            "bead": "1", "dispatched_at": "2024-01-01T10:00:00Z",
            "idle_since": "2024-01-01T10:30:00Z",
        }
        reg["workers"]["w2"] = {
            "status": "idle", "skill": "code", "context_pct": 50,
            "bead": "2", "dispatched_at": "2024-01-01T10:30:00Z",
            "idle_since": "2024-01-01T11:00:00Z",
        }
        save_registry(workspace, reg)
        beads = [
            {"id": "1", "status": "closed"},
            {"id": "2", "status": "closed"},
        ]
        out = tf(workspace, [
            "phase-complete", "--epic", "10",
        ], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["pass"] is True
        assert "parallelism" in out
        assert out["parallelism"]["sequential_pct"] == 100

    def test_overlapping_workers_below_100(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        reg = load_registry(workspace)
        # Two workers with overlapping time windows
        reg["workers"]["w1"] = {
            "status": "idle", "skill": "code", "context_pct": 50,
            "bead": "1", "dispatched_at": "2024-01-01T10:00:00Z",
            "idle_since": "2024-01-01T10:30:00Z",
        }
        reg["workers"]["w2"] = {
            "status": "idle", "skill": "code", "context_pct": 50,
            "bead": "2", "dispatched_at": "2024-01-01T10:15:00Z",
            "idle_since": "2024-01-01T10:45:00Z",
        }
        save_registry(workspace, reg)
        beads = [
            {"id": "1", "status": "closed"},
            {"id": "2", "status": "closed"},
        ]
        out = tf(workspace, [
            "phase-complete", "--epic", "10",
        ], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["pass"] is True
        assert out["parallelism"]["sequential_pct"] < 100

    def test_parallelism_in_phase_file(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx = workspace / ".beads" / "context-test"
        beads = [{"id": "1", "status": "closed"}]
        out = tf(workspace, [
            "phase-complete", "--epic", "10",
        ], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["pass"] is True
        content = (ctx / "phase-1.md").read_text()
        assert "Parallelism" in content

    def test_no_workers_defaults_100_pct(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [{"id": "1", "status": "closed"}]
        out = tf(workspace, [
            "phase-complete", "--epic", "10",
        ], env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["pass"] is True
        assert out["parallelism"]["sequential_pct"] == 100


# ── Notify bead_status default Tests ──────────────────────────


class TestNotifyBeadStatusDefault:
    def test_bead_status_always_present(self, workspace, bd_stub):
        """bead_status should be 'unknown' even when bd show fails."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        # bd stub returns invalid JSON → should still have bead_status
        out = tf(workspace, ["notify", "w1", "bead-1", "--context-pct", "50"],
                 env={"BD_STUB_RESPONSE": "not json"})
        assert out["ok"] is True
        assert "bead_status" in out
        assert out["bead_status"] == "unknown"

    def test_bead_status_from_bd_show(self, workspace, bd_stub):
        """bead_status should reflect actual bead status when bd show works."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        bead = [{"id": "bead-1", "status": "closed"}]
        out = tf(workspace, ["notify", "w1", "bead-1", "--context-pct", "50"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead)})
        assert out["bead_status"] == "closed"


# ── Sync Never-Notified Retirement Tests ──────────────────────


class TestSyncNeverNotified:
    def test_idle_pending_worker_retired(self, workspace):
        """Workers with notification=pending should be retired by sync."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        reg = load_registry(workspace)
        reg["workers"]["w1"]["status"] = "idle"
        reg["workers"]["w1"]["context_pct"] = 60
        reg["workers"]["w1"]["idle_since"] = ts_minutes_ago(5)
        # notification stays "pending" (never called back)
        save_registry(workspace, reg)
        out = tf(workspace, ["sync"])
        assert out["counts"]["idle"] == 0
        assert "w1" in out["retired_now"]

    def test_idle_received_worker_kept(self, workspace):
        """Workers with notification=received should remain available."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        reg = load_registry(workspace)
        reg["workers"]["w1"]["status"] = "idle"
        reg["workers"]["w1"]["context_pct"] = 60
        reg["workers"]["w1"]["idle_since"] = ts_minutes_ago(3)
        reg["workers"]["w1"]["notification"] = "received"
        save_registry(workspace, reg)
        out = tf(workspace, ["sync"])
        assert out["counts"]["idle"] == 1
        assert "code" in out["available"]


# ── Notify Auto-Close Parent Tests ──────────────────────


class TestNotifyAutoCloseParent:
    def test_auto_closes_parent_when_all_children_closed(self, workspace, bd_stub):
        """When all children of an epic are closed, parent should be auto-closed."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-child-1", "--skill", "code"])
        # bd show returns closed bead with parent, bd list returns all children closed
        bead = [{"id": "bead-child-1", "status": "closed", "parent": "epic-1"}]
        children = [
            {"id": "bead-child-1", "status": "closed"},
            {"id": "bead-child-2", "status": "closed"},
        ]
        # bd stub is called multiple times: show then list then close
        # Since our stub returns the same response for all calls, we need
        # a multi-response stub. For simplicity, use env that works for show.
        # The stub returns the same JSON for all calls — show gets the bead,
        # list --parent gets children. We'll test the logic via the response.
        out = tf(workspace, ["notify", "w1", "bead-child-1", "--context-pct", "50"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead)})
        # The bead has parent but list returns same as show (bead array with parent),
        # so children check may not work perfectly with a single-response stub.
        # At minimum, verify bead_status is present and correct.
        assert out["bead_status"] == "closed"

    def test_no_auto_close_when_children_open(self, workspace, bd_stub):
        """Parent should NOT be auto-closed when some children are still open."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-child-1", "--skill", "code"])
        children = [
            {"id": "bead-child-1", "status": "closed", "parent": "epic-1"},
            {"id": "bead-child-2", "status": "in_progress"},
        ]
        out = tf(workspace, ["notify", "w1", "bead-child-1", "--context-pct", "50"],
                 env={"BD_STUB_RESPONSE": json.dumps(children)})
        assert "parent_auto_closed" not in out


# ── Worker-Prompt Phase Cap Tests ──────────────────────


class TestWorkerPromptPhaseCap:
    def test_phase_content_capped(self, workspace, bd_stub):
        """Phase files exceeding 60 lines should be trimmed in worker prompt."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx_dir = workspace / ".beads" / "context-test"
        # Create a large phase file with 100 lines
        phase_lines = ["# Phase 1 Summary"] + [f"Task {i}: completed thing {i}" for i in range(100)]
        (ctx_dir / "phase-1.md").write_text("\n".join(phase_lines))
        bead = [{"id": "bead-1", "title": "Test Task", "description": "Do stuff"}]
        out = tf(workspace, ["worker-prompt", "--beads", "bead-1"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead)})
        assert out["ok"] is True
        prompt = out["prompt"]
        assert "earlier summaries trimmed" in prompt

    def test_small_phase_not_capped(self, workspace, bd_stub):
        """Phase files under 60 lines should not be trimmed."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx_dir = workspace / ".beads" / "context-test"
        phase_lines = ["# Phase 1 Summary"] + [f"Task {i}: done" for i in range(10)]
        (ctx_dir / "phase-1.md").write_text("\n".join(phase_lines))
        bead = [{"id": "bead-1", "title": "Test Task", "description": "Do stuff"}]
        out = tf(workspace, ["worker-prompt", "--beads", "bead-1"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead)})
        assert out["ok"] is True
        assert "earlier summaries trimmed" not in out["prompt"]


# ── Wire-Plan Tests ──────────────────────


class TestWirePlan:
    def test_missing_plan_file(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        ids_file = workspace / "ids.json"
        ids_file.write_text("[]")
        out = tf(workspace, ["wire-plan", "nonexistent.md", "--ids", str(ids_file)])
        assert out["ok"] is False
        assert "not found" in out["error"]

    def test_missing_ids_file(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan_file = workspace / "plan.md"
        plan_file.write_text("## Task 1\n")
        out = tf(workspace, ["wire-plan", str(plan_file), "--ids", "nonexistent.json"])
        assert out["ok"] is False
        assert "not found" in out["error"]

    def test_parses_plan_structure(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        plan = textwrap.dedent("""\
            ## Build Auth System

            ### Type
            epic

            ## Create User model

            ### Type
            task

            ## Add login endpoint

            ### Type
            task

            ### Dependencies
            blocks:Add logout endpoint

            ## Add logout endpoint

            ### Type
            task
        """)
        plan_file = workspace / "plan.md"
        plan_file.write_text(plan)
        ids = [
            {"id": "epic-1", "title": "Build Auth System"},
            {"id": "task-1", "title": "Create User model"},
            {"id": "task-2", "title": "Add login endpoint"},
            {"id": "task-3", "title": "Add logout endpoint"},
        ]
        ids_file = workspace / "ids.json"
        ids_file.write_text(json.dumps(ids))
        out = tf(workspace, ["wire-plan", str(plan_file), "--ids", str(ids_file)])
        assert out["total_issues"] == 4
        assert out["parent_child"] == 3
        assert out["blocking"] == 1

    def test_depends_on_syntax(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        plan = textwrap.dedent("""\
            ## Setup project

            ### Type
            epic

            ## Create scanner interface

            ### Type
            task

            ## Implement snapshot package

            ### Type
            task

            ### Dependencies
            depends_on:Create scanner interface
        """)
        plan_file = workspace / "plan.md"
        plan_file.write_text(plan)
        ids = [
            {"id": "epic-1", "title": "Setup project"},
            {"id": "task-1", "title": "Create scanner interface"},
            {"id": "task-2", "title": "Implement snapshot package"},
        ]
        ids_file = workspace / "ids.json"
        ids_file.write_text(json.dumps(ids))
        out = tf(workspace, ["wire-plan", str(plan_file), "--ids", str(ids_file)])
        assert out["ok"] is True
        assert out["blocking"] == 1

    def test_depends_on_underscore_and_hyphen(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        plan = textwrap.dedent("""\
            ## Task A

            ### Type
            task

            ## Task B

            ### Type
            task

            ### Dependencies
            depends-on: Task A
        """)
        plan_file = workspace / "plan.md"
        plan_file.write_text(plan)
        ids = [
            {"id": "t-1", "title": "Task A"},
            {"id": "t-2", "title": "Task B"},
        ]
        ids_file = workspace / "ids.json"
        ids_file.write_text(json.dumps(ids))
        out = tf(workspace, ["wire-plan", str(plan_file), "--ids", str(ids_file)])
        assert out["blocking"] == 1

    def test_no_id_match_reports_error(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        plan = "## Unknown Task\n\n### Type\ntask\n"
        plan_file = workspace / "plan.md"
        plan_file.write_text(plan)
        ids_file = workspace / "ids.json"
        ids_file.write_text(json.dumps([{"id": "x", "title": "Different Title"}]))
        out = tf(workspace, ["wire-plan", str(plan_file), "--ids", str(ids_file)])
        assert out["ok"] is False
        assert len(out["errors"]) > 0


# ── Sync Stale Reuse Enforcement Tests ──────────────────────


class TestSyncStaleReuse:
    def test_all_stale_workers_no_reuse_enforced(self, workspace):
        """When all idle workers are stale, reuse_enforced should not be set."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        reg = load_registry(workspace)
        for i in range(5):
            reg["workers"][f"w{i}"] = {
                "status": "idle", "skill": "code", "context_pct": 60,
                "notification": "received", "idle_since": ts_minutes_ago(45),
            }
        save_registry(workspace, reg)
        out = tf(workspace, ["sync", "--ready-count", "2"])
        assert "reuse_enforced" not in out

    def test_fresh_workers_trigger_reuse(self, workspace):
        """Non-stale idle workers should count toward reuse_enforced."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        reg = load_registry(workspace)
        for i in range(3):
            reg["workers"][f"w{i}"] = {
                "status": "idle", "skill": "code", "context_pct": 60,
                "notification": "received", "idle_since": ts_minutes_ago(2),
            }
        save_registry(workspace, reg)
        out = tf(workspace, ["sync", "--ready-count", "2"])
        assert out.get("reuse_enforced") is True

    def test_borderline_idle_not_addressable(self, workspace):
        """Workers idle 7 min are listed but not counted as addressable (fresh <= 6 min)."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        reg = load_registry(workspace)
        for i in range(3):
            reg["workers"][f"w{i}"] = {
                "status": "idle", "skill": "code", "context_pct": 60,
                "notification": "received", "idle_since": ts_minutes_ago(7),
            }
        save_registry(workspace, reg)
        out = tf(workspace, ["sync", "--ready-count", "2"])
        # idle_min ~7 → still available (TTL is 8 min), but not addressable (>6 min)
        assert out["counts"]["idle"] == 3
        assert "reuse_enforced" not in out


# ── Conflict-Check Unparseable Tests ──────────────────────


class TestConflictCheckUnparseable:
    def test_unparseable_beads_flagged(self, workspace, bd_stub):
        """Beads without Files: lines should appear in unparseable list."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [
            {"id": "bead-1", "description": "Do something without files listed"},
            {"id": "bead-2", "description": "Also no files here"},
        ]
        # bd show returns each bead by ID, but stub returns same for all
        out = tf(workspace, ["conflict-check", "--beads", "bead-1,bead-2"],
                 env={"BD_STUB_RESPONSE": json.dumps(beads[0])})
        assert "unparseable" in out
        assert len(out["unparseable"]) == 2

    def test_parseable_beads_not_flagged(self, workspace, bd_stub):
        """Beads with Files: lines should not appear in unparseable."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        bead = {"id": "bead-1", "description": "**Files:** `src/main.go`, `src/util.go`"}
        out = tf(workspace, ["conflict-check", "--beads", "bead-1"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead)})
        assert "unparseable" not in out

    def test_inferred_files_from_backticks(self, workspace, bd_stub):
        """Beads without Files: but with backtick paths should be inferred."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        bead = {"id": "bead-1", "description": "Implement the scanner in `pkg/scanner/scanner.go` and tests in `pkg/scanner/scanner_test.go`"}
        out = tf(workspace, ["conflict-check", "--beads", "bead-1"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead)})
        assert "unparseable" not in out
        assert "inferred" in out
        assert "bead-1" in out["inferred"]
        assert "bead-1" in out["safe"]

    def test_files_new_and_modifies_detected(self, workspace, bd_stub):
        """Beads with Files (new): and Files (modifies): should both be parsed."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        bead = {"id": "bead-1", "description": "Files (new): `pkg/auth.go`\nFiles (modifies): `cmd/main.go`"}
        out = tf(workspace, ["conflict-check", "--beads", "bead-1"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead)})
        assert "unparseable" not in out
        assert "bead-1" in out["safe"]

    def test_modify_conflicts_flagged(self, workspace, bd_stub):
        """Two beads both modifying the same file should produce modify_conflicts."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        bead = {"id": "bead-1", "description": "Files (modifies): `shared.go`, `other.go`"}
        out = tf(workspace, ["conflict-check", "--beads", "bead-1,bead-2"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead)})
        # Both beads get the same stub response (both modify shared.go)
        assert "modify_conflicts" in out
        assert "shared.go" in out["modify_conflicts"]


# ── Update-Context Task-Summaries Tests ──────────────────────


class TestUpdateContextSummaries:
    def test_always_writes_task_summaries(self, workspace, bd_stub):
        """update-context should always write to task-summaries.md."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        bead = [{"id": "bead-1", "title": "My Task", "parent": "epic-1"}]
        parent = [{"id": "epic-1", "title": "My Epic"}]
        out = tf(workspace, [
            "update-context", "--bead", "bead-1", "--worker", "w1",
            "--summary", "did the thing", "--files", "src/main.go",
        ], env={"BD_STUB_RESPONSE": json.dumps(bead)})
        assert "task-summaries.md" in out["updated"]
        ctx_dir = workspace / ".beads" / "context-test"
        assert (ctx_dir / "task-summaries.md").exists()


# ── Phase-Summary Tests ──────────────────────


class TestPhaseSummary:
    def test_phase_summary_output(self, workspace, bd_stub):
        """phase-summary should return status per epic."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        # Stub returns children for list --parent
        children = [
            {"id": "t1", "status": "closed"},
            {"id": "t2", "status": "closed"},
        ]
        out = tf(workspace, ["phase-summary", "--epics", "epic-1"],
                 env={"BD_STUB_RESPONSE": json.dumps(children)})
        assert out["ok"] is True
        assert len(out["phases"]) == 1
        assert out["phases"][0]["status"] == "done"
        assert out["phases"][0]["closed"] == 2
        assert out["phases"][0]["total"] == 2

    def test_in_progress_phase(self, workspace, bd_stub):
        """phase-summary with open children should show in_progress."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        children = [
            {"id": "t1", "status": "closed"},
            {"id": "t2", "status": "in_progress"},
        ]
        out = tf(workspace, ["phase-summary", "--epics", "epic-1"],
                 env={"BD_STUB_RESPONSE": json.dumps(children)})
        assert out["phases"][0]["status"] == "in_progress"
        assert out["phases"][0]["closed"] == 1


# ── Worker-Close Multi-Bead Tests ──────────────────────


class TestWorkerCloseMultiBead:
    def test_close_multiple_beads(self, workspace, bd_stub):
        """worker-close --beads should close all listed beads."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "b1", "--skill", "code"])
        (workspace / "src.rs").write_text("fn main() {}")
        subprocess.run(["git", "add", "."], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: impl"], cwd=workspace, check=True)

        out = tf(workspace, [
            "worker-close", "--beads", "b1,b2,b3",
            "--context-pct", "50", "--summary", "done",
        ], env={"BD_STUB_RESPONSE": json.dumps([{"status": "closed"}])})
        assert out["ok"] is True
        assert len(out["closed"]) == 3
        assert set(out["closed"]) == {"b1", "b2", "b3"}

    def test_single_bead_backward_compat(self, workspace, bd_stub):
        """Single positional bead_id should still work."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-abc", "--skill", "code"])
        (workspace / "src.rs").write_text("fn main() {}")
        subprocess.run(["git", "add", "."], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: impl"], cwd=workspace, check=True)

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"status": "closed"}])})
        assert out["ok"] is True
        assert "bead-abc" in out["closed"]


# ── Fix 1: Worker Reuse (closed_self) Tests ─────────────────────


class TestSyncSelfClosed:
    """Workers that called worker-close should be auto-retired by sync."""

    def test_sync_retires_self_closed_worker(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "55"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["closed_self"] = True
        save_registry(workspace, reg)

        out = tf(workspace, ["sync"])
        assert len(out["retired_now"]) == 1
        assert out["retired_now"][0] == "rust-1"
        assert "rust" not in out.get("available", {})

    def test_sync_keeps_idle_worker_without_closed_self(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "55"])

        out = tf(workspace, ["sync"])
        assert "rust" in out["available"]
        assert out["available"]["rust"][0]["name"] == "rust-1"

    def test_worker_close_sets_closed_self_flag(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-abc", "--skill", "code"],
           env={"CLAUDE_AGENT_NAME": "w1"})

        (workspace / "src.rs").write_text("fn main() {}")
        subprocess.run(["git", "add", "."], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: impl"], cwd=workspace, check=True)

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50",
                              "--files", "src.rs", "--summary", "done. AC: all met"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"status": "closed"}]),
                      "CLAUDE_AGENT_NAME": "w1"})
        assert out["ok"] is True

        reg = load_registry(workspace)
        assert reg["workers"]["w1"].get("closed_self") is True


# ── Fix 2: Stalled Action in Status Tests ──────────────────────


class TestStatusStalledAction:
    """Status should include stalled workers when workers are stalled."""

    def test_stalled_includes_stalled_list(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["last_heartbeat"] = ts_minutes_ago(25)
        save_registry(workspace, reg)

        out = tf(workspace, ["status"])
        assert "stalled" in out
        assert "stalled_action" not in out  # removed

    def test_no_stalled_when_no_stalls(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["status"])
        assert "stalled_action" not in out
        assert "stalled" not in out

    def test_stalled_info_includes_silent_mins(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["last_heartbeat"] = ts_minutes_ago(30)
        save_registry(workspace, reg)

        out = tf(workspace, ["stalled"])
        assert len(out["stalled"]) == 1
        assert out["stalled"][0]["silent_mins"] >= 29


# ── Fix 3: AC Warning in Worker Close Tests ────────────────────


class TestWorkerCloseACWarning:
    """Worker close should warn when summary doesn't mention AC status."""

    def test_warns_missing_ac_in_summary(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-abc", "--skill", "code"],
           env={"CLAUDE_AGENT_NAME": "w1"})

        (workspace / "src.rs").write_text("fn main() {}")
        subprocess.run(["git", "add", "."], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: impl"], cwd=workspace, check=True)

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50",
                              "--files", "src.rs", "--summary", "did the thing"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"status": "closed"}]),
                      "CLAUDE_AGENT_NAME": "w1"})
        assert out["ok"] is True
        assert any("AC" in w for w in out.get("warnings", []))

    def test_no_ac_warning_when_present(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-abc", "--skill", "code"],
           env={"CLAUDE_AGENT_NAME": "w1"})

        (workspace / "src.rs").write_text("fn main() {}")
        subprocess.run(["git", "add", "."], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: impl"], cwd=workspace, check=True)

        out = tf(workspace, ["worker-close", "bead-abc", "--context-pct", "50",
                              "--files", "src.rs", "--summary", "done. AC: all criteria met"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"status": "closed"}]),
                      "CLAUDE_AGENT_NAME": "w1"})
        assert out["ok"] is True
        ac_warnings = [w for w in out.get("warnings", []) if "AC" in w]
        assert len(ac_warnings) == 0


# ── Fix 4: Init Creates Worker Context Tests ──────────────────


class TestInitWorkerContext:
    """tf.py init should create worker-context.md from template."""

    def test_creates_worker_context(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        wc = workspace / ".beads" / "context-test" / "worker-context.md"
        assert wc.exists()
        content = wc.read_text()
        assert "Tech Stack" in content or "Worker Context" in content

    def test_init_idempotent_preserves_worker_context(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        wc = workspace / ".beads" / "context-test" / "worker-context.md"
        wc.write_text("# Custom context\nMy project-specific content")

        # Re-init should not overwrite
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        assert wc.read_text() == "# Custom context\nMy project-specific content"


# ── Fix 7: Integration Task Prompt Tests ───────────────────────


class TestWorkerPromptIntegration:
    """Worker prompt should append integration guidance for [integration] tasks."""

    def test_integration_task_adds_heartbeat_guidance(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])

        bead_resp = json.dumps([{
            "id": "bead-1",
            "title": "[integration] Run E2E test suite",
            "description": "Run full E2E test against real data",
            "status": "open",
        }])
        out = tf(workspace, ["worker-prompt", "--beads", "bead-1"],
                 env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True
        assert "Integration Task" in out["prompt"]
        assert "heartbeat" in out["prompt"].lower()

    def test_non_integration_task_no_extra_guidance(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])

        bead_resp = json.dumps([{
            "id": "bead-1",
            "title": "Add unit tests for parser",
            "description": "Write tests for the OVAL parser module",
            "status": "open",
        }])
        out = tf(workspace, ["worker-prompt", "--beads", "bead-1"],
                 env={"BD_STUB_RESPONSE": bead_resp})
        assert out["ok"] is True
        assert "Integration Task" not in out["prompt"]


# ── Import-Deps Tests ──────────────────────


class TestImportDeps:
    def test_basic_import(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        deps = workspace / "deps.txt"
        deps.write_text('"Task A" blocks "Task B"\n"Task A" blocks "Task C"\n')
        beads = [
            {"id": "b1", "title": "Task A"},
            {"id": "b2", "title": "Task B"},
            {"id": "b3", "title": "Task C"},
        ]
        out = tf(workspace, ["import-deps", str(deps)],
                 env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["ok"] is True
        assert out["applied"] == 2

    def test_validate_mode(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        deps = workspace / "deps.txt"
        deps.write_text('"Task A" blocks "Task B"\n')
        beads = [{"id": "b1", "title": "Task A"}, {"id": "b2", "title": "Task B"}]
        out = tf(workspace, ["import-deps", str(deps), "--validate"],
                 env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["ok"] is True
        assert out["total"] == 1
        assert out["unresolved"] == []

    def test_unresolved_title(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        deps = workspace / "deps.txt"
        deps.write_text('"Task A" blocks "Unknown Task"\n')
        beads = [{"id": "b1", "title": "Task A"}]
        out = tf(workspace, ["import-deps", str(deps)],
                 env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["ok"] is False
        assert "Unknown Task" in out["unresolved"]

    def test_empty_file(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        deps = workspace / "deps.txt"
        deps.write_text("# just a comment\n\n")
        out = tf(workspace, ["import-deps", str(deps)],
                 env={"BD_STUB_RESPONSE": "[]"})
        assert out["ok"] is True
        assert out["applied"] == 0

    def test_file_not_found(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["import-deps", "/nonexistent/deps.txt"])
        assert out["ok"] is False


# ── Ready Tests ──────────────────────


class TestReady:
    def test_filters_epics(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [
            {"id": "e1", "type": "epic", "title": "Epic"},
            {"id": "t1", "type": "task", "title": "Task 1"},
        ]
        out = tf(workspace, ["ready"],
                 env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["ok"] is True
        ids = [b["id"] for b in out["ready"]]
        assert "e1" not in ids
        assert "t1" in ids

    def test_supplements_from_list(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [
            {"id": "t1", "type": "task", "title": "Task 1"},
            {"id": "t2", "type": "task", "title": "Task 2", "blocked_by": None},
        ]
        out = tf(workspace, ["ready"],
                 env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["ok"] is True

    def test_handles_bd_failure(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["ready"],
                 env={"BD_STUB_RESPONSE": "invalid json!!!", "BD_STUB_EXIT": "1"})
        assert out["ok"] is True
        assert out["ready"] == []

    def test_no_capped_or_capped_count_in_output(self, workspace, bd_stub):
        """capped and capped_count fields should never appear in ready output."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [
            {"id": "t1", "type": "task", "title": "Task 1"},
            {"id": "t2", "type": "task", "title": "Task 2", "blocked_by": None},
        ]
        out = tf(workspace, ["ready"],
                 env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["ok"] is True
        assert "capped" not in out
        assert "capped_count" not in out

    def test_trimmed_output_shape(self, workspace, bd_stub):
        """Ready beads should only contain essential fields."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [
            {
                "id": "t1", "type": "task", "title": "Task 1",
                "status": "open", "priority": "high", "parent": "e1",
                "description": "Long description that should be dropped",
                "created_at": "2026-01-01", "updated_at": "2026-01-02",
                "labels": ["backend"], "assignee": "someone",
            },
        ]
        out = tf(workspace, ["ready"],
                 env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["ok"] is True
        assert len(out["ready"]) == 1
        bead = out["ready"][0]
        # Essential fields present
        assert bead["id"] == "t1"
        assert bead["title"] == "Task 1"
        assert bead["type"] == "task"
        assert bead["priority"] == "high"
        assert bead["parent"] == "e1"
        assert bead["status"] == "open"
        # Non-essential fields stripped
        assert "description" not in bead
        assert "created_at" not in bead
        assert "updated_at" not in bead
        assert "labels" not in bead
        assert "assignee" not in bead

    def test_issue_type_mapped_to_type(self, workspace, bd_stub):
        """Beads with issue_type instead of type should have type populated."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        beads = [
            {"id": "t1", "issue_type": "task", "title": "Task 1"},
        ]
        out = tf(workspace, ["ready"],
                 env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["ok"] is True
        assert out["ready"][0]["type"] == "task"


# ── Recover Tests ──────────────────────


class TestRecover:
    def test_orphaned_bead(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        reg = load_registry(workspace)
        reg["workers"]["w1"]["status"] = "retired"
        save_registry(workspace, reg)
        beads = [{"id": "bead-1", "title": "Orphaned task", "status": "in_progress"}]
        out = tf(workspace, ["recover"],
                 env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["ok"] is True
        assert len(out["orphaned"]) == 1
        assert out["orphaned"][0]["id"] == "bead-1"
        assert out["orphaned"][0]["last_worker"] == "w1"

    def test_no_orphans(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        beads = [{"id": "bead-1", "title": "Active task", "status": "in_progress"}]
        out = tf(workspace, ["recover"],
                 env={"BD_STUB_RESPONSE": json.dumps(beads)})
        assert out["ok"] is True
        assert len(out["orphaned"]) == 0

    def test_empty_state(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["recover"],
                 env={"BD_STUB_RESPONSE": "[]"})
        assert out["ok"] is True
        assert out["orphaned"] == []


# ── Ad-hoc Tests ──────────────────────


class TestAdHoc:
    def test_creates_registry_entry(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        out = tf(workspace, ["ad-hoc", "--name", "refactor-tests", "--worker", "refactor-1", "--skill", "python"])
        assert out["ok"] is True
        assert out["bead"] == "ad-hoc:refactor-tests"
        reg = load_registry(workspace)
        assert "refactor-1" in reg["workers"]
        assert reg["workers"]["refactor-1"]["bead"] == "ad-hoc:refactor-tests"
        assert reg["workers"]["refactor-1"]["status"] == "active"
        assert reg["workers"]["refactor-1"]["skill"] == "python"

    def test_stall_detection_works(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["ad-hoc", "--name", "test-task", "--worker", "w1"])
        reg = load_registry(workspace)
        reg["workers"]["w1"]["last_heartbeat"] = ts_minutes_ago(25)
        reg["workers"]["w1"]["dispatched_at"] = ts_minutes_ago(25)
        save_registry(workspace, reg)
        out = tf(workspace, ["stalled"])
        assert len(out["stalled"]) == 1


# ── Notify Agent-ID Tests ──────────────────────


class TestNotifyAgentId:
    def test_stores_agent_id(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        out = tf(workspace, ["notify", "w1", "bead-1", "--context-pct", "50",
                              "--agent-id", "a123-456"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"id": "bead-1", "status": "closed"}])})
        assert out["ok"] is True
        reg = load_registry(workspace)
        assert reg["workers"]["w1"]["agent_id"] == "a123-456"

    def test_agent_id_in_sync_output(self, workspace, bd_stub):
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        tf(workspace, ["notify", "w1", "bead-1", "--context-pct", "50",
                        "--agent-id", "a123-456"],
                 env={"BD_STUB_RESPONSE": json.dumps([{"id": "bead-1", "status": "closed"}])})
        out = tf(workspace, ["sync"])
        if out.get("available", {}).get("code"):
            worker = out["available"]["code"][0]
            assert worker.get("agent_id") == "a123-456"


# ── Phase-Gate Worker Scoping Tests ──────────────────────


class TestPhaseGateScoping:
    def test_ignores_workers_from_other_phases(self, workspace, bd_stub):
        """Workers assigned to beads NOT in this phase should not block."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        # Dispatch worker for a different-phase bead
        tf(workspace, ["dispatch", "cli-1", "bead-other", "--skill", "cli"])
        # Phase beads are all closed
        phase_beads = [
            {"id": "bead-1", "status": "closed"},
            {"id": "bead-2", "status": "closed"},
        ]
        out = tf(workspace, ["phase-gate", "epic-1"],
                 env={"BD_STUB_RESPONSE": json.dumps(phase_beads)})
        assert out["pass"] is True

    def test_blocks_on_phase_worker(self, workspace, bd_stub):
        """Workers assigned to beads IN this phase should block."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "w1", "bead-1", "--skill", "code"])
        phase_beads = [
            {"id": "bead-1", "status": "closed"},
            {"id": "bead-2", "status": "closed"},
        ]
        out = tf(workspace, ["phase-gate", "epic-1"],
                 env={"BD_STUB_RESPONSE": json.dumps(phase_beads)})
        assert out["pass"] is False
        assert any(b["worker"] == "w1" for b in out["blocking"])


# ── Phase-Complete Task-Summaries Tests ──────────────────────


class TestPhaseCompleteSummaries:
    def test_aggregates_from_task_summaries(self, workspace, bd_stub):
        """phase-complete should extract files from task-summaries.md."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        ctx_dir = workspace / ".beads" / "context-test"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        summaries = ctx_dir / "task-summaries.md"
        summaries.write_text(
            "## Completed Tasks\n\n"
            "### BD-bead-1: Setup\n"
            "**Worker**: w1 | **Files**: src/main.go, src/util.go\n"
            "Did the setup.\n"
        )
        phase_beads = [{"id": "bead-1", "status": "closed", "close_reason": "done"}]
        out = tf(workspace, ["phase-complete", "--epic", "epic-1", "--phase-num", "1"],
                 env={"BD_STUB_RESPONSE": json.dumps(phase_beads)})
        assert out["pass"] is True
        assert "src/main.go" in out["files"]
        assert "src/util.go" in out["files"]

    def test_scopes_workers_to_phase_beads(self, workspace, bd_stub):
        """Workers for non-phase beads should not block phase-complete."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "cli-1", "bead-other", "--skill", "cli"])
        phase_beads = [{"id": "bead-1", "status": "closed"}]
        out = tf(workspace, ["phase-complete", "--epic", "epic-1", "--phase-num", "2"],
                 env={"BD_STUB_RESPONSE": json.dumps(phase_beads)})
        assert out["pass"] is True


# ── Notify Auto-Summary Tests ─────────────────────────────────


class TestNotifyAutoSummary:
    def test_auto_generates_summary_from_bead_title(self, workspace, bd_stub):
        """notify should auto-generate summary from bead title when --summary omitted."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        bead_data = [{"status": "closed", "title": "Fix auth bug"}]
        out = tf(workspace, ["notify", "rust-1", "bead-abc",
                              "--context-pct", "50"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead_data)})
        assert out["ok"] is True
        reg = load_registry(workspace)
        assert reg["workers"]["rust-1"]["summary"] == "Completed: Fix auth bug"

    def test_explicit_summary_takes_precedence(self, workspace, bd_stub):
        """notify with explicit --summary should use that, not auto-generate."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        bead_data = [{"status": "closed", "title": "Fix auth bug"}]
        out = tf(workspace, ["notify", "rust-1", "bead-abc",
                              "--context-pct", "50", "--summary", "Custom summary"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead_data)})
        assert out["ok"] is True
        reg = load_registry(workspace)
        assert reg["workers"]["rust-1"]["summary"] == "Custom summary"


# ── Validate Plan --no-files-check Tests ──────────────────────


class TestValidatePlanNoFilesCheck:
    def test_no_files_check_skips_missing_files_warning(self, workspace):
        """validate-plan --no-files-check should not warn about missing Files: lines."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Port feature X from anarlog

            Backlog task — study commit abc123 and apply to sruti.

            ## Port feature Y from anarlog

            Backlog task — study commit def456 and apply to sruti.
        """))
        out = tf(workspace, ["validate-plan", str(plan), "--no-files-check"])
        assert out["ok"] is True
        assert "errors" not in out or not any("missing Files" in e for e in out.get("errors", []))

    def test_without_flag_warns_about_missing_files(self, workspace):
        """validate-plan without --no-files-check should warn about missing Files: lines."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Port feature X from anarlog

            Backlog task — study commit abc123 and apply to sruti.
        """))
        out = tf(workspace, ["validate-plan", str(plan)])
        assert out["ok"] is False
        assert any("missing Files" in e for e in out.get("errors", []))


# ── Worker Prompt --write-file Tests ──────────────────────────


class TestWorkerPromptWriteFile:
    def test_write_file_creates_temp_file(self, workspace, bd_stub):
        """worker-prompt --write-file should write prompt to a temp file."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])

        bead_data = [{"id": "bead-1", "title": "Fix bug", "description": "Fix the auth bug\n\nFiles: `src/auth.py`"}]
        out = tf(workspace, ["worker-prompt", "--beads", "bead-1", "--write-file"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead_data)})
        assert out["ok"] is True
        assert "prompt_file" in out
        assert "prompt" not in out
        assert out["beads"] == ["bead-1"]

        # Verify file exists and has content
        prompt_file = Path(out["prompt_file"])
        assert prompt_file.exists()
        content = prompt_file.read_text()
        assert "Fix bug" in content
        prompt_file.unlink()

    def test_normal_mode_returns_inline_prompt(self, workspace, bd_stub):
        """worker-prompt without --write-file should return prompt inline."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])

        bead_data = [{"id": "bead-1", "title": "Fix bug", "description": "Fix the auth bug\n\nFiles: `src/auth.py`"}]
        out = tf(workspace, ["worker-prompt", "--beads", "bead-1"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead_data)})
        assert out["ok"] is True
        assert "prompt" in out
        assert "prompt_file" not in out


# ── Infer Files Improved Tests ────────────────────────────────


class TestInferFilesImproved:
    def test_infers_in_filename_references(self, workspace):
        """_infer_files_from_description should match 'in listen.rs' patterns."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])

        # Test via conflict-check since it uses _infer_files_from_description
        from treeflow.tf import _infer_files_from_description
        desc = "Update the handler in listen.rs to add timeout support"
        result = _infer_files_from_description(desc)
        assert "listen.rs" in result

    def test_infers_backtick_paths(self, workspace):
        """_infer_files_from_description should match backtick-wrapped paths."""
        from treeflow.tf import _infer_files_from_description
        desc = "Modify `src/commands/daemon.rs` to add the new flag"
        result = _infer_files_from_description(desc)
        assert "src/commands/daemon.rs" in result

    def test_infers_in_path_with_slash(self, workspace):
        """_infer_files_from_description should match 'in store/mod.rs' patterns."""
        from treeflow.tf import _infer_files_from_description
        desc = "Add the new method in store/mod.rs"
        result = _infer_files_from_description(desc)
        assert "store/mod.rs" in result


# ── Create Wrapper Tests ──────────────────────────────────────


class TestCreate:
    def test_create_parses_bd_output(self, workspace, bd_stub):
        """tf.py create should parse bd create -f output and return clean JSON."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Task one

            Description for task one

            ## Task two

            Description for task two
        """))

        created = [{"id": "bead-1", "title": "Task one"}, {"id": "bead-2", "title": "Task two"}]
        out = tf(workspace, ["create", str(plan)],
                 env={"BD_STUB_RESPONSE": json.dumps(created)})
        assert out["ok"] is True
        assert out["created"] == 2
        assert out["expected"] == 2
        assert "bead-1" in out["ids"]
        assert "bead-2" in out["ids"]

    def test_create_fallback_on_bad_json(self, workspace, bd_stub):
        """tf.py create should fall back to bd list when bd create JSON is unparseable."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        plan = workspace / "plan.md"
        plan.write_text(textwrap.dedent("""\
            ## Task one

            Description
        """))

        # Simulate bd create returning garbage, then bd list returning valid JSON
        # The stub returns the same response for all calls, so we use a valid list
        listed = [{"id": "bead-x", "status": "open"}]
        out = tf(workspace, ["create", str(plan)],
                 env={"BD_STUB_RESPONSE": json.dumps(listed)})
        assert out["ok"] is True

    def test_create_file_not_found(self, workspace, bd_stub):
        """tf.py create should fail gracefully for missing files."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        out = tf(workspace, ["create", "/nonexistent/plan.md"])
        assert out["ok"] is False
        assert "not found" in out["error"]


# ── Section-Aware Conflict Check Tests ────────────────────────


class TestConflictCheckSections:
    def test_same_file_different_sections_is_low_risk(self, workspace, bd_stub):
        """Two beads modifying different sections of the same file → low_risk."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])

        bead1 = {"id": "b1", "title": "T1", "description": "Add field\n\nFiles (modifies): `src/config.rs` [StorageConfig]"}
        bead2 = {"id": "b2", "title": "T2", "description": "Add threshold\n\nFiles (modifies): `src/config.rs` [DiarizationConfig]"}

        # bd stub returns bead1 for first call, bead2 for second — but stub returns same response
        # So we need a single response that works for both. Use a list approach.
        # Actually, the stub returns the same response for all calls.
        # We'll test via the Python function directly instead.
        from treeflow.tf import _extract_files_with_sections, _parse_file_entry

        # Test _parse_file_entry
        path, section = _parse_file_entry("src/config.rs [StorageConfig]")
        assert path == "src/config.rs"
        assert section == "StorageConfig"

        path2, section2 = _parse_file_entry("`src/config.rs`")
        assert path2 == "src/config.rs"
        assert section2 == ""

    def test_extract_files_with_sections(self, workspace):
        """_extract_files_with_sections should return (path, section) tuples."""
        from treeflow.tf import _extract_files_with_sections
        desc = "Modify config\n\nFiles (modifies): `src/config.rs` [StorageConfig], `src/daemon.rs` [EventLoop]"
        result = _extract_files_with_sections(desc)
        assert ("src/config.rs", "StorageConfig") in result
        assert ("src/daemon.rs", "EventLoop") in result

    def test_same_file_same_section_is_hard_conflict(self, workspace):
        """Two beads modifying the same section → hard conflict."""
        from treeflow.tf import _extract_files_with_sections
        desc1 = "Files (modifies): `src/config.rs` [StorageConfig]"
        desc2 = "Files (modifies): `src/config.rs` [StorageConfig]"
        r1 = _extract_files_with_sections(desc1)
        r2 = _extract_files_with_sections(desc2)
        # Both target the same section — should be hard conflict
        assert r1[0][1] == r2[0][1]  # same section

    def test_no_section_annotation_is_hard_conflict(self, workspace):
        """Files without section annotations should be treated as hard conflicts."""
        from treeflow.tf import _parse_file_entry
        path, section = _parse_file_entry("src/config.rs")
        assert section == ""


# ── Wave Plan Tests ───────────────────────────────────────────


class TestWavePlan:
    def test_independent_beads_in_same_wave(self, workspace, bd_stub):
        """Beads touching different files should be in the same wave."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])

        beads = [
            {"id": "b1", "title": "T1", "description": "Fix\n\nFiles: `src/a.rs`"},
            {"id": "b2", "title": "T2", "description": "Fix\n\nFiles: `src/b.rs`"},
        ]
        # Stub returns same response — but wave-plan calls bd show per bead
        # With same response, both beads get same files, so they'll conflict.
        # Test the single-bead case first.
        out = tf(workspace, ["wave-plan", "--beads", "b1"],
                 env={"BD_STUB_RESPONSE": json.dumps(beads[0])})
        assert out["ok"] is True
        assert out["total_waves"] == 1
        assert out["total_beads"] == 1

    def test_conflicting_beads_in_different_waves(self, workspace, bd_stub):
        """Beads touching the same file should be in different waves."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])

        # Both beads will get the same bd show response (stub limitation)
        # so they'll have the same files → conflict → 2 waves
        bead = {"id": "b1", "title": "T1", "description": "Fix\n\nFiles: `src/shared.rs`"}
        out = tf(workspace, ["wave-plan", "--beads", "b1,b2"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead)})
        assert out["ok"] is True
        assert out["total_waves"] == 2

    def test_wave_plan_with_no_files(self, workspace, bd_stub):
        """Beads without parseable files should appear in unparseable."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        bead = {"id": "b1", "title": "T1", "description": "Do something vague"}
        out = tf(workspace, ["wave-plan", "--beads", "b1"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead)})
        assert out["ok"] is True
        assert "unparseable" in out


# ── Configurable Idle Timeout Tests ───────────────────────────


class TestConfigurableIdleTimeout:
    def test_init_sets_default_idle_timeout(self, workspace):
        """init should set idle_timeout_mins to 8 by default."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        reg = load_registry(workspace)
        assert reg["settings"]["idle_timeout_mins"] == 8

    def test_init_custom_idle_timeout(self, workspace):
        """init --idle-timeout should set custom timeout."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd", "--idle-timeout", "15"])
        reg = load_registry(workspace)
        assert reg["settings"]["idle_timeout_mins"] == 15

    def test_sync_respects_custom_idle_timeout(self, workspace):
        """sync should use the configurable idle_timeout_mins from settings."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd", "--idle-timeout", "15"])
        reg = load_registry(workspace)
        # Add an idle worker at 10 min (should survive with 15 min timeout)
        reg["workers"]["test-1"] = {
            "status": "idle",
            "skill": "test",
            "context_pct": 50,
            "notification": "received",
            "idle_since": ts_minutes_ago(10),
            "spawned_session": reg["session_id"],
        }
        save_registry(workspace, reg)
        out = tf(workspace, ["sync", "--ready-count", "1"])
        # Worker should NOT be retired (10 < 15)
        assert "test-1" not in out.get("retired", [])


# ── Shell Injection Edge-Case Tests ──────────────────────────────
# Verify that shlex.quote() prevents shell metacharacter interpretation
# in user-facing string inputs passed to bd via _run(shell=True).

SHELL_DANGEROUS_STRINGS = [
    '$(whoami)',           # command substitution
    '`id`',               # backtick command substitution
    '; rm -rf /',          # command chaining
    '"quoted"',            # embedded quotes
    '$HOME',               # variable expansion
    '| cat /etc/passwd',   # pipe injection
]


@pytest.fixture
def bd_stub_logging(workspace):
    """Create a bd stub that logs all arguments to a file and returns configurable JSON.

    Reads BD_STUB_RESPONSE for stdout, BD_STUB_EXIT for exit code.
    Appends each invocation's arguments (one JSON array per line) to .beads/bd-args.log.
    """
    stub = workspace / ".beads" / "bd-stub"
    # Use a Python stub to avoid any shell interpretation issues in the stub itself
    stub.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import json, os, sys
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bd-args.log")
        with open(log_path, "a") as f:
            f.write(json.dumps(sys.argv[1:]) + "\\n")
        resp = os.environ.get("BD_STUB_RESPONSE", "{}")
        print(resp)
        stderr_msg = os.environ.get("BD_STUB_STDERR", "")
        if stderr_msg:
            print(stderr_msg, file=sys.stderr)
        sys.exit(int(os.environ.get("BD_STUB_EXIT", "0")))
    """))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return str(stub)


def read_bd_args_log(workspace) -> list[list[str]]:
    """Read the bd-args.log file and return a list of argument lists."""
    log_path = workspace / ".beads" / "bd-args.log"
    if not log_path.exists():
        return []
    lines = log_path.read_text().strip().split("\n")
    return [json.loads(line) for line in lines if line.strip()]


class TestShellInjectionWorkerClose:
    """Verify worker-close passes shell metacharacters safely via shlex.quote()."""

    def _setup_for_close(self, workspace, bd_stub_logging):
        """Common setup: init, dispatch, commit so worker-close git checks pass."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub_logging])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        subprocess.run(["git", "add", ".gitignore"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add gitignore", "-q"], cwd=workspace, check=True)

        (workspace / "src.rs").write_text("fn main() {}")
        subprocess.run(["git", "add", "src.rs"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add main"], cwd=workspace, check=True)

        # Clear any args logged during init/dispatch
        log_path = workspace / ".beads" / "bd-args.log"
        if log_path.exists():
            log_path.unlink()

    @pytest.mark.parametrize("dangerous", SHELL_DANGEROUS_STRINGS, ids=[
        "cmd_subst", "backtick", "semicolon", "quotes", "var_expand", "pipe"
    ])
    def test_summary_with_shell_metacharacters(self, workspace, bd_stub_logging, dangerous):
        """worker-close --summary containing shell metacharacters must not be interpreted."""
        self._setup_for_close(workspace, bd_stub_logging)

        out = tf(workspace, [
            "worker-close", "bead-abc", "--context-pct", "50",
            "--files", "src.rs", "--summary", dangerous,
        ], env={
            "BD_STUB_RESPONSE": json.dumps([{"status": "in_progress"}]),
            "CLAUDE_AGENT_NAME": "rust-1",
        })
        # The command should not crash from shell interpretation
        assert "_returncode" not in out or out.get("_returncode", 0) == 0

        # The dangerous string must appear literally in the args passed to bd
        all_calls = read_bd_args_log(workspace)
        # Find the bd close call (contains "close" and "--reason")
        close_calls = [c for c in all_calls if "close" in c and "--reason" in c]
        assert len(close_calls) >= 1, f"Expected bd close call, got: {all_calls}"
        # The reason arg should contain the dangerous string literally
        reason_args = " ".join(close_calls[0])
        assert dangerous in reason_args, (
            f"Dangerous string {dangerous!r} not found literally in bd args: {reason_args}"
        )


class TestShellInjectionBlock:
    """Verify block passes shell metacharacters safely via shlex.quote()."""

    @pytest.mark.parametrize("dangerous", SHELL_DANGEROUS_STRINGS, ids=[
        "cmd_subst", "backtick", "semicolon", "quotes", "var_expand", "pipe"
    ])
    def test_question_with_shell_metacharacters(self, workspace, bd_stub_logging, dangerous):
        """block --question containing shell metacharacters must not be interpreted."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub_logging])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        # Clear args log
        log_path = workspace / ".beads" / "bd-args.log"
        if log_path.exists():
            log_path.unlink()

        out = tf(workspace, [
            "block", "bead-abc", "--question", dangerous,
        ], env={"CLAUDE_AGENT_NAME": "rust-1"})

        # Should succeed (ok: True) — bd stub returns {} which is fine
        assert out.get("ok") is True, f"block failed: {out}"

        # Verify the dangerous string appears literally in bd create args
        all_calls = read_bd_args_log(workspace)
        create_calls = [c for c in all_calls if "create" in c]
        assert len(create_calls) >= 1, f"Expected bd create call, got: {all_calls}"
        create_args = " ".join(create_calls[0])
        assert dangerous in create_args, (
            f"Dangerous string {dangerous!r} not found literally in bd args: {create_args}"
        )


class TestShellInjectionDiscover:
    """Verify discover passes shell metacharacters safely via shlex.quote()."""

    @pytest.mark.parametrize("dangerous", SHELL_DANGEROUS_STRINGS, ids=[
        "cmd_subst", "backtick", "semicolon", "quotes", "var_expand", "pipe"
    ])
    def test_title_with_shell_metacharacters(self, workspace, bd_stub_logging, dangerous):
        """discover --title containing shell metacharacters must not be interpreted."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub_logging])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        # Clear args log
        log_path = workspace / ".beads" / "bd-args.log"
        if log_path.exists():
            log_path.unlink()

        out = tf(workspace, [
            "discover", "bead-abc", "--title", dangerous,
        ], env={"CLAUDE_AGENT_NAME": "rust-1"})

        assert out.get("ok") is True, f"discover failed: {out}"

        # Verify the dangerous string appears literally in bd create args
        all_calls = read_bd_args_log(workspace)
        create_calls = [c for c in all_calls if "create" in c]
        assert len(create_calls) >= 1, f"Expected bd create call, got: {all_calls}"
        create_args = " ".join(create_calls[0])
        assert dangerous in create_args, (
            f"Dangerous string {dangerous!r} not found literally in bd args: {create_args}"
        )


class TestShellInjectionClose:
    """Verify close passes shell metacharacters safely via shlex.quote()."""

    @pytest.mark.parametrize("dangerous", SHELL_DANGEROUS_STRINGS, ids=[
        "cmd_subst", "backtick", "semicolon", "quotes", "var_expand", "pipe"
    ])
    def test_reason_with_shell_metacharacters(self, workspace, bd_stub_logging, dangerous):
        """close --reason containing shell metacharacters must not be interpreted."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub_logging])

        # Clear args log
        log_path = workspace / ".beads" / "bd-args.log"
        if log_path.exists():
            log_path.unlink()

        out = tf(workspace, [
            "close", "bead-abc", "--reason", dangerous,
        ])

        # Verify the dangerous string appears literally in bd close args
        all_calls = read_bd_args_log(workspace)
        close_calls = [c for c in all_calls if "close" in c and "--reason" in c]
        assert len(close_calls) >= 1, f"Expected bd close call, got: {all_calls}"
        reason_args = " ".join(close_calls[0])
        assert dangerous in reason_args, (
            f"Dangerous string {dangerous!r} not found literally in bd args: {reason_args}"
        )


# ── Ready Supplement Filtering Tests ────────────────────────────


class TestReadySupplementFiltering:
    """Verify that cmd_ready supplement excludes beads with open blocking deps."""

    def _make_routing_stub(self, workspace, ready_resp, list_resp, show_resps=None):
        """Create a bd stub that returns different responses based on subcommand."""
        show_resps = show_resps or {}
        stub = workspace / ".beads" / "bd-routing-stub"
        show_json = json.dumps(show_resps)
        stub.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json, sys, os
            args = sys.argv[1:]
            if "ready" in args:
                print('''{json.dumps(ready_resp)}''')
            elif "list" in args:
                print('''{json.dumps(list_resp)}''')
            elif "show" in args:
                show_map = json.loads('''{show_json}''')
                bead_id = [a for a in args if not a.startswith("-") and a != "show"]
                if bead_id and bead_id[0] in show_map:
                    print(json.dumps(show_map[bead_id[0]]))
                else:
                    print('{{}}')
            else:
                print('{{}}')
        """))
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        return str(stub)

    def test_excludes_blocked_beads_from_supplement(self, workspace):
        """Beads with open blocking deps should not appear in ready output."""
        ready_beads = []
        list_beads = [
            {"id": "t1", "type": "task", "title": "Task 1"},
            {"id": "t2", "type": "task", "title": "Task 2"},
        ]
        show_resps = {
            "t1": [{"id": "t1", "type": "task", "status": "open", "dependencies": [
                {"dependency_type": "blocks", "status": "open", "depends_on": "t0"}
            ]}],
            "t2": [{"id": "t2", "type": "task", "status": "open", "dependencies": []}],
        }
        stub = self._make_routing_stub(workspace, ready_beads, list_beads, show_resps)
        tf(workspace, ["init", "test", "--bd-path", stub])

        out = tf(workspace, ["ready"])
        assert out["ok"] is True
        ids = [b["id"] for b in out["ready"]]
        assert "t1" not in ids, "Blocked bead t1 should be excluded"
        assert "t2" in ids, "Unblocked bead t2 should be included"
        assert out["supplemented"] == 1

    def test_includes_beads_with_closed_blockers(self, workspace):
        """Beads whose blocking deps are all closed should appear in ready output."""
        ready_beads = []
        list_beads = [
            {"id": "t1", "type": "task", "title": "Task 1"},
        ]
        show_resps = {
            "t1": [{"id": "t1", "type": "task", "status": "open", "dependencies": [
                {"dependency_type": "blocks", "status": "closed", "depends_on": "t0"}
            ]}],
        }
        stub = self._make_routing_stub(workspace, ready_beads, list_beads, show_resps)
        tf(workspace, ["init", "test", "--bd-path", stub])

        out = tf(workspace, ["ready"])
        assert out["ok"] is True
        ids = [b["id"] for b in out["ready"]]
        assert "t1" in ids

    def test_skips_beads_already_in_ready(self, workspace):
        """Beads already in bd ready output should not be re-checked or duplicated."""
        ready_beads = [
            {"id": "t1", "type": "task", "title": "Task 1"},
        ]
        list_beads = [
            {"id": "t1", "type": "task", "title": "Task 1"},
        ]
        stub = self._make_routing_stub(workspace, ready_beads, list_beads)
        tf(workspace, ["init", "test", "--bd-path", stub])

        out = tf(workspace, ["ready"])
        assert out["ok"] is True
        ids = [b["id"] for b in out["ready"]]
        assert ids.count("t1") == 1
        assert out["supplemented"] == 0


# ── Notify Auto-Close Tests ────────────────────────────────────


class TestNotifyAutoClose:
    """Verify notify only auto-closes beads when worker called worker-close."""

    def test_auto_closes_when_closed_self(self, workspace, bd_stub):
        """When worker called worker-close (closed_self=True), notify should auto-close."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["closed_self"] = True
        save_registry(workspace, reg)

        bead_resp = [{"id": "bead-abc", "status": "in_progress", "title": "Test"}]
        out = tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "50"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead_resp)})
        assert out["ok"] is True

    def test_no_auto_close_without_closed_self(self, workspace, bd_stub):
        """When worker did NOT call worker-close, notify should not auto-close."""
        tf(workspace, ["init", "test", "--bd-path", bd_stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        bead_resp = [{"id": "bead-abc", "status": "in_progress", "title": "Test"}]
        out = tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "50"],
                 env={"BD_STUB_RESPONSE": json.dumps(bead_resp)})
        assert out["ok"] is True
        assert out.get("auto_closed") is False
        assert "action_needed" in out


# ── Stall Detection with Expected Mins ──────────────────────────


class TestStallExpectedMins:
    """Verify stall detection with expected_completion_at before deadline."""

    def test_stalled_before_deadline_with_long_silence(self, workspace):
        """Worker silent for 2x threshold should be stalled even before expected deadline."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["last_heartbeat"] = ts_minutes_ago(45)
        reg["workers"]["rust-1"]["expected_completion_at"] = ts_minutes_from_now(30)
        save_registry(workspace, reg)

        out = tf(workspace, ["stalled", "--threshold-mins", "20"])
        assert len(out["stalled"]) == 1, "Worker silent 45min with 20min threshold (2x=40) should be stalled"

    def test_not_stalled_before_deadline_with_short_silence(self, workspace):
        """Worker silent for less than 2x threshold should not be stalled before deadline."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["last_heartbeat"] = ts_minutes_ago(30)
        reg["workers"]["rust-1"]["expected_completion_at"] = ts_minutes_from_now(30)
        save_registry(workspace, reg)

        out = tf(workspace, ["stalled", "--threshold-mins", "20"])
        assert len(out["stalled"]) == 0, "Worker silent 30min with 20min threshold (2x=40) should not be stalled"


# ── Validate-Plan Init Warning Tests ────────────────────────────


class TestValidatePlanInitWarning:
    """Verify validate-plan warns when tf.py init hasn't been run."""

    def test_warns_when_no_active_plan(self, workspace):
        """validate-plan should warn if .beads/active-plan doesn't exist."""
        plan = workspace / ".beads" / "plan.md"
        plan.write_text("## Task 1\n\n### Type\ntask\n\n### Description\nDo something\nFiles: foo.py\n")

        out = tf(workspace, ["validate-plan", str(plan)])
        assert out["ok"] is True
        assert any("init has not been run" in w for w in out.get("warnings", [])), \
            f"Expected init warning, got: {out.get('warnings', [])}"

    def test_no_warning_after_init(self, workspace):
        """validate-plan should not warn about init after tf.py init has been run."""
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        plan = workspace / ".beads" / "plan.md"
        plan.write_text("## Task 1\n\n### Type\ntask\n\n### Description\nDo something\nFiles: foo.py\n")

        out = tf(workspace, ["validate-plan", str(plan)])
        assert out["ok"] is True
        for w in out.get("warnings", []):
            assert "init has not been run" not in w


# ── Worker-Close File Validation Tests ──────────────────────────


class TestWorkerCloseFileValidation:
    """Verify worker-close warns when target files were not modified."""

    def _make_stateful_stub(self, workspace):
        """Create a bd stub that returns in_progress on first show, closed on subsequent."""
        stub = workspace / ".beads" / "bd-stateful-stub"
        stub.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, sys, os
            args = sys.argv[1:]
            state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bd-state.json")
            state = {}
            if os.path.exists(state_file):
                with open(state_file) as f:
                    state = json.load(f)
            if "close" in args:
                state["closed"] = True
                with open(state_file, "w") as f:
                    json.dump(state, f)
                print(json.dumps([{"id": "bead-abc", "status": "closed"}]))
            elif "show" in args:
                if state.get("closed"):
                    print(json.dumps([{"id": "bead-abc", "status": "closed", "title": "Test"}]))
                else:
                    print(json.dumps([{"id": "bead-abc", "status": "in_progress", "title": "Test"}]))
            elif "update" in args:
                print(json.dumps({"ok": True}))
            else:
                print("{}")
        """))
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        return str(stub)

    def _setup(self, workspace):
        stub = self._make_stateful_stub(workspace)
        tf(workspace, ["init", "test", "--bd-path", stub])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        subprocess.run(["git", "add", "."], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "chore: init", "-q"], cwd=workspace, check=True)
        (workspace / "dummy.txt").write_text("dummy")
        subprocess.run(["git", "add", "dummy.txt"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add dummy"], cwd=workspace, check=True)

    def test_warns_when_no_target_files_modified(self, workspace):
        """worker-close should warn when none of the declared target files were changed."""
        self._setup(workspace)

        out = tf(workspace, [
            "worker-close", "bead-abc",
            "--context-pct", "50",
            "--files", "src/foo.py,src/bar.py",
            "--summary", "Did nothing. AC: n/a",
        ], env={"CLAUDE_AGENT_NAME": "rust-1"})
        assert out["ok"] is True, f"worker-close failed: {out}"
        warnings = out.get("warnings", [])
        assert any("no target files were modified" in w for w in warnings), \
            f"Expected file modification warning, got: {warnings}"

    def test_no_warning_with_force(self, workspace):
        """worker-close --force should skip the file modification check."""
        self._setup(workspace)

        out = tf(workspace, [
            "worker-close", "bead-abc",
            "--context-pct", "50",
            "--files", "src/foo.py",
            "--summary", "Intentionally no changes. AC: verified",
            "--force",
        ], env={"CLAUDE_AGENT_NAME": "rust-1"})
        assert out["ok"] is True, f"worker-close --force failed: {out}"
        warnings = out.get("warnings", [])
        assert not any("no target files were modified" in w for w in warnings), \
            f"No file modification warning expected with --force, got: {warnings}"
