# codify-taskforce

mdで運用されるルールのうち、コード化できる部分を週次で抽出・判定し、
開発Issueとして起票するタスクフォースの実装。
AI組織 Issue #30 の開発（企画の正本は同Issueの本文）。

## 目的

文章ルールの適用漏れを、発生の都度の場当たり対応ではなく
週次の定常プロセスで減らす。起票したケースはコード化の実施まで追跡する。
走査段は読み取り専用で、対象mdには触らない。

## 構成

- `src/codify_taskforce/` — CLIの実装（標準ライブラリのみ）
  - `config.py` — 設定の読み込みと検証、denylist判定
  - `sections.py` — mdのセクション分割とセクションハッシュ
  - `diff.py` — gitのコミット済み差分取得
  - `state.py` — 状態ファイルの読み書き（原子的書き込み）
  - `scan.py` — 秘匿パターンのテキスト走査
  - `queue.py` — 本人確認待ちキュー
  - `registry.py` — 先行事例のレジストリ
  - `cli.py` — コマンドの振り分けとJSON出力
- `docs/CRITERIA.md` — 判定基準文書（チェックリスト・2軸・境界例・
  相反の判定基準・わかりにくさの判定基準と書き換えの原則）
- `codify-taskforce-weekly.js` — 週次ワークフローの公開コピー
  （実働ファイルとの差分は伏せ字の箇所のみ）
- `scripts/pre_push_scan.py` — push前検閲スクリプト
- `config.example.json` — 設定の雛形（実値は含まない）

## 使い方

設定ファイルのパスを環境変数 `CODIFY_TASKFORCE_CONFIG` に渡して使う。

```sh
export CODIFY_TASKFORCE_CONFIG=/path/to/config.local.json
export PYTHONPATH=src
python -m codify_taskforce extract
python -m codify_taskforce hash-files
python -m codify_taskforce scan-text --file <path>
python -m codify_taskforce state-commit --file <results.json>
python -m codify_taskforce queue-add --file <items.json>
python -m codify_taskforce queue-list --date YYYY-MM-DD
python -m codify_taskforce queue-present --date YYYY-MM-DD --ids <key1,key2>
python -m codify_taskforce queue-resolve --key <key>
python -m codify_taskforce registry-seed
python -m codify_taskforce registry-list
```

全コマンドは stdout に JSON を1つ出し、成功時 exit 0。
`extract` は `--full` を付けると状態に関わらず全件走査する。

## 設定の置き場所

実働の設定はリポジトリ外に置く。雛形は `config.example.json` を見ること。
設定のキー:

- `allowlist` — 走査対象を列挙する（リポジトリの絶対パス＋
  リポジトリ相対パスのファイル一覧）。globの再帰走査はしない。
- `denylist` — パスベースの除外（完全一致 or パスセグメント一致）
- `state_file` / `queue_file` — 状態ファイル／確認待ちキューの置き場所
- `weekly_issue_cap` — 週あたり起票上限
- `daily_presentation_cap` — 1日の確認提示上限
- `trial_weeks` — 初回試行期間（提示のみ）の週数。0なら起票する
- `checklist_version` — 判定チェックリストの版
- `external_paid_api_allowed` — 外部有料API不使用の固定項目（false）
- `org_repo` — 起票先リポジトリ（`OWNER/REPO` 形式）
- `local_patterns_file` — 秘匿チェックのローカルパターンファイル（任意）

## 状態ファイルの保持項目

リポジトリごとの前回成功コミット、ファイルごとのハッシュ、
セクション記録（判定コード・Issue番号つき）、レジストリのみ。
本文・要約・判定理由は保持しない。書き込みは原子的
（tmpファイル＋os.replace）。

## diff モードの抽出範囲

`extract` は状態が無い／破損していれば全件走査する。
状態がある場合は、コミット済み差分に含まれる allowlist ファイルに加え、
判定記録を持つファイルも含める（判定履歴の可視化のため。
再提示・再起票の抑制は `previously_judged` で行う）。

## テスト

```sh
python -m pytest tests -q
```

## push前の検閲

```sh
python scripts/pre_push_scan.py
```

ヒット0件であることを確認してから push すること。
汎用パターンのみをスクリプトに持ち、個人特定パターンは
gitignore対象の `scripts/pre_push_patterns.local.txt` から読む。
テスト配下のダミー値（RFCの文書用ドメイン・IP範囲、
`DUMMY` を含むプレースホルダ、連番文字列等）は
明らかに合成値のため検閲の対象外とする。実値の混入は別途検出される。

## 週次ワークフロー（公開コピー）の伏せ字

`codify-taskforce-weekly.js` は公開用のコピーで、実働ファイル
（`~/.jarvis/workflows/codify-taskforce-weekly.js`）との差分は
次の置き換えのみ。実値は伏せ字で置き換えてある。

| 伏せ字 | 実値の意味 |
|---|---|
| `__ORG_OWNER__` | 組織のGitHubオーナー名 |
| `__ORG_REPO__` | 組織リポジトリ名（ai-organization） |
| `__DATA_DIR__` | タスクフォースのデータ置き場（設定・状態・キュー） |
| `__TASKFORCE_REPO__` | 本リポジトリの絶対パス |
| `__PYTHON__` | 実行用Python（venv）のパス |
| `__GH__` | `gh` コマンドのパス |
