"""本人確認待ちキュー定期提示ドライバー（Issue #88）。

週1回の提示の選定・提示記録・回答反映を担う。提示回数の数え方と
hold の扱いはここに集約する（queue.py の present() は last_presented
の記録のみで、回数・hold は触らない）。
"""

from __future__ import annotations

from . import queue as queue_mod
from .config import Config

ANSWER_KINDS = ("approved", "rejected", "re-review")
ANSWER_LABELS = {"承認": "approved", "却下": "rejected", "再審査": "re-review"}

HOLD_AFTER_PRESENTATIONS = 3


def select_for_presentation(queue: dict, date: str, config: Config) -> list[dict]:
    """提示対象を選ぶ。キューを変更しない。"""
    items, _total_pending = queue_mod.list_presentable(
        queue,
        date,
        config.driver_presentation_cap,
        config.driver_presentation_interval_days,
    )
    return items


def record_presented(queue: dict, keys: list[str], date: str) -> int:
    """提示した項目の last_presented と提示回数を記録する。

    各 key の pending 項目について last_presented=date、
    presentation_count を +1（None/欠落は 0 とみなす）し、回数が
    3 に達したら hold=True にする。hold 済み・answered の項目は
    触らない。更新件数を返す。

    date は YYYY-MM-DD 形式でなければ QueueError（CLI を経由しない
    直接呼び出しでも、不正な日付を last_presented に残さない）。
    """
    queue_mod._parse_date(date, "date")
    wanted = set(keys)
    updated = 0
    for item in queue.get("items", []):
        if item.get("key") not in wanted:
            continue
        if item.get("status") != "pending" or item.get("hold"):
            continue
        item["last_presented"] = date
        count = (item.get("presentation_count") or 0) + 1
        item["presentation_count"] = count
        if count >= HOLD_AFTER_PRESENTATIONS:
            item["hold"] = True
        updated += 1
    return updated


def apply_answer(queue: dict, key: str, answer_kind: str) -> bool:
    """本人の回答を反映する。status=answered と answer_kind を記録する。

    answer_kind が ANSWER_KINDS に無い場合は ValueError。
    該当 key の項目があれば True、無ければ False。
    """
    if answer_kind not in ANSWER_KINDS:
        raise ValueError(
            f"answer_kind が不正です: {answer_kind!r} "
            f"（許容値: {', '.join(ANSWER_KINDS)}）"
        )
    for item in queue.get("items", []):
        if item.get("key") == key:
            queue_mod.resolve(queue, key, answer_kind)
            return True
    return False


def key_for_hash(queue: dict, section_hash: str) -> str | None:
    """section_hash から key を引く。無ければ None、複数一致は ValueError。

    key は repo|path|heading|hash の複合文字列で、見出しにカンマを
    含む項目は CLI の --keys（カンマ区切り）では渡せない。
    section_hash は16進文字列のみで区切り文字と衝突しないため、
    提示記録・回答反映ではこちらでも指定できるようにする。
    """
    matches = [
        item.get("key")
        for item in queue.get("items", [])
        if item.get("section_hash") == section_hash
    ]
    if len(matches) > 1:
        raise ValueError(f"section_hash が複数の項目と一致します: {section_hash!r}")
    return matches[0] if matches else None


def record_presented_by_hashes(queue: dict, hashes: list[str], date: str) -> int:
    """section_hash 指定で提示を記録する。未知の hash は数えない。

    未知の key を数えない record_presented と扱いをそろえる。
    """
    keys = []
    for section_hash in hashes:
        key = key_for_hash(queue, section_hash)
        if key is not None:
            keys.append(key)
    return record_presented(queue, keys, date)


def apply_answer_by_hash(queue: dict, section_hash: str, answer_kind: str) -> bool:
    """section_hash 指定で回答を反映する。該当が無ければ False。"""
    key = key_for_hash(queue, section_hash)
    if key is None:
        return False
    return apply_answer(queue, key, answer_kind)
