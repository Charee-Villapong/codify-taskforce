"""``hash-files`` and the read-only nature of scanning.

Acceptance criteria covered:
- ``hash-files`` reports sha256 + mtime for every allowlisted file and
  the digest matches an independently computed sha256 of the content;
- changing a file changes its reported sha256;
- running ``extract`` (the scan) does not change any scanned file's
  sha256 or mtime -- the scan is read-only.
"""

from __future__ import annotations

import hashlib
import os


def _hashes_by_path(harness) -> dict:
    payload = harness.run_json("hash-files")
    return {entry["abs_path"]: entry for entry in payload["files"]}


def test_hash_files_covers_all_allowlisted_files(harness, simple_repo):
    by_path = _hashes_by_path(harness)
    expected = {
        str(simple_repo / "rules/a.md"),
        str(simple_repo / "rules/b.md"),
    }
    assert set(by_path) == expected


def test_hash_files_digests_match_independent_sha256(harness, simple_repo):
    by_path = _hashes_by_path(harness)
    for abs_path, entry in by_path.items():
        # ``Path / <absolute path>`` resolves to the absolute path itself.
        expected = hashlib.sha256((simple_repo / abs_path).read_bytes()).hexdigest()
        assert entry["sha256"] == expected
        assert isinstance(entry["mtime"], (int, float))


def test_hash_files_sha256_changes_after_file_change(harness, simple_repo):
    target = str(simple_repo / "rules/a.md")
    before = _hashes_by_path(harness)[target]["sha256"]

    harness.write_file(simple_repo, "rules/a.md", "# Alpha\n\nchanged\n")
    after = _hashes_by_path(harness)[target]["sha256"]
    assert after != before
    assert (
        after == hashlib.sha256((simple_repo / "rules/a.md").read_bytes()).hexdigest()
    )


def test_hash_files_does_not_report_non_allowlisted_files(harness, simple_repo):
    harness.write_file(simple_repo, "extra.md", "# Extra\n\nx\n")
    by_path = _hashes_by_path(harness)
    assert str(simple_repo / "extra.md") not in by_path


def test_extract_is_read_only_for_scanned_files(harness, simple_repo):
    targets = [
        simple_repo / "rules/a.md",
        simple_repo / "rules/b.md",
    ]
    before = {path: (path.read_bytes(), os.stat(path).st_mtime_ns) for path in targets}

    harness.extract()
    harness.extract()  # twice: repeated scans must also stay read-only

    for path, (content, mtime_ns) in before.items():
        assert path.read_bytes() == content
        assert os.stat(path).st_mtime_ns == mtime_ns
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            hashlib.sha256(content).hexdigest()
        )


def test_hash_files_output_is_stable_across_extract(harness, simple_repo):
    before = _hashes_by_path(harness)
    harness.extract()
    after = _hashes_by_path(harness)
    assert before == after
