"""``extract`` in diff mode.

Acceptance criteria covered:
- after ``last_success_commit`` is recorded, only allowlisted files that
  changed in *committed* diffs between that commit and HEAD are returned;
- changing and committing exactly one file makes exactly that file the
  diff target;
- uncommitted changes are never diff targets;
- a missing or corrupt state file falls back to a full scan, with
  ``state_warning`` set for the corrupt case.
"""

from __future__ import annotations


def _two_file_repo(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha v1\n")
    harness.write_file(repo, "rules/b.md", "# Beta\n\nbeta v1\n")
    harness.commit_all(repo, "v1")
    harness.config_for_repo(repo, ["rules/a.md", "rules/b.md"])
    harness.seed_state_at_head(repo)
    return repo


def test_diff_with_no_new_commits_returns_no_files(harness):
    _two_file_repo(harness)
    payload = harness.extract()
    assert payload["mode"] == "diff"
    assert payload["files"] == []


def test_diff_returns_only_the_one_committed_changed_file(harness):
    repo = _two_file_repo(harness)
    harness.write_file(repo, "rules/b.md", "# Beta\n\nbeta v2\n")
    harness.commit_all(repo, "beta v2")

    payload = harness.extract()
    assert payload["mode"] == "diff"
    assert [entry["path"] for entry in payload["files"]] == ["rules/b.md"]
    section = harness.section("rules/b.md", "Beta")
    assert section["heading"] == "Beta"


def test_diff_ignores_uncommitted_changes(harness):
    repo = _two_file_repo(harness)
    # Working-tree change that is never committed.
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha dirty\n")

    payload = harness.extract()
    assert payload["mode"] == "diff"
    assert payload["files"] == []


def test_diff_ignores_staged_but_uncommitted_changes(harness):
    repo = _two_file_repo(harness)
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha staged\n")
    harness.git(repo, "add", "rules/a.md")  # staged, still uncommitted

    payload = harness.extract()
    assert payload["files"] == []


def test_diff_covers_the_whole_range_since_last_success(harness):
    repo = _two_file_repo(harness)
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha v2\n")
    harness.commit_all(repo, "alpha v2")
    harness.write_file(repo, "rules/b.md", "# Beta\n\nbeta v2\n")
    harness.commit_all(repo, "beta v2")

    paths = sorted(entry["path"] for entry in harness.extracted_files())
    assert paths == ["rules/a.md", "rules/b.md"]


def test_diff_still_respects_allowlist_and_denylist(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha v1\n")
    harness.write_file(repo, "rules/blocked.md", "# Blocked\n\nb v1\n")
    harness.write_file(repo, "rules/other.md", "# Other\n\no v1\n")
    harness.commit_all(repo, "v1")
    harness.config_for_repo(
        repo,
        ["rules/a.md", "rules/blocked.md"],
        denylist=["rules/blocked.md"],
    )
    harness.seed_state_at_head(repo)

    harness.write_file(repo, "rules/blocked.md", "# Blocked\n\nb v2\n")
    harness.write_file(repo, "rules/other.md", "# Other\n\no v2\n")
    harness.commit_all(repo, "change denied and non-allowlisted files")

    payload = harness.extract()
    assert payload["files"] == []


def test_missing_state_file_falls_back_to_full(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha\n")
    harness.commit_all(repo, "v1")
    harness.config_for_repo(repo, ["rules/a.md"])
    assert not harness.state_file.exists()

    payload = harness.extract()
    assert payload["mode"] == "full"
    assert [e["path"] for e in payload["files"]] == ["rules/a.md"]


def test_corrupt_state_file_falls_back_to_full_with_warning(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha\n")
    harness.commit_all(repo, "v1")
    harness.config_for_repo(repo, ["rules/a.md"])
    harness.state_file.write_text("{ corrupt json !!!", encoding="utf-8")

    payload = harness.extract()
    assert payload["mode"] == "full"
    assert payload.get(
        "state_warning"
    ), "a corrupt state file must be reported via state_warning"
    assert [e["path"] for e in payload["files"]] == ["rules/a.md"]


def test_state_with_wrong_shape_falls_back_to_full_with_warning(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha\n")
    harness.commit_all(repo, "v1")
    harness.config_for_repo(repo, ["rules/a.md"])
    harness.state_file.write_text('["not", "a", "state"]', encoding="utf-8")

    payload = harness.extract()
    assert payload["mode"] == "full"
    assert payload.get("state_warning")
