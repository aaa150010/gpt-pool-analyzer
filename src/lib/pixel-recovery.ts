import type {
  PixelAccountPage,
  PixelBulkOperationResponse,
  PixelShareAllResponse,
  PixelTarget,
} from "./types";

export type PixelRecoveryPhase = "scanning" | "testing" | "sharing";

export type PixelRecoveryProgress = {
  phase: PixelRecoveryPhase;
  completedTargets: number;
  totalTargets: number;
  targetEmail: string;
  scannedAccounts: number;
  testedAccounts: number;
  testSuccess: number;
  testFailed: number;
};

export type PixelRecoveryTargetResult = {
  targetId: string;
  email: string;
  scanned: number;
  tested: number;
  success: number;
  failed: number;
  error: string | null;
};

export type PixelRecoveryResult = {
  totalTargets: number;
  scannedAccounts: number;
  testedAccounts: number;
  testSuccess: number;
  testFailed: number;
  results: PixelRecoveryTargetResult[];
  shareAll: PixelShareAllResponse | null;
  shareError: string | null;
};

export type PixelRecoveryOperations = {
  listAccounts: (targetId: string, page: number, pageSize: number) => Promise<PixelAccountPage>;
  bulkTest: (targetId: string, accountIds: number[]) => Promise<PixelBulkOperationResponse>;
  shareAll: (targetIds: string[]) => Promise<PixelShareAllResponse>;
};

export type PixelRecoveryOptions = {
  targets: PixelTarget[];
  operations: PixelRecoveryOperations;
  onProgress?: (progress: PixelRecoveryProgress) => void;
};

const PAGE_SIZE = 100;
const BATCH_SIZE = 100;
const MAX_SCAN_PAGES = 10_000;

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function emit(
  onProgress: (progress: PixelRecoveryProgress) => void,
  progress: PixelRecoveryProgress,
): void {
  onProgress(progress);
}

export async function recoverPixelAccounts({
  targets,
  operations,
  onProgress = () => undefined,
}: PixelRecoveryOptions): Promise<PixelRecoveryResult> {
  const results: PixelRecoveryTargetResult[] = [];
  let scannedAccounts = 0;
  let testedAccounts = 0;
  let testSuccess = 0;
  let testFailed = 0;

  for (let targetIndex = 0; targetIndex < targets.length; targetIndex += 1) {
    const target = targets[targetIndex];
    const accountIds = new Set<number>();
    let targetError: string | null = null;

    emit(onProgress, {
      phase: "scanning",
      completedTargets: targetIndex,
      totalTargets: targets.length,
      targetEmail: target.email,
      scannedAccounts,
      testedAccounts,
      testSuccess,
      testFailed,
    });

    try {
      for (let page = 1; ; page += 1) {
        if (page > MAX_SCAN_PAGES) throw new Error("分页数量异常，已停止该平台测试");
        const response = await operations.listAccounts(target.id, page, PAGE_SIZE);
        response.items.forEach((account) => accountIds.add(account.id));
        if (page >= Math.max(response.pages, 1)) break;
      }
    } catch (error) {
      targetError = `扫描失败：${errorMessage(error, "账号读取失败")}`;
    }

    const ids = [...accountIds];
    scannedAccounts += ids.length;
    let targetSuccess = 0;
    let targetFailed = 0;

    for (let start = 0; start < ids.length; start += BATCH_SIZE) {
      const batch = ids.slice(start, start + BATCH_SIZE);
      emit(onProgress, {
        phase: "testing",
        completedTargets: targetIndex,
        totalTargets: targets.length,
        targetEmail: target.email,
        scannedAccounts,
        testedAccounts,
        testSuccess,
        testFailed,
      });
      try {
        const response = await operations.bulkTest(target.id, batch);
        const success = Math.min(Math.max(Number(response.success) || 0, 0), batch.length);
        const failed = Math.min(Math.max(Number(response.failed) || batch.length - success, 0), batch.length - success);
        targetSuccess += success;
        targetFailed += failed;
        testedAccounts += batch.length;
        testSuccess += success;
        testFailed += failed;
      } catch (error) {
        const message = errorMessage(error, "批量测试失败");
        targetError = targetError ? `${targetError}；${message}` : `测试失败：${message}`;
        targetFailed += batch.length;
        testedAccounts += batch.length;
        testFailed += batch.length;
      }
    }

    results.push({
      targetId: target.id,
      email: target.email,
      scanned: ids.length,
      tested: ids.length,
      success: targetSuccess,
      failed: targetFailed,
      error: targetError,
    });
  }

  emit(onProgress, {
    phase: "sharing",
    completedTargets: targets.length,
    totalTargets: targets.length,
    targetEmail: "全部平台",
    scannedAccounts,
    testedAccounts,
    testSuccess,
    testFailed,
  });

  let shareAll: PixelShareAllResponse | null = null;
  let shareError: string | null = null;
  try {
    shareAll = await operations.shareAll(targets.map((target) => target.id));
  } catch (error) {
    shareError = errorMessage(error, "一键打开共享失败");
  }

  return {
    totalTargets: targets.length,
    scannedAccounts,
    testedAccounts,
    testSuccess,
    testFailed,
    results,
    shareAll,
    shareError,
  };
}
