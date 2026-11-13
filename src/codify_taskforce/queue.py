"""本人確認待ちキュー（確認待ちキュー）。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_KNOWN_FIELDS = (
    "key",
    "title",
    "repo",
    "path",
    "heading",
    "section_hash",
    "reason_code",
    "created",
)


class QueueError(ValueError):
    """キューが読めない・使えない場合のエラー。"""


def empty_queue() -> dict:
    return {"items": []}


def load_queue(path: str | Path) -> dict:
    """キューファイルを読み込む。無ければ空のキュー。破損はエラー。"""
    queue_path = Path(path)
    if not queue_path.exists():
        return empty_queue()
    try:
        raw = json.loads(queue_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise QueueError(f"キューファイルが読めません: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("items", []), list):
        raise QueueError("キューファイルの形状が不正です。")
    queue = empty_queue()
    queue.update(raw)
    return queue


def save_queue_atomic(path: str | Path, queue: dict) -> None:
    """キューを原子的書き込み（tmpファイル＋os.replace）で保存する。"""
    queue_path = Path(path)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(queue_path.parent), prefix=queue_path.name + ".tmp."
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(queue, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, queue_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _clean_item(item: dict) -> dict:
    clean = {key: item.get(key) for key in _KNOWN_FIELDS}
    clean["status"] = "pending"
    clean["last_presented"] = None
    return clean


def add_items(queue: dict, items: list[dict]) -> tuple[int, int]:
    """キューへ追加する。key の重複排除は pending のものだけが対象。

    同じ key の pending が既にあれば追加しない（バッチ内も含む）。
    戻り値は (added, skipped)。
    """
    pending_keys = {
        item.get("key")
        for item in queue.get("items", [])
        if item.get("status") == "pending"
    }
    added = 0
    skipped = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("key"):
            raise QueueError("キュー追加の各項目には key が必要です。")
        if item["key"] in pending_keys:
            skipped += 1
            continue
        queue.setdefault("items", []).append(_clean_item(item))
        pending_keys.add(item["key"])
        added += 1
    return added, skipped


def list_presentable(queue: dict, date: str, cap: int) -> tuple[list[dict], int]:
    """提示対象を created の古い順に cap 件まで返す。total_pending も返す。

    提示対象 = status が pending かつ last_presented が null か date より前。
    total_pending は pending の全件数（提示対象に限らない）。
    """
    pending = [
        item for item in queue.get("items", []) if item.get("status") == "pending"
    ]
    eligible = [
        item
        for item in pending
        if item.get("last_presented") is None or str(item.get("last_presented")) < date
    ]
    eligible.sort(key=lambda item: str(item.get("created") or ""))
    return eligible[:cap], len(pending)


def present(queue: dict, date: str, ids: list[str]) -> int:
    """指定 key の pending 項目の last_presented を date に更新する。"""
    wanted = set(ids)
    updated = 0
    for item in queue.get("items", []):
        if item.get("key") in wanted and item.get("status") == "pending":
            item["last_presented"] = date
            updated += 1
    return updated


def resolve(queue: dict, key: str) -> None:
    """指定 key の項目を answered にする（見つからなければ何もしない）。"""
    for item in queue.get("items", []):
        if item.get("key") == key:
            item["status"] = "answered"
