"""Tests for session-viewer, grouped by implementation phase.

Phase 1 — determinism foundation (resolution, idempotency, compact/schema JSON, caps)
Phase 2 — coverage/correctness (meta filtering, compaction, API errors, unknown types)
Phase 3 — progressive disclosure (sections, map, auto-whole, drill, grep, tiering, estimate)

CLI tests shell out via subprocess (project convention); internal helpers are
imported dynamically.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "claude_session.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("claude_session", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cs = _load_module()


# -- Fixture builders ----------------------------------------------------------

def write_session(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return str(path)


def user_text(text, **extra):
    e = {"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": text}]}, "timestamp": "2026-01-01T00:00:00Z",
        "sessionId": "sid-000", "gitBranch": "main", "cwd": "/tmp/proj",
        "version": "9.9.9", "permissionMode": "default"}
    e.update(extra)
    return e


def assistant(blocks, **extra):
    e = {"type": "assistant", "message": {"role": "assistant", "content": blocks},
         "timestamp": "2026-01-01T00:00:01Z"}
    e.update(extra)
    return e


def tool_use(name, tid, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def tool_result(tid, text, is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text,
         "is_error": is_error}]}, "timestamp": "2026-01-01T00:00:02Z"}


def rich_session(path):
    """A session exercising most schema features (small: auto-whole by default)."""
    return write_session(path, [
        user_text("<system-reminder>injected context</system-reminder>", isMeta=True),
        user_text("first real prompt: build the thing"),
        assistant([{"type": "text", "text": "on it"},
                   tool_use("Read", "t1", {"file_path": "/a.py"}),
                   tool_use("Bash", "t2", {"command": "pytest"})]),
        tool_result("t1", "file contents"),
        tool_result("t2", "boom: failure", is_error=True),
        {"type": "system", "subtype": "compact_boundary",
         "timestamp": "2026-01-01T00:00:03Z"},
        {"type": "system", "subtype": "api_error",
         "text": "rate limited", "timestamp": "2026-01-01T00:00:04Z"},
        user_text("second real prompt: now commit"),
        assistant([tool_use("Bash", "t3", {"command": "git commit"})]),
        tool_result("t3", "committed"),
        {"type": "quiche-operation", "detail": "unknown future type"},
        {"type": "system", "subtype": "turn_duration", "durationMs": 1500,
         "timestamp": "2026-01-01T00:00:05Z", "gitBranch": "main",
         "sessionId": "sid-000", "version": "9.9.9", "cwd": "/tmp/proj"},
    ])


def run(*args):
    r = subprocess.run([sys.executable, SCRIPT, *args],
                       capture_output=True, text=True)
    return r


# -- Phase 1: determinism ------------------------------------------------------

class TestPhase1Determinism:
    def test_resolution_exact(self, tmp_path, monkeypatch):
        proj = tmp_path / "projA"
        proj.mkdir()
        p = write_session(proj / "abc123.jsonl", [user_text("hi")])
        monkeypatch.setattr(cs, "CLAUDE_DIR", str(tmp_path))
        assert cs.find_session_file("abc123") == p

    def test_resolution_ambiguous_raises(self, tmp_path, monkeypatch):
        for name in ("projA", "projB"):
            d = tmp_path / name
            d.mkdir()
            write_session(d / "dup.jsonl", [user_text("hi")])
        monkeypatch.setattr(cs, "CLAUDE_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="ambiguous"):
            cs.find_session_file("dup")

    def test_resolution_prefix_ambiguous_raises(self, tmp_path, monkeypatch):
        d = tmp_path / "projA"
        d.mkdir()
        write_session(d / "abcdef.jsonl", [user_text("hi")])
        write_session(d / "abcxyz.jsonl", [user_text("hi")])
        monkeypatch.setattr(cs, "CLAUDE_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="multiple"):
            cs.find_session_file("abc")

    def test_resolution_missing_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cs, "CLAUDE_DIR", str(tmp_path))
        with pytest.raises(FileNotFoundError):
            cs.find_session_file("nope")

    def test_project_disambiguates(self, tmp_path, monkeypatch):
        for name in ("alpha", "beta"):
            d = tmp_path / name
            d.mkdir()
            write_session(d / "dup.jsonl", [user_text("hi")])
        monkeypatch.setattr(cs, "CLAUDE_DIR", str(tmp_path))
        got = cs.find_session_file("dup", project="beta")
        assert got.endswith(os.path.join("beta", "dup.jsonl"))

    def test_json_idempotent(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        a = run(p, "--json").stdout
        b = run(p, "--json").stdout
        assert a == b and a.strip()

    def test_json_compact_and_schema(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        out = run(p, "--json").stdout
        d = json.loads(out)
        assert d["_schema"] == cs.SCHEMA_VERSION
        # compact: single line, and byte-equal to compact re-serialization
        assert out.count("\n") == 1  # only trailing newline
        assert out.rstrip("\n") == json.dumps(d, separators=(",", ":"))

    def test_json_pretty_differs(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        compact = run(p, "--json").stdout
        pretty = run(p, "--json", "--pretty").stdout
        assert json.loads(compact) == json.loads(pretty)
        assert len(pretty) > len(compact)

    def test_max_text_cap(self, tmp_path):
        big = "x" * 500
        p = write_session(tmp_path / "s.jsonl",
                          [user_text("go"), assistant([{"type": "text", "text": big}])])
        out = run(p, "--full", "--max-text", "50").stdout
        assert "total chars]" in out

    def test_list_utc(self, tmp_path, monkeypatch):
        d = tmp_path / "proj"
        d.mkdir()
        write_session(d / "s.jsonl", [user_text("hello there")])
        monkeypatch.setattr(cs, "CLAUDE_DIR", str(tmp_path))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            cs.list_sessions()
        assert "DATE (UTC)" in buf.getvalue()
        assert "Z " in buf.getvalue()  # UTC-marked timestamp


# -- Phase 2: coverage / correctness -------------------------------------------

class TestPhase2Coverage:
    def test_meta_filtered_by_default(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        d = json.loads(run(p, "--json").stdout)
        assert d["user_messages"] == ["first real prompt: build the thing",
                                       "second real prompt: now commit"]

    def test_include_meta(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        d = json.loads(run(p, "--json", "--include-meta").stdout)
        assert any("injected context" in m for m in d["user_messages"])

    def test_compaction_points(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        d = json.loads(run(p, "--json").stdout)
        assert len(d["compaction_points"]) == 1

    def test_api_errors_in_json_and_errors_mode(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        d = json.loads(run(p, "--json").stdout)
        assert len(d["api_errors"]) == 1 and d["api_errors"][0]["text"] == "rate limited"
        assert "API ERROR" in run(p, "--errors").stdout

    def test_unknown_types_tracked(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        d = json.loads(run(p, "--json").stdout)
        assert d["unknown_types"].get("quiche-operation") == 1

    def test_tool_error_counted(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        d = json.loads(run(p, "--json").stdout)
        assert d["error_count"] == 1
        assert d["errors"][0]["name"] == "Bash"


# -- Phase 3: progressive disclosure -------------------------------------------

class TestPhase3Disclosure:
    def test_build_sections_numbering(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        session = cs.parse_session(p)
        secs = cs.build_sections(session["messages"])
        ids = [s["id"] for s in secs]
        # preamble T0, then T1, T2 for the two real prompts
        assert ids == ["T0", "T1", "T2"]
        assert secs[1]["prompt"].startswith("first real prompt")
        assert secs[1]["error_count"] == 1

    def test_map_flag(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        out = run(p, "--map").stdout
        assert "[map]" in out
        assert "[T1]" in out
        assert "--- CONTEXT COMPACTED ---" in out

    def test_auto_whole_small_default(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        out = run(p).stdout  # small session, no flag -> whole transcript
        assert "[map]" not in out
        assert "USER: first real prompt" in out

    def test_map_default_large(self, tmp_path):
        big = "y" * 30000
        p = write_session(tmp_path / "s.jsonl", [
            user_text("do a big thing"),
            assistant([{"type": "text", "text": big}]),
        ])
        out = run(p).stdout  # large session, no flag -> map
        assert "[map]" in out

    def test_drill_turn(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        out = run(p, "--turn", "T2", "--no-timestamps").stdout
        assert "second real prompt: now commit" in out
        assert "first real prompt" not in out

    def test_drill_section_range(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        out = run(p, "--section", "2:2", "--no-timestamps").stdout
        assert "first real prompt" in out
        assert "second real prompt" not in out

    def test_last_limits_map(self, tmp_path):
        big = "z" * 30000
        p = write_session(tmp_path / "s.jsonl", [
            user_text("prompt one"), assistant([{"type": "text", "text": big}]),
            user_text("prompt two"), assistant([{"type": "text", "text": big}]),
        ])
        out = run(p, "--map", "--last", "1").stdout
        assert "prompt two" in out
        assert "prompt one" not in out

    def test_grep(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        out = run(p, "--grep", "commit", "--no-timestamps").stdout
        assert "now commit" in out
        out2 = run(p, "--grep", "zzznomatch").stdout
        assert "No matches" in out2

    def test_json_tiering(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        lean = json.loads(run(p, "--json").stdout)
        full = json.loads(run(p, "--json", "--full").stdout)
        assert "tool_calls" not in lean
        assert "tool_calls" in full and len(full["tool_calls"]) == 3

    def test_estimate(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        out = run(p, "--estimate").stdout
        assert "MODE" in out and "--map" in out and "--full" in out

    def test_sections_in_json(self, tmp_path):
        p = rich_session(tmp_path / "s.jsonl")
        d = json.loads(run(p, "--json").stdout)
        ids = [s["id"] for s in d["sections"]]
        assert ids == ["T0", "T1", "T2"]
        assert d["sections"][1]["msg_range"][0] >= 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-x", "-q"]))
