"""先行事例のレジストリ（管理対象として登録する2件）。"""

from __future__ import annotations

PRECEDENTS: list[dict] = [
    {
        "id": "hard-title",
        "title": "HARD-TITLEルール（求人収集のPdM除外）",
        "kind": "precedent",
        "source": "ai-organization#30",
    },
    {
        "id": "ledger-scope-check",
        "title": "タスク台帳の対象範囲チェック",
        "kind": "precedent",
        "source": "ai-organization#30",
    },
]


def seed_registry(state: dict) -> list[dict]:
    """先行事例2件を状態の registry に登録する（既にあれば追加しない）。"""
    registry = state.setdefault("registry", [])
    existing_ids = {entry.get("id") for entry in registry if isinstance(entry, dict)}
    for precedent in PRECEDENTS:
        if precedent["id"] not in existing_ids:
            registry.append(dict(precedent))
    return registry


def list_registry(state: dict) -> list[dict]:
    return list(state.get("registry", []))
