export const meta = { name: "codify-taskforce-weekly", description: "mdで運用されるルールのコード化候補を週次で抽出・判定し、開発Issueとして起票するハーネス。段構成は差分抽出→候補抽出→相反点検→判定→秘匿チェック→起票→記録の直列。各段は専任エージェントで、失敗した段は最大3回再実行する。", phases: ["extract", "candidates", "contradictions", "judge", "secrecy", "file", "record"] };

const inputs = args ?? {};
const date = inputs.date;
const firstRun = inputs.firstRun === true;

const CONFIG_PATH = "__DATA_DIR__/config.local.json";
const TASKFORCE_REPO = "__TASKFORCE_REPO__";
const CRITERIA_PATH = "__TASKFORCE_REPO__/docs/CRITERIA.md";
const PYTHON = "__PYTHON__";
const GH = "__GH__";
const ORG_REPO = "__ORG_OWNER__/__ORG_REPO__";

// === CODIFY-GATES-BEGIN ===
// 判定の必須項目ゲート。文章のルールではなく、この関数の判定が正。
// 必須項目が1つでも欠けた「可」候補は「要・本人確認」へ落とす。
const CHECKLIST_REQUIRED = ["input_source", "output", "pass_condition", "failure_action", "test_normal", "test_abnormal"];
function checklistComplete(candidate) {
  const checklist = (candidate && candidate.checklist) || {};
  return CHECKLIST_REQUIRED.every((key) => {
    const value = checklist[key];
    return typeof value === "string" && value.trim() !== "";
  });
}
function candidateKey(candidate) {
  return [candidate.repo, candidate.path, candidate.heading, candidate.section_hash].join("|");
}
function applyChecklistGate(candidates) {
  const gated = [];
  for (const c of candidates || []) {
    const copy = Object.assign({}, c);
    if (copy.verdict === "codable" && !checklistComplete(copy)) {
      copy.verdict = "needs_user";
      copy.gate_note = "checklist-incomplete";
    }
    gated.push(copy);
  }
  return gated;
}
// 判定の衝突解消ゲート。文章のルールではなく、この関数の判定が正。
// 同一セクション（repo・path・heading・section_hash が同じ）に複数の
// 判定が入ったら、上書きで片方を黙って消さずに畳む:
// - 書き換え候補に付いた「可」は「書き換え可」に正規化する
// - 同じ判定の重複は1件にする
// - 判定が食い違う場合は衝突として「要・本人確認」へ回す
//   （reason_code は "verdict-conflict"。記録段で確認待ちキューへ載る）
function resolveVerdictConflicts(candidates) {
  const order = [];
  const byKey = new Map();
  for (const c of candidates || []) {
    const copy = Object.assign({}, c);
    if (copy.kind === "rewrite" && copy.verdict === "codable") {
      copy.verdict = "rewritable";
    }
    const key = candidateKey(copy);
    if (!byKey.has(key)) {
      byKey.set(key, []);
      order.push(key);
    }
    byKey.get(key).push(copy);
  }
  const resolved = [];
  for (const key of order) {
    const group = byKey.get(key);
    const verdicts = [...new Set(group.map((c) => c.verdict))];
    if (verdicts.length === 1) {
      resolved.push(group[0]);
      continue;
    }
    const merged = Object.assign({}, group[0]);
    merged.verdict = "needs_user";
    merged.reason_code = "verdict-conflict";
    merged.guard = group.some((c) => !!c.guard);
    merged.has_precedent = group.some((c) => !!c.has_precedent);
    const kinds = group.map((c) => c.kind);
    merged.kind = kinds.includes("contradiction")
      ? "contradiction"
      : kinds.includes("codable")
        ? "codable"
        : group[0].kind;
    resolved.push(merged);
  }
  return resolved;
}
// 秘匿ゲート。文章のルールではなく、この関数の判定が正。
// scan-text でヒットした候補は起票せず「要・本人確認」へ回す。
function applySecrecyGate(candidates, scanResults) {
  const hits = {};
  for (const r of scanResults || []) {
    if (r && r.key) hits[r.key] = !!r.hit;
  }
  return (candidates || []).map((c) => {
    const copy = Object.assign({}, c);
    if ((copy.verdict === "codable" || copy.verdict === "rewritable") && hits[candidateKey(copy)]) {
      copy.verdict = "needs_user";
      copy.gate_note = "secrecy-hit";
    }
    return copy;
  });
}
// 起票上限ゲート。文章のルールではなく、この関数の判定が正。
// 起票対象は「可」と「書き換え可」で、週あたり上限は設定値
// （weekly_issue_cap）の1つの枠を共有する。
// 優先順は「相反 ＞ コード化候補（実績あり） ＞ コード化候補 ＞ 書き換え候補」。
// 上限を超えた分は起票せず翌週へ繰り越す（破棄しない）。
function filingPriority(candidate) {
  if (candidate.kind === "contradiction") return 0;
  if (candidate.verdict === "rewritable") return 3;
  if (candidate.kind === "codable" && candidate.has_precedent) return 1;
  if (candidate.kind === "codable") return 2;
  return 3;
}
function applyFilingCap(candidates, cap) {
  const fileable = (candidates || []).filter((c) => c.verdict === "codable" || c.verdict === "rewritable");
  const ordered = fileable.slice().sort((a, b) => filingPriority(a) - filingPriority(b));
  return { fileNow: ordered.slice(0, cap), carriedOver: ordered.slice(cap) };
}
// === CODIFY-GATES-END ===

