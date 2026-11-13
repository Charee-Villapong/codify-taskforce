"""実装係による追加の回帰テスト。

先行テスト（テスト係作）には無いが、週次ワークフローが依存する挙動を固定する。
- ``extract --full``: 状態があっても全件走査する（週次ワークフローの初回全件用）。
- diff モードで判定記録を持つファイルが含まれること
  （判定履歴の可視化。previously_judged による再提示の抑制と対になる）。
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


def test_diff_mode_includes_files_with_judged_sections(harness):
    repo = harness.init_repo()
    harness.write_file(repo, "rules/a.md", "# Alpha\n\nalpha v1\n")
    harness.write_file(repo, "rules/b.md", "# Beta\n\nbeta v1\n")
    harness.commit_all(repo, "v1")
    harness.config_for_repo(repo, ["rules/a.md", "rules/b.md"])
    harness.seed_state_at_head(repo)

    # 新規コミット無し・判定記録無しでは空。
    assert harness.extract()["files"] == []

    # a.md のセクションを判定して状態へ記録する（新規コミットは無し）。
    # diff では空のため full で取り直す。
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

    # 新規コミットが無くても、判定記録を持つ a.md は diff に含まれる。
    # b.md（判定記録なし・変更なし）は含まれない。
    payload = harness.extract()
    assert payload["mode"] == "diff"
    assert [e["path"] for e in payload["files"]] == ["rules/a.md"]
    judged = payload["files"][0]["sections"][0]["previously_judged"]
    assert judged == {"verdict_code": "not_codable", "issue_number": None}
