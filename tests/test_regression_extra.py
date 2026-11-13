"""実装係による追加の回帰テスト。

先行テスト（テスト係作）には無いが、週次ワークフローが依存する挙動を固定する。
- ``extract --full``: 状態があっても全件走査する（週次ワークフローの初回全件用）。
- diff モードの範囲は「コミット済み差分に含まれる allowlist ファイルだけ」
  （Issue #30 決定コメント 2026-11-13）。判定記録の有無は範囲に影響せず、
  再提示・再起票の抑制は ``previously_judged`` が担う。
"""

from __future__ import annotations


def test_extract_full_flag_forces_full_mode_even_with_state(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha v1\n")
    harness.write_file(repo, "rules/b.md", "# Beta\n\nbeta v1\n")
    harness.commit_all(repo, "v1")
    harness.config_for_repo(repo, ["rules/a.md", "rules/b.md"])
    harness.seed_state_at_head(repo)

    # 状態があっても --full なら全件走査。
    payload = harness.run_json("extract", "--full")
    assert payload["mode"] == "full"
    assert sorted(e["path"] for e in payload["files"]) == [
        "rules/a.md",
        "rules/b.md",
    ]


def test_extract_full_flag_still_reports_previously_judged(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha v1\n")
    harness.commit_all(repo, "v1")
    harness.config_for_repo(repo, ["rules/a.md"])
    harness.seed_state_at_head(repo)

    keep = harness.run_json("extract", "--full")["files"][0]["sections"][0]
    harness.state_commit(
        [
            {
                "repo": str(repo),
                "path": "rules/a.md",
                "heading": keep["heading"],
                "section_hash": keep["section_hash"],
                "verdict_code": "codable",
                "issue_number": 7,
                "guard": False,
            }
        ],
        {str(repo): harness.head(repo)},
    )

    payload = harness.run_json("extract", "--full")
    section = payload["files"][0]["sections"][0]
    assert section["previously_judged"] == {
        "verdict_code": "codable",
        "issue_number": 7,
    }


def test_diff_mode_with_no_judged_records_returns_only_the_changed_file(
    harness,
):
    """diff モードは判定記録なしの状態で、変更された1ファイルだけを返す。

    Issue #30 決定コメント（2026-11-13）により、diff モードの範囲は
    「コミット済み差分に含まれる allowlist ファイルだけ」となった。
    旧テストは「判定記録のあるファイルが新規コミット無しでも extract に
    出る」ことを前提としていたため、仕様に合わせて書き換えた。
    """
    repo = harness.init_repo()
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha v1\n")
    harness.write_file(repo, "rules/b.md", "# Beta\n\nbeta v1\n")
    harness.commit_all(repo, "v1")
    harness.config_for_repo(repo, ["rules/a.md", "rules/b.md"])
    harness.seed_state_at_head(repo)

    # 新規コミット無し・判定記録無しでは空。
    assert harness.extract()["files"] == []

    # b.md だけを変更してコミットする。
    harness.write_file(repo, "rules/b.md", "# Beta\n\nbeta v2\n")
    harness.commit_all(repo, "beta v2")

    payload = harness.extract()
    assert payload["mode"] == "diff"
    assert [e["path"] for e in payload["files"]] == ["rules/b.md"]


def test_diff_mode_does_not_include_judged_file_without_new_commit(harness):
    """判定記録だけでは diff に出ない。変更されたファイルだけが対象。

    判定記録のある a.md に新規コミットが無ければ diff には含まれない。
    再提示・再起票の抑制は previously_judged が担うため、範囲への
    含め直しは不要（Issue #30 決定コメント 2026-11-13）。
    """
    repo = harness.init_repo()
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha v1\n")
    harness.write_file(repo, "rules/b.md", "# Beta\n\nbeta v1\n")
    harness.commit_all(repo, "v1")
    harness.config_for_repo(repo, ["rules/a.md", "rules/b.md"])
    harness.seed_state_at_head(repo)

    # a.md のセクションを判定して状態へ記録する（新規コミットは無し）。
    full = harness.run_json("extract", "--full")
    section = next(
        s for e in full["files"] if e["path"] == "rules/a.md" for s in e["sections"]
    )
    harness.state_commit(
        [
            {
                "repo": str(repo),
                "path": "rules/a.md",
                "heading": section["heading"],
                "section_hash": section["section_hash"],
                "verdict_code": "not_codable",
                "issue_number": None,
                "guard": False,
            }
        ],
        {str(repo): harness.head(repo)},
    )

    # 新規コミットが無いので、判定記録があっても diff は空。
    payload = harness.extract()
    assert payload["mode"] == "diff"
    assert payload["files"] == []

    # b.md だけを変更してコミットすると、b.md だけが出る。
    harness.write_file(repo, "rules/b.md", "# Beta\n\nbeta v2\n")
    harness.commit_all(repo, "beta v2")
    payload = harness.extract()
    assert [e["path"] for e in payload["files"]] == ["rules/b.md"]
