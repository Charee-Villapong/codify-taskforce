"""``registry-seed`` / ``registry-list``: the two precedent entries.

Acceptance criteria covered:
- seeding registers exactly the two precedents, identified by
  ``"hard-title"`` and ``"ledger-scope-check"``;
- seeding is idempotent: calling it twice still leaves two entries;
- ``registry-list`` reflects what was seeded.
"""

from __future__ import annotations

EXPECTED_IDS = {"hard-title", "ledger-scope-check"}


def _identifiers(entries) -> set:
    """Collect identifier strings without assuming the entry schema.

    The spec fixes the two identifiers but not the field name carrying
    them, so every string value in each entry is considered.
    """
    found: set = set()

    def visit(node):
        if isinstance(node, dict):
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
        elif isinstance(node, str):
            found.add(node)

    for entry in entries:
        visit(entry)
    return found


def test_registry_starts_empty(harness, simple_repo):
    payload = harness.run_json("registry-list")
    assert payload["entries"] == []


def test_seed_registers_the_two_precedents(harness, simple_repo):
    payload = harness.run_json("registry-seed")
    assert len(payload["entries"]) == 2
    assert EXPECTED_IDS <= _identifiers(payload["entries"])


def test_list_reflects_the_seeded_entries(harness, simple_repo):
    harness.run_json("registry-seed")
    payload = harness.run_json("registry-list")
    assert len(payload["entries"]) == 2
    assert EXPECTED_IDS <= _identifiers(payload["entries"])


def test_seed_is_idempotent(harness, simple_repo):
    first = harness.run_json("registry-seed")
    second = harness.run_json("registry-seed")
    third = harness.run_json("registry-seed")
    assert len(first["entries"]) == 2
    assert len(second["entries"]) == 2
    assert len(third["entries"]) == 2

    payload = harness.run_json("registry-list")
    assert len(payload["entries"]) == 2
    assert EXPECTED_IDS <= _identifiers(payload["entries"])