const stageSchema = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["success", "failed"] },
    summary: { type: "string" },
    reports: {
      type: "array",
      items: {
        type: "object",
        properties: {
          destination: { type: "string", enum: ["main", "ai-org"] },
          text: { type: "string" }
        },
        required: ["destination", "text"]
      }
    }
  },
  required: ["status", "summary", "reports"]
};

const candidateShape = {
  type: "object",
  properties: {
    kind: { type: "string", enum: ["codable", "rewrite", "contradiction"] },
    repo: { type: "string" },
    path: { type: "string" },
    heading: { type: "string" },
    section_hash: { type: "string" },
    title: { type: "string" },
    summary: { type: "string" },
    has_precedent: { type: "boolean" },
    checklist: {
      type: "object",
      properties: {
        input_source: { type: "string" },
        output: { type: "string" },
        pass_condition: { type: "string" },
        failure_action: { type: "string" },
        test_normal: { type: "string" },
        test_abnormal: { type: "string" }
      }
    }
  },
  required: ["kind", "repo", "path", "heading", "section_hash", "title"]
};

const extractSchema = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["success", "failed"] },
    summary: { type: "string" },
    reports: {
      type: "array",
      items: {
        type: "object",
        properties: {
          destination: { type: "string", enum: ["main", "ai-org"] },
          text: { type: "string" }
        },
        required: ["destination", "text"]
      }
    },
    mode: { type: "string", enum: ["full", "diff"] },
    files: {
      type: "array",
      items: {
        type: "object",
        properties: {
          repo: { type: "string" },
          path: { type: "string" },
          sections: {
            type: "array",
            items: {
              type: "object",
              properties: {
                heading: { type: "string" },
                section_hash: { type: "string" },
                previously_judged: { type: "object" }
              },
              required: ["heading", "section_hash"]
            }
          }
        },
        required: ["repo", "path", "sections"]
      }
    },
    limits: {
      type: "object",
      properties: {
        weekly_issue_cap: { type: "number" },
        daily_presentation_cap: { type: "number" }
      },
      required: ["weekly_issue_cap", "daily_presentation_cap"]
    },
    trial_weeks: { type: "number" },
    checklist_version: { type: "string" },
    org_repo: { type: "string" }
  },
  required: ["status", "summary", "reports"]
};

const candidatesSchema = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["success", "failed"] },
    summary: { type: "string" },
    reports: {
      type: "array",
      items: {
        type: "object",
        properties: {
          destination: { type: "string", enum: ["main", "ai-org"] },
          text: { type: "string" }
        },
        required: ["destination", "text"]
      }
    },
    candidates: { type: "array", items: candidateShape }
  },
  required: ["status", "summary", "reports"]
};

const judgeSchema = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["success", "failed"] },
    summary: { type: "string" },
    reports: {
      type: "array",
      items: {
        type: "object",
        properties: {
          destination: { type: "string", enum: ["main", "ai-org"] },
          text: { type: "string" }
        },
        required: ["destination", "text"]
      }
    },
    judged: {
      type: "array",
      items: {
        type: "object",
        properties: {
          kind: { type: "string", enum: ["codable", "rewrite", "contradiction"] },
          repo: { type: "string" },
          path: { type: "string" },
          heading: { type: "string" },
          section_hash: { type: "string" },
          title: { type: "string" },
          verdict: { type: "string", enum: ["codable", "rewritable", "not_codable", "needs_user"] },
          guard: { type: "boolean" },
          has_precedent: { type: "boolean" },
          reason_code: { type: "string" },
          checklist: {
            type: "object",
            properties: {
              input_source: { type: "string" },
              output: { type: "string" },
              pass_condition: { type: "string" },
              failure_action: { type: "string" },
              test_normal: { type: "string" },
              test_abnormal: { type: "string" }
            }
          }
        },
        required: ["kind", "repo", "path", "heading", "section_hash", "title", "verdict"]
      }
    }
  },
  required: ["status", "summary", "reports"]
};

const secrecySchema = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["success", "failed"] },
    summary: { type: "string" },
    reports: {
      type: "array",
      items: {
        type: "object",
        properties: {
          destination: { type: "string", enum: ["main", "ai-org"] },
          text: { type: "string" }
        },
        required: ["destination", "text"]
      }
    },
    scans: {
      type: "array",
      items: {
        type: "object",
        properties: {
          key: { type: "string" },
          hit: { type: "boolean" }
        },
        required: ["key", "hit"]
      }
    }
  },
  required: ["status", "summary", "reports"]
};

