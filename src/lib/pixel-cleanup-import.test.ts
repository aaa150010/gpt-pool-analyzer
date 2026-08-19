import assert from "node:assert/strict";
import test from "node:test";
import {
  buildFailedImportPayload,
  buildCleanupImportReport,
  extractImportAccounts,
  isAccountAlreadyExistsError,
  isSubscriptionLevelError,
  splitImportAccounts,
} from "./pixel-cleanup-import.ts";

test("extracts accounts and splits them into groups of ten", () => {
  const accounts = extractImportAccounts({ accounts: Array.from({ length: 21 }, (_, index) => ({ name: `user-${index}` })) });
  assert.deepEqual(splitImportAccounts(accounts).map((batch) => batch.length), [10, 10, 1]);
});

test("recognizes Chinese and English subscription level failures", () => {
  assert.equal(isSubscriptionLevelError("无法验证账号真实订阅等级，请稍后重试"), true);
  assert.equal(isSubscriptionLevelError("所选等级与账号真实订阅（team）不符"), true);
  assert.equal(isSubscriptionLevelError("network timeout"), false);
  assert.equal(isSubscriptionLevelError("profile request failed"), false);
});

test("maps batch error indexes back to the original account", () => {
  const accounts = Array.from({ length: 12 }, (_, index) => ({ name: `user-${index}` }));
  const report = buildCleanupImportReport({
    sourceFileName: "accounts.json",
    targetId: "target-1",
    targetEmail: "target@example.com",
    accounts,
    batches: [
      { index: 1, start: 0, accounts: 10, attempts: 1, status: "partial", error: null, results: [{
        targetId: "target-1",
        email: "target@example.com",
        generatedFileName: "batch.json",
        sourceCount: 10,
        created: 9,
        updated: 0,
        failed: 1,
        shared: 0,
        shareFailed: 0,
        failedShareIds: [],
        concurrencyById: {},
        importErrors: [{ index: "3", name: "", message: "所选等级与账号真实订阅（team）不符" }],
        generatedNames: [],
        status: "partial",
        message: "部分失败",
      }] },
    ],
  });
  assert.equal(report.levelErrors.length, 1);
  assert.equal(report.levelErrors[0].sourceIndex, 3);
  assert.equal((report.levelErrors[0].account as { name: string }).name, "user-3");
});

test("prefers the uploaded account name over a platform-generated error name", () => {
  const accounts = [{ name: "8月18日23.04发车-7d-buntingmark146442+bkgge team-5ebcda-960EBCDB9578" }];
  const report = buildCleanupImportReport({
    sourceFileName: "accounts.json",
    targetId: "target-1",
    targetEmail: "target@example.com",
    accounts,
    batches: [{
      index: 1,
      start: 0,
      accounts: 1,
      attempts: 1,
      status: "partial",
      error: null,
      results: [{
        targetId: "target-1",
        email: "target@example.com",
        generatedFileName: "batch.json",
        sourceCount: 1,
        created: 0,
        updated: 0,
        failed: 1,
        shared: 0,
        shareFailed: 0,
        failedShareIds: [],
        concurrencyById: {},
        importErrors: [{ index: "0", name: "acct-generated@gmail.com", message: "无法识别账号真实订阅等级，请稍后重试" }],
        generatedNames: [],
        status: "partial",
        message: "部分失败",
      }],
    }],
  });
  assert.equal(report.failures[0].name, accounts[0].name);
});

test("keeps the imported JSON shape while selecting failed accounts", () => {
  const source = { exported_at: "now", proxies: [], accounts: [{ name: "ok" }, { name: "failed" }, { name: "ok-2" }] };
  const payload = buildFailedImportPayload(source, [{
    batch: 1,
    sourceIndex: 1,
    index: "1",
    name: "failed",
    message: "等级不符",
    account: source.accounts[1],
    levelRelated: true,
    attempts: 1,
  }]);
  assert.deepEqual(payload, { exported_at: "now", proxies: [], accounts: [{ name: "failed" }] });
});

test("excludes account-already-exists failures from the downloadable payload", () => {
  assert.equal(isAccountAlreadyExistsError("account already exists"), true);
  assert.equal(isAccountAlreadyExistsError("账号已存在"), true);
  assert.equal(isAccountAlreadyExistsError("subscription level mismatch"), false);
  const source = { accounts: [{ name: "duplicate" }, { name: "level-failed" }] };
  const payload = buildFailedImportPayload(source, [
    {
      batch: 1,
      sourceIndex: 0,
      index: "0",
      name: "duplicate",
      message: "account already exists",
      account: source.accounts[0],
      levelRelated: false,
      attempts: 1,
    },
    {
      batch: 1,
      sourceIndex: 1,
      index: "1",
      name: "level-failed",
      message: "subscription level mismatch",
      account: source.accounts[1],
      levelRelated: true,
      attempts: 1,
    },
  ]);
  assert.deepEqual(payload, { accounts: [{ name: "level-failed" }] });
});
