import type {
  PixelAccountPage,
  PixelBulkOperationResponse,
  PixelCleanupKind,
  PixelCleanupProgress,
  PixelCleanupResult,
  PixelCleanupTargetResult,
  PixelTarget,
} from "./types";

type PixelCleanupOperations = {
  listAccounts: (targetId: string, page: number, pageSize: number, status: string) => Promise<PixelAccountPage>;
  deleteAccounts: (targetId: string, accountIds: number[]) => Promise<PixelBulkOperationResponse>;
};

type PixelCleanupOptions = {
  kind: PixelCleanupKind;
  targets: PixelTarget[];
  operations: PixelCleanupOperations;
  onProgress?: (progress: PixelCleanupProgress) => void;
};

const RETIRED_ACCOUNT = "1745627971@qq.com";
const PAGE_SIZE = 100;
const MAX_SCAN_PAGES = 10_000;

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export async function cleanupPixelAccounts({
  kind,
  targets,
  operations,
  onProgress = () => undefined,
}: PixelCleanupOptions): Promise<PixelCleanupResult> {
  const cleanupTargets = targets.filter((target) => target.email.trim().toLowerCase() !== RETIRED_ACCOUNT);
  const statuses = kind === "limited" ? ["rate_limited", "codex_quota_protected"] : ["error"];
  const scanned = new Map<string, number[]>();
  const resultByTarget = new Map<string, PixelCleanupTargetResult>();
  let found = 0;
  let deleted = 0;

  onProgress({
    phase: "scanning",
    completedTargets: 0,
    totalTargets: cleanupTargets.length,
    targetEmail: cleanupTargets[0]?.email ?? "",
    found: 0,
    deleted: 0,
  });

  for (let targetIndex = 0; targetIndex < cleanupTargets.length; targetIndex += 1) {
    const target = cleanupTargets[targetIndex];
    onProgress({
      phase: "scanning",
      completedTargets: targetIndex,
      totalTargets: cleanupTargets.length,
      targetEmail: target.email,
      found,
      deleted: 0,
    });
    const accountIds = new Set<number>();
    try {
      for (const status of statuses) {
        for (let scanPage = 1; ; scanPage += 1) {
          if (scanPage > MAX_SCAN_PAGES) throw new Error("分页数量异常，已停止该平台清理");
          const response = await operations.listAccounts(target.id, scanPage, PAGE_SIZE, status);
          response.items.forEach((account) => accountIds.add(account.id));
          if (scanPage >= Math.max(response.pages, 1)) break;
        }
      }
      const ids = [...accountIds];
      scanned.set(target.id, ids);
      found += ids.length;
    } catch (error) {
      resultByTarget.set(target.id, {
        targetId: target.id,
        email: target.email,
        found: 0,
        deleted: 0,
        failed: 0,
        failedIds: [],
        status: "skipped",
        message: `扫描失败，已跳过：${errorMessage(error, "账号读取失败")}`,
      });
    }
  }

  for (let targetIndex = 0; targetIndex < cleanupTargets.length; targetIndex += 1) {
    const target = cleanupTargets[targetIndex];
    onProgress({
      phase: "deleting",
      completedTargets: targetIndex,
      totalTargets: cleanupTargets.length,
      targetEmail: target.email,
      found,
      deleted,
    });
    const accountIds = scanned.get(target.id);
    if (!accountIds) continue;
    let targetDeleted = 0;
    let targetFailed = 0;
    const failedIds: number[] = [];
    const errors: string[] = [];
    for (let start = 0; start < accountIds.length; start += PAGE_SIZE) {
      const chunk = accountIds.slice(start, start + PAGE_SIZE);
      try {
        const response = await operations.deleteAccounts(target.id, chunk);
        targetDeleted += response.success;
        targetFailed += response.failed;
        failedIds.push(...(response.failedIds ?? []));
      } catch (error) {
        targetFailed += chunk.length;
        failedIds.push(...chunk);
        errors.push(errorMessage(error, "删除请求失败"));
      }
      onProgress({
        phase: "deleting",
        completedTargets: targetIndex,
        totalTargets: cleanupTargets.length,
        targetEmail: target.email,
        found,
        deleted: deleted + targetDeleted,
      });
    }
    deleted += targetDeleted;
    resultByTarget.set(target.id, {
      targetId: target.id,
      email: target.email,
      found: accountIds.length,
      deleted: targetDeleted,
      failed: targetFailed,
      failedIds,
      status: targetFailed ? "partial" : "success",
      message: errors.length
        ? `部分删除请求失败：${[...new Set(errors)].join("；")}`
        : accountIds.length === 0
          ? "没有匹配账号"
          : targetFailed
            ? `仍有 ${targetFailed} 个账号删除失败`
            : "匹配账号已全部删除",
    });
  }

  const results = cleanupTargets
    .map((target) => resultByTarget.get(target.id))
    .filter((item): item is PixelCleanupTargetResult => Boolean(item));
  return {
    kind,
    totalTargets: cleanupTargets.length,
    found,
    deleted,
    failed: results.reduce((sum, item) => sum + item.failed, 0),
    skippedTargets: results.filter((item) => item.status === "skipped").length,
    results,
  };
}
