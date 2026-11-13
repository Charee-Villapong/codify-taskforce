"""Confirmation queue: ``queue-add`` / ``queue-list`` / ``queue-present``
/ ``queue-resolve``.

Acceptance criteria covered:
- duplicate suppression by ``key`` while an item is pending;
- new items start as ``status == "pending"`` with ``last_presented``
  null;
- presentation eligibility: never presented -> listed, presented today
  -> not listed again today, presented before today -> listed again;
- ``daily_presentation_cap`` limits one listing, oldest ``created``
  first, while ``total_pending`` still counts every pending item;
- ``queue-present`` records the presentation date;
- ``queue-resolve`` removes the item from the presentation targets.
"""

from __future__ import annotations

from conftest import queue_item

DAY = "2026-11-13"
NEXT_DAY = "2026-11-14"


def _add(harness, items, name="queue-items.json"):
    path = harness.write_json_input(name, items)
    return harness.run_json("queue-add", "--file", str(path))


def _list(harness, date=DAY):
    return harness.run_json("queue-list", "--date", date)


def _listed_keys(harness, date=DAY):
    return [item["key"] for item in _list(harness, date)["items"]]


# ----------------------------------------------------------------------
# queue-add
# ----------------------------------------------------------------------


def test_add_reports_added_and_skipped_counts(harness, simple_repo):
    payload = _add(
        harness,
        [
            queue_item("k1", "2026-11-10T09:00:00"),
            queue_item("k2", "2026-11-10T10:00:00"),
        ],
    )
    assert payload == {"added": 2, "skipped": 0}


def test_new_items_are_pending_and_never_presented(harness, simple_repo):
    _add(harness, [queue_item("k1", "2026-11-10T09:00:00")])
    items = _list(harness)["items"]
    assert len(items) == 1
    assert items[0]["status"] == "pending"
    assert items[0]["last_presented"] is None
    assert items[0]["title"] == "Confirm k1"


def test_duplicate_key_is_not_added_twice_while_pending(harness, simple_repo):
    first = _add(harness, [queue_item("k1", "2026-11-10T09:00:00")])
    assert first == {"added": 1, "skipped": 0}

    # Same key, even with a different title, is a duplicate.
    second = _add(
        harness,
        [queue_item("k1", "2026-11-11T09:00:00", title="Changed title")],
        name="queue-items-2.json",
    )
    assert second == {"added": 0, "skipped": 1}
    assert _list(harness)["total_pending"] == 1


def test_duplicates_inside_one_batch_are_deduplicated(harness, simple_repo):
    payload = _add(
        harness,
        [
            queue_item("k1", "2026-11-10T09:00:00"),
            queue_item("k1", "2026-11-10T09:00:00"),
        ],
    )
    assert payload == {"added": 1, "skipped": 1}


def test_same_key_can_be_added_again_after_it_is_resolved(harness, simple_repo):
    # The spec only suppresses duplicates of *pending* items.
    _add(harness, [queue_item("k1", "2026-11-10T09:00:00")])
    harness.run_json("queue-resolve", "--key", "k1")
    payload = _add(
        harness,
        [queue_item("k1", "2026-11-12T09:00:00")],
        name="queue-items-again.json",
    )
    assert payload == {"added": 1, "skipped": 0}
    assert _listed_keys(harness) == ["k1"]


def test_queue_add_with_missing_input_file_fails(harness, simple_repo):
    result = harness.run_raw(
        "queue-add", "--file", str(harness.tmp_path / "missing.json")
    )
    assert result.returncode != 0


# ----------------------------------------------------------------------
# queue-list: ordering, cap and total
# ----------------------------------------------------------------------


def test_list_returns_oldest_created_first(harness, simple_repo):
    _add(
        harness,
        [
            queue_item("newest", "2026-11-12T09:00:00"),
            queue_item("oldest", "2026-11-10T09:00:00"),
            queue_item("middle", "2026-11-11T09:00:00"),
        ],
    )
    # The ordering check needs the cap raised above the item count
    # (the harness default daily cap is 2).  Rewriting the config keeps
    # the same state/queue file paths, so queued items are preserved.
    harness.config_for_repo(
        simple_repo,
        ["rules/a.md", "rules/b.md"],
        daily_presentation_cap=10,
    )
    assert _listed_keys(harness) == ["oldest", "middle", "newest"]


def test_list_is_capped_but_total_pending_counts_everything(harness, simple_repo):
    # Harness default daily_presentation_cap is 2.
    _add(
        harness,
        [
            queue_item("k1", "2026-11-10T09:00:00"),
            queue_item("k2", "2026-11-10T10:00:00"),
            queue_item("k3", "2026-11-10T11:00:00"),
        ],
    )
    payload = _list(harness)
    assert [item["key"] for item in payload["items"]] == ["k1", "k2"]
    assert payload["total_pending"] == 3


def test_list_on_empty_queue(harness, simple_repo):
    payload = _list(harness)
    assert payload == {"items": [], "total_pending": 0}


# ----------------------------------------------------------------------
# queue-present
# ----------------------------------------------------------------------


def test_presented_item_is_not_listed_again_on_the_same_day(harness, simple_repo):
    _add(
        harness,
        [
            queue_item("k1", "2026-11-10T09:00:00"),
            queue_item("k2", "2026-11-10T10:00:00"),
        ],
    )
    payload = harness.run_json("queue-present", "--date", DAY, "--ids", "k1")
    assert payload == {"ok": True, "updated": 1}
    assert _listed_keys(harness, DAY) == ["k2"]


def test_presented_item_is_listed_again_on_the_next_day(harness, simple_repo):
    _add(harness, [queue_item("k1", "2026-11-10T09:00:00")])
    harness.run_json("queue-present", "--date", DAY, "--ids", "k1")
    assert _listed_keys(harness, DAY) == []
    assert _listed_keys(harness, NEXT_DAY) == ["k1"]


def test_present_records_last_presented_date(harness, simple_repo):
    _add(harness, [queue_item("k1", "2026-11-10T09:00:00")])
    harness.run_json("queue-present", "--date", DAY, "--ids", "k1")
    # On the next day the item reappears and must show the recorded date.
    items = _list(harness, NEXT_DAY)["items"]
    assert len(items) == 1
    assert items[0]["last_presented"] == DAY
    assert items[0]["status"] == "pending"


def test_present_accepts_multiple_comma_separated_ids(harness, simple_repo):
    _add(
        harness,
        [
            queue_item("k1", "2026-11-10T09:00:00"),
            queue_item("k2", "2026-11-10T10:00:00"),
            queue_item("k3", "2026-11-10T11:00:00"),
        ],
    )
    payload = harness.run_json("queue-present", "--date", DAY, "--ids", "k1,k2")
    assert payload == {"ok": True, "updated": 2}
    assert _listed_keys(harness, DAY) == ["k3"]


# ----------------------------------------------------------------------
# queue-resolve
# ----------------------------------------------------------------------


def test_resolved_item_leaves_the_presentation_targets(harness, simple_repo):
    _add(
        harness,
        [
            queue_item("k1", "2026-11-10T09:00:00"),
            queue_item("k2", "2026-11-10T10:00:00"),
        ],
    )
    payload = harness.run_json("queue-resolve", "--key", "k1")
    assert payload == {"ok": True}

    listing = _list(harness)
    assert [item["key"] for item in listing["items"]] == ["k2"]
    assert listing["total_pending"] == 1
    # Resolved items do not come back on later days either.
    assert _listed_keys(harness, NEXT_DAY) == ["k2"]
