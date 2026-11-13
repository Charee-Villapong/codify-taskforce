"""mdファイルのセクション分割とセクションハッシュ。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$")
_CLOSING_HASHES_RE = re.compile(r"\s+#+\s*$")


@dataclass(frozen=True)
class Section:
    heading: str
    text: str
    section_hash: str


def _heading_text(raw: str) -> str:
    """見出し行から見出しテキストだけを取り出す。"""
    text = raw.strip()
    return _CLOSING_HASHES_RE.sub("", text)


def split_sections(content: str) -> list[Section]:
    """ATX見出し（#〜######）で分割する。

    heading は見出しパスの結合（"親 > 子"）。先頭の見出し前の導入部は
    heading を空文字のセクションとする。section_hash はセクション本文
    （見出し行＋本文）の sha256。全セクションの text を連結すると元の
    ファイル内容と一致する。
    """
    lines = content.splitlines(keepends=True)
    chunks: list[tuple[list[str], str]] = []
    current_lines: list[str] = []
    current_heading = ""
    stack: list[tuple[int, str]] = []

    for line in lines:
        stripped = line.rstrip("\r\n")
        match = _HEADING_RE.match(stripped)
        if match:
            if current_lines:
                chunks.append((list(current_lines), current_heading))
            current_lines = [line]
            level = len(match.group(1))
            text = _heading_text(match.group(2))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            current_heading = " > ".join(item[1] for item in stack)
        else:
            current_lines.append(line)
    if current_lines or not chunks:
        chunks.append((list(current_lines), current_heading))

    sections: list[Section] = []
    for chunk_lines, heading in chunks:
        text = "".join(chunk_lines)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        sections.append(Section(heading=heading, text=text, section_hash=digest))
    return sections