const fileSchema = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["success", "failed"] },
    summary: { type: "string" },
    reports: {
      type: "array",
      items: {
        type: "object",
        properties: {
          destination: { type: "string", enum: ["main", "ai-org"] },
          text: { type: "string" }
        },
        required: ["destination", "text"]
      }
    },
    filed: {
      type: "array",
      items: {
        type: "object",
        properties: {
          key: { type: "string" },
          number: { type: "number" },
          url: { type: "string" },
          kind: { type: "string", enum: ["codable", "rewrite", "contradiction"] }
        },
        required: ["key", "number", "url", "kind"]
      }
    }
  },
  required: ["status", "summary", "reports"]
};

const recordSchema = {
  type: "object",
  properties: {
    status: { type: "string", enum: ["success", "failed"] },
    summary: { type: "string" },
    reports: {
      type: "array",
      items: {
        type: "object",
        properties: {
          destination: { type: "string", enum: ["main", "ai-org"] },
          text: { type: "string" }
        },
        required: ["destination", "text"]
      }
    },
    judged_count: { type: "number" },
    queued_count: { type: "number" },
    advanced_repos: { type: "array", items: { type: "string" } }
  },
  required: ["status", "summary", "reports"]
};

const commonRules = [
  "今日の日付は " + date + " です。",
  "設定ファイル: " + CONFIG_PATH + "。CLIは " + PYTHON + " -m codify_taskforce で、リポジトリ " + TASKFORCE_REPO + " を作業ディレクトリにして実行する。環境変数 CODIFY_TASKFORCE_CONFIG に設定ファイルのパスを渡す。",
  "対象mdは読み取り専用。走査段で対象ファイルには触らない（書き換えは開発段の仕事で、このワークフローでは行わない）。",
  "原文の引用・転載はしない。要約は具体値（接続先、ホスト名、固有名、数値）でなく、変数名・構造（例: 「上限チェック」）で書く。",
  "失敗したら1回で諦めない。原因を特定し、手段や順序を変えて再試行する（最大3回。同じ手段の単純な繰り返しは数えない）。",
  "結果は必ず schema の形で返す: status（成功なら success、自分の役目を完了できなかったら failed）、summary（実施内容の短い要約）、reports（報告の配列、無ければ空配列）。報告は自分でチャットに送信せず、結果の reports に {destination, text} の形で入れて返すこと（destination は \"main\"＝メインチャット、\"ai-org\"＝AI組織チャット）。"
].join("\n");

function retryNote(last, attempt) {
  if (!last) return "";
  return "これは再実行（" + attempt + "回目）です。前回の試行は次の理由で失敗しました：" + last.summary + "。原因を踏まえ、手段や順序を変えて実施してください。書き込みや送信は対象を読み直してから行い、済んだ分を二重に行わないでください。";
}

async function runStage(stageKey, label, buildPrompt, schema) {
  let last = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    let res = null;
    try {
      res = await agent(buildPrompt(last, attempt), {
        key: stageKey + "-" + attempt,
        label: attempt > 1 ? label + "（再実行" + attempt + "回目）" : label,
        schema: schema || stageSchema,
        timeoutMs: 1800000
      });
    } catch (e) {
      res = { status: "failed", summary: "エージェントが異常終了した: " + String((e && e.message) || e), reports: [] };
    }
    if (res && res.status === "success") return res;
    last = res || { status: "failed", summary: "結果が返らなかった", reports: [] };
    log(label + " attempt " + attempt + " failed", { summary: last.summary });
  }
  return last;
}

function stageSummary(stage) {
  return { status: stage.status, summary: stage.summary };
}

function publicFiled(filedList) {
  return (filedList || []).map((f) => ({ number: f.number, url: f.url, kind: f.kind }));
}

function earlyReturn(stages, filed, queued, reports, failedStageName) {
  return {
    stages: stages,
    filed: publicFiled(filed),
    queued: queued,
    reports: reports.concat([
      { destination: "ai-org", text: "🤖AI組織・事務連絡 コード化タスクフォース週次実行: 「" + failedStageName + "」が失敗したため、状態は進めず次週に同じ差分を再走査します。" }
    ])
  };
}

