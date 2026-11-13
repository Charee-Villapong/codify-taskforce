"""``state-commit``: re-proposal suppression, retained fields, atomicity.

Acceptance criteria covered:
- a section recorded as judged comes back with ``previously_judged`` on
  the next extract; once its ``section_hash`` changes it returns to null;
- the state file retains only the whitelisted items (per-repo
  ``last_success_commit``, per-file hashes, section records, registry) --
  extra input keys such as body text or judgement reasons are never
  persisted, verified also by searching the raw state file text;
- state writes are atomic in effect: the state file is always complete,
  parseable JSON, and a failed update leaves the previous state intact.
"""

from __future__ import annotations

import json

import pytest

BODY_MARKER = "UNIQUE_BODY_MARKER_9f27c1"
REASON_MARKER = "UNIQUE_REASON_MARKER_41bd08"
SUMMARY_MARKER = "UNIQUE_SUMMARY_MARKER_77aa10"

FORBIDDEN_KEYS = {
    "body",
    "body_text",
    "content",
    "full_text",
    "reason",
    "judgement_reason",
    "judgment_reason",
    "summary",
}


def _all_keys(node) -> set:
    keys: set = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            keys |= _all_keys(value)
    elif isinstance(node, list):
        for value in node:
            keys |= _all_keys(value)
    return keys


def _judged(section: dict, **overrides) -> dict:
    record = {
        "repo": "/tmp/dummy-repo",  # replaced by caller where needed
        "path": "rules/doc.md",
        "heading": section["heading"],
        "section_hash": section["section_hash"],
        "verdict_code": "codable",
        "issue_number": 101,
        "guard": True,
    }
    record.update(overrides)
    return record


def _repo_with_two_sections(harness):
    repo = harness.init_repo()
    harness.write_file(
        repo,
        "rules/doc.md",
        "# Doc\n\nintro\n\n## Keep\n\nkeep body v1\n\n" "## Change\n\nchange body v1\n",
    )
    base_commit = harness.commit_all(repo, "v1")
    harness.config_for_repo(repo, ["rules/doc.md"])
    return repo, base_commit


def _judge_keep_section_and_reopen_file(harness, repo, base_commit):
    """Judge section 'Doc > Keep', then commit a change to another section.

    The second commit puts the file back into the diff (so its sections
    are extracted again) while the judged section itself is untouched.
    """
    keep = harness.section("rules/doc.md", "Doc > Keep")
    record = _judged(keep, repo=str(repo))
    payload = harness.state_commit([record], {str(repo): base_commit})
    assert payload["ok"] is True

    harness.write_file(
        repo,
        "rules/doc.md",
        "# Doc\n\nintro\n\n## Keep\n\nkeep body v1\n\n" "## Change\n\nchange body v2\n",
    )
    harness.commit_all(repo, "change the other section")
    return keep


# ----------------------------------------------------------------------
# state-commit result shape
# ----------------------------------------------------------------------


def test_state_commit_reports_counts_and_advanced_repos(harness):
    repo, base_commit = _repo_with_two_sections(harness)
    keep = harness.section("rules/doc.md", "Doc > Keep")
    payload = harness.state_commit(
        [_judged(keep, repo=str(repo))], {str(repo): base_commit}
    )
    assert payload["ok"] is True
    assert payload["judged_count"] == 1
    assert payload["advanced_repos"] == [str(repo)]


def test_state_commit_with_no_judgements_still_advances(harness):
    repo, base_commit = _repo_with_two_sections(harness)
    payload = harness.state_commit([], {str(repo): base_commit})
    assert payload["ok"] is True
    assert payload["judged_count"] == 0
    assert payload["advanced_repos"] == [str(repo)]


# ----------------------------------------------------------------------
# re-proposal suppression (previously_judged)
# ----------------------------------------------------------------------


def test_judged_section_gets_previously_judged_on_next_extract(harness):
    repo, base_commit = _repo_with_two_sections(harness)
    _judge_keep_section_and_reopen_file(harness, repo, base_commit)

    keep = harness.section("rules/doc.md", "Doc > Keep")
    assert keep["previously_judged"] == {
        "verdict_code": "codable",
        "issue_number": 101,
    }
    # The changed, never-judged sibling section stays unjudged.
    changed = harness.section("rules/doc.md", "Doc > Change")
    assert changed["previously_judged"] is None


def test_previously_judged_resets_to_null_when_section_hash_changes(
    harness,
):
    repo, base_commit = _repo_with_two_sections(harness)
    _judge_keep_section_and_reopen_file(harness, repo, base_commit)
    assert (
        harness.section("rules/doc.md", "Doc > Keep")["previously_judged"] is not None
    )

    harness.write_file(
        repo,
        "rules/doc.md",
        "# Doc\n\nintro\n\n## Keep\n\nkeep body v2 (edited)\n\n"
        "## Change\n\nchange body v2\n",
    )
    harness.commit_all(repo, "edit the judged section itself")

    keep = harness.section("rules/doc.md", "Doc > Keep")
    assert keep["previously_judged"] is None


