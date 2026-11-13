"""codify-taskforce: mdで運用されるルールのコード化候補を週次で抽出・判定する。

AI組織 Issue #30 の開発。対象mdを allowlist＋denylist で走査し、
コード化候補を判定・起票・追跡する。標準ライブラリのみで動く。
"""

from __future__ import annotations

__all__: list[str] = []