phase("extract");
const extract = await runStage("extract-diff", "1段目：差分抽出", (last, attempt) =>
  [
    "あなたは「コード化タスクフォースの差分抽出」専任のエージェントです。週次ハーネスは7段（差分抽出→候補抽出→相反点検→判定→秘匿チェック→起票→記録）で、あなたは1段目です。役割は1つだけ: CLI の extract を実行し、今回の走査対象セクションを構造化して返すこと。対象mdへの書き込みは絶対にしないでください。",
    commonRules,
    "手順:",
    "1. CLI の extract 結果の limits に設定ファイルの上限値（weekly_issue_cap、daily_presentation_cap）が入る。その値を結果の limits に入れる。trial_weeks、checklist_version、org_repo は設定ファイル（" + CONFIG_PATH + "）を読んで結果に含める。",
    "2. CLI を実行する: " + (firstRun ? "初回のため `extract --full`（全件走査）" : "`extract`（前回成功時コミットからのコミット済み差分）") + "。未コミットの変更は対象にならない。",
    "3. 結果の files をそのまま返す（repo・path・sections の heading・section_hash・previously_judged）。セクション本文は結果に含めないこと（原文の引用・転載をしない）。",
    "4. 判定済み（previously_judged がある）セクションも files には含めたまま返す。候補抽出の対象外になるのは起票済み・不可・要確認のものだけで、未起票の「可」「書き換え可」は繰り越しとして候補抽出段が再提示する（判定段が再提示の抑制を確認するため）。",
    "5. 3回再試行しても抽出自体ができなかった場合は status を failed にする。",
    retryNote(last, attempt)
  ].join("\n"), extractSchema
);
log("extract finished", { status: extract.status, mode: extract.mode || "", files: (extract.files || []).length });

if (extract.status !== "success") {
  return earlyReturn(
    { extract: stageSummary(extract), candidates: { status: "skipped", summary: "前段の失敗のため未実施" }, contradictions: { status: "skipped", summary: "前段の失敗のため未実施" }, judge: { status: "skipped", summary: "前段の失敗のため未実施" }, secrecy: { status: "skipped", summary: "前段の失敗のため未実施" }, file: { status: "skipped", summary: "前段の失敗のため未実施" }, record: { status: "skipped", summary: "前段の失敗のため未実施" } },
    [], 0, extract.reports || [], "差分抽出"
  );
}

// 起票上限は CLI の extract 結果の limits から読む。値が取れなければ
// 無言で既定値にせず、この段を failed として扱う。
const limits = extract.limits || {};
const weeklyIssueCap = limits.weekly_issue_cap;
if (typeof weeklyIssueCap !== "number" || typeof limits.daily_presentation_cap !== "number") {
  const failedExtract = { status: "failed", summary: "extract 結果に limits（weekly_issue_cap / daily_presentation_cap）が無いため起票上限を決められない" };
  log("extract limits missing", { summary: failedExtract.summary });
  return earlyReturn(
    { extract: stageSummary(failedExtract), candidates: { status: "skipped", summary: "前段の失敗のため未実施" }, contradictions: { status: "skipped", summary: "前段の失敗のため未実施" }, judge: { status: "skipped", summary: "前段の失敗のため未実施" }, secrecy: { status: "skipped", summary: "前段の失敗のため未実施" }, file: { status: "skipped", summary: "前段の失敗のため未実施" }, record: { status: "skipped", summary: "前段の失敗のため未実施" } },
    [], 0, extract.reports || [], "差分抽出"
  );
}
const trialWeeks = typeof extract.trial_weeks === "number" ? extract.trial_weeks : 0;
const trialMode = trialWeeks > 0;

phase("candidates");
const candidates = await runStage("extract-candidates", "2段目：候補抽出（コード化＋書き換え）", (last, attempt) =>
  [
    "あなたは「コード化タスクフォースの候補抽出」専任のエージェントです。週次ハーネスの2段目です。役割は1つだけ: 差分対象のセクションから、コード化候補と書き換え候補を抽出し、構造化して返すこと。対象mdへの書き込みは絶対にしないでください。",
    commonRules,
    "差分対象のセクション（repo・path・heading・section_hash・previously_judged の一覧）:",
    JSON.stringify(extract.files || []),
    "判定基準文書: " + CRITERIA_PATH + "（docs/CRITERIA.md）。",
    "抽出のルール:",
    "- previously_judged があるセクションは、原則として候補にしない（判定済みは実質変更なしに再提示しない）。ただし例外として、previously_judged の verdict_code が \"codable\" または \"rewritable\" で issue_number が無い（未起票＝前回までに上限で繰り越された）セクションは、繰り越し分として同じ kind で候補に再提示する（起票済み・not_codable・needs_user の再提示はしない）。",
    "- コード化候補（kind: \"codable\"）: チェックリストの必須項目（入力の出所／出力／合否条件／失敗時の動作／正常系・異常系テスト例各1件）が見込めるルール。必須項目は分かる範囲で埋め、分からなければ空文字にする（空欄のまま「可」にしないのは判定段とハーネスのゲートの仕事）。",
    "- 書き換え候補（kind: \"rewrite\"）: docs/CRITERIA.md の「わかりにくさの判定基準」に当たる書き方のルール（曖昧な数量詞が本質に含まれる／長い・入れ子・二重否定の文／読む人依存の解釈が残る／条件と例外が分離していない）。書き換え自体はここでは行わない（開発段の仕事）。",
    "- 候補は candidates に1件ずつ入れる: kind、repo、path、heading、section_hash、title（短い件名）、summary（変数名・構造での要約。具体値は書かない）、has_precedent（過去に実際に漏れた実績のあるルールなら true）、checklist（6項目。コード化候補のみ）。",
    "- 候補が0件でも成功とする（summary に「候補0件」と書く）。",
    "- 3回再試行しても抽出自体ができなかった場合は status を failed にする。",
    retryNote(last, attempt)
  ].join("\n"), candidatesSchema
);
log("candidates finished", { status: candidates.status, count: (candidates.candidates || []).length });

