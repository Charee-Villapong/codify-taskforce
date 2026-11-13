// ワークフロー内の3つの純粋関数ゲート（チェックリスト／秘匿／起票上限）の
// 自動テスト。公開コピーのマーカー区間
// `// === CODIFY-GATES-BEGIN ===` 〜 `// === CODIFY-GATES-END ===` を
// 抽出して eval し、正常系・異常系を検証する。実働ファイルが存在すれば、
// ゲート区間が公開コピーと同一であることも確認する。
//
// 実行: node --test tests/node/

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const BEGIN_MARKER = "// === CODIFY-GATES-BEGIN ===";
const END_MARKER = "// === CODIFY-GATES-END ===";

const PUBLIC_COPY = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "codify-taskforce-weekly.js",
);
const LIVE_WORKFLOW =
  process.env.CODIFY_WORKFLOW_PATH ||
  join(homedir(), "workspace", ".jarvis", "workflows", "codify-taskforce-weekly.js");

function extractGates(source) {
  const start = source.indexOf(BEGIN_MARKER);
  const stop = source.indexOf(END_MARKER, start);
  assert.ok(start !== -1, "CODIFY-GATES-BEGIN マーカーが無い");
  assert.ok(stop !== -1, "CODIFY-GATES-END マーカーが無い");
  assert.ok(start < stop, "BEGIN マーカーが END マーカーより後に有る");
  return source.slice(start + BEGIN_MARKER.length, stop);
}

function loadGates(source) {
  const section = extractGates(source);
  return eval(
    section +
      "\n;({ checklistComplete, applyChecklistGate, applySecrecyGate, applyFilingCap, filingPriority, candidateKey })",
  );
}

const publicSource = readFileSync(PUBLIC_COPY, "utf8");
const gates = loadGates(publicSource);

const FULL_CHECKLIST = {
  input_source: "設定ファイル",
  output: "Issue",
  pass_condition: "上限以内",
  failure_action: "翌週へ繰り越す",
  test_normal: "上限以内の起票",
  test_abnormal: "上限超過の繰り越し",
};

function candidate(overrides = {}) {
  return {
    kind: "codable",
    repo: "owner/repo",
    path: "rules/a.md",
    heading: "Alpha",
    section_hash: "a".repeat(64),
    verdict: "codable",
    has_precedent: false,
    checklist: { ...FULL_CHECKLIST },
    ...overrides,
  };
}

test("ゲート区間のマーカーが公開コピーに有る", () => {
  assert.ok(publicSource.includes(BEGIN_MARKER));
  assert.ok(publicSource.includes(END_MARKER));
});

test("実働ファイルと公開コピーでゲート区間が同一", (t) => {
  if (!existsSync(LIVE_WORKFLOW)) {
    t.skip("実働ファイルが無いため同一性チェックをスキップする");
    return;
  }
  const liveSource = readFileSync(LIVE_WORKFLOW, "utf8");
  assert.equal(extractGates(liveSource), extractGates(publicSource));
});

// ----------------------------------------------------------------------
// チェックリストゲート
// ----------------------------------------------------------------------

test("チェックリスト: 必須項目が揃った「可」はそのまま", () => {
  assert.equal(gates.checklistComplete(candidate()), true);
  const [gated] = gates.applyChecklistGate([candidate()]);
  assert.equal(gated.verdict, "codable");
  assert.equal(gated.gate_note, undefined);
});

test("チェックリスト: 必須項目が1つ欠けた「可」は「要・本人確認」へ落とす", () => {
  const incomplete = candidate();
  delete incomplete.checklist.pass_condition;
  assert.equal(gates.checklistComplete(incomplete), false);
  const [gated] = gates.applyChecklistGate([incomplete]);
  assert.equal(gated.verdict, "needs_user");
  assert.equal(gated.gate_note, "checklist-incomplete");
});

test("チェックリスト: 空白だけの項目は欠けとみなす", () => {
  const blank = candidate({
    checklist: { ...FULL_CHECKLIST, output: "   " },
  });
  const [gated] = gates.applyChecklistGate([blank]);
  assert.equal(gated.verdict, "needs_user");
  assert.equal(gated.gate_note, "checklist-incomplete");
});

test("チェックリスト: checklist 自体が無い「可」も落とす", () => {
  const noChecklist = candidate();
  delete noChecklist.checklist;
  const [gated] = gates.applyChecklistGate([noChecklist]);
  assert.equal(gated.verdict, "needs_user");
});