@pytest.mark.parametrize("verdict_code", ["codable", "not_codable", "needs_user"])
def test_all_specified_verdict_codes_are_recorded(harness, verdict_code):
    repo, base_commit = _repo_with_two_sections(harness)
    keep = harness.section("rules/doc.md", "Doc > Keep")
    record = _judged(
        keep,
        repo=str(repo),
        verdict_code=verdict_code,
        issue_number=None,
    )
    payload = harness.state_commit([record], {str(repo): base_commit})
    assert payload["ok"] is True

    harness.write_file(
        repo,
        "rules/doc.md",
        "# Doc\n\nintro\n\n## Keep\n\nkeep body v1\n\n" "## Change\n\nchange body v2\n",
    )
    harness.commit_all(repo, "reopen file")
    judged = harness.section("rules/doc.md", "Doc > Keep")["previously_judged"]
    assert judged is not None
    assert judged["verdict_code"] == verdict_code


def test_unknown_verdict_code_is_rejected_and_state_unchanged(harness):
    repo, base_commit = _repo_with_two_sections(harness)
    keep = harness.section("rules/doc.md", "Doc > Keep")
    harness.seed_state_at_head(repo)
    before = harness.state_file.read_bytes()

    results = harness.write_json_input(
        "bad-results.json",
        {
            "judged": [_judged(keep, repo=str(repo), verdict_code="maybe")],
            "advance_commits": {},
        },
    )
    result = harness.run_raw("state-commit", "--file", str(results))
    assert result.returncode != 0
    assert harness.state_file.read_bytes() == before


# ----------------------------------------------------------------------
# retained fields are limited
# ----------------------------------------------------------------------


def test_extra_input_fields_are_not_persisted_in_state(harness):
    repo, base_commit = _repo_with_two_sections(harness)
    keep = harness.section("rules/doc.md", "Doc > Keep")
    record = _judged(
        keep,
        repo=str(repo),
        body=BODY_MARKER,
        body_text=BODY_MARKER,
        content=BODY_MARKER,
        reason=REASON_MARKER,
        judgement_reason=REASON_MARKER,
        summary=SUMMARY_MARKER,
    )
    results = harness.write_json_input(
        "results-with-extras.json",
        {
            "judged": [record],
            "advance_commits": {str(repo): base_commit},
            "notes": SUMMARY_MARKER,
        },
    )
    payload = harness.run_json("state-commit", "--file", str(results))
    assert payload["ok"] is True

    raw = harness.state_file.read_text(encoding="utf-8")
    for marker in (BODY_MARKER, REASON_MARKER, SUMMARY_MARKER):
        assert (
            marker not in raw
        ), "non-whitelisted input text must never reach the state file"
    state = json.loads(raw)
    assert not (
        _all_keys(state) & FORBIDDEN_KEYS
    ), f"state contains forbidden keys: {_all_keys(state) & FORBIDDEN_KEYS}"


def test_state_file_records_the_advanced_commit_behaviourally(harness):
    # After advancing to HEAD, a diff extract finds nothing new -- this
    # proves last_success_commit was stored, without depending on the
    # state file's internal key names.
    repo, _base = _repo_with_two_sections(harness)
    harness.seed_state_at_head(repo)
    payload = harness.extract()
    assert payload["mode"] == "diff"
    assert payload["files"] == []


# ----------------------------------------------------------------------
# atomicity / robustness
# ----------------------------------------------------------------------


def test_state_file_is_complete_valid_json_after_every_commit(harness):
    repo, base_commit = _repo_with_two_sections(harness)
    for index in range(3):
        # セクションの取り直しは --full で行う。diff モードは新規コミット
        # が無ければ判定記録のあるファイルも出さない（Issue #30 決定
        # 2026-11-13）ため、ここでは全件走査で取り直す。
        full = harness.run_json("extract", "--full")
        keep = next(
            s
            for e in full["files"]
            if e["path"] == "rules/doc.md"
            for s in e["sections"]
            if s["heading"] == "Doc > Keep"
        )
        harness.state_commit(
            [_judged(keep, repo=str(repo), issue_number=100 + index)],
            {str(repo): base_commit},
        )
        # Must parse as one complete JSON document every single time.
        state = json.loads(harness.state_file.read_text(encoding="utf-8"))
        assert isinstance(state, dict)


def test_failed_state_commit_leaves_previous_state_intact(harness):
    repo, base_commit = _repo_with_two_sections(harness)
    harness.seed_state_at_head(repo)
    before = harness.state_file.read_bytes()

    broken = harness.tmp_path / "broken-results.json"
    broken.write_text("{ not json", encoding="utf-8")
    result = harness.run_raw("state-commit", "--file", str(broken))
    assert result.returncode != 0
    assert harness.state_file.read_bytes() == before
    json.loads(harness.state_file.read_text(encoding="utf-8"))


def test_state_commit_with_missing_input_file_fails(harness):
    missing = harness.tmp_path / "no-results.json"
    result = harness.run_raw("state-commit", "--file", str(missing))
    assert result.returncode != 0
    assert not harness.state_file.exists()