if (candidates.status !== "success") {
  return earlyReturn(
    { extract: stageSummary(extract), candidates: stageSummary(candidates), contradictions: { status: "skipped", summary: "前段の失敗のため未実施" }, judge: { status: "skipped", summary: "前段の失敗のため未実施" }, secrecy: { status: "skipped", summary: "前段の失敗のため未実施" }, file: { status: "skipped", summary: "前段の失敗のため未実施" }, record: { status: "skipped", summary: "前段の失敗のため未実施" } },
    [], 0, (extract.reports || []).concat(candidates.reports || []), "候補抽出"
  );
}

phase("contradictions");
const contradictions = await runStage("check-contradictions", "3段目：相反点検", (last, attempt) =>
  [
    "あなたは「コード化タスクフォースの相反点検」専任のエージェントです。週次ハーネスの3段目です。役割は1つだけ: allowlist 全ファイルの現行文を対象に、ルール間の矛盾（相反）を抽出し、構造化して返すこと。対象mdへの書き込みは絶対にしないでください。",
    commonRules,
    "設定ファイル: " + CONFIG_PATH + "（allowlist・denylist を読む）。判定基準文書: " + CRITERIA_PATH + "。",
    "相反とは docs/CRITERIA.md の「相反の判定基準」のいずれか: 同一対象・同一条件で結論が食い違う／同一トリガで異なる動作をする／数値・期限・上限が不一致である／適用範囲の重なりで矛盾する。",
    "抽出のルール:",
    "- 対象は allowlist 全ファイルの現行文（差分対象に限らない）。ファイルは読み取り専用で読む。",
    "- 候補抽出段の候補（下記）と相反する既存ルールがあれば、その照合もここで行い、相反候補として出す。",
    JSON.stringify(candidates.candidates || []),
    "- 候補は candidates に1件ずつ入れる: kind は \"contradiction\" 固定、repo・path・heading・section_hash（相反の一方のルールがある場所。両方の場所は summary に構造で書く）、title（短い件名）、summary（どことどこがどう食い違うか。変数名・構造で書く。具体値は書かない）、has_precedent（過去に実際に漏れた実績のあるルールなら true）。",
    "- 相反が0件でも成功とする（summary に「相反0件」と書く）。",
    "- 3回再試行しても点検自体ができなかった場合は status を failed にする。",
    retryNote(last, attempt)
  ].join("\n"), candidatesSchema
);
log("contradictions finished", { status: contradictions.status, count: (contradictions.candidates || []).length });

if (contradictions.status !== "success") {
  return earlyReturn(
    { extract: stageSummary(extract), candidates: stageSummary(candidates), contradictions: stageSummary(contradictions), judge: { status: "skipped", summary: "前段の失敗のため未実施" }, secrecy: { status: "skipped", summary: "前段の失敗のため未実施" }, file: { status: "skipped", summary: "前段の失敗のため未実施" }, record: { status: "skipped", summary: "前段の失敗のため未実施" } },
    [], 0, (extract.reports || []).concat(candidates.reports || []).concat(contradictions.reports || []), "相反点検"
  );
}

const allCandidates = (candidates.candidates || []).concat(contradictions.candidates || []);

