"""Shared fixtures for the codify-taskforce CLI specification tests.

These tests are written from the specification / CLI contract only, before
any implementation exists.  They therefore never import the implementation
at module level (that would break collection while the package is missing);
instead every test drives the public CLI in a subprocess:

    python -m codify_taskforce <command> [options]

with ``CODIFY_TASKFORCE_CONFIG`` pointing at a JSON config file and
``PYTHONPATH`` containing the repository's ``src`` directory.  This is the
only invocation contract derivable from the spec (package
``src/codify_taskforce/`` + the listed command names).

All fixtures build throwaway data under ``tmp_path`` only: temporary git
repositories with real commits, dummy rule files and dummy secret-like
strings.  No network access and no real personal data is used.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

# Making ``src`` importable is explicitly allowed by the task; it also keeps
# any future in-process import test working.  The CLI subprocess receives
# the same directory through PYTHONPATH (see ``CodifyHarness.run_raw``).
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Dummy organisation repository identifier, copied from the specification.
ORG_REPO = "Charee-Villapong/ai-organization"


class CodifyHarness:
    """Small factory + CLI runner bound to one ``tmp_path``."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.config_path = tmp_path / "config.json"
        self.state_file = tmp_path / "state.json"
        self.queue_file = tmp_path / "queue.json"
        self._repo_counter = 0

    # ------------------------------------------------------------------
    # git helpers
    # ------------------------------------------------------------------
    def git(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
        return result

    def init_repo(self, name: str | None = None) -> Path:
        self._repo_counter += 1
        repo = self.tmp_path / (name or f"repo{self._repo_counter}")
        repo.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q", str(repo)],
            capture_output=True,
            text=True,
            check=True,
        )
        # Local, dummy-only identity so real commits can be created.
        self.git(repo, "config", "user.name", "Codify Tests")
        self.git(repo, "config", "user.email", "codify-tests@example.invalid")
        self.git(repo, "config", "commit.gpgsign", "false")
        return repo

    def write_file(self, repo: Path, rel_path: str, content) -> Path:
        target = repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def commit_all(self, repo: Path, message: str = "commit") -> str:
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", message)
        return self.head(repo)

    def head(self, repo: Path) -> str:
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    # ------------------------------------------------------------------
    # config helpers
    # ------------------------------------------------------------------
    def write_config(
        self,
        allowlist: list[dict],
        *,
        denylist: list[str] | None = None,
        weekly_issue_cap: int = 3,
        daily_presentation_cap: int = 2,
        checklist_version: str = "1",
        local_patterns_file: str | Path | None = None,
        config_path: Path | None = None,
    ) -> Path:
        config: dict = {
            "allowlist": allowlist,
            "denylist": denylist or [],
            "state_file": str(self.state_file),
            "queue_file": str(self.queue_file),
            "weekly_issue_cap": weekly_issue_cap,
            "daily_presentation_cap": daily_presentation_cap,
            "checklist_version": checklist_version,
            "org_repo": ORG_REPO,
        }
        if local_patterns_file is not None:
            config["local_patterns_file"] = str(local_patterns_file)
        path = config_path or self.config_path
        path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def config_for_repo(
        self, repo: Path, files: list[str], **kwargs
    ) -> Path:
        return self.write_config(
            [{"repo": str(repo), "files": list(files)}], **kwargs
        )

    # ------------------------------------------------------------------
    # generic JSON-file helper (CLI ``--file`` inputs)
    # ------------------------------------------------------------------
    def write_json_input(self, name: str, data) -> Path:
        path = self.tmp_path / name
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------
    # CLI runner
    # ------------------------------------------------------------------
    def run_raw(
        self,
        *args: str,
        config_path: Path | None = "__default__",
        extra_env: dict | None = None,
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        pythonpath = str(SRC_DIR)
        if env.get("PYTHONPATH"):
            pythonpath = pythonpath + os.pathsep + env["PYTHONPATH"]
        env["PYTHONPATH"] = pythonpath
        if config_path == "__default__":
            env["CODIFY_TASKFORCE_CONFIG"] = str(self.config_path)
        elif config_path is None:
            env.pop("CODIFY_TASKFORCE_CONFIG", None)
        else:
            env["CODIFY_TASKFORCE_CONFIG"] = str(config_path)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, "-m", "codify_taskforce", *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )

    def run_json(self, *args: str, **kwargs):
        """Run the CLI, require exit 0 and a single JSON object on stdout."""
        result = self.run_raw(*args, **kwargs)
        assert result.returncode == 0, (
            f"CLI {args} exited {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover - failure aid
            raise AssertionError(
                f"CLI {args} did not emit a single JSON document on "
                f"stdout: {result.stdout!r}"
            ) from exc
        assert isinstance(payload, dict), (
            f"CLI {args} must emit a JSON object, got {type(payload)}"
        )
        return payload

    # ------------------------------------------------------------------
    # domain conveniences shared by several test modules
    # ------------------------------------------------------------------
    def extract(self) -> dict:
        return self.run_json("extract")

    def extracted_files(self) -> list[dict]:
        return self.extract()["files"]

    def extracted_file(self, rel_path: str) -> dict:
        for entry in self.extracted_files():
            if entry["path"] == rel_path:
                return entry
        raise AssertionError(
            f"{rel_path!r} not present in extract result: "
            f"{self.extracted_files()!r}"
        )

    def section(self, rel_path: str, heading: str) -> dict:
        entry = self.extracted_file(rel_path)
        for section in entry["sections"]:
            if section["heading"] == heading:
                return section
        raise AssertionError(
            f"section {heading!r} not found in {rel_path!r}: "
            f"{entry['sections']!r}"
        )

    def state_commit(self, judged: list[dict], advance_commits: dict) -> dict:
        results = self.write_json_input(
            "results.json",
            {"judged": judged, "advance_commits": advance_commits},
        )
        return self.run_json("state-commit", "--file", str(results))

    def seed_state_at_head(self, repo: Path) -> None:
        """Record ``last_success_commit = HEAD`` without judging anything."""
        payload = self.state_commit([], {str(repo): self.head(repo)})
        assert payload["ok"] is True


@pytest.fixture
def harness(tmp_path) -> CodifyHarness:
    return CodifyHarness(tmp_path)


@pytest.fixture
def simple_repo(harness: CodifyHarness) -> Path:
    """One committed repository with two allowlisted rule files."""
    repo = harness.init_repo()
    harness.write_file(
        repo,
        "rules/a.md",
        "# Alpha\n\nalpha body\n\n## Alpha detail\n\ndetail body\n",
    )
    harness.write_file(repo, "rules/b.md", "# Beta\n\nbeta body\n")
    harness.commit_all(repo, "initial rules")
    harness.config_for_repo(repo, ["rules/a.md", "rules/b.md"])
    return repo


def queue_item(key: str, created: str, **overrides) -> dict:
    """Build one queue-add input item; ``key`` is treated as opaque."""
    item = {
        "key": key,
        "title": f"Confirm {key}",
        "repo": "/tmp/dummy-repo",
        "path": "rules/a.md",
        "heading": "Alpha",
        "section_hash": "a" * 64,
        "reason_code": "needs_decision",
        "created": created,
    }
    item.update(overrides)
    return item
