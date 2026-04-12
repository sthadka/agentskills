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
        # Reads BD_STUB_RESPONSE env var for stdout, BD_STUB_EXIT for exit code.
        DEFAULT_RESP='{}'
        echo "${BD_STUB_RESPONSE:-$DEFAULT_RESP}"
        exit "${BD_STUB_EXIT:-0}"
    """))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return str(stub)


def tf(workspace, args: list[str], env: dict | None = None) -> dict:
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
        assert out["late"] is False
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
        assert out["retired_now"][0]["reason"] == "high_ctx"

    def test_auto_retire_low_context(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "30"])

        out = tf(workspace, ["sync"])
        assert len(out["retired_now"]) == 1
        assert out["retired_now"][0]["reason"] == "low_ctx"

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

    def test_stale_idle_worker_flagged(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/usr/bin/bd"])
        tf(workspace, ["dispatch", "rust-1", "bead-abc", "--skill", "rust"])
        tf(workspace, ["notify", "rust-1", "bead-abc", "--context-pct", "55"])

        reg = load_registry(workspace)
        reg["workers"]["rust-1"]["idle_since"] = ts_minutes_ago(45)
        save_registry(workspace, reg)

        out = tf(workspace, ["sync"])
        available = out["available"]["rust"][0]
        assert available["stale"] is True
        assert available["idle_min"] > 40

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


class TestMultiContext:
    def test_picks_newest_context_dir(self, workspace):
        beads = workspace / ".beads"

        # Create old context dir
        old = beads / "context-old"
        old.mkdir()
        old_reg = old / "registry.json"
        old_reg.write_text(json.dumps({"plan_name": "old", "bd_path": "bd", "workers": {}, "routing": {}, "phases": {}}))

        import time
        time.sleep(0.1)

        # Create new context dir
        new = beads / "context-new"
        new.mkdir()
        new_reg = new / "registry.json"
        new_reg.write_text(json.dumps({"plan_name": "new", "bd_path": "/usr/bin/bd", "workers": {}, "routing": {}, "phases": {}, "settings": {"stall_threshold_mins": 20}}))

        # Touch new one to ensure it's newest
        new_reg.touch()

        out = tf(workspace, ["sync"])
        assert out["counts"]["total"] == 0  # Uses "new" which has no workers


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
        assert info == {
            "worker": "rust-1",
            "bead": "abc",
            "last_hb": "2026-04-12T12:00:00Z",
            "note": "compiling",
        }

    def test_stalled_info_defaults(self):
        info = self.tf_mod._stalled_info("rust-1", {})
        assert info["bead"] == "?"
        assert info["last_hb"] == "?"
        assert info["note"] == ""

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


# ── Bd Path Tests ──────────────────────────────────────────────


class TestBdPath:
    def test_bd_path_from_registry(self, workspace):
        tf(workspace, ["init", "test", "--bd-path", "/custom/path/bd"])
        r = subprocess.run(
            ["python3", str(workspace / ".beads" / "tf.py"), "bd-path"],
            cwd=workspace, capture_output=True, text=True,
        )
        assert r.stdout.strip() == "/custom/path/bd"


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