phase("judge");
const judge = await runStage("judge-candidates", "4段目：判定", (last, attempt) =>
  [
    "あなたは「コード化タスクフォースの判定」専任のエージェントです。週次ハーネスの4段目です。役割は1つだけ: 候補を docs/CRITERIA.md のチェックリストで2軸に分類すること。対象mdへの書き込みは絶対にしないでください。",
    commonRules,
    "判定基準文書: " + CRITERIA_PATH + "。",
    "判定対象の候補:",
    JSON.stringify(allCandidates),
    "分類のルール:",
    "- 第1軸「コード化可否」: codable（可）／rewritable（書き換え可。書き換え候補のみ）／not_codable（不可）／needs_user（要・本人確認）。チェックリストの必須項目（入力の出所／出力／合否条件／失敗時の動作／正常系・異常系テスト例各1件）が1つでも欠ければ「可」にしない（「要・本人確認」以下に落とす）。",
    "- 第2軸「リスク」: guard（要ガード）の有無。不可逆操作（送信・削除・支払い等）や情報漏洩に触れる候補は guard を true にする（可否とは別軸）。",
    "- 相反候補（kind: \"contradiction\"）は、正の判断（どちらを正とするか）が要る場合は needs_user にする。",
    "- 書き換え候補（kind: \"rewrite\"）はコード化チェックリストではなく「わかりにくさの判定基準」で判定する。基準に当たり、本人の原則に従う書き換えで解消できる場合は verdict を \"rewritable\"（書き換え可）とする（コード化可否の物差しで \"not_codable\" にしない）。書き換えでも解消できない場合は \"not_codable\"、本人の判断が要る場合は \"needs_user\" とする（書き換えの実施は開発段の仕事で、ここでは判定だけ）。",
    "- 同じセクションに複数の候補（例: コード化候補と書き換え候補）が来ている場合は、それぞれを独立に判定して返してよい。同一セクションへの判定の衝突の解消はハーネスのコード側が行う（上書きはしない）。 繰り越し分として再提示された候補（前週に上限で繰り越された未起票の「可」「書き換え可」）も、新規候補と同じくチェックリストで改めて判定する（前週の判定をそのまま引き継がない）。再判定で「可」「書き換え可」になれば起票上限ゲートの対象になる。",
    "- judged に1件ずつ入れる: kind、repo、path、heading、section_hash、title、verdict、guard、has_precedent、reason_code（needs_user の理由を短い符号で。例: \"checklist-incomplete\"、\"needs-decision\"、\"unclear-rule\"）、checklist（6項目。分かる範囲で埋める）。",
    "- 候補が0件でも成功とする。",
    "- 3回再試行しても判定自体ができなかった場合は status を failed にする。",
    retryNote(last, attempt)
  ].join("\n"), judgeSchema
);
log("judge finished", { status: judge.status, count: (judge.judged || []).length });

if (judge.status !== "success") {
  return earlyReturn(
    { extract: stageSummary(extract), candidates: stageSummary(candidates), contradictions: stageSummary(contradictions), judge: stageSummary(judge), secrecy: { status: "skipped", summary: "前段の失敗のため未実施" }, file: { status: "skipped", summary: "前段の失敗のため未実施" }, record: { status: "skipped", summary: "前段の失敗のため未実施" } },
    [], 0, (extract.reports || []).concat(candidates.reports || []).concat(contradictions.reports || []).concat(judge.reports || []), "判定"
  );
}

// 判定の衝突解消ゲート（コード側）。同一セクションへの矛盾する2判定を
// 上書きせず、衝突として「要・本人確認」へ畳む。
const resolvedJudged = resolveVerdictConflicts(judge.judged || []);
log("verdict conflict gate applied", { total: (judge.judged || []).length, resolved: resolvedJudged.length, conflicts: resolvedJudged.filter((c) => c.reason_code === "verdict-conflict").length });

// 判定の必須項目ゲート（コード側）。必須項目が欠けた「可」は「要・本人確認」へ落とす。
const gatedJudged = applyChecklistGate(resolvedJudged);
log("checklist gate applied", { total: gatedJudged.length, codable: gatedJudged.filter((c) => c.verdict === "codable").length, needsUser: gatedJudged.filter((c) => c.verdict === "needs_user").length });

phase("secrecy");
const secrecy = await runStage("check-secrecy", "5段目：秘匿チェック", (last, attempt) =>
  [
    "あなたは「コード化タスクフォースの秘匿チェック」専任のエージェントです。週次ハーネスの5段目です。役割は1つだけ: 起票する Issue 本文の案を CLI の scan-text で検査し、候補ごとのヒット有無を構造化して返すこと。対象mdへの書き込みは絶対にしないでください。",
    commonRules,
    "判定済みの候補（verdict が codable／rewritable のものが起票対象）:",
    JSON.stringify(gatedJudged),
    "手順:",
    "1. verdict が codable または rewritable の候補ごとに、Issue 本文の案を作る。原文の引用・コードブロックでの転載はしない。要約は具体値（接続先、ホスト名、固有名、数値）でなく、変数名・構造（例: 「上限チェック」）で書く。対象mdへの参照（リポジトリ内はパスと見出し）、判定理由、想定する担保の形（検証コード／ハーネスの段／カウンタ等）を書く。書き換え候補の本文案には、わかりにくさの判定基準のどれに当たるかと、書き換えの原則（シンプルに・短く・平易に、意味の保存）に従う旨を書く。",
    "2. 本文の案を1候補1ファイルの一時ファイルに書き（ワークフロー作業ディレクトリ配下）、CLI `scan-text --file <一時ファイル>` で検査する。ローカルパターンファイルは設定の local_patterns_file を使う（CLIが設定から読む）。",
    "3. scans に1件ずつ入れる: key（repo・path・heading・section_hash を \"|\" で結合したもの）、hit（ヒットすれば true）。verdict が codable でも rewritable でもない候補は scans に入れない。",
    "4. ヒットした候補の起票可否の判断はハーネスのコード側が行うので、あなたはヒット有無だけを返す（起票を止める判断はしない）。",
    "5. 3回再試行しても検査自体ができなかった場合は status を failed にする。",
    retryNote(last, attempt)
  ].join("\n"), secrecySchema
);
log("secrecy finished", { status: secrecy.status, scans: (secrecy.scans || []).length });

