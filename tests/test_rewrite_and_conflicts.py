"""書き換えケースの種別・判定と、同一セクションへの判定衝突の扱い。

Issue #30 初回実行（2026-11-13）で見つかった設計と指示のズレ2点の仕様:

- ズレ1: 書き換え候補がコード化の物差しで判定され ``not_codable`` に
  なり、起票も繰り越しもされなかった。書き換え候補には専用の判定
  ``rewritable``（書き換え可）を持たせ、判定記録には種別 ``kind``
  （codable / rewrite / contradiction）を残す。
- ズレ2: 同一セクションに codable と not_codable の2判定が入り、
  state-commit の上書きで codable 側が黙って消えた。同一バッチ内に
  同一キーで複数の判定があれば、state-commit は優先順位
  （起票済み > 可・未起票 > 要・本人確認 > 不可）で1件にマージ
  して記録する（未起票の「可」が「不可」に消されるのを防ぐ）。
  ワークフロー側のゲートは上流で衝突を「要・本人確認」へ畳む。
"""

from __future__ import annotations

import json


def _repo_with_section(harness):
    repo = harness.init_repo()
    harness.write_file(
        repo,
        "rules/doc.md",
        "# Doc\n\nintro\n\n## Rule\n\nrule body v1\n\n## Other\n\nother body\n",
    )
    base_commit = harness.commit_all(repo, "v1")
    harness.config_for_repo(repo, ["rules/doc.md"])
    return repo, base_commit


def _record(section: dict, repo: str, **overrides) -> dict:
    record = {
        "repo": repo,
        "path": "rules/doc.md",
        "heading": section["heading"],
        "section_hash": section["section_hash"],
        "verdict_code": "codable",
        "issue_number": None,
        "guard": False,
    }
    record.update(overrides)
    return record


def _reopen_file(harness, repo) -> None:
    harness.write_file(
        repo,
        "rules/doc.md",
        "# Doc\n\nintro\n\n## Rule\n\nrule body v1\n\n## Other\n\nother body v2\n",
    )
    harness.commit_all(repo, "reopen file")


# ----------------------------------------------------------------------
# ズレ1: rewritable 判定と種別 kind の保持
# ----------------------------------------------------------------------


def test_rewritable_verdict_is_recorded(harness):
    repo, base_commit = _repo_with_section(harness)
    section = harness.section("rules/doc.md", "Doc > Rule")
    payload = harness.state_commit(
        [
            _record(
                section,
                str(repo),
                verdict_code="rewritable",
                kind="rewrite",
            )
        ],
        {str(repo): base_commit},
    )
    assert payload["ok"] is True

    _reopen_file(harness, repo)
    judged = harness.section("rules/doc.md", "Doc > Rule")["previously_judged"]
    assert judged is not None
    assert judged["verdict_code"] == "rewritable"


def test_kind_is_persisted_in_state_record(harness):
    repo, base_commit = _repo_with_section(harness)
    section = harness.section("rules/doc.md", "Doc > Rule")
    harness.state_commit(
        [
            _record(
                section,
                str(repo),
                verdict_code="rewritable",
                kind="rewrite",
            )
        ],
        {str(repo): base_commit},
    )
    state = json.loads(harness.state_file.read_text(encoding="utf-8"))
    records = [r for r in state["judged"] if r["heading"] == "Doc > Rule"]
    assert len(records) == 1
    assert records[0]["kind"] == "rewrite"
    assert records[0]["verdict_code"] == "rewritable"


def test_unknown_kind_is_rejected_and_state_unchanged(harness):
    repo, base_commit = _repo_with_section(harness)
    section = harness.section("rules/doc.md", "Doc > Rule")
    harness.seed_state_at_head(repo)
    before = harness.state_file.read_bytes()

    results = harness.write_json_input(
        "bad-kind.json",
        {
            "judged": [_record(section, str(repo), kind="other")],
            "advance_commits": {},
        },
    )
    result = harness.run_raw("state-commit", "--file", str(results))
    assert result.returncode != 0
    assert harness.state_file.read_bytes() == before


# ----------------------------------------------------------------------
# ズレ2: 同一キーへの複数判定は優先順位で1件にマージする
# ----------------------------------------------------------------------


def test_conflicting_duplicates_keep_codable_unfiled(harness):
    """初回実行の再現: 同一セクションに codable と not_codable。

    後勝ちの上書きでは codable 側が消えた。マージ優先順位により
    未起票の「可」が残ること（「不可」に消されないこと）。
    """
    repo, base_commit = _repo_with_section(harness)
    section = harness.section("rules/doc.md", "Doc > Rule")
    harness.seed_state_at_head(repo)

    payload = harness.state_commit(
        [
            _record(section, str(repo), verdict_code="codable", kind="codable"),
            _record(
                section,
                str(repo),
                verdict_code="not_codable",
                kind="rewrite",
            ),
        ],
        {str(repo): base_commit},
    )
    assert payload["ok"] is True
    assert payload["judged_count"] == 1

    state = json.loads(harness.state_file.read_text(encoding="utf-8"))
    records = [r for r in state["judged"] if r["heading"] == "Doc > Rule"]
    assert len(records) == 1
    assert records[0]["verdict_code"] == "codable"
    assert records[0]["issue_number"] is None


def test_filed_record_beats_codable_unfiled(harness):
    """起票済みは未起票の「可」より優先して残る（入力順によらない）。"""
    repo, base_commit = _repo_with_section(harness)
    section = harness.section("rules/doc.md", "Doc > Rule")
    harness.seed_state_at_head(repo)

    payload = harness.state_commit(
        [
            _record(section, str(repo), verdict_code="codable", kind="codable"),
            _record(
                section,
                str(repo),
                verdict_code="codable",
                kind="codable",
                issue_number=101,
            ),
        ],
        {str(repo): base_commit},
    )
    assert payload["ok"] is True
    assert payload["judged_count"] == 1

    state = json.loads(harness.state_file.read_text(encoding="utf-8"))
    records = [r for r in state["judged"] if r["heading"] == "Doc > Rule"]
    assert len(records) == 1
    assert records[0]["verdict_code"] == "codable"
    assert records[0]["issue_number"] == 101


def test_same_verdict_duplicates_collapse_to_one_record(harness):
    repo, base_commit = _repo_with_section(harness)
    section = harness.section("rules/doc.md", "Doc > Rule")
    record = _record(section, str(repo), verdict_code="codable", kind="codable")
    payload = harness.state_commit(
        [record, dict(record)],
        {str(repo): base_commit},
    )
    assert payload["ok"] is True

    state = json.loads(harness.state_file.read_text(encoding="utf-8"))
    records = [r for r in state["judged"] if r["heading"] == "Doc > Rule"]
    assert len(records) == 1
