"""設定ファイルの読み込みと検証。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_ENV_VAR = "CODIFY_TASKFORCE_CONFIG"

REQUIRED_KEYS = (
    "allowlist",
    "state_file",
    "queue_file",
    "weekly_issue_cap",
    "daily_presentation_cap",
    "checklist_version",
    "org_repo",
)


class ConfigError(ValueError):
    """設定が読めない・使えない場合のエラー。"""


@dataclass
class AllowlistEntry:
    repo: str
    files: list[str] = field(default_factory=list)


@dataclass
class Config:
    allowlist: list[AllowlistEntry]
    denylist: list[str]
    state_file: str
    queue_file: str
    weekly_issue_cap: int
    daily_presentation_cap: int
    checklist_version: str
    org_repo: str
    local_patterns_file: str | None = None
    trial_weeks: int = 0
    external_paid_api_allowed: bool = False


def load_config(path: str | Path | None = None) -> Config:
    """設定JSONを読み、必須キーを検証して Config を返す。

    パスは引数か環境変数 CODIFY_TASKFORCE_CONFIG から取る。
    読めない・壊れている・必須キー欠落の場合は ConfigError。
    """
    config_path = path or os.environ.get(CONFIG_ENV_VAR)
    if not config_path:
        raise ConfigError(
            f"設定ファイルが指定されていません。"
            f"環境変数 {CONFIG_ENV_VAR} を設定してください。"
        )
    try:
        raw_text = Path(config_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"設定ファイルが読めません: {config_path}: {exc}") from exc
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"設定ファイルがJSONとして読めません: {config_path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError(
            f"設定ファイルはJSONオブジェクトである必要があります: {config_path}"
        )
    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"設定ファイルに必須キーがありません: {', '.join(missing)}")
    allowlist_raw = raw["allowlist"]
    if not isinstance(allowlist_raw, list):
        raise ConfigError("設定の allowlist は配列である必要があります。")
    allowlist: list[AllowlistEntry] = []
    for entry in allowlist_raw:
        if not isinstance(entry, dict) or "repo" not in entry:
            raise ConfigError("設定の allowlist の各要素は repo を持つ必要があります。")
        files = entry.get("files", [])
        if not isinstance(files, list):
            raise ConfigError("設定の allowlist の files は配列である必要があります。")
        allowlist.append(
            AllowlistEntry(repo=str(entry["repo"]), files=[str(f) for f in files])
        )
    denylist = raw.get("denylist", [])
    if not isinstance(denylist, list):
        raise ConfigError("設定の denylist は配列である必要があります。")
    return Config(
        allowlist=allowlist,
        denylist=[str(item) for item in denylist],
        state_file=str(raw["state_file"]),
        queue_file=str(raw["queue_file"]),
        weekly_issue_cap=int(raw["weekly_issue_cap"]),
        daily_presentation_cap=int(raw["daily_presentation_cap"]),
        checklist_version=str(raw["checklist_version"]),
        org_repo=str(raw["org_repo"]),
        local_patterns_file=(
            str(raw["local_patterns_file"]) if raw.get("local_patterns_file") else None
        ),
        trial_weeks=int(raw.get("trial_weeks", 0)),
        external_paid_api_allowed=bool(raw.get("external_paid_api_allowed", False)),
    )


def is_denylisted(rel_path: str, denylist: list[str]) -> bool:
    """パスベースの除外判定。内容は開かない。

    denylist の各要素は、リポジトリ相対パスの完全一致か、
    パスセグメント（"/" で区切った要素）の一致で判定する。
    部分一致（"private" が "private-notes.md" に当たる等）はしない。
    """
    segments = rel_path.split("/")
    for entry in denylist:
        if entry == rel_path or entry in segments:
            return True
    return False
