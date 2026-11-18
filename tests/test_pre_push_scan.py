"""push 前検閲（scripts/pre_push_scan.py）のダミー判定。

ダミー除外は行単位ではなく、パターンにマッチした値そのものに対して
行う: ダミーと同じ行に本物の値があれば検出されなければならない。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "pre_push_scan.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pre_push_scan", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scan = _load_module()
COMPILED = [(name, re.compile(pattern)) for name, pattern in scan.GENERIC_PATTERNS]

# 合成したトークン風の値。実行時に組み立てるため、このファイルの
# ソース上ではトークンのパターンに一致しない（検閲を素通りさせる
# ためではなく、フィクスチャ自体を本物と誤認させないため）。
SYNTHETIC_VALUE = "ghp_" + "Ab3" * 12


def test_dummy_value_alone_is_not_a_hit():
    line = "contact: dummy.user@example.invalid"
    assert scan._line_hit_patterns(line, COMPILED) == []


def test_real_secret_on_a_line_with_dummy_text_is_a_hit():
    # 行に "dummy" が含まれていても、本物の値のマッチは検出する。
    line = f"dummy example, but a real-looking token follows: {SYNTHETIC_VALUE}"
    assert "github-token" in scan._line_hit_patterns(line, COMPILED)


def test_public_identifier_match_is_not_a_hit():
    compiled = [("owner-name", re.compile(r"some-owner"))]
    markers = ("some-owner", "some-owner/some-repo")
    line = "see some-owner/some-repo for details"
    assert scan._line_hit_patterns(line, compiled, markers) == []


def test_public_identifier_does_not_mask_a_real_secret_on_the_same_line():
    line = f"some-owner issued token {SYNTHETIC_VALUE}"
    hits = scan._line_hit_patterns(line, COMPILED, ("some-owner",))
    assert "github-token" in hits
