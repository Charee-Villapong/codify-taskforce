"""``extract`` in full mode: allowlist handling and denylist handling.

Acceptance criteria covered:
- with no state, extract is a full scan of exactly the allowlisted files;
- the allowlist never expands itself (other files in the same repository
  are never returned);
- denylisted paths are excluded by path alone, before the file content is
  opened, and never appear in the result at all.
"""

from __future__ import annotations


def test_no_state_means_full_mode(harness, simple_repo):
    payload = harness.extract()
    assert payload["mode"] == "full"


def test_full_mode_lists_exactly_the_allowlisted_files(
    harness, simple_repo
):
    payload = harness.extract()
    paths = sorted(entry["path"] for entry in payload["files"])
    assert paths == ["rules/a.md", "rules/b.md"]
    for entry in payload["files"]:
        assert entry["repo"] == str(simple_repo)
        assert entry["abs_path"] == str(simple_repo / entry["path"])
        assert isinstance(entry["sections"], list)
        assert entry["sections"], "allowlisted file should have sections"


def test_first_extract_has_no_previously_judged(harness, simple_repo):
    for entry in harness.extracted_files():
        for section in entry["sections"]:
            assert section["previously_judged"] is None


def test_allowlist_does_not_expand_to_other_repo_files(
    harness, simple_repo
):
    # A file that lives in the same repository but is not allowlisted.
    harness.write_file(simple_repo, "rules/extra.md", "# Extra\n\nx\n")
    harness.write_file(simple_repo, "notes.md", "# Notes\n\nx\n")
    harness.commit_all(simple_repo, "add non-allowlisted files")

    paths = [entry["path"] for entry in harness.extracted_files()]
    assert "rules/extra.md" not in paths
    assert "notes.md" not in paths
    assert sorted(paths) == ["rules/a.md", "rules/b.md"]


def test_allowlisted_file_with_no_headings_is_still_listed(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "plain.md", "just some text, no headings\n")
    harness.commit_all(repo, "plain")
    harness.config_for_repo(repo, ["plain.md"])

    entry = harness.extracted_file("plain.md")
    assert entry["path"] == "plain.md"
    assert isinstance(entry["sections"], list)


# ----------------------------------------------------------------------
# denylist
# ----------------------------------------------------------------------

def test_denylist_exact_relative_path_is_excluded(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "rules/keep.md", "# Keep\n\nkeep\n")
    harness.write_file(repo, "rules/secret.md", "# Secret\n\nsecret\n")
    harness.commit_all(repo, "two files")
    harness.config_for_repo(
        repo,
        ["rules/keep.md", "rules/secret.md"],
        denylist=["rules/secret.md"],
    )

    paths = [entry["path"] for entry in harness.extracted_files()]
    assert paths == ["rules/keep.md"]


def test_denylist_path_segment_matches_at_any_depth(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "rules/keep.md", "# Keep\n\nkeep\n")
    harness.write_file(repo, "private/one.md", "# One\n\n1\n")
    harness.write_file(repo, "docs/private/two.md", "# Two\n\n2\n")
    harness.commit_all(repo, "files")
    harness.config_for_repo(
        repo,
        ["rules/keep.md", "private/one.md", "docs/private/two.md"],
        denylist=["private"],
    )

    paths = [entry["path"] for entry in harness.extracted_files()]
    assert paths == ["rules/keep.md"]


def test_denylist_segment_does_not_match_partial_names(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "rules/private-notes.md", "# Notes\n\nn\n")
    harness.write_file(repo, "privately/keep.md", "# Keep\n\nk\n")
    harness.commit_all(repo, "files")
    harness.config_for_repo(
        repo,
        ["rules/private-notes.md", "privately/keep.md"],
        denylist=["private"],
    )

    paths = sorted(entry["path"] for entry in harness.extracted_files())
    assert paths == ["privately/keep.md", "rules/private-notes.md"]


def test_denylisted_file_content_is_never_opened(harness):
    """A denylisted file with undecodable bytes must not break extract.

    If the implementation opened/decoded the content before applying the
    denylist, the invalid UTF-8 below would make the scan fail; applying
    the denylist by path alone makes it succeed and omit the file.
    """
    repo = harness.init_repo()
    harness.write_file(repo, "rules/keep.md", "# Keep\n\nkeep\n")
    harness.write_file(repo, "rules/blocked.md", b"\xff\xfe\x00\x01binary")
    harness.commit_all(repo, "files")
    harness.config_for_repo(
        repo,
        ["rules/keep.md", "rules/blocked.md"],
        denylist=["rules/blocked.md"],
    )

    payload = harness.extract()  # must not raise / exit non-zero
    paths = [entry["path"] for entry in payload["files"]]
    assert paths == ["rules/keep.md"]
