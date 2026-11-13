"""状態ファイルの読み書き。保持項目は限定し、書き込みは原子的。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

STATE_VERSION = 1

VERDICT_CODES = ("codable", "rewritable", "not_codable", "needs_user")

# 候補の種別。コード化ケース／書き換えケース／相反ケースを区別する。
KIND_CODES = ("codable", "rewrite", "contradiction")

# 状態ファイルに保持してよい項目はこれだけ。本文・要約・判定理由は保持しない。
_JUDGED_FIELDS = (
    "repo",
    "path",
    "heading",
    "section_hash",
    "verdict_code",
    "kind",
    "issue_number",
    "guard",
)


class StateError(ValueError):
    """状態ファイルが読めない・使えない場合のエラー。"""


def empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "repos": {},
        "files": {},
        "judged": [],
        "registry": [],
    }


def _is_valid_shape(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    if raw.get("version", STATE_VERSION) != STATE_VERSION:
        return False
    if not isinstance(raw.get("repos", {}), dict):
        return False
    if not isinstance(raw.get("files", {}), dict):
        return False
    if not isinstance(raw.get("judged", []), list):
        return False
    if not isinstance(raw.get("registry", []), list):
        return False
    return True


def load_state(path: str | Path) -> tuple[dict, str | None, bool]:
    """状態ファイルを読み込む。

    戻り値は (state, warning, had_valid_state)。
    ファイルが無ければ空の状態（警告なし）。破損・形状違いなら
    空の状態と警告文を返す（呼び出し側は full 走査へ落とす）。
    """
    state_path = Path(path)
    if not state_path.exists():
        return empty_state(), None, False
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return (
            empty_state(),
            f"状態ファイルが読めません（全件走査します）: {exc}",
            False,
        )
    if not _is_valid_shape(raw):
        return (
            empty_state(),
            "状態ファイルの形状が不正です（全件走査します）",
            False,
        )
    state = empty_state()
    state.update(raw)
    return state, None, True


def load_state_strict(path: str | Path) -> dict:
    """state-commit / registry 用の厳密読み込み。

    ファイルが無ければ空の状態。破損・形状違いはエラーにする
    （壊れた状態を無言で捨てて上書きしないため）。
    """
    state_path = Path(path)
    if not state_path.exists():
        return empty_state()
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise StateError(f"状態ファイルが読めません: {exc}") from exc
    if not _is_valid_shape(raw):
        raise StateError("状態ファイルの形状が不正です。")
    state = empty_state()
    state.update(raw)
    return state


def save_state_atomic(path: str | Path, state: dict) -> None:
    """状態ファイルを原子的書き込み（tmpファイル＋os.replace）で保存する。"""
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(state_path.parent), prefix=state_path.name + ".tmp."
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, state_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def find_judged(
    state: dict, repo: str, path: str, heading: str, section_hash: str
) -> dict | None:
    """(repo, path, heading, section_hash) が一致する判定記録を探す。"""
    for record in state.get("judged", []):
        if (
            record.get("repo") == repo
            and record.get("path") == path
            and record.get("heading") == heading
            and record.get("section_hash") == section_hash
        ):
            return record
    return None


def previously_judged(
    state: dict, repo: str, path: str, heading: str, section_hash: str
) -> dict | None:
    """extract 出力用の previously_judged（verdict_code と issue_number のみ）。"""
    record = find_judged(state, repo, path, heading, section_hash)
    if record is None:
        return None
    return {
        "verdict_code": record.get("verdict_code"),
        "issue_number": record.get("issue_number"),
    }


def upsert_judged(state: dict, record: dict) -> None:
    """判定記録を保存する。保持するのは限定項目だけ。"""
    clean = {key: record.get(key) for key in _JUDGED_FIELDS}
    clean["guard"] = bool(record.get("guard", False))
    judged = state.setdefault("judged", [])
    for index, existing in enumerate(judged):
        if (
            existing.get("repo") == clean["repo"]
            and existing.get("path") == clean["path"]
            and existing.get("heading") == clean["heading"]
            and existing.get("section_hash") == clean["section_hash"]
        ):
            judged[index] = clean
            return
    judged.append(clean)


def last_success_commit(state: dict, repo: str) -> str | None:
    entry = state.get("repos", {}).get(repo)
    if isinstance(entry, dict):
        commit = entry.get("last_success_commit")
        return commit if isinstance(commit, str) else None
    return None


def advance_commit(state: dict, repo: str, commit: str) -> None:
    state.setdefault("repos", {})[repo] = {"last_success_commit": commit}


def record_file_hashes(
    state: dict, repo: str, files: dict[str, tuple[str, float]]
) -> None:
    """ファイルごとのハッシュを記録する（非git対象の変更検出用）。"""
    state.setdefault("files", {})[repo] = {
        path: {"sha256": digest, "mtime": mtime}
        for path, (digest, mtime) in files.items()
    }


def hash_file(path: str | Path) -> tuple[str, float] | None:
    """ファイルの sha256 と mtime。読めなければ None。"""
    target = Path(path)
    try:
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        mtime = target.stat().st_mtime
    except OSError:
        return None
    return digest, mtime
