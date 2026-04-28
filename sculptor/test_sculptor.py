"""Comprehensive tests for sculptor.py — sculptor validation tool.

Tests pure functions via direct import and CLI commands via subprocess.
No external dependencies (bd) required — all tests use filesystem fixtures.

Run: uv run --with pytest pytest sculptor/test_sculptor.py -v
"""

import importlib.util
import subprocess
import textwrap
from pathlib import Path

import pytest

SCULPTOR_PY = Path(__file__).parent / "sculptor.py"

# Load sculptor.py as a module directly (it's a standalone script, not a package)
import sys
_spec = importlib.util.spec_from_file_location("sculptor_mod", SCULPTOR_PY)
sculptor_mod = importlib.util.module_from_spec(_spec)
sys.modules["sculptor_mod"] = sculptor_mod
_spec.loader.exec_module(sculptor_mod)


# ── Helpers ──────────────────────────────────────────────────────


def sculptor(args: list[str], cwd: Path | None = None) -> dict:
    """Run sculptor.py and return parsed output."""
    r = subprocess.run(
        ["python3", str(SCULPTOR_PY)] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return {
        "stdout": r.stdout.strip(),
        "stderr": r.stderr.strip(),
        "returncode": r.returncode,
    }


@pytest.fixture
def idea_dir(tmp_path):
    """Create a minimal idea directory with standard files."""
    d = tmp_path / "test-idea"
    d.mkdir()
    return d


def write_file(path: Path, content: str):
    path.write_text(textwrap.dedent(content).lstrip())


# ── parse_annotations Tests ──────────────────────────────────────


class TestParseAnnotations:
    def test_extracts_annotations(self):
        from sculptor_mod import parse_annotations

        p = Path("/tmp/test_ann.md")
        p.write_text("# Title\n>> fix this\n## Section\n>> ? why\n")
        result = parse_annotations(p)
        p.unlink()

        assert len(result) == 2
        assert result[0]["line"] == 2
        assert result[0]["text"] == "fix this"
        assert result[1]["prefix"] == "?"

    def test_ignores_annotations_in_code_blocks(self):
        from sculptor_mod import parse_annotations

        p = Path("/tmp/test_ann_code.md")
        p.write_text("```python\n>> not an annotation\n```\n>> real annotation\n")
        result = parse_annotations(p)
        p.unlink()

        assert len(result) == 1
        assert result[0]["text"] == "real annotation"

    def test_all_prefix_types(self):
        from sculptor_mod import parse_annotations

        p = Path("/tmp/test_ann_prefix.md")
        p.write_text(
            ">> plain\n>> ? question\n>> + addition\n>> - remove\n>> * strong\n"
        )
        result = parse_annotations(p)
        p.unlink()

        assert len(result) == 5
        assert result[0]["prefix"] == ""
        assert result[1]["prefix"] == "?"
        assert result[2]["prefix"] == "+"
        assert result[3]["prefix"] == "-"
        assert result[4]["prefix"] == "*"

    def test_empty_file(self):
        from sculptor_mod import parse_annotations

        p = Path("/tmp/test_ann_empty.md")
        p.write_text("")
        result = parse_annotations(p)
        p.unlink()

        assert result == []


# ── cmd_annotations Tests (CLI) ──────────────────────────────────


class TestAnnotationsCLI:
    def test_shows_annotations(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# Doc\n>> fix typo\n>> ? why this\n")
        r = sculptor(["annotations", str(f)])
        assert r["returncode"] == 0
        assert "2 annotation(s)" in r["stdout"]
        assert "fix typo" in r["stdout"]

    def test_no_annotations(self, tmp_path):
        f = tmp_path / "clean.md"
        f.write_text("# Clean doc\nNo annotations here.\n")
        r = sculptor(["annotations", str(f)])
        assert r["returncode"] == 0
        assert "No annotations" in r["stdout"]

    def test_missing_file(self):
        r = sculptor(["annotations", "/nonexistent/file.md"])
        assert r["returncode"] == 1


# ── cmd_verify_clean Tests ───────────────────────────────────────


class TestVerifyClean:
    def test_pass_no_annotations(self, tmp_path):
        f = tmp_path / "clean.md"
        f.write_text("# Title\nClean content.\n")
        r = sculptor(["verify-clean", str(f)])
        assert r["returncode"] == 0
        assert "PASS" in r["stdout"]

    def test_fail_with_annotations(self, tmp_path):
        f = tmp_path / "dirty.md"
        f.write_text("# Title\n>> leftover\n")
        r = sculptor(["verify-clean", str(f)])
        assert r["returncode"] == 1
        assert "FAIL" in r["stdout"]
        assert "1 unaddressed" in r["stdout"]


# ── cmd_phase Tests ──────────────────────────────────────────────


class TestPhase:
    def test_empty_dir_is_phase_1(self, idea_dir):
        r = sculptor(["phase", str(idea_dir)])
        assert r["returncode"] == 0
        assert "Phase 1" in r["stdout"]
        assert "INTAKE" in r["stdout"]

    def test_research_is_phase_2(self, idea_dir):
        (idea_dir / "research.md").write_text("# Research\n")
        r = sculptor(["phase", str(idea_dir)])
        assert "Phase 2" in r["stdout"]

    def test_spec_and_plan_is_phase_5(self, idea_dir):
        (idea_dir / "research.md").write_text("# Research\n")
        (idea_dir / "idea.md").write_text("# Idea\n")
        (idea_dir / "spec.md").write_text("# Spec\n")
        (idea_dir / "plan.md").write_text("# Plan\n")
        r = sculptor(["phase", str(idea_dir)])
        assert "Phase 5" in r["stdout"]

    def test_pending_annotations_shown(self, idea_dir):
        (idea_dir / "research.md").write_text("# Research\n>> unresolved\n")
        r = sculptor(["phase", str(idea_dir)])
        assert "Pending annotations" in r["stdout"]
        assert "research.md" in r["stdout"]


# ── extract_code_blocks Tests ────────────────────────────────────


class TestExtractCodeBlocks:
    def test_extracts_typed_blocks(self):
        from sculptor_mod import extract_code_blocks

        text = "Prose\n```typescript\nconst x = 1;\n```\nMore\n```python\ny = 2\n```\n"
        ts = extract_code_blocks(text, "typescript")
        assert len(ts) == 1
        assert "const x = 1;" in ts[0]

        all_blocks = extract_code_blocks(text)
        assert len(all_blocks) == 2

    def test_empty_text(self):
        from sculptor_mod import extract_code_blocks

        assert extract_code_blocks("", "python") == []


# ── extract_type_names Tests ─────────────────────────────────────


class TestExtractTypeNames:
    def test_extracts_types(self):
        from sculptor_mod import extract_type_names

        code = "interface Foo { x: number }\ntype Bar = string\nenum Baz { A, B }\nclass Qux {}"
        names = extract_type_names(code)
        assert names == {"Foo", "Bar", "Baz", "Qux"}

    def test_no_types(self):
        from sculptor_mod import extract_type_names

        assert extract_type_names("const x = 1;\nlet y = 2;") == set()


# ── parse_spec_coverage_table Tests ──────────────────────────────


class TestParseSpecCoverageTable:
    def test_parses_table(self):
        from sculptor_mod import parse_spec_coverage_table

        text = textwrap.dedent("""\
            ## Spec Coverage

            | Spec Section | Task |
            |---|---|
            | Architecture | S1: Init scaffold |
            | Data Model | 1.1: Build client |
            | API Surface | 2.1: Auth module |

            ## Dependencies
        """)
        rows = parse_spec_coverage_table(text)
        assert len(rows) == 3
        assert rows[0] == {"spec_section": "Architecture", "task_ref": "S1: Init scaffold"}
        assert rows[2] == {"spec_section": "API Surface", "task_ref": "2.1: Auth module"}

    def test_no_table(self):
        from sculptor_mod import parse_spec_coverage_table

        text = "## Dependencies\nStuff\n## Risks\n"
        assert parse_spec_coverage_table(text) == []

    def test_empty_text(self):
        from sculptor_mod import parse_spec_coverage_table

        assert parse_spec_coverage_table("") == []

    def test_ignores_header_row(self):
        from sculptor_mod import parse_spec_coverage_table

        text = textwrap.dedent("""\
            ## Spec Coverage

            | Spec Section | Task |
            |:---|:---|
            | Architecture | S1: Init |
        """)
        rows = parse_spec_coverage_table(text)
        assert len(rows) == 1
        assert rows[0]["spec_section"] == "Architecture"


# ── cmd_lint_spec Tests ──────────────────────────────────────────


class TestLintSpec:
    def test_clean_spec_passes(self, tmp_path):
        f = tmp_path / "spec.md"
        f.write_text("# Spec\n## Architecture\nClean spec.\n")
        r = sculptor(["lint-spec", str(f)])
        assert r["returncode"] == 0
        assert "PASS" in r["stdout"]

    def test_detects_todo(self, tmp_path):
        f = tmp_path / "spec.md"
        f.write_text("# Spec\n## Architecture\nTODO: finish this\n")
        r = sculptor(["lint-spec", str(f)])
        assert r["returncode"] == 1
        assert "TODO" in r["stdout"]

    def test_detects_dead_type(self, tmp_path):
        f = tmp_path / "spec.md"
        f.write_text(
            "# Spec\n```typescript\ninterface DeadType { x: number }\n```\n"
        )
        r = sculptor(["lint-spec", str(f)])
        assert r["returncode"] == 1
        assert "Dead type" in r["stdout"]
        assert "DeadType" in r["stdout"]

    def test_live_type_not_flagged(self, tmp_path):
        f = tmp_path / "spec.md"
        f.write_text(
            "# Spec\n```typescript\ninterface Foo { x: number }\n```\n"
            "Uses Foo for config.\n"
        )
        r = sculptor(["lint-spec", str(f)])
        assert r["returncode"] == 0

    def test_detects_untagged_code_block(self, tmp_path):
        f = tmp_path / "spec.md"
        f.write_text(
            "# Spec\n```\nconst x = 1;\nfunction foo() {\n  return x;\n}\n```\n"
        )
        r = sculptor(["lint-spec", str(f)])
        assert r["returncode"] == 1
        assert "without language tag" in r["stdout"]

    def test_detects_remaining_annotations(self, tmp_path):
        f = tmp_path / "spec.md"
        f.write_text("# Spec\n>> leftover\n")
        r = sculptor(["lint-spec", str(f)])
        assert r["returncode"] == 1
        assert "annotation" in r["stdout"]


# ── cmd_lint_plan Tests ──────────────────────────────────────────


class TestLintPlan:
    GOOD_PLAN = textwrap.dedent("""\
        # Implementation Plan: Test

        ## Setup
        - [ ] S1: Init
          - AC: scaffold created

        ## Phase 1: Core
        - [ ] 1.1: Build it
          - AC: it builds

        ## Cross-worker Invariants
        - All writes atomic

        ## Dependencies
        S1 blocks 1.1

        ## Risks
        None known
    """)

    def test_clean_plan_passes(self, tmp_path):
        f = tmp_path / "plan.md"
        f.write_text(self.GOOD_PLAN)
        r = sculptor(["lint-plan", str(f)])
        assert r["returncode"] == 0
        assert "PASS" in r["stdout"]

    def test_missing_ac(self, tmp_path):
        f = tmp_path / "plan.md"
        plan = self.GOOD_PLAN.replace("  - AC: it builds\n", "")
        f.write_text(plan)
        r = sculptor(["lint-plan", str(f)])
        assert r["returncode"] == 1
        assert "missing AC" in r["stdout"]

    def test_missing_setup(self, tmp_path):
        f = tmp_path / "plan.md"
        plan = self.GOOD_PLAN.replace("## Setup\n- [ ] S1: Init\n  - AC: scaffold created\n\n", "")
        f.write_text(plan)
        r = sculptor(["lint-plan", str(f)])
        assert r["returncode"] == 1
        assert "Setup" in r["stdout"]

    def test_missing_invariants(self, tmp_path):
        f = tmp_path / "plan.md"
        plan = self.GOOD_PLAN.replace(
            "## Cross-worker Invariants\n- All writes atomic\n\n", ""
        )
        f.write_text(plan)
        r = sculptor(["lint-plan", str(f)])
        assert r["returncode"] == 1
        assert "Invariants" in r["stdout"]

    def test_missing_risks(self, tmp_path):
        f = tmp_path / "plan.md"
        plan = self.GOOD_PLAN.replace("## Risks\nNone known\n", "")
        f.write_text(plan)
        r = sculptor(["lint-plan", str(f)])
        assert r["returncode"] == 1
        assert "Risks" in r["stdout"]

    def test_spec_coverage_table_validated(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text("# Spec\n## Architecture\nArch\n## Data Model\nModel\n## API\nAPI\n")

        plan = tmp_path / "plan.md"
        plan_text = self.GOOD_PLAN + textwrap.dedent("""\

            ## Spec Coverage

            | Spec Section | Task |
            |---|---|
            | Architecture | S1: Init |
        """)
        plan.write_text(plan_text)

        r = sculptor(["lint-plan", str(plan), "--spec", str(spec)])
        assert r["returncode"] == 1
        assert "Data Model" in r["stdout"]
        assert "API" in r["stdout"]

    def test_spec_coverage_dangling_ref(self, tmp_path):
        # Task refs are checked as simple IDs (no spaces) split by comma.
        # "S99" is an ID-style ref that doesn't match any plan task.
        spec = tmp_path / "spec.md"
        spec.write_text("# Spec\n## Architecture\nArch\n")

        plan = tmp_path / "plan.md"
        plan_text = self.GOOD_PLAN + textwrap.dedent("""\

            ## Spec Coverage

            | Spec Section | Task |
            |---|---|
            | Architecture | S99 |
        """)
        plan.write_text(plan_text)

        r = sculptor(["lint-plan", str(plan), "--spec", str(spec)])
        assert r["returncode"] == 1
        assert "S99" in r["stdout"]

    def test_no_spec_reference_warning(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text("# Spec\n## Architecture\nArch\n")

        plan = tmp_path / "plan.md"
        plan.write_text(self.GOOD_PLAN)

        r = sculptor(["lint-plan", str(plan), "--spec", str(spec)])
        assert r["returncode"] == 1
        assert "Spec Coverage" in r["stdout"] or "spec.md" in r["stdout"]


# ── cmd_lint_cross Tests ─────────────────────────────────────────


class TestLintCross:
    def test_clean_dir_passes(self, idea_dir):
        write_file(idea_dir / "spec.md", """\
            # Spec
            ## Architecture
            ```typescript
            interface Config { x: number }
            ```
        """)
        write_file(idea_dir / "plan.md", """\
            # Plan
            ## Setup
            - [ ] S1: Build Config handler
              - AC: Config interface works
        """)
        r = sculptor(["lint-cross", str(idea_dir)])
        assert r["returncode"] == 0
        assert "PASS" in r["stdout"]

    def test_broken_appendix_link(self, idea_dir):
        write_file(idea_dir / "spec.md", """\
            # Spec
            See [appendix-api.md](appendix-api.md) for details.
            See [appendix-missing.md](appendix-missing.md) for more.
        """)
        (idea_dir / "appendix-api.md").write_text("# API\n")
        r = sculptor(["lint-cross", str(idea_dir)])
        assert r["returncode"] == 1
        assert "appendix-missing.md" in r["stdout"]
        assert "appendix-api.md" not in r["stdout"]

    def test_spec_type_not_in_plan(self, idea_dir):
        write_file(idea_dir / "spec.md", """\
            # Spec
            ```typescript
            interface UserConfig { name: string }
            class AuthClient { login(): void }
            ```
        """)
        write_file(idea_dir / "plan.md", """\
            # Plan
            - [ ] Build AuthClient
              - AC: client works
        """)
        r = sculptor(["lint-cross", str(idea_dir)])
        assert r["returncode"] == 1
        assert "UserConfig" in r["stdout"]
        assert "AuthClient" not in r["stdout"]

    def test_bad_spec_section_ref(self, idea_dir):
        write_file(idea_dir / "spec.md", """\
            # Spec
            ## Architecture
            Arch details
        """)
        write_file(idea_dir / "plan.md", """\
            # Plan
            - [ ] Build it
              - AC: done
              - Spec: spec.md §Nonexistent Section
        """)
        r = sculptor(["lint-cross", str(idea_dir)])
        assert r["returncode"] == 1
        assert "Nonexistent Section" in r["stdout"]

    def test_valid_spec_section_ref(self, idea_dir):
        write_file(idea_dir / "spec.md", """\
            # Spec
            ## Architecture
            Arch details
        """)
        write_file(idea_dir / "plan.md", """\
            # Plan
            - [ ] Build it
              - AC: done
              - Spec: spec.md §Architecture
        """)
        r = sculptor(["lint-cross", str(idea_dir)])
        assert r["returncode"] == 0

    def test_missing_dir(self):
        r = sculptor(["lint-cross", "/nonexistent/dir"])
        assert r["returncode"] == 1


# ── parse_plan Tests ─────────────────────────────────────────────


class TestParsePlan:
    def test_basic_structure(self):
        from sculptor_mod import parse_plan

        text = textwrap.dedent("""\
            # Implementation Plan: My Project

            ## Setup
            - [ ] S1: Initialize
              - AC: ready

            ## Phase 1: Core [parallel]
            - [ ] 1.1: Task A
              - AC: A done
            - [ ] 1.2: Task B
              - AC: B done

            ## Phase 2: Integration
            - [ ] 2.1: Wire up
              - [ ] 2.1a: Sub wire
              - AC: wired

            ## Cross-worker Invariants
            - All writes atomic

            ## Dependencies
            Setup blocks Phase 1

            ## Risks
            None
        """)
        plan = parse_plan(text)
        assert plan["title"] == "My Project"
        assert len(plan["phases"]) == 3
        assert plan["phases"][0]["is_setup"]
        assert plan["phases"][1]["is_parallel"]
        assert not plan["phases"][2]["is_parallel"]
        assert len(plan["phases"][1]["tasks"]) == 2
        assert len(plan["phases"][2]["tasks"][0]["subtasks"]) == 1
        assert "All writes atomic" in plan["invariants"]

    def test_parallel_flag_parsed(self):
        from sculptor_mod import parse_plan

        text = "# Plan: X\n## Phase 1: Build [parallel]\n- [ ] T1\n  - AC: done\n"
        plan = parse_plan(text)
        assert plan["phases"][0]["is_parallel"]
        assert plan["phases"][0]["name"] == "Phase 1: Build"

    def test_tdd_flag_parsed(self):
        from sculptor_mod import parse_plan

        text = "# Plan: X\n## Setup\n- [ ] T1 [TDD]\n  - AC: done\n"
        plan = parse_plan(text)
        assert plan["phases"][0]["tasks"][0]["is_tdd"]

    def test_empty_plan(self):
        from sculptor_mod import parse_plan

        plan = parse_plan("")
        assert plan["phases"] == []
        assert plan["title"] == ""


# ── make_task_slug Tests ─────────────────────────────────────────


class TestMakeTaskSlug:
    def test_strips_formatting(self):
        from sculptor_mod import make_task_slug

        assert make_task_slug("**`src/foo.py`** — Build handler") == "Build handler"

    def test_plain_desc(self):
        from sculptor_mod import make_task_slug

        assert make_task_slug("Build the auth client") == "Build the auth client"

    def test_path_prefix_stripped(self):
        from sculptor_mod import make_task_slug

        assert make_task_slug("src/utils/diff.py — Implement diff engine") == "Implement diff engine"


# ── generate_deps Tests ──────────────────────────────────────────


class TestGenerateDeps:
    def _make_plan(self, text: str) -> dict:
        from sculptor_mod import parse_plan

        return parse_plan(textwrap.dedent(text))

    def test_setup_blocks_all_phase1(self):
        from sculptor_mod import generate_deps

        plan = self._make_plan("""\
            # Plan: Test

            ## Setup
            - [ ] S1: Init
              - AC: done
            - [ ] S2: Deps
              - AC: done

            ## Phase 1: Core
            - [ ] 1.1: Build
              - AC: built

            ## Cross-worker Invariants
            None

            ## Dependencies
            None

            ## Risks
            None
        """)
        deps = generate_deps(plan)
        assert '"S1: Init" blocks "1.1: Build"' in deps
        assert '"S2: Deps" blocks "1.1: Build"' in deps

    def test_sequential_phase_chains(self):
        from sculptor_mod import generate_deps

        plan = self._make_plan("""\
            # Plan: Test

            ## Phase 1: Core
            - [ ] 1.1: First
              - AC: done
            - [ ] 1.2: Second
              - AC: done
            - [ ] 1.3: Third
              - AC: done

            ## Cross-worker Invariants
            None

            ## Dependencies
            None

            ## Risks
            None
        """)
        deps = generate_deps(plan)
        assert '"1.1: First" blocks "1.2: Second"' in deps
        assert '"1.2: Second" blocks "1.3: Third"' in deps

    def test_parallel_phase_no_chain(self):
        from sculptor_mod import generate_deps

        plan = self._make_plan("""\
            # Plan: Test

            ## Phase 1: Core [parallel]
            - [ ] 1.1: A
              - AC: done
            - [ ] 1.2: B
              - AC: done
            - [ ] 1.3: C
              - AC: done

            ## Cross-worker Invariants
            None

            ## Dependencies
            None

            ## Risks
            None
        """)
        deps = generate_deps(plan)
        assert '"1.1: A" blocks "1.2: B"' not in deps
        assert '"1.2: B" blocks "1.3: C"' not in deps

    def test_sequential_to_parallel_fan_out(self):
        from sculptor_mod import generate_deps

        plan = self._make_plan("""\
            # Plan: Test

            ## Setup
            - [ ] S1: Init
              - AC: done

            ## Phase 1: Core [parallel]
            - [ ] 1.1: A
              - AC: done
            - [ ] 1.2: B
              - AC: done
            - [ ] 1.3: C
              - AC: done

            ## Cross-worker Invariants
            None

            ## Dependencies
            None

            ## Risks
            None
        """)
        deps = generate_deps(plan)
        assert '"S1: Init" blocks "1.1: A"' in deps
        assert '"S1: Init" blocks "1.2: B"' in deps
        assert '"S1: Init" blocks "1.3: C"' in deps

    def test_parallel_to_sequential_fan_in(self):
        from sculptor_mod import generate_deps

        plan = self._make_plan("""\
            # Plan: Test

            ## Phase 1: Core [parallel]
            - [ ] 1.1: A
              - AC: done
            - [ ] 1.2: B
              - AC: done

            ## Phase 2: Integration
            - [ ] 2.1: Wire
              - AC: done

            ## Cross-worker Invariants
            None

            ## Dependencies
            None

            ## Risks
            None
        """)
        deps = generate_deps(plan)
        assert '"1.1: A" blocks "2.1: Wire"' in deps
        assert '"1.2: B" blocks "2.1: Wire"' in deps

    def test_parallel_to_parallel_full_mesh(self):
        from sculptor_mod import generate_deps

        plan = self._make_plan("""\
            # Plan: Test

            ## Phase 1: Core [parallel]
            - [ ] 1.1: A
              - AC: done
            - [ ] 1.2: B
              - AC: done

            ## Phase 2: Polish [parallel]
            - [ ] 2.1: X
              - AC: done
            - [ ] 2.2: Y
              - AC: done

            ## Cross-worker Invariants
            None

            ## Dependencies
            None

            ## Risks
            None
        """)
        deps = generate_deps(plan)
        assert '"1.1: A" blocks "2.1: X"' in deps
        assert '"1.1: A" blocks "2.2: Y"' in deps
        assert '"1.2: B" blocks "2.1: X"' in deps
        assert '"1.2: B" blocks "2.2: Y"' in deps


# ── parse_deps_file Tests ────────────────────────────────────────


class TestParseDepsFile:
    def test_parses_blocks(self):
        from sculptor_mod import parse_deps_file

        text = '"S1: Init" blocks "1.1: Build"\n"1.1: Build" blocks "2.1: Test"\n'
        deps = parse_deps_file(text)
        assert len(deps) == 2
        assert deps[0] == {"type": "blocks", "blocker": "S1: Init", "blocked": "1.1: Build"}

    def test_parses_child_of(self):
        from sculptor_mod import parse_deps_file

        text = '"Task A" child-of "Epic"\n'
        deps = parse_deps_file(text)
        assert len(deps) == 1
        assert deps[0] == {"type": "child-of", "child": "Task A", "parent": "Epic"}

    def test_skips_comments_and_blanks(self):
        from sculptor_mod import parse_deps_file

        text = '# Comment\n\n"A" blocks "B"\n# Another comment\n'
        deps = parse_deps_file(text)
        assert len(deps) == 1

    def test_empty_input(self):
        from sculptor_mod import parse_deps_file

        assert parse_deps_file("") == []


# ── match_title_to_id Tests ──────────────────────────────────────


class TestMatchTitleToId:
    def test_exact_match(self):
        from sculptor_mod import match_title_to_id

        issues = [{"id": "abc-1", "title": "S1: Init"}]
        assert match_title_to_id("S1: Init", issues) == "abc-1"

    def test_substring_match(self):
        from sculptor_mod import match_title_to_id

        issues = [{"id": "abc-1", "title": "S1: Initialize collection scaffold"}]
        assert match_title_to_id("S1: Initialize collection scaffold", issues) == "abc-1"

    def test_no_match(self):
        from sculptor_mod import match_title_to_id

        issues = [{"id": "abc-1", "title": "Something else"}]
        assert match_title_to_id("S1: Init", issues) is None

    def test_case_insensitive(self):
        from sculptor_mod import match_title_to_id

        issues = [{"id": "abc-1", "title": "S1: INIT"}]
        assert match_title_to_id("s1: init", issues) == "abc-1"


# ── find_epic_id Tests ───────────────────────────────────────────


class TestFindEpicId:
    def test_finds_goal_prefix(self):
        from sculptor_mod import find_epic_id

        issues = [
            {"id": "x-1", "title": "Goal: My Project"},
            {"id": "x-2", "title": "Task 1"},
        ]
        assert find_epic_id(issues) == "x-1"

    def test_finds_epic_type(self):
        from sculptor_mod import find_epic_id

        issues = [
            {"id": "x-1", "title": "My Project", "type": "epic"},
            {"id": "x-2", "title": "Task 1"},
        ]
        assert find_epic_id(issues) == "x-1"

    def test_falls_back_to_first(self):
        from sculptor_mod import find_epic_id

        issues = [
            {"id": "x-1", "title": "Task 1"},
            {"id": "x-2", "title": "Task 2"},
        ]
        assert find_epic_id(issues) == "x-1"

    def test_empty_list(self):
        from sculptor_mod import find_epic_id

        assert find_epic_id([]) is None


# ── generate_beads_plan Tests ────────────────────────────────────


class TestGenerateBeadsPlan:
    def test_generates_epic_and_tasks(self):
        from sculptor_mod import generate_beads_plan, parse_plan

        text = textwrap.dedent("""\
            # Implementation Plan: My Project

            ## Setup
            - [ ] S1: Init scaffold
              - AC: dirs exist

            ## Phase 1: Core [parallel]
            - [ ] 1.1: Build client [TDD]
              - AC: client works

            ## Cross-worker Invariants
            None

            ## Dependencies
            None

            ## Risks
            Some risk
        """)
        plan = parse_plan(text)
        output = generate_beads_plan(plan, "Build something cool")

        assert "## Goal: My Project" in output
        assert "### Type\nepic" in output
        assert "## S1: Init scaffold" in output or "## Init scaffold" in output
        assert "### Priority\n1" in output
        assert "### Acceptance Criteria" in output
        assert "tdd" in output.lower()

    def test_includes_risks_in_epic(self):
        from sculptor_mod import generate_beads_plan, parse_plan

        text = "# Plan: X\n## Setup\n- [ ] S1: Init\n  - AC: done\n## Risks\nData loss possible\n"
        plan = parse_plan(text)
        output = generate_beads_plan(plan, "")
        assert "Data loss possible" in output


# ── read_idea_description Tests ──────────────────────────────────


class TestReadIdeaDescription:
    def test_extracts_problem_and_solution(self, tmp_path):
        from sculptor_mod import read_idea_description

        (tmp_path / "idea.md").write_text(textwrap.dedent("""\
            # My Idea

            ## Problem
            Things are broken.

            ## Solution
            Fix them.

            ## Open Questions
            None
        """))
        desc = read_idea_description(tmp_path)
        assert "Things are broken" in desc
        assert "Fix them" in desc
        assert "Open Questions" not in desc

    def test_no_idea_file(self, tmp_path):
        from sculptor_mod import read_idea_description

        assert read_idea_description(tmp_path) == ""


# ── export-beads Tests (no --run, filesystem only) ───────────────


class TestExportBeads:
    def test_generates_files(self, idea_dir):
        write_file(idea_dir / "plan.md", """\
            # Implementation Plan: Test Export

            ## Setup
            - [ ] S1: Init
              - AC: scaffold ready

            ## Phase 1: Build [parallel]
            - [ ] 1.1: A
              - AC: A done
            - [ ] 1.2: B
              - AC: B done

            ## Cross-worker Invariants
            - All writes atomic

            ## Dependencies
            Setup blocks Phase 1

            ## Risks
            None
        """)

        r = sculptor(["export-beads", str(idea_dir)])
        assert r["returncode"] == 0

        beads_dir = idea_dir / ".beads"
        assert (beads_dir / "plan.md").exists()
        assert (beads_dir / "deps.txt").exists()
        assert (beads_dir / "invariants.md").exists()

        plan_content = (beads_dir / "plan.md").read_text()
        assert "## Goal: Test Export" in plan_content
        assert "S1: Init" in plan_content or "Init" in plan_content

        deps_content = (beads_dir / "deps.txt").read_text()
        assert "blocks" in deps_content

        inv_content = (beads_dir / "invariants.md").read_text()
        assert "atomic" in inv_content

    def test_no_plan_file(self, idea_dir):
        r = sculptor(["export-beads", str(idea_dir)])
        assert r["returncode"] == 1
        assert "not found" in r["stdout"]

    def test_parallel_deps_correct(self, idea_dir):
        write_file(idea_dir / "plan.md", """\
            # Implementation Plan: Parallel Test

            ## Setup
            - [ ] S1: Init
              - AC: done

            ## Phase 1: Core [parallel]
            - [ ] 1.1: A
              - AC: done
            - [ ] 1.2: B
              - AC: done
            - [ ] 1.3: C
              - AC: done

            ## Phase 2: Integrate
            - [ ] 2.1: Wire
              - AC: done

            ## Cross-worker Invariants
            None

            ## Dependencies
            None

            ## Risks
            None
        """)

        sculptor(["export-beads", str(idea_dir)])
        deps = (idea_dir / ".beads" / "deps.txt").read_text()

        assert '"S1: Init" blocks "1.1: A"' in deps
        assert '"S1: Init" blocks "1.2: B"' in deps
        assert '"S1: Init" blocks "1.3: C"' in deps

        assert '"1.1: A" blocks "1.2: B"' not in deps
        assert '"1.2: B" blocks "1.3: C"' not in deps

        assert '"1.1: A" blocks "2.1: Wire"' in deps
        assert '"1.2: B" blocks "2.1: Wire"' in deps
        assert '"1.3: C" blocks "2.1: Wire"' in deps


# ── wire-deps Tests (CLI, no bd) ────────────────────────────────


class TestWireDepsCLI:
    def test_usage_without_flags(self, tmp_path):
        f = tmp_path / "deps.txt"
        f.write_text('"A" blocks "B"\n')
        r = sculptor(["wire-deps", str(f)])
        assert r["returncode"] == 1
        assert "--from-bd" in r["stdout"] or "--id-map" in r["stdout"]

    def test_missing_deps_file(self):
        r = sculptor(["wire-deps", "/nonexistent/deps.txt", "--from-bd"])
        assert r["returncode"] == 1
        assert "not found" in r["stdout"]

    def test_missing_id_map(self, tmp_path):
        f = tmp_path / "deps.txt"
        f.write_text('"A" blocks "B"\n')
        r = sculptor(["wire-deps", str(f), "--id-map", "/nonexistent/map.json"])
        assert r["returncode"] == 1
        assert "not found" in r["stdout"]

    def test_no_args(self):
        r = sculptor(["wire-deps"])
        assert r["returncode"] == 1
        assert "Usage" in r["stdout"]


# ── CLI Smoke Tests ──────────────────────────────────────────────


class TestCLISmoke:
    def test_help(self):
        r = sculptor(["--help"])
        assert r["returncode"] == 0
        assert "sculptor" in r["stdout"]
        assert "lint-cross" in r["stdout"]
        assert "wire-deps" in r["stdout"]

    def test_unknown_command(self):
        r = sculptor(["nonexistent-cmd"])
        assert r["returncode"] == 1
        assert "Unknown command" in r["stdout"]

    def test_all_commands_listed(self):
        r = sculptor(["--help"])
        for cmd in [
            "annotations", "verify-clean", "phase", "lint-spec",
            "lint-plan", "lint-cross", "export-beads", "wire-deps",
        ]:
            assert cmd in r["stdout"], f"Missing command: {cmd}"


# ── PHASE_RE Regex Tests ────────────────────────────────────────


class TestPhaseRegex:
    def test_parallel_captured(self):
        import re
        from sculptor_mod import PHASE_RE

        m = PHASE_RE.match("## Phase 1: Foundation [parallel]")
        assert m is not None
        assert m.group(1) == "Phase 1: Foundation"
        assert "[parallel]" in m.group(2)

    def test_no_marker(self):
        from sculptor_mod import PHASE_RE

        m = PHASE_RE.match("## Phase 2: Integration")
        assert m is not None
        assert m.group(1) == "Phase 2: Integration"
        assert m.group(2) is None

    def test_setup(self):
        from sculptor_mod import PHASE_RE

        m = PHASE_RE.match("## Setup")
        assert m is not None
        assert m.group(1) == "Setup"

    def test_non_phase_heading_no_match(self):
        from sculptor_mod import PHASE_RE

        assert PHASE_RE.match("## Dependencies") is None
        assert PHASE_RE.match("## Cross-worker Invariants") is None
        assert PHASE_RE.match("## Risks") is None
