"""Cross-cutting CLI contract: package importable, JSON on stdout, exit 0.

Error behaviour for the *basic* abnormal cases (missing / malformed /
empty configuration) is also pinned here.  Error exit codes are only
specified negatively in the spec ("success is exit 0"), so these tests
require a non-zero exit rather than a specific code.
"""

from __future__ import annotations

import json


def test_package_is_importable():
    # Imported inside the test (not at module level) so that collection
    # still succeeds while the implementation does not exist yet.
    import codify_taskforce

    assert codify_taskforce is not None


def test_extract_emits_single_json_object_on_stdout(harness, simple_repo):
    result = harness.run_raw("extract")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)  # whole stdout must be one JSON doc
    assert isinstance(payload, dict)
    assert payload["mode"] in ("full", "diff")
    assert isinstance(payload["files"], list)


def test_extract_emits_limits_from_config(harness, simple_repo):
    """``extract`` の出力に設定ファイルの上限値が limits として入る。

    週次ワークフローは起票上限をこの値から読む（フォールバック無し）。
    """
    payload = harness.extract()
    assert payload["limits"] == {
        "weekly_issue_cap": 3,
        "daily_presentation_cap": 2,
    }


def test_extract_limits_weekly_cap_null_means_unlimited(harness):
    """``weekly_issue_cap`` が null の設定では limits にも null が入る。

    本人の決定で週あたり起票上限は撤廃された。設定変数は残すが、
    null の場合は無制限としてワークフローへそのまま渡る（数値への
    フォールバックはしない）。
    """
    repo = harness.init_repo()
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha\n")
    harness.commit_all(repo, "v1")
    harness.config_for_repo(
        repo,
        ["rules/a.md"],
        weekly_issue_cap=None,
        daily_presentation_cap=4,
    )

    payload = harness.extract()
    assert payload["limits"] == {
        "weekly_issue_cap": None,
        "daily_presentation_cap": 4,
    }


def test_extract_limits_follow_custom_config_values(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha\n")
    harness.commit_all(repo, "v1")
    harness.config_for_repo(
        repo,
        ["rules/a.md"],
        weekly_issue_cap=5,
        daily_presentation_cap=4,
    )

    payload = harness.extract()
    assert payload["limits"] == {
        "weekly_issue_cap": 5,
        "daily_presentation_cap": 4,
    }


def test_hash_files_emits_json_object_on_stdout(harness, simple_repo):
    payload = harness.run_json("hash-files")
    assert isinstance(payload["files"], list)


def test_registry_list_emits_json_object_on_stdout(harness, simple_repo):
    payload = harness.run_json("registry-list")
    assert isinstance(payload["entries"], list)


def test_queue_list_emits_json_object_on_stdout(harness, simple_repo):
    payload = harness.run_json("queue-list", "--date", "2026-11-13")
    assert payload["items"] == []
    assert payload["total_pending"] == 0


def test_missing_config_env_var_fails(harness, simple_repo):
    result = harness.run_raw("extract", config_path=None)
    assert result.returncode != 0


def test_nonexistent_config_file_fails(harness, simple_repo):
    missing = harness.tmp_path / "no-such-config.json"
    result = harness.run_raw("extract", config_path=missing)
    assert result.returncode != 0


def test_malformed_config_json_fails(harness, simple_repo):
    bad = harness.tmp_path / "bad-config.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    result = harness.run_raw("extract", config_path=bad)
    assert result.returncode != 0


def test_empty_config_file_fails(harness, simple_repo):
    empty = harness.tmp_path / "empty-config.json"
    empty.write_text("", encoding="utf-8")
    result = harness.run_raw("extract", config_path=empty)
    assert result.returncode != 0


def test_empty_config_object_fails(harness, simple_repo):
    # ``{}`` lacks every required key (allowlist, state_file, ...), so it
    # cannot be treated as a usable configuration.
    empty_obj = harness.tmp_path / "empty-object-config.json"
    empty_obj.write_text("{}", encoding="utf-8")
    result = harness.run_raw("extract", config_path=empty_obj)
    assert result.returncode != 0


def test_unknown_command_fails(harness, simple_repo):
    result = harness.run_raw("no-such-command")
    assert result.returncode != 0
