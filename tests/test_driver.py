"""Issue #88 本人確認待ちキュー定期提示ドライバー: 先行テスト.

仕様の根拠は ``issue88-driver-dod.md``（完了の定義と設計契約）と
GitHub Issue Charee-Villapong/ai-organization #88 の本文のみ。
実装コードは見ていない。すべてのテストは既存の流儀どおり CLI を
サブプロセスで駆動し、tmp_path 配下のダミーデータだけを使う。

key は実物と同じ複合文字列形式
``repo|path|heading|section_hash`` を使う。key を英数字のみと
決め打つテストはここには無い。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from conftest import queue_item

DAY = date(2026, 11, 20)
DAY_S = DAY.isoformat()
NEXT_DAY_S = (DAY + timedelta(days=1)).isoformat()

# 14 日抑制の境界値: DAY から 13 日前は出ない、14 日前は出る。
DAY_MINUS_13_S = (DAY - timedelta(days=13)).isoformat()
DAY_MINUS_14_S = (DAY - timedelta(days=14)).isoformat()
FUTURE_S = (DAY + timedelta(days=5)).isoformat()

DRIVER_CAP = 3
DRIVER_INTERVAL_DAYS = 14


def _key(tag: str) -> str:
    """Composite key in the real ``repo|path|heading|section_hash`` form."""
    return f"/tmp/dummy-repo|rules/a.md|Alpha|{tag * 64}"


def _item(tag: str, created: str, **overrides) -> dict:
    item = queue_item(_key(tag), created)
    item["section_hash"] = tag * 64
    item.update(overrides)
    return item


def _driver_config(
    harness,
    repo: Path,
    *,
    cap: int = DRIVER_CAP,
    interval_days: int = DRIVER_INTERVAL_DAYS,
    daily_presentation_cap: int = 10,
) -> Path:
    """Write a config that carries the driver keys (cap / interval)."""
    path = harness.config_for_repo(
        repo,
        ["rules/a.md", "rules/b.md"],
        daily_presentation_cap=daily_presentation_cap,
    )
    config = json.loads(path.read_text(encoding="utf-8"))
    config["driver_presentation_cap"] = cap
    config["driver_presentation_interval_days"] = interval_days
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _add(harness, items, name="driver-items.json"):
    path = harness.write_json_input(name, items)
    return harness.run_json("queue-add", "--file", str(path))


def _read_queue(harness) -> dict:
    return json.loads(harness.queue_file.read_text(encoding="utf-8"))


def _queue_items(harness) -> list[dict]:
    return _read_queue(harness)["items"]


def _item_by_key(harness, key: str) -> dict:
    for item in _queue_items(harness):
        if item["key"] == key:
            return item
    raise AssertionError(f"key {key!r} not present in queue: {_queue_items(harness)!r}")


def _set_item_fields(harness, key: str, **fields) -> None:
    queue = _read_queue(harness)
    for item in queue["items"]:
        if item["key"] == key:
            item.update(fields)
            break
    else:
        raise AssertionError(f"key {key!r} not present in queue")
    harness.queue_file.write_text(
        json.dumps(queue, ensure_ascii=False), encoding="utf-8"
    )


def _select(harness, select_date: str = DAY_S) -> dict:
    return harness.run_json("queue-driver-select", "--date", select_date)


def _select_keys(harness, select_date: str = DAY_S) -> list[str]:
    return [item["key"] for item in _select(harness, select_date)["items"]]


def _present(harness, keys, select_date: str = DAY_S) -> dict:
    return harness.run_json(
        "queue-driver-presented", "--date", select_date, "--keys", ",".join(keys)
    )


def _answer(harness, key: str, answer_kind: str) -> dict:
    return harness.run_json(
        "queue-driver-answer", "--key", key, "--answer-kind", answer_kind
    )


# ----------------------------------------------------------------------
# 1. queue-driver-select
# ----------------------------------------------------------------------


def test_driver_select_returns_unpresented_oldest_first(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _item("c", "2026-11-12T09:00:00"),
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-11T09:00:00"),
        ],
    )
    assert _select_keys(harness) == [_key("a"), _key("b"), _key("c")]
    assert _select(harness)["total_pending"] == 3


def test_driver_select_is_capped_but_total_pending_counts_everything(
    harness, simple_repo
):
    # driver_presentation_cap is 3: the 4th oldest item stays out, but
    # total_pending still counts every pending item.
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
            _item("c", "2026-11-10T11:00:00"),
            _item("d", "2026-11-10T12:00:00"),
        ],
    )
    payload = _select(harness)
    assert [item["key"] for item in payload["items"]] == [
        _key("a"),
        _key("b"),
        _key("c"),
    ]
    assert payload["total_pending"] == 4


def test_driver_select_excludes_answered_and_hold(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
            _item("c", "2026-11-10T11:00:00"),
            _item("d", "2026-11-10T12:00:00"),
        ],
    )
    harness.run_json("queue-resolve", "--key", _key("b"))
    _set_item_fields(harness, _key("c"), hold=True)
    payload = _select(harness)
    assert [item["key"] for item in payload["items"]] == [_key("a"), _key("d")]
    # total_pending counts every pending item, hold included, but not
    # the answered one.
    assert payload["total_pending"] == 3


def test_driver_select_interval_boundary_13_days_out_14_days_in(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
            _item("c", "2026-11-10T11:00:00"),
        ],
    )
    _set_item_fields(harness, _key("a"), last_presented=DAY_MINUS_14_S)
    _set_item_fields(harness, _key("b"), last_presented=DAY_MINUS_13_S)
    assert _select_keys(harness) == [_key("a"), _key("c")]
    assert _select(harness)["total_pending"] == 3


def test_driver_select_excludes_future_last_presented(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
        ],
    )
    _set_item_fields(harness, _key("a"), last_presented=FUTURE_S)
    assert _select_keys(harness) == [_key("b")]
    assert _select(harness)["total_pending"] == 2


def test_driver_select_on_empty_queue(harness, simple_repo):
    _driver_config(harness, simple_repo)
    assert _select(harness) == {"items": [], "total_pending": 0}


# ----------------------------------------------------------------------
# 2. list_presentable の interval_days 引数
# ----------------------------------------------------------------------


def test_list_presentable_default_interval_next_day(harness, simple_repo):
    # 既定値 interval_days=1 は現行動作: 提示した翌日から再提示される。
    # 既存の queue-list / queue-present 経由で確認する。
    _add(harness, [_item("a", "2026-11-10T09:00:00")])
    harness.run_json("queue-present", "--date", DAY_S, "--ids", _key("a"))
    assert harness.run_json("queue-list", "--date", DAY_S)["items"] == []
    assert [
        item["key"]
        for item in harness.run_json("queue-list", "--date", NEXT_DAY_S)["items"]
    ] == [_key("a")]


def test_driver_select_uses_14_day_interval_not_default(harness, simple_repo):
    # 前日に提示された項目は queue-list (既定 1 日) では翌日に出るが、
    # driver-select (14 日抑制) では出ない。
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
        ],
    )
    _set_item_fields(
        harness, _key("a"), last_presented=(DAY - timedelta(days=1)).isoformat()
    )
    assert [
        item["key"] for item in harness.run_json("queue-list", "--date", DAY_S)["items"]
    ] == [_key("a"), _key("b")]
    assert _select_keys(harness) == [_key("b")]


# ----------------------------------------------------------------------
# 3. record_presented (queue-driver-presented 経由)
# ----------------------------------------------------------------------


def test_driver_presented_records_last_presented_and_count_none_to_one(
    harness, simple_repo
):
    _driver_config(harness, simple_repo)
    _add(harness, [_item("a", "2026-11-10T09:00:00")])
    payload = _present(harness, [_key("a")])
    assert payload == {"ok": True, "updated": 1}
    item = _item_by_key(harness, _key("a"))
    assert item["last_presented"] == DAY_S
    assert item["presentation_count"] == 1
    assert item["status"] == "pending"


def test_driver_presented_third_presentation_sets_hold(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_item("a", "2026-11-10T09:00:00")])
    _set_item_fields(harness, _key("a"), presentation_count=2)
    payload = _present(harness, [_key("a")])
    assert payload == {"ok": True, "updated": 1}
    item = _item_by_key(harness, _key("a"))
    assert item["presentation_count"] == 3
    assert item["hold"] is True
    assert item["last_presented"] == DAY_S
    # 3 回目で hold になった項目は以後提示対象から外れる。
    assert _select_keys(harness) == []


def test_driver_presented_second_presentation_does_not_hold(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_item("a", "2026-11-10T09:00:00")])
    _set_item_fields(harness, _key("a"), presentation_count=1)
    _present(harness, [_key("a")])
    item = _item_by_key(harness, _key("a"))
    assert item["presentation_count"] == 2
    assert item.get("hold") is not True


def test_driver_presented_skips_hold_and_answered_items(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
            _item("c", "2026-11-10T11:00:00"),
        ],
    )
    _set_item_fields(
        harness, _key("a"), hold=True, presentation_count=2, last_presented=None
    )
    harness.run_json("queue-resolve", "--key", _key("b"))
    before_answered = _item_by_key(harness, _key("b"))
    payload = _present(harness, [_key("a"), _key("b"), _key("c")])
    assert payload == {"ok": True, "updated": 1}
    held = _item_by_key(harness, _key("a"))
    assert held["presentation_count"] == 2
    assert held["last_presented"] is None
    assert held["hold"] is True
    assert _item_by_key(harness, _key("b")) == before_answered
    assert _item_by_key(harness, _key("c"))["presentation_count"] == 1


def test_driver_presented_unknown_key_is_not_counted(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_item("a", "2026-11-10T09:00:00")])
    payload = _present(harness, [_key("a"), _key("e")])
    assert payload == {"ok": True, "updated": 1}
    assert _item_by_key(harness, _key("a"))["last_presented"] == DAY_S


# ----------------------------------------------------------------------
# 4. apply_answer (queue-driver-answer 経由)
# ----------------------------------------------------------------------


def test_driver_answer_records_status_and_answer_kind(harness, simple_repo):
    _driver_config(harness, simple_repo)
    cases = [
        ("a", "approved"),
        ("b", "rejected"),
        ("c", "re-review"),
    ]
    _add(
        harness,
        [_item(tag, f"2026-11-10T0{i}:00:00") for i, (tag, _) in enumerate(cases)],
    )
    for tag, kind in cases:
        payload = _answer(harness, _key(tag), kind)
        assert payload == {"ok": True, "applied": True}
        item = _item_by_key(harness, _key(tag))
        assert item["status"] == "answered"
        assert item["answer_kind"] == kind
    # 回答済みは以後提示対象から外れる。
    assert _select_keys(harness) == []
    assert _select(harness)["total_pending"] == 0


def test_driver_answer_invalid_kind_exits_1(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_item("a", "2026-11-10T09:00:00")])
    result = harness.run_raw(
        "queue-driver-answer", "--key", _key("a"), "--answer-kind", "maybe"
    )
    assert result.returncode == 1
    assert _item_by_key(harness, _key("a"))["status"] == "pending"


def test_driver_answer_unknown_key_applied_false(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_item("a", "2026-11-10T09:00:00")])
    payload = _answer(harness, _key("e"), "approved")
    assert payload == {"ok": True, "applied": False}
    assert _item_by_key(harness, _key("a"))["status"] == "pending"


# ----------------------------------------------------------------------
# 5. _KNOWN_FIELDS: queue-add 経由で新フィールドが落ちない
# ----------------------------------------------------------------------


def test_queue_add_preserves_answer_kind_hold_and_presentation_count(
    harness, simple_repo
):
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _item(
                "a",
                "2026-11-10T09:00:00",
                answer_kind="approved",
                hold=False,
                presentation_count=2,
            ),
            _item(
                "b",
                "2026-11-10T10:00:00",
                hold=True,
                presentation_count=0,
            ),
        ],
    )
    first = _item_by_key(harness, _key("a"))
    assert first["answer_kind"] == "approved"
    assert first["hold"] is False
    assert first["presentation_count"] == 2
    second = _item_by_key(harness, _key("b"))
    assert second["hold"] is True
    assert second["presentation_count"] == 0


# ----------------------------------------------------------------------
# 6. config: driver キー
# ----------------------------------------------------------------------


def test_config_without_driver_keys_fails_startup(harness, simple_repo):
    # simple_repo の設定は write_config の既定で driver キーを含むため、
    # ここで自前で 2 キーを削除した config に書き戻す。REQUIRED_KEYS に
    # driver キーが入っていれば起動時に ConfigError で失敗する。
    config = json.loads(harness.config_path.read_text(encoding="utf-8"))
    config.pop("driver_presentation_cap", None)
    config.pop("driver_presentation_interval_days", None)
    harness.config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = harness.run_raw("queue-list", "--date", DAY_S)
    assert result.returncode != 0


def test_config_with_driver_keys_loads_and_cap_applies(harness, simple_repo):
    _driver_config(harness, simple_repo, cap=3, interval_days=14)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
            _item("c", "2026-11-10T11:00:00"),
            _item("d", "2026-11-10T12:00:00"),
        ],
    )
    payload = _select(harness)
    assert len(payload["items"]) == 3
    assert payload["total_pending"] == 4


def test_config_driver_cap_value_is_read(harness, simple_repo):
    # cap=2 と書けば 2 件までになる。値が読めず 3 に固定なら失敗する。
    _driver_config(harness, simple_repo, cap=2, interval_days=14)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
            _item("c", "2026-11-10T11:00:00"),
        ],
    )
    assert len(_select(harness)["items"]) == 2


def test_config_driver_interval_value_is_read(harness, simple_repo):
    # interval_days=7 と書けば 6 日前は出ず 7 日前は出る。
    _driver_config(harness, simple_repo, cap=3, interval_days=7)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
            _item("c", "2026-11-10T11:00:00"),
        ],
    )
    _set_item_fields(
        harness, _key("a"), last_presented=(DAY - timedelta(days=7)).isoformat()
    )
    _set_item_fields(
        harness, _key("b"), last_presented=(DAY - timedelta(days=6)).isoformat()
    )
    assert _select_keys(harness) == [_key("a"), _key("c")]


# ----------------------------------------------------------------------
# 7. CLI の異常系・保存の再読込
# ----------------------------------------------------------------------


def test_driver_select_with_malformed_date_exits_1(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_item("a", "2026-11-10T09:00:00")])
    result = harness.run_raw("queue-driver-select", "--date", "20/11/2026")
    assert result.returncode == 1


def test_driver_select_does_not_modify_queue(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
        ],
    )
    before = harness.queue_file.read_bytes()
    _select(harness)
    assert harness.queue_file.read_bytes() == before


def test_driver_presented_preserves_external_queue_change(harness, simple_repo):
    # 保存直前にキューを再読込する: presented の呼び出し前に外部で
    # 変えた内容が、保存後も消えずに残ること。
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
        ],
    )
    queue = _read_queue(harness)
    queue["items"][0]["title"] = "Externally changed title"
    extra = dict(queue["items"][0])
    extra["key"] = _key("e")
    extra["title"] = "Externally added item"
    extra["created"] = "2026-11-10T13:00:00"
    extra["section_hash"] = "e" * 64
    queue["items"].append(extra)
    harness.queue_file.write_text(
        json.dumps(queue, ensure_ascii=False), encoding="utf-8"
    )
    payload = _present(harness, [_key("b")])
    assert payload == {"ok": True, "updated": 1}
    assert _item_by_key(harness, _key("a"))["title"] == "Externally changed title"
    assert _item_by_key(harness, _key("e"))["title"] == "Externally added item"
    assert _item_by_key(harness, _key("b"))["last_presented"] == DAY_S


def test_driver_answer_preserves_external_queue_change(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
        ],
    )
    queue = _read_queue(harness)
    queue["items"][0]["title"] = "Externally changed title"
    harness.queue_file.write_text(
        json.dumps(queue, ensure_ascii=False), encoding="utf-8"
    )
    payload = _answer(harness, _key("b"), "approved")
    assert payload == {"ok": True, "applied": True}
    assert _item_by_key(harness, _key("a"))["title"] == "Externally changed title"
    item = _item_by_key(harness, _key("b"))
    assert item["status"] == "answered"
    assert item["answer_kind"] == "approved"


# ----------------------------------------------------------------------
# 8. 週次走査の起動: 新キーを含む設定で load_config が成功する
# ----------------------------------------------------------------------


def test_weekly_scan_starts_with_driver_keys_in_config(harness, simple_repo):
    _driver_config(harness, simple_repo, cap=3, interval_days=14)
    result = harness.run_raw("extract")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert isinstance(payload["files"], list)
    result = harness.run_raw("queue-list", "--date", DAY_S)
    assert result.returncode == 0, result.stderr


# ----------------------------------------------------------------------
# 9. section_hash 指定（見出しにカンマを含む key への対処）
#
# 不具合の仕様: key は repo|path|heading|hash の複合文字列で、見出しに
# カンマを含む項目（実例: ig-repurpose の "Extract, do not summarise"）は
# --keys のカンマ区切りでは分割されてしまい、提示を記録できない。
# section_hash は16進文字列のみで区切り文字と衝突しないため、提示記録と
# 回答反映は hash でも指定できること。未知の hash の扱いは未知の key と
# そろえる（数えない／applied=False）。
# ----------------------------------------------------------------------


def _comma_item(tag: str, created: str, **overrides) -> dict:
    key = f"/tmp/dummy-repo|rules/a.md|Extract, do not summarise|{tag * 64}"
    item = queue_item(key, created)
    item["section_hash"] = tag * 64
    item["heading"] = "Extract, do not summarise"
    item.update(overrides)
    return item


def _present_hashes(harness, hashes, select_date: str = DAY_S) -> dict:
    return harness.run_json(
        "queue-driver-presented", "--date", select_date, "--hashes", ",".join(hashes)
    )


def _answer_hash(harness, section_hash: str, answer_kind: str) -> dict:
    return harness.run_json(
        "queue-driver-answer", "--hash", section_hash, "--answer-kind", answer_kind
    )


def test_driver_presented_by_hash_records_comma_key_item(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_comma_item("a", "2026-11-10T09:00:00")])
    payload = _present_hashes(harness, ["a" * 64])
    assert payload == {"ok": True, "updated": 1}
    item = _queue_items(harness)[0]
    assert item["last_presented"] == DAY_S
    assert item["presentation_count"] == 1
    assert item["status"] == "pending"


def test_driver_presented_by_hashes_records_each(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _comma_item("a", "2026-11-10T09:00:00"),
            _item("b", "2026-11-10T10:00:00"),
        ],
    )
    payload = _present_hashes(harness, ["a" * 64, "b" * 64])
    assert payload == {"ok": True, "updated": 2}
    for item in _queue_items(harness):
        assert item["last_presented"] == DAY_S


def test_driver_presented_unknown_hash_is_not_counted(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_comma_item("a", "2026-11-10T09:00:00")])
    payload = _present_hashes(harness, ["a" * 64, "e" * 64])
    assert payload == {"ok": True, "updated": 1}
    assert _queue_items(harness)[0]["last_presented"] == DAY_S


def test_driver_presented_ambiguous_hash_exits_1(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(
        harness,
        [
            _item("a", "2026-11-10T09:00:00", section_hash="f" * 64),
            _item("b", "2026-11-10T10:00:00", section_hash="f" * 64),
        ],
    )
    result = harness.run_raw(
        "queue-driver-presented", "--date", DAY_S, "--hashes", "f" * 64
    )
    assert result.returncode == 1
    for item in _queue_items(harness):
        assert item["last_presented"] is None


def test_driver_presented_without_keys_or_hashes_exits_1(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_item("a", "2026-11-10T09:00:00")])
    result = harness.run_raw("queue-driver-presented", "--date", DAY_S)
    assert result.returncode == 1
    assert _item_by_key(harness, _key("a"))["last_presented"] is None


def test_driver_answer_by_hash_records_comma_key_item(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_comma_item("a", "2026-11-10T09:00:00")])
    payload = _answer_hash(harness, "a" * 64, "approved")
    assert payload == {"ok": True, "applied": True}
    item = _queue_items(harness)[0]
    assert item["status"] == "answered"
    assert item["answer_kind"] == "approved"


def test_driver_answer_unknown_hash_applied_false(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_comma_item("a", "2026-11-10T09:00:00")])
    payload = _answer_hash(harness, "e" * 64, "approved")
    assert payload == {"ok": True, "applied": False}
    assert _queue_items(harness)[0]["status"] == "pending"


def test_driver_answer_with_both_key_and_hash_exits_1(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_comma_item("a", "2026-11-10T09:00:00")])
    result = harness.run_raw(
        "queue-driver-answer",
        "--key",
        _queue_items(harness)[0]["key"],
        "--hash",
        "a" * 64,
        "--answer-kind",
        "approved",
    )
    assert result.returncode == 1
    assert _queue_items(harness)[0]["status"] == "pending"


def test_queue_present_by_hashes_records_comma_key_item(harness, simple_repo):
    # queue-present（台帳ハーネス側の記録経路）でも、カンマを含む key の
    # 項目を --ids では記録できないため、--hashes で記録できること。
    _driver_config(harness, simple_repo)
    _add(harness, [_comma_item("a", "2026-11-10T09:00:00")])
    payload = harness.run_json("queue-present", "--date", DAY_S, "--hashes", "a" * 64)
    assert payload == {"ok": True, "updated": 1}
    assert _queue_items(harness)[0]["last_presented"] == DAY_S


def test_queue_present_without_ids_or_hashes_exits_1(harness, simple_repo):
    _driver_config(harness, simple_repo)
    _add(harness, [_comma_item("a", "2026-11-10T09:00:00")])
    result = harness.run_raw("queue-present", "--date", DAY_S)
    assert result.returncode == 1
    assert _queue_items(harness)[0]["last_presented"] is None
