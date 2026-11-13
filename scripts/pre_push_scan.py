#!/usr/bin/env python3
"""push 前検閲スクリプト。

コミット対象ファイルに個人情報・接続情報・認証情報が混入していないかを
機械的にスキャンし、ヒットがあれば終了コード 1 で止める。
汎用パターンのみを本スクリプトに持ち、個人特定パターンは gitignore 対象の
ローカルパターンファイル（scripts/pre_push_patterns.local.txt、
1 行 1 正規表現）から読む。ヒット箇所の実値は出力せず、ファイル名・行番号・
パターン名のみを出す。

テスト配下のダミー値は検閲の対象外とする。tests/ のフィクスチャは
秘匿風文字列を意図的に含む（scan-text のテスト対象そのもの）ため、
明らかに合成値であることが分かる行だけを _DUMMY_MARKERS で除外する。
実値（本物のメール・電話番号・APIキー等）にはマーカーが付かないため、
混入は別途検出される。PEMヘッダのパターンは行頭に限定する
（本物の秘密鍵ファイルではヘッダは行頭にある。テストの
Pythonリテラル内に埋め込まれたヘッダは行頭に無い）。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_PATTERNS = Path(__file__).resolve().parent / "pre_push_patterns.local.txt"
SELF_PATH = Path(__file__).resolve()

GENERIC_PATTERNS: list[tuple[str, str]] = [
    ("email", r"[\w.+-]+@[\w-]+\.[\w.]+"),
    ("phone", r"\b0\d{1,4}-\d{1,4}-\d{4}\b"),
    ("private-key", r"^\s*-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    (
        "credential-assignment",
        r"(?i)(password|passwd|api[_-]?key|secret|token|credential)"
        r"\s*[:=]\s*[\"']?\S+",
    ),
    ("github-token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ("aws-access-key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("slack-token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ("bearer", r"(?i)Bearer\s+[A-Za-z0-9._-]{16,}"),
    ("ssh-fingerprint", r"\bSHA256:[A-Za-z0-9+/=]{30,}\b"),
    ("ipv4-private-context", r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
]

_EXCLUDED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".pyc"}

# 明らかに合成値である行の目印（小文字で比較する）。
# RFC 2606 の文書用ドメイン、RFC 5737 の文書用IP範囲、
# 全ゼロの電話番号プレースホルダ、"dummy" を含む値、連番文字列。
_DUMMY_MARKERS = (
    "example.invalid",
    "192.0.2.",
    "198.51.100.",
    "203.0.113.",
    "0000-0000",
    "dummy",
    "abcdefghijklmnopqrstuvwxyz",
    "0123456789",
)


def _is_dummy_line(line: str, extra_markers: tuple[str, ...] = ()) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in _DUMMY_MARKERS + extra_markers)


def _public_remote_markers() -> tuple[str, ...]:
    """リモートの公開識別子（オーナー名・リポジトリ名）。

    リポジトリ自身の公開情報は漏洩ではないため検閲の対象外とする。
    テストのフィクスチャが仕様書から写した公開リポジトリ識別子を
    含む場合も、ここで除外される。
    """
    completed = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    url = completed.stdout.strip()
    match = re.search(r"github\.com[/:]([^/\s]+)/([^/\s.]+)", url)
    if not match:
        return ()
    owner, repo = match.group(1), match.group(2)
    return (owner.lower(), f"{owner}/{repo}".lower())


def _tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return [
            p
            for p in REPO_ROOT.rglob("*")
            if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts
        ]
    return [REPO_ROOT / line for line in completed.stdout.splitlines() if line]


def _load_local_patterns() -> list[tuple[str, str]]:
    if not LOCAL_PATTERNS.is_file():
        return []
    patterns = []
    for line in LOCAL_PATTERNS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(("local-pattern", line))
    return patterns


def main() -> int:
    patterns = GENERIC_PATTERNS + _load_local_patterns()
    compiled = [(name, re.compile(pat)) for name, pat in patterns]
    public_markers = _public_remote_markers()
    hits = 0
    for path in _tracked_files():
        if path.resolve() == SELF_PATH:
            continue
        if path.suffix.lower() in _EXCLUDED_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relative = path.relative_to(REPO_ROOT)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _is_dummy_line(line, public_markers):
                continue
            for name, regex in compiled:
                if regex.search(line):
                    print(f"HIT: {relative}:{lineno} pattern={name}")
                    hits += 1
    if hits:
        print(f"検閲で {hits} 件ヒットしました。push を止めて内容を確認してください。")
        return 1
    print("検閲を通過しました（ヒット 0 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
