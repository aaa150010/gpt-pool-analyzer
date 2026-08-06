import assert from "node:assert/strict";
import test from "node:test";
import { cleanupPixelAccounts } from "./pixel-cleanup.ts";
import type { PixelAccount, PixelAccountPage, PixelTarget } from "./types.ts";

function target(id: string, email = `${id}@example.com`): PixelTarget {
  return { id, email, connected: true, accountCount: null, lastCheckedAt: null, error: null };
}

function page(targetValue: PixelTarget, ids: number[], pageNumber: number, pages: number): PixelAccountPage {
  const items = ids.map((id) => ({ id } as PixelAccount));
  return { items, total: ids.length, page: pageNumber, pageSize: 100, pages, target: targetValue };
}

test("limited cleanup scans every page, merges statuses, deduplicates, and deletes in 100-item chunks", async () => {
  const currentTarget = target("one");
  const calls: Array<{ status: string; page: number }> = [];
  const deletedChunks: number[][] = [];
  const rateIds = Array.from({ length: 100 }, (_, index) => index + 1);
  const protectedIds = Array.from({ length: 22 }, (_, index) => index + 100);

  const result = await cleanupPixelAccounts({
    kind: "limited",
    targets: [currentTarget],
    operations: {
      listAccounts: async (_targetId, pageNumber, pageSize, status) => {
        assert.equal(pageSize, 100);
        calls.push({ status, page: pageNumber });
        if (status === "rate_limited") return page(currentTarget, pageNumber === 1 ? rateIds : [101], pageNumber, 2);
        return page(currentTarget, protectedIds, pageNumber, 1);
      },
      deleteAccounts: async (_targetId, accountIds) => {
        deletedChunks.push(accountIds);
        return { ok: true, success: accountIds.length, failed: 0, successIds: accountIds, failedIds: [] };
      },
    },
  });

  assert.deepEqual(calls, [
    { status: "rate_limited", page: 1 },
    { status: "rate_limited", page: 2 },
    { status: "codex_quota_protected", page: 1 },
  ]);
  assert.equal(result.found, 121);
  assert.equal(result.deleted, 121);
  assert.deepEqual(deletedChunks.map((chunk) => chunk.length), [100, 21]);
});

test("a failed platform scan is skipped before any delete while other platforms continue", async () => {
  const failedTarget = target("failed");
  const goodTarget = target("good");
  const deleteTargets: string[] = [];

  const result = await cleanupPixelAccounts({
    kind: "error",
    targets: [failedTarget, goodTarget],
    operations: {
      listAccounts: async (targetId) => {
        if (targetId === failedTarget.id) throw new Error("page unavailable");
        return page(goodTarget, [7], 1, 1);
      },
      deleteAccounts: async (targetId, accountIds) => {
        deleteTargets.push(targetId);
        return { ok: true, success: accountIds.length, failed: 0, failedIds: [] };
      },
    },
  });

  assert.deepEqual(deleteTargets, [goodTarget.id]);
  assert.equal(result.skippedTargets, 1);
  assert.equal(result.results[0].status, "skipped");
  assert.match(result.results[0].message, /page unavailable/);
});

test("partial delete failures and empty matches are reported without aborting", async () => {
  const partialTarget = target("partial");
  const emptyTarget = target("empty");

  const result = await cleanupPixelAccounts({
    kind: "error",
    targets: [partialTarget, emptyTarget, target("retired", "1745627971@QQ.COM")],
    operations: {
      listAccounts: async (targetId) => page(
        targetId === partialTarget.id ? partialTarget : emptyTarget,
        targetId === partialTarget.id ? [1, 2, 3] : [],
        1,
        1,
      ),
      deleteAccounts: async (_targetId, accountIds) => ({
        ok: false,
        success: accountIds.length - 1,
        failed: 1,
        successIds: accountIds.slice(0, -1),
        failedIds: accountIds.slice(-1),
      }),
    },
  });

  assert.equal(result.totalTargets, 2);
  assert.equal(result.found, 3);
  assert.equal(result.deleted, 2);
  assert.equal(result.failed, 1);
  assert.equal(result.results[0].status, "partial");
  assert.equal(result.results[1].message, "没有匹配账号");
});
