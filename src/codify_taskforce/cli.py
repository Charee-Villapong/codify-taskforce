"""codify-taskforce の CLI。stdout に JSON を1つ出し、成功時 exit 0。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import diff as diff_mod
from . import queue as queue_mod
from . import registry as registry_mod
from . import scan as scan_mod
from . import state as state_mod
from .config import (
    Config,
    ConfigError,
    is_denylisted,
    load_config,
)
from .sections import split_sections


class CliError(ValueError):
    """CLIの失敗（終了コード非0・stderrへ出す）。"""


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _read_json_file(path: str):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(f"入力ファイルが読めません: {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliError(f"入力ファイルがJSONとして読めません: {path}: {exc}") from exc


def _allowlisted_files(config: Config) -> list[tuple[str, str, str]]:
    """allowlist の (repo, rel_path, abs_path) を denylist 適用済みで返す。"""
    result: list[tuple[str, str, str]] = []
    for entry in config.allowlist:
        for rel_path in entry.files:
            if is_denylisted(rel_path, config.denylist):
                continue
            result.append((entry.repo, rel_path, str(Path(entry.repo) / rel_path)))
    return result


def _read_text_file(abs_path: str) -> str | None:
    """テキストファイルをUTF-8で読む。読めなければ None。"""
    try:
        return Path(abs_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ----------------------------------------------------------------------
# extract
# ----------------------------------------------------------------------


def cmd_extract(config: Config, args: argparse.Namespace) -> dict:
    state, warning, had_valid_state = state_mod.load_state(config.state_file)
    force_full = bool(getattr(args, "full", False))
    mode = "full" if (force_full or not had_valid_state) else "diff"

    # diff モードでは、コミット済み差分に含まれる allowlist ファイルだけ
    # を対象にする。判定記録の有無は範囲に影響しない。
    # 再提示・再起票の抑制は previously_judged（ハッシュ一致）で行い、
    # 変更されたファイルが差分に入ればその中で働く。
    selected: list[tuple[str, str, str]] = []
    if mode == "full":
        selected = _allowlisted_files(config)
    else:
        for entry in config.allowlist:
            last = state_mod.last_success_commit(state, entry.repo)
            if last is None:
                # このリポジトリの前回成功コミットが無ければ全件走査。
                for rel_path in entry.files:
                    if is_denylisted(rel_path, config.denylist):
                        continue
                    selected.append(
                        (entry.repo, rel_path, str(Path(entry.repo) / rel_path))
                    )
                continue
            changed = diff_mod.changed_files(entry.repo, last)
            if changed is None:
                warning = (
                    warning
                    or f"差分取得に失敗しました: {entry.repo}" "（全件走査します）"
                )
                for rel_path in entry.files:
                    if is_denylisted(rel_path, config.denylist):
                        continue
                    selected.append(
                        (entry.repo, rel_path, str(Path(entry.repo) / rel_path))
                    )
                continue
            changed_set = set(changed)
            for rel_path in entry.files:
                if rel_path not in changed_set:
                    continue
                if is_denylisted(rel_path, config.denylist):
                    continue
                selected.append(
                    (entry.repo, rel_path, str(Path(entry.repo) / rel_path))
                )

    files: list[dict] = []
    for repo, rel_path, abs_path in selected:
        content = _read_text_file(abs_path)
        if content is None:
            continue
        sections = []
        for section in split_sections(content):
            sections.append(
                {
                    "heading": section.heading,
                    "section_hash": section.section_hash,
                    "previously_judged": state_mod.previously_judged(
                        state,
                        repo,
                        rel_path,
                        section.heading,
                        section.section_hash,
                    ),
                }
            )
        files.append(
            {
                "repo": repo,
                "path": rel_path,
                "abs_path": abs_path,
                "sections": sections,
            }
        )

    payload: dict = {
        "mode": mode,
        "files": files,
        "limits": {
            "weekly_issue_cap": config.weekly_issue_cap,
            "daily_presentation_cap": config.daily_presentation_cap,
        },
    }
    if warning:
        payload["state_warning"] = warning
    return payload


# ----------------------------------------------------------------------
# hash-files
# ----------------------------------------------------------------------


def cmd_hash_files(config: Config, args: argparse.Namespace) -> dict:
    files: list[dict] = []
    for _repo, _rel_path, abs_path in _allowlisted_files(config):
        hashed = state_mod.hash_file(abs_path)
        if hashed is None:
            continue
        digest, mtime = hashed
        files.append({"abs_path": abs_path, "sha256": digest, "mtime": mtime})
    return {"files": files}


# ----------------------------------------------------------------------
# scan-text
# ----------------------------------------------------------------------


def cmd_scan_text(config: Config, args: argparse.Namespace) -> dict:
    local_patterns = scan_mod.load_local_patterns(config.local_patterns_file)
    return scan_mod.scan_text(args.file, local_patterns)


# ----------------------------------------------------------------------
# state-commit
# ----------------------------------------------------------------------


def _judged_rank(record: dict) -> int:
    """同一セクションの複数判定のマージ優先順位（小さいほど優先）。

    起票済み（issue_number あり）> 可・未起票（codable / rewritable
    かつ issue_number なし）> 要・本人確認（needs_user）> 不可
    （not_codable）。「書き換え可・未起票」はコード化可・未起票と
    同じ扱い（どちらも起票対象の未起票のため）。未起票の「可」が
    「不可」に上書きで消え、次週の抽出から漏れるのを防ぐ。
    """
    if record.get("issue_number") is not None:
        return 0
    if record["verdict_code"] in ("codable", "rewritable"):
        return 1
    if record["verdict_code"] == "needs_user":
        return 2
    return 3


def cmd_state_commit(config: Config, args: argparse.Namespace) -> dict:
    data = _read_json_file(args.file)
    if not isinstance(data, dict):
        raise CliError("state-commit の入力はJSONオブジェクトである必要があります。")
    judged = data.get("judged", [])
    advance_commits = data.get("advance_commits", {})
    if not isinstance(judged, list):
        raise CliError("state-commit の judged は配列である必要があります。")
    if not isinstance(advance_commits, dict):
        raise CliError(
            "state-commit の advance_commits はオブジェクトである必要があります。"
        )
    for record in judged:
        if not isinstance(record, dict):
            raise CliError("judged の各要素はオブジェクトである必要があります。")
        for field in ("repo", "path", "heading", "section_hash", "verdict_code"):
            if field not in record:
                raise CliError(f"judged の記録に {field} がありません。")
        if record["verdict_code"] not in state_mod.VERDICT_CODES:
            raise CliError(
                "verdict_code が不正です: "
                f"{record['verdict_code']!r} "
                f"（許容値: {', '.join(state_mod.VERDICT_CODES)}）"
            )
        kind = record.get("kind")
        if kind is not None and kind not in state_mod.KIND_CODES:
            raise CliError(
                "kind が不正です: "
                f"{kind!r} "
                f"（許容値: {', '.join(state_mod.KIND_CODES)}）"
            )
    for repo, commit in advance_commits.items():
        if not isinstance(commit, str) or not commit:
            raise CliError(f"advance_commits の値が不正です: {repo!r}")

    # 同一セクションに複数の判定が入っていたら、優先順位で1件に
    # マージする（上書きで片方を黙って消さない）。同順なら先勝ち。
    # このマージは同じ入力バッチ内に限る。別バッチ（別週）の新しい
    # 判定が来た場合は upsert_judged が最新の判定で置き換える。
    merged: dict[tuple, dict] = {}
    for record in judged:
        key = (
            record["repo"],
            record["path"],
            record["heading"],
            record["section_hash"],
        )
        current = merged.get(key)
        if current is None or _judged_rank(record) < _judged_rank(current):
            merged[key] = record
    judged = list(merged.values())

    state = state_mod.load_state_strict(config.state_file)
    for record in judged:
        state_mod.upsert_judged(state, record)
    for repo, commit in advance_commits.items():
        state_mod.advance_commit(state, repo, commit)
        hashes: dict[str, tuple[str, float]] = {}
        for entry in config.allowlist:
            if entry.repo != repo:
                continue
            for rel_path in entry.files:
                if is_denylisted(rel_path, config.denylist):
                    continue
                hashed = state_mod.hash_file(str(Path(repo) / rel_path))
                if hashed is not None:
                    hashes[rel_path] = hashed
        state_mod.record_file_hashes(state, repo, hashes)
    state_mod.save_state_atomic(config.state_file, state)
    return {
        "ok": True,
        "judged_count": len(judged),
        "advanced_repos": list(advance_commits.keys()),
    }


# ----------------------------------------------------------------------
# queue
# ----------------------------------------------------------------------


def cmd_queue_add(config: Config, args: argparse.Namespace) -> dict:
    data = _read_json_file(args.file)
    if not isinstance(data, list):
        raise CliError("queue-add の入力は配列である必要があります。")
    queue = queue_mod.load_queue(config.queue_file)
    added, skipped = queue_mod.add_items(queue, data)
    queue_mod.save_queue_atomic(config.queue_file, queue)
    return {"added": added, "skipped": skipped}


def cmd_queue_list(config: Config, args: argparse.Namespace) -> dict:
    queue = queue_mod.load_queue(config.queue_file)
    items, total_pending = queue_mod.list_presentable(
        queue, args.date, config.daily_presentation_cap
    )
    return {"items": items, "total_pending": total_pending}


def cmd_queue_present(config: Config, args: argparse.Namespace) -> dict:
    queue = queue_mod.load_queue(config.queue_file)
    ids = [item for item in args.ids.split(",") if item]
    updated = queue_mod.present(queue, args.date, ids)
    queue_mod.save_queue_atomic(config.queue_file, queue)
    return {"ok": True, "updated": updated}


def cmd_queue_resolve(config: Config, args: argparse.Namespace) -> dict:
    queue = queue_mod.load_queue(config.queue_file)
    queue_mod.resolve(queue, args.key)
    queue_mod.save_queue_atomic(config.queue_file, queue)
    return {"ok": True}


# ----------------------------------------------------------------------
# registry
# ----------------------------------------------------------------------


def cmd_registry_seed(config: Config, args: argparse.Namespace) -> dict:
    state = state_mod.load_state_strict(config.state_file)
    entries = registry_mod.seed_registry(state)
    state_mod.save_state_atomic(config.state_file, state)
    return {"entries": entries}


def cmd_registry_list(config: Config, args: argparse.Namespace) -> dict:
    state, _warning, _had = state_mod.load_state(config.state_file)
    return {"entries": registry_mod.list_registry(state)}


# ----------------------------------------------------------------------
# parser / entry point
# ----------------------------------------------------------------------

_COMMANDS = {
    "extract": cmd_extract,
    "hash-files": cmd_hash_files,
    "scan-text": cmd_scan_text,
    "state-commit": cmd_state_commit,
    "queue-add": cmd_queue_add,
    "queue-list": cmd_queue_list,
    "queue-present": cmd_queue_present,
    "queue-resolve": cmd_queue_resolve,
    "registry-seed": cmd_registry_seed,
    "registry-list": cmd_registry_list,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codify-taskforce",
        description="mdルールのコード化タスクフォース CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser(
        "extract", help="対象mdを走査してセクションを抽出する"
    )
    extract_parser.add_argument(
        "--full", action="store_true", help="状態に関わらず全件走査する"
    )

    subparsers.add_parser(
        "hash-files", help="allowlistファイルの sha256 と mtime を出す"
    )

    scan_parser = subparsers.add_parser(
        "scan-text", help="テキストを秘匿パターンで走査する"
    )
    scan_parser.add_argument("--file", required=True)

    state_parser = subparsers.add_parser(
        "state-commit", help="判定記録と前回成功コミットを状態へ反映する"
    )
    state_parser.add_argument("--file", required=True)

    queue_add_parser = subparsers.add_parser(
        "queue-add", help="確認待ちキューへ項目を追加する"
    )
    queue_add_parser.add_argument("--file", required=True)

    queue_list_parser = subparsers.add_parser(
        "queue-list", help="確認待ちキューの提示対象を出す"
    )
    queue_list_parser.add_argument("--date", required=True)

    queue_present_parser = subparsers.add_parser(
        "queue-present", help="提示した項目の提示日を記録する"
    )
    queue_present_parser.add_argument("--date", required=True)
    queue_present_parser.add_argument("--ids", required=True)

    queue_resolve_parser = subparsers.add_parser(
        "queue-resolve", help="確認待ち項目を回答済みにする"
    )
    queue_resolve_parser.add_argument("--key", required=True)

    subparsers.add_parser("registry-seed", help="先行事例2件をレジストリへ登録する")
    subparsers.add_parser("registry-list", help="レジストリの登録内容を出す")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _COMMANDS[args.command]
    try:
        config = load_config()
        payload = handler(config, args)
    except (
        CliError,
        ConfigError,
        state_mod.StateError,
        scan_mod.ScanError,
        queue_mod.QueueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
