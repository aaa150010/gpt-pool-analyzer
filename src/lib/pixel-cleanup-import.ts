import type {
  PixelCleanupImportBatchResult,
  PixelCleanupImportFailure,
  PixelCleanupImportReport,
  PixelImportTargetResult,
} from "./types";

export const CLEANUP_IMPORT_BATCH_SIZE = 10;

export type CleanupImportAccount = Record<string, unknown>;

export function extractImportAccounts(payload: unknown): CleanupImportAccount[] {
  if (Array.isArray(payload)) {
    return payload.filter((item): item is CleanupImportAccount => Boolean(item) && typeof item === "object");
  }
  if (!payload || typeof payload !== "object") return [];
  const record = payload as Record<string, unknown>;
  if (Array.isArray(record.accounts)) {
    return record.accounts.filter((item): item is CleanupImportAccount => Boolean(item) && typeof item === "object");
  }
  if (Array.isArray(record.contents)) {
    const accounts: CleanupImportAccount[] = [];
    for (const item of record.contents) {
      if (typeof item !== "string") {
        if (item && typeof item === "object") accounts.push(item as CleanupImportAccount);
        continue;
      }
      try {
        accounts.push(...extractImportAccounts(JSON.parse(item)));
      } catch {
        // The server will provide the authoritative validation message.
      }
    }
    return accounts;
  }
  return [];
}

export function splitImportAccounts(accounts: CleanupImportAccount[], batchSize = CLEANUP_IMPORT_BATCH_SIZE): CleanupImportAccount[][] {
  const size = Math.max(Math.floor(batchSize), 1);
  const batches: CleanupImportAccount[][] = [];
  for (let index = 0; index < accounts.length; index += size) batches.push(accounts.slice(index, index + size));
  return batches;
}

function failedIndexes(failures: PixelCleanupImportFailure[]): Set<number> {
  return new Set(
    failures
      .filter((failure) => !isAccountAlreadyExistsError(failure.message))
      .map((failure) => failure.sourceIndex)
      .filter((index): index is number => index !== null && Number.isInteger(index) && index >= 0),
  );
}

export function isAccountAlreadyExistsError(message: string): boolean {
  return /account\s+already\s+exists|账号已存在/i.test(message.trim());
}

export function buildFailedImportPayload(
  sourcePayload: unknown,
  failures: PixelCleanupImportFailure[],
): unknown {
  const indexes = failedIndexes(failures);
  if (Array.isArray(sourcePayload)) {
    return sourcePayload.filter((item, index) => indexes.has(index));
  }
  if (!sourcePayload || typeof sourcePayload !== "object") return { accounts: [] };
  const source = sourcePayload as Record<string, unknown>;
  if (Array.isArray(source.accounts)) {
    return {
      ...source,
      accounts: source.accounts.filter((_, index) => indexes.has(index)),
    };
  }
  if (Array.isArray(source.contents)) {
    let offset = 0;
    const contents: unknown[] = [];
    for (const item of source.contents) {
      let parsed: unknown = item;
      if (typeof item === "string") {
        try {
          parsed = JSON.parse(item);
        } catch {
          continue;
        }
      }
      const nestedAccounts = extractImportAccounts(parsed);
      const selected = nestedAccounts.filter((_, index) => indexes.has(offset + index));
      offset += nestedAccounts.length;
      if (!selected.length) continue;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed) && Array.isArray((parsed as Record<string, unknown>).accounts)) {
        const next = { ...(parsed as Record<string, unknown>), accounts: selected };
        contents.push(typeof item === "string" ? JSON.stringify(next) : next);
      } else {
        contents.push(...selected);
      }
    }
    return { ...source, contents };
  }
  return { ...source, accounts: [] };
}

export function isSubscriptionLevelError(message: string): boolean {
  const normalized = message.trim().toLowerCase();
  if (!normalized) return false;
  return /订阅|等级|真实订阅|无法验证账号真实|\b(?:plan|tier|subscription|team|plus|pro|enterprise|free)\b/.test(normalized);
}

function sourceIndex(index: string, batchStart: number, batchSize: number): number | null {
  const parsed = Number.parseInt(index, 10);
  if (!Number.isInteger(parsed) || parsed < 0) return null;
  return batchStart + (parsed < batchSize ? parsed : parsed - 1);
}

function accountName(account: CleanupImportAccount | undefined): string {
  if (!account) return "";
  const credentials = account.credentials;
  const extra = account.extra;
  const candidates = [
    account.name,
    typeof credentials === "object" && credentials ? (credentials as Record<string, unknown>).email : "",
    typeof extra === "object" && extra ? (extra as Record<string, unknown>).email : "",
  ];
  return candidates.find((value): value is string => typeof value === "string" && value.trim().length > 0)?.trim() ?? "";
}

export function buildCleanupImportReport({
  sourceFileName,
  targetId,
  targetEmail,
  accounts,
  batches,
}: {
  sourceFileName: string;
  targetId: string;
  targetEmail: string;
  accounts: CleanupImportAccount[];
  batches: PixelCleanupImportBatchResult[];
}): PixelCleanupImportReport {
  const failures: PixelCleanupImportFailure[] = [];
  for (const batch of batches) {
    for (const targetResult of batch.results) {
      for (const error of targetResult.importErrors ?? []) {
        const index = sourceIndex(error.index, batch.start, batch.accounts);
        const account = index === null ? undefined : accounts[index];
        failures.push({
          batch: batch.index,
          sourceIndex: index,
          index: error.index,
          // The upstream service may return a generated/normalized name. The
          // cleanup report must identify the account from the uploaded file.
          name: accountName(account) || error.name || "",
          message: error.message,
          account: account ?? null,
          levelRelated: isSubscriptionLevelError(error.message),
          attempts: batch.attempts,
        });
      }
      if (targetResult.status !== "success" && targetResult.message && !targetResult.importErrors?.length) {
        failures.push({
          batch: batch.index,
          sourceIndex: null,
          index: "",
          name: "",
          message: targetResult.message,
          account: null,
          levelRelated: isSubscriptionLevelError(targetResult.message),
          attempts: batch.attempts,
        });
      }
    }
    if (batch.error && !batch.results.length) {
      failures.push({
        batch: batch.index,
        sourceIndex: null,
        index: "",
        name: "",
        message: batch.error,
        account: null,
        levelRelated: isSubscriptionLevelError(batch.error),
        attempts: batch.attempts,
      });
    }
  }
  const levelErrors = failures.filter((failure) => failure.levelRelated);
  return {
    createdAt: new Date().toISOString(),
    sourceFileName,
    targetId,
    targetEmail,
    batchSize: CLEANUP_IMPORT_BATCH_SIZE,
    totalAccounts: accounts.length,
    processedAccounts: batches.reduce((sum, batch) => sum + batch.accounts, 0),
    batches: batches.map(({ results: _results, ...batch }) => batch),
    failures,
    levelErrors,
  };
}
