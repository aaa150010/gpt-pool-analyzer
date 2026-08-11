import assert from "node:assert/strict";
import test from "node:test";
import { recoverPixelAccounts } from "./pixel-recovery.ts";
import type { PixelAccountPage, PixelTarget } from "./types.ts";

function target(id: string): PixelTarget {
  return { id, email: `${id}@example.com`, connected: true, accountCount: null, lastCheckedAt: null, error: null };
}

function page(value: PixelTarget, ids: number[], pageNumber: number, pages: number): PixelAccountPage {
  return {
    items: ids.map((id) => ({ id }) as PixelAccountPage["items"][number]),
    total: ids.length,
    page: pageNumber,
    pageSize: 100,
    pages,
    target: value,
  };
}

test("scans every page, batches tests, and shares all targets after testing", async () => {
  const targets = [target("one"), target("two")];
  const tested: Array<{ targetId: string; ids: number[] }> = [];
  const calls: string[] = [];
  const result = await recoverPixelAccounts({
    targets,
    operations: {
      listAccounts: async (targetId, pageNumber) => {
        calls.push(`list:${targetId}:${pageNumber}`);
        if (targetId === "one") return page(targets[0], pageNumber === 1 ? [1, 2] : [3], pageNumber, 2);
        return page(targets[1], [4], pageNumber, 1);
      },
      bulkTest: async (targetId, ids) => {
        tested.push({ targetId, ids });
        calls.push(`test:${targetId}`);
        return { ok: true, success: ids.length, failed: 0 };
      },
      shareAll: async (targetIds) => {
        calls.push(`share:${targetIds.join(",")}`);
        return { ok: true, status: "success", totalTargets: targetIds.length, total: 4, shared: 4, failed: 0, results: [], message: "共享完成" };
      },
    },
  });

  assert.deepEqual(tested, [
    { targetId: "one", ids: [1, 2, 3] },
    { targetId: "two", ids: [4] },
  ]);
  assert.equal(result.scannedAccounts, 4);
  assert.equal(result.testSuccess, 4);
  assert.equal(result.testFailed, 0);
  assert.equal(result.shareError, null);
  assert.equal(calls.at(-1), "share:one,two");
});

test("continues after test failures and always attempts sharing", async () => {
  const targets = [target("one"), target("two")];
  let shareCalls = 0;
  const result = await recoverPixelAccounts({
    targets,
    operations: {
      listAccounts: async (targetId, pageNumber) => page(targets.find((item) => item.id === targetId)!, [targetId === "one" ? 1 : 2], pageNumber, 1),
      bulkTest: async (targetId) => {
        if (targetId === "one") throw new Error("测试接口不可用");
        return { ok: true, success: 1, failed: 0 };
      },
      shareAll: async () => {
        shareCalls += 1;
        return { ok: true, status: "success", totalTargets: 2, total: 2, shared: 2, failed: 0, results: [], message: "共享完成" };
      },
    },
  });

  assert.equal(shareCalls, 1);
  assert.equal(result.testSuccess, 1);
  assert.equal(result.testFailed, 1);
  assert.match(result.results[0].error ?? "", /测试接口不可用/);
});

test("tests accounts in batches of 100 without duplicate ids", async () => {
  const value = target("one");
  const accountIds = Array.from({ length: 205 }, (_, index) => index + 1);
  const batches: number[][] = [];
  const result = await recoverPixelAccounts({
    targets: [value],
    operations: {
      listAccounts: async (_targetId, pageNumber) => page(
        value,
        accountIds.slice((pageNumber - 1) * 100, pageNumber * 100),
        pageNumber,
        3,
      ),
      bulkTest: async (_targetId, ids) => {
        batches.push(ids);
        return { ok: true, success: ids.length, failed: 0 };
      },
      shareAll: async () => ({ ok: true, status: "success", totalTargets: 1, total: 205, shared: 205, failed: 0, results: [], message: "共享完成" }),
    },
  });

  assert.deepEqual(batches.map((batch) => batch.length), [100, 100, 5]);
  assert.equal(new Set(batches.flat()).size, 205);
  assert.equal(result.testedAccounts, 205);
});

test("continues with other targets when one target cannot be scanned", async () => {
  const targets = [target("one"), target("two")];
  const tested: string[] = [];
  const result = await recoverPixelAccounts({
    targets,
    operations: {
      listAccounts: async (targetId, pageNumber) => {
        if (targetId === "one") throw new Error("账号列表不可用");
        return page(targets[1], [7], pageNumber, 1);
      },
      bulkTest: async (targetId, ids) => {
        tested.push(targetId);
        return { ok: true, success: ids.length, failed: 0 };
      },
      shareAll: async () => ({ ok: true, status: "success", totalTargets: 2, total: 1, shared: 1, failed: 0, results: [], message: "共享完成" }),
    },
  });

  assert.deepEqual(tested, ["two"]);
  assert.match(result.results[0].error ?? "", /账号列表不可用/);
  assert.equal(result.results[1].success, 1);
});

test("reports a sharing error without losing test results", async () => {
  const result = await recoverPixelAccounts({
    targets: [],
    operations: {
      listAccounts: async () => { throw new Error("not called"); },
      bulkTest: async () => { throw new Error("not called"); },
      shareAll: async () => { throw new Error("共享服务不可用"); },
    },
  });

  assert.equal(result.testedAccounts, 0);
  assert.equal(result.shareError, "共享服务不可用");
  assert.equal(result.shareAll, null);
});