if (secrecy.status !== "success") {
  return earlyReturn(
    { extract: stageSummary(extract), candidates: stageSummary(candidates), contradictions: stageSummary(contradictions), judge: stageSummary(judge), secrecy: stageSummary(secrecy), file: { status: "skipped", summary: "前段の失敗のため未実施" }, record: { status: "skipped", summary: "前段の失敗のため未実施" } },
    [], 0, (extract.reports || []).concat(candidates.reports || []).concat(contradictions.reports || []).concat(judge.reports || []).concat(secrecy.reports || []), "秘匿チェック"
  );
}

// 秘匿ゲート（コード側）。scan-text でヒットした候補は起票せず「要・本人確認」へ回す。
const finalJudged = applySecrecyGate(gatedJudged, secrecy.scans || []);
log("secrecy gate applied", { codable: finalJudged.filter((c) => c.verdict === "codable").length, needsUser: finalJudged.filter((c) => c.verdict === "needs_user").length });

// 起票上限ゲート（コード側）。上限を超えた分は翌週へ繰り越す（破棄しない）。
const filingSplit = applyFilingCap(finalJudged, weeklyIssueCap);
const fileNow = trialMode ? [] : filingSplit.fileNow;
const carriedOver = trialMode ? filingSplit.fileNow.concat(filingSplit.carriedOver) : filingSplit.carriedOver;
log("filing cap applied", { fileNow: fileNow.length, carriedOver: carriedOver.length, trialMode: trialMode });

let filed = [];
let fileStage = { status: "skipped", summary: trialMode ? "試行期間（提示のみ）のため起票しない" : "起票対象0件のため未実施" };
if (fileNow.length > 0) {
  phase("file");
  const file = await runStage("file-issues", "6段目：起票", (last, attempt) =>
    [
      "あなたは「コード化タスクフォースの起票」専任のエージェントです。週次ハーネスの6段目です。役割は1つだけ: 起票対象の候補を " + ORG_REPO + " へ Issue として起票すること。gh は " + GH + " を使う。対象mdへの書き込みは絶対にしないでください。",
      commonRules,
      "起票対象の候補（優先順・上限はハーネスのコード側で決め済み。この全件を起票する）:",
      JSON.stringify(fileNow),
      "起票のルール:",
      "- 起票前に既存 Issue（閉じた分を含む）と #19 の候補・台帳行を照合し、重なれば新規起票せず既存側へリンクする（その候補は filed に入れず、summary に「既存 Issue #N へリンク」と書く）。",
      "- Issue 本文には対象mdへの参照（リポジトリ内はパスと見出し）、判定理由、想定する担保の形（検証コード／ハーネスの段／カウンタ等）を書く。原文の引用・コードブロックでの転載はしない。要約は具体値でなく変数名・構造で書く。",
      "- タイトルは「何についてのIssueか」だけを表す形にする（状態や進捗はタイトルに入れない）。",
      "- 起票は `gh issue create --repo " + ORG_REPO + " --title ... --body ...` で行う。本文の案は秘匿チェック済みのものを使う（秘匿チェックを受けていない本文案で起票しない）。",
      "- filed に1件ずつ入れる: key（候補の repo・path・heading・section_hash を \"|\" で結合したもの）、number（Issue番号）、url、kind（codable／rewrite／contradiction）。",
      "- 起票対象が0件の場合は成功として filed を空にする。",
      "- 3回再試行しても起票自体ができなかった場合は status を failed にする（再実行時は既存 Issue との照合をやり直し、二重起票しないこと）。",
      retryNote(last, attempt)
    ].join("\n"), fileSchema
  );
  log("file finished", { status: file.status, filed: (file.filed || []).length });
  fileStage = file;
  if (file.status !== "success") {
    return earlyReturn(
      { extract: stageSummary(extract), candidates: stageSummary(candidates), contradictions: stageSummary(contradictions), judge: stageSummary(judge), secrecy: stageSummary(secrecy), file: stageSummary(file), record: { status: "skipped", summary: "前段の失敗のため未実施" } },
      [], 0, (extract.reports || []).concat(candidates.reports || []).concat(contradictions.reports || []).concat(judge.reports || []).concat(secrecy.reports || []).concat(file.reports || []), "起票"
    );
  }
  filed = file.filed || [];
}

// 記録段: queue-add → state-commit の順に行い、state-commit を最後にすることで、
// 途中で失敗したら状態を進めず、次週に同じ差分を再走査する。
// 繰り越しがあるリポジトリは last_success_commit を進めない（翌週に同じ差分を
// 再走査し、繰り越し分を優先的に起票する。判定済みのうち起票済み・不可・
// 要確認は previously_judged で再提示しない（未起票の「可」「書き換え可」は
// 繰り越しとして再提示する）。
const issueNumbers = {};
for (const f of filed) {
  if (f.key) issueNumbers[f.key] = f.number;
}
const reposWithCarryover = {};
for (const c of carriedOver) {
  reposWithCarryover[c.repo] = true;
}
const advanceRepos = [];
for (const f of extract.files || []) {
  if (!reposWithCarryover[f.repo] && advanceRepos.indexOf(f.repo) < 0) {
    advanceRepos.push(f.repo);
  }
}
for (const c of finalJudged) {
  if (!reposWithCarryover[c.repo] && advanceRepos.indexOf(c.repo) < 0) {
    advanceRepos.push(c.repo);
  }
}

