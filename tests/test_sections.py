"""Section splitting and ``section_hash``.

Acceptance criteria covered:
- files are split on ATX headings (``#`` .. ``######``);
- ``heading`` is the joined heading path (``"parent > child"``);
- ``section_hash`` is the sha256 of the section text (heading line +
  body): identical input gives an identical hash, a body change changes
  the hash.
"""

from __future__ import annotations

import hashlib
import re

DOCUMENT = """preamble before the first heading

# Top

top body

## Child A

body A

## Child B

body B

# Second

second body
"""


def _repo_with_document(harness, content=DOCUMENT, name="doc.md"):
    repo = harness.init_repo()
    harness.write_file(repo, name, content)
    harness.commit_all(repo, "document")
    harness.config_for_repo(repo, [name])
    return repo


def _headings(harness, path="doc.md"):
    entry = harness.extracted_file(path)
    return [section["heading"] for section in entry["sections"]]


def test_split_on_atx_headings_with_joined_heading_paths(harness):
    _repo_with_document(harness)
    headings = _headings(harness)
    # The preamble before the first heading must not swallow or rename
    # any headed section; headed sections appear in document order.
    headed = [h for h in headings if h]
    assert headed == ["Top", "Top > Child A", "Top > Child B", "Second"]


def test_deep_heading_levels_extend_the_path(harness):
    _repo_with_document(
        harness,
        "# L1\n\nx\n\n## L2\n\nx\n\n### L3\n\nx\n\n#### L4\n\nx\n",
    )
    assert _headings(harness) == [
        "L1",
        "L1 > L2",
        "L1 > L2 > L3",
        "L1 > L2 > L3 > L4",
    ]


def test_heading_path_resets_at_a_new_top_level_heading(harness):
    _repo_with_document(
        harness,
        "# First\n\n## Sub\n\nx\n\n# Second\n\n## Sub\n\nx\n",
    )
    assert _headings(harness) == [
        "First",
        "First > Sub",
        "Second",
        "Second > Sub",
    ]


def test_section_hash_is_a_sha256_hex_digest(harness):
    _repo_with_document(harness)
    entry = harness.extracted_file("doc.md")
    for section in entry["sections"]:
        assert re.fullmatch(r"[0-9a-f]{64}", section["section_hash"])


def test_section_hash_equals_sha256_of_the_section_text(harness):
    # A single-section file: the section text is the whole file, so the
    # expected digest can be computed directly from the spec's definition
    # (sha256 of heading line + body).
    content = "# Only\nline one\nline two\n"
    _repo_with_document(harness, content)
    section = harness.section("doc.md", "Only")
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert section["section_hash"] == expected


def test_section_hash_is_stable_when_content_is_unchanged(harness):
    _repo_with_document(harness)
    first = harness.section("doc.md", "Top > Child A")["section_hash"]
    second = harness.section("doc.md", "Top > Child A")["section_hash"]
    assert first == second


def test_section_hash_changes_when_the_body_changes(harness):
    repo = _repo_with_document(harness)
    before = harness.section("doc.md", "Top > Child A")["section_hash"]
    sibling_before = harness.section("doc.md", "Top > Child B")["section_hash"]

    harness.write_file(repo, "doc.md", DOCUMENT.replace("body A", "body A, revised"))
    harness.commit_all(repo, "revise child A")
    harness.state_file.unlink(missing_ok=True)  # force a fresh full scan

    after = harness.section("doc.md", "Top > Child A")["section_hash"]
    sibling_after = harness.section("doc.md", "Top > Child B")["section_hash"]
    assert after != before
    assert sibling_after == sibling_before, "an untouched section must keep its hash"


def test_section_hash_changes_when_the_heading_line_changes(harness):
    repo = _repo_with_document(harness)
    before = harness.section("doc.md", "Top > Child A")["section_hash"]

    harness.write_file(repo, "doc.md", DOCUMENT.replace("## Child A", "## Child A2"))
    harness.commit_all(repo, "rename heading")
    harness.state_file.unlink(missing_ok=True)

    renamed = harness.section("doc.md", "Top > Child A2")
    assert renamed["section_hash"] != before