test("チェックリスト: 「可」でない候補は触らない", () => {
  const other = candidate({ verdict: "needs_user" });
  delete other.checklist.test_normal;
  const [gated] = gates.applyChecklistGate([other]);
  assert.equal(gated.verdict, "needs_user");
  assert.equal(gated.gate_note, undefined);
});

// ----------------------------------------------------------------------
// 秘匿ゲート
// ----------------------------------------------------------------------

test("秘匿: scan-text でヒットした「可」は起票せず「要・本人確認」へ回す", () => {
  const c = candidate();
  const scans = [{ key: gates.candidateKey(c), hit: true }];
  const [gated] = gates.applySecrecyGate([c], scans);
  assert.equal(gated.verdict, "needs_user");
  assert.equal(gated.gate_note, "secrecy-hit");
});

test("秘匿: ヒットが無い「可」はそのまま", () => {
  const c = candidate();
  const scans = [{ key: gates.candidateKey(c), hit: false }];
  const [gated] = gates.applySecrecyGate([c], scans);
  assert.equal(gated.verdict, "codable");
});

test("秘匿: 別候補のヒットは影響しない", () => {
  const c = candidate();
  const other = candidate({ heading: "Other" });
  const scans = [{ key: gates.candidateKey(other), hit: true }];
  const [gated] = gates.applySecrecyGate([c], scans);
  assert.equal(gated.verdict, "codable");
});

test("秘匿: 「可」でない候補は触らない", () => {
  const c = candidate({ verdict: "not_codable" });
  const scans = [{ key: gates.candidateKey(c), hit: true }];
  const [gated] = gates.applySecrecyGate([c], scans);
  assert.equal(gated.verdict, "not_codable");
  assert.equal(gated.gate_note, undefined);
});

// ----------------------------------------------------------------------
// 起票上限ゲート
// ----------------------------------------------------------------------

test("起票上限: 優先順は相反＞実績あり＞コード化候補＞書き換え候補", () => {
  const rewrite = candidate({ kind: "rewrite", heading: "Rewrite" });
  const plain = candidate({ kind: "codable", heading: "Plain" });
  const precedent = candidate({
    kind: "codable",
    heading: "Precedent",
    has_precedent: true,
  });
  const contradiction = candidate({
    kind: "contradiction",
    heading: "Contradiction",
  });
  const { fileNow, carriedOver } = gates.applyFilingCap(
    [rewrite, plain, precedent, contradiction],
    10,
  );
  assert.deepEqual(
    fileNow.map((c) => c.heading),
    ["Contradiction", "Precedent", "Plain", "Rewrite"],
  );
  assert.deepEqual(carriedOver, []);
});

test("起票上限: 上限を超えた分は繰り越しへ回し、破棄しない", () => {
  const contradiction = candidate({
    kind: "contradiction",
    heading: "Contradiction",
  });
  const precedent = candidate({
    kind: "codable",
    heading: "Precedent",
    has_precedent: true,
  });
  const plain = candidate({ kind: "codable", heading: "Plain" });
  const rewrite = candidate({ kind: "rewrite", heading: "Rewrite" });
  const { fileNow, carriedOver } = gates.applyFilingCap(
    [rewrite, plain, precedent, contradiction],
    2,
  );
  assert.deepEqual(
    fileNow.map((c) => c.heading),
    ["Contradiction", "Precedent"],
  );
  assert.deepEqual(
    carriedOver.map((c) => c.heading),
    ["Plain", "Rewrite"],
  );
});

test("起票上限: 「可」でない候補は起票対象にも繰り越しにも入らない", () => {
  const ok = candidate({ heading: "Ok" });
  const needsUser = candidate({ heading: "NeedsUser", verdict: "needs_user" });
  const notCodable = candidate({
    heading: "NotCodable",
    verdict: "not_codable",
  });
  const { fileNow, carriedOver } = gates.applyFilingCap(
    [needsUser, notCodable, ok],
    10,
  );
  assert.deepEqual(
    fileNow.map((c) => c.heading),
    ["Ok"],
  );
  assert.deepEqual(carriedOver, []);
});

test("起票上限: 上限0なら全件が繰り越しになる", () => {
  const { fileNow, carriedOver } = gates.applyFilingCap(
    [candidate({ heading: "A" }), candidate({ heading: "B" })],
    0,
  );
  assert.deepEqual(fileNow, []);
  assert.equal(carriedOver.length, 2);
});
