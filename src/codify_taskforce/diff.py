"""git管理下のリポジトリの差分取得。コミット済みの差分だけが対象。"""

from __future__ import annotations

import subprocess


def head_commit(repo: str) -> str | None:
    """HEAD のコミットハッシュ。取得できなければ None。"""
    result = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def changed_files(repo: str, base_commit: str) -> list[str] | None:
    """base_commit と HEAD の間のコミット済み差分に含まれるパス一覧。

    未コミット（作業ツリー・ステージ済み）の変更は対象にならない。
    git が失敗した場合は None を返す。
    """
    result = subprocess.run(
        ["git", "-C", repo, "diff", "--name-only", base_commit, "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line]