phase("record");
const record = await runStage("record-results", "7段目：記録", (last, attempt) =>
  [
    "あなたは「コード化タスクフォースの記録」専任のエージェントです。週次ハーネスの7段目です。役割は1つだけ: 判定記録と前回成功コミットの前進（state-commit）と、「要・本人確認」の候補の確認待ちキューへの追加（queue-add）を行い、週次報告を返すこと。対象mdへの書き込みは絶対にしないでください。",
    commonRules,
    "判定済みの候補（ゲート適用後の最終判定）:",
    JSON.stringify(finalJudged),
    "起票済みの Issue（候補キー→Issue番号）:",
    JSON.stringify(issueNumbers),
    "繰り越し分（上限超過のため今週は起票しない候補）:",
    JSON.stringify(carriedOver),
    "前進させるリポジトリ（繰り越しがあるリポジトリは含まない）:",
    JSON.stringify(advanceRepos),
    "手順（この順序を守ること）:",
    "1. queue-add: verdict が needs_user の候補を、確認待ちキューへ追加する（判定の衝突で落とされた候補も含む。その場合 reason_code は \"verdict-conflict\" になっている）。入力は [{key, title, repo, path, heading, section_hash, reason_code, created}] の配列で、key は repo・path・heading・section_hash を \"|\" で結合したもの、created は今日の日付（" + date + "）の ISO 時刻、reason_code は候補の reason_code（無ければ \"needs_user\"）。CLI: `queue-add --file <items.json>`。同じ key の pending が既にあれば追加されない（重複排除）。",
    "2. state-commit: 判定記録と前回成功コミットの前進を反映する。入力は {judged: [{repo, path, heading, section_hash, verdict_code, kind, issue_number, guard}], advance_commits: {repo: commit}}。judged には最終判定の全件を入れる（起票済みは issue_number に「起票済みの Issue（候補キー→Issue番号）」の対応表の番号、繰り越し分は verdict_code は判定のまま（\"codable\"／\"rewritable\"）・issue_number null、needs_user・not_codable は issue_number null）。kind（codable／rewrite／contradiction）も必ず入れる（状態記録に種別を残すため）。同一セクションに複数の判定が入った場合、CLI は優先順位（起票済み＞可・未起票＞要・本人確認＞不可）で1件にマージして記録する（上書きで片方を黙って消さない）。本文・要約・判定理由は入力に入れない（状態ファイルに保持しない項目のため）。advance_commits には前進させるリポジトリだけを入れ、各リポジトリの HEAD コミット（`git -C <repo> rev-parse HEAD`）を値にする。CLI: `state-commit --file <results.json>`。",
    "3. 週次報告を reports（destination は \"ai-org\"）に1件入れる: 「🤖AI組織・事務連絡 コード化タスクフォース週次実行（" + date + "）: 抽出○件・起票○件（Issue番号つき）・確認待ち○件・繰り越し○件」の形で、起票した Issue の URL を付ける。起票も確認待ちも無い週は「今週の候補はありません」と書く。",
    "4. 3回再試行しても記録自体ができなかった場合は status を failed にする（state-commit が失敗したら状態は進んでいない。queue-add 済みの分は重複排除されるため再実行で二重追加されない）。",
    retryNote(last, attempt)
  ].join("\n"), recordSchema
);
log("record finished", { status: record.status });

const allReports = []
  .concat(extract.reports || [])
  .concat(candidates.reports || [])
  .concat(contradictions.reports || [])
  .concat(judge.reports || [])
  .concat(secrecy.reports || [])
  .concat(record.reports || []);

if (record.status !== "success") {
  return earlyReturn(
    { extract: stageSummary(extract), candidates: stageSummary(candidates), contradictions: stageSummary(contradictions), judge: stageSummary(judge), secrecy: stageSummary(secrecy), file: stageSummary(fileStage), record: stageSummary(record) },
    filed, 0, allReports, "記録"
  );
}

const queuedCount = typeof record.queued_count === "number" ? record.queued_count : finalJudged.filter((c) => c.verdict === "needs_user").length;

return {
  stages: {
    extract: stageSummary(extract),
    candidates: stageSummary(candidates),
    contradictions: stageSummary(contradictions),
    judge: stageSummary(judge),
    secrecy: stageSummary(secrecy),
    file: stageSummary(fileStage),
    record: stageSummary(record)
  },
  filed: publicFiled(filed),
  queued: queuedCount,
  reports: allReports
};
