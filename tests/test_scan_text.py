"""``scan-text``: generic secret-pattern detection without value leakage.

Acceptance criteria covered:
- dummy secret-like text (email address, private-key header, token-like
  string, IP address, phone-like digits, API-key assignment) is a hit;
- the output contains only ``pattern_id`` and ``line_no`` -- never the
  matched value itself;
- clean text is not a hit;
- patterns from ``local_patterns_file`` (one regex per line, blank lines
  and ``#`` comments ignored) are applied in addition.
"""

from __future__ import annotations

import pytest

# All values below are inert dummies (documentation ranges / obvious
# placeholders), never real credentials or personal data.
SECRET_CASES = [
    # (id, secret text placed on line 3, value that must not leak)
    ("email", "contact: dummy.user@example.invalid", "dummy.user@example.invalid"),
    (
        "private_key_header",
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----",
    ),
    (
        "token_like",
        "token: abcdefghijklmnopqrstuvwxyz0123456789ABCD",
        "abcdefghijklmnopqrstuvwxyz0123456789ABCD",
    ),
    ("ip_address", "server at 192.0.2.123 responded", "192.0.2.123"),
    ("phone_like", "call 090-0000-0000 please", "090-0000-0000"),
    (
        "api_key_assignment",
        'api_key = "DUMMYKEYVALUE1234567890"',
        "DUMMYKEYVALUE1234567890",
    ),
]


def _write_text(harness, name: str, lines: list[str]):
    path = harness.tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _scan(harness, path):
    return harness.run_json("scan-text", "--file", str(path))


@pytest.fixture(autouse=True)
def _config(harness, simple_repo):
    # scan-text still runs under a normal configuration.
    return simple_repo


@pytest.mark.parametrize(
    "secret_line, secret_value",
    [(line, value) for _id, line, value in SECRET_CASES],
    ids=[case_id for case_id, _line, _value in SECRET_CASES],
)
def test_secret_like_text_is_a_hit_on_the_right_line(
    harness, secret_line, secret_value
):
    path = _write_text(
        harness,
        "target.txt",
        ["an ordinary first line", "nothing to see here", secret_line],
    )
    payload = _scan(harness, path)
    assert payload["hit"] is True
    assert payload["matches"], "a hit must report at least one match"
    for match in payload["matches"]:
        assert set(match) >= {"pattern_id", "line_no"}
        assert isinstance(match["pattern_id"], str) and match["pattern_id"]
        assert isinstance(match["line_no"], int)
    assert 3 in [match["line_no"] for match in payload["matches"]]


@pytest.mark.parametrize(
    "secret_line, secret_value",
    [(line, value) for _id, line, value in SECRET_CASES],
    ids=[case_id for case_id, _line, _value in SECRET_CASES],
)
def test_matched_values_never_appear_in_the_output(harness, secret_line, secret_value):
    path = _write_text(harness, "target.txt", [secret_line])
    result = harness.run_raw("scan-text", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert secret_value not in result.stdout
    assert secret_value not in result.stderr


def test_clean_text_is_not_a_hit(harness):
    path = _write_text(
        harness,
        "clean.md",
        [
            "# Weekly rules",
            "",
            "Run the extraction every Monday morning.",
            "File an issue for each codable rule.",
        ],
    )
    payload = _scan(harness, path)
    assert payload["hit"] is False
    assert payload["matches"] == []


def test_multiple_hits_report_each_line(harness):
    path = _write_text(
        harness,
        "multi.txt",
        [
            "dummy.user@example.invalid",
            "ordinary line",
            "-----BEGIN PRIVATE KEY-----",
        ],
    )
    payload = _scan(harness, path)
    assert payload["hit"] is True
    line_numbers = sorted(match["line_no"] for match in payload["matches"])
    assert line_numbers == [1, 3]


def test_scan_text_on_missing_file_fails(harness):
    result = harness.run_raw(
        "scan-text", "--file", str(harness.tmp_path / "missing.txt")
    )
    assert result.returncode != 0


# ----------------------------------------------------------------------
# local patterns file
# ----------------------------------------------------------------------


def _config_with_local_patterns(harness, repo):
    patterns = harness.tmp_path / "local-patterns.txt"
    patterns.write_text(
        "# a comment line that must be ignored\n" "\n" "PROJECT_CODENAME_[A-Z]+\n",
        encoding="utf-8",
    )
    harness.config_for_repo(repo, ["rules/a.md"], local_patterns_file=patterns)


def test_local_pattern_is_applied(harness, simple_repo):
    _config_with_local_patterns(harness, simple_repo)
    path = _write_text(
        harness,
        "local.txt",
        ["ordinary line", "see PROJECT_CODENAME_FALCON for details"],
    )
    payload = _scan(harness, path)
    assert payload["hit"] is True
    assert 2 in [match["line_no"] for match in payload["matches"]]


def test_local_pattern_value_does_not_leak(harness, simple_repo):
    _config_with_local_patterns(harness, simple_repo)
    path = _write_text(harness, "local.txt", ["see PROJECT_CODENAME_FALCON here"])
    result = harness.run_raw("scan-text", "--file", str(path))
    assert result.returncode == 0, result.stderr
    assert "PROJECT_CODENAME_FALCON" not in result.stdout


def test_text_matching_only_a_commented_local_pattern_is_clean(harness, simple_repo):
    # The ``#`` line in the patterns file is a comment, not a pattern, and
    # the codename pattern does not occur here, so this must stay clean.
    _config_with_local_patterns(harness, simple_repo)
    path = _write_text(
        harness,
        "local-clean.txt",
        ["# a comment line that must be ignored"],
    )
    payload = _scan(harness, path)
    assert payload["hit"] is False
    assert payload["matches"] == []
