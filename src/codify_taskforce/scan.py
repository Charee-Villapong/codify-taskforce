"""秘匿パターンのテキスト走査。値は出力に含めない。"""

from __future__ import annotations

import re
from pathlib import Path

# 汎用パターン（job-candidate-list の pre_push_scan.py の
# GENERIC_PATTERNS と同等の定義。個人特定パターンは含めない）。
GENERIC_PATTERNS: list[tuple[str, str]] = [
    ("email", r"[\w.+-]+@[\w-]+\.[\w.]+"),
    ("phone", r"\b0\d{1,4}-\d{1,4}-\d{4}\b"),
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    (
        "credential-assignment",
        r"(?i)(password|passwd|api[_-]?key|secret|token|credential)"
        r"\s*[:=]\s*[\"']?\S+",
    ),
    (
        "ipv4",
        r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)" r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b",
    ),
]

LOCAL_PATTERN_ID = "local-pattern"


class ScanError(ValueError):
    """走査対象やパターンが読めない場合のエラー。"""


def load_local_patterns(path: str | Path | None) -> list[tuple[str, str]]:
    """ローカルパターンファイル（1行1正規表現）を読み込む。

    空行と # で始まる行は無視する。ファイルが無ければ空リスト。
    不正な正規表現があればエラーにする。
    """
    if not path:
        return []
    pattern_file = Path(path)
    if not pattern_file.exists():
        raise ScanError(f"ローカルパターンファイルがありません: {path}")
    patterns: list[tuple[str, str]] = []
    for line in pattern_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            re.compile(stripped)
        except re.error as exc:
            raise ScanError(f"ローカルパターンが不正です: {stripped!r}: {exc}") from exc
        patterns.append((LOCAL_PATTERN_ID, stripped))
    return patterns


def compile_patterns(
    local_patterns: list[tuple[str, str]],
) -> list[tuple[str, "re.Pattern[str]"]]:
    return [
        (pattern_id, re.compile(pattern))
        for pattern_id, pattern in GENERIC_PATTERNS + local_patterns
    ]


def scan_text(
    path: str | Path, local_patterns: list[tuple[str, str]] | None = None
) -> dict:
    """テキストファイルを走査し、ヒットしたパターンを行番号つきで返す。

    戻り値は {"hit": bool, "matches": [{"pattern_id", "line_no"}]}。
    マッチした値そのものは出力に含めない。
    """
    target = Path(path)
    if not target.is_file():
        raise ScanError(f"走査対象ファイルがありません: {path}")
    compiled = compile_patterns(local_patterns or [])
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ScanError(f"走査対象ファイルが読めません: {exc}") from exc
    matches: list[dict] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern_id, regex in compiled:
            if regex.search(line):
                matches.append({"pattern_id": pattern_id, "line_no": line_no})
    return {"hit": bool(matches), "matches": matches}
