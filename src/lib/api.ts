import type {
  BalanceAccount,
  CostAddition,
  CursorPage,
  PixelAccountPage,
  PixelAccountUsage,
  PixelBulkOperationResponse,
  PixelBulkUpdateRequest,
  PixelExportDownload,
  PixelExportJob,
  PixelImportJob,
  PixelImportRecord,
  PixelShareAllResponse,
  PixelShareResponse,
  PixelTarget,
  PoolAnalyticsResponse,
  PoolAnalyzerState,
  PoolCredentials,
  PoolSnapshot,
  ServerRefreshResponse,
  ServerStateResponse,
  Snapshot,
  StoredState,
  WithdrawalDraft,
  WithdrawalHistoryResponse,
  WithdrawalJob,
  WithdrawalPlan,
} from "./types";

const API_BASE = import.meta.env.DEV ? "/gpt-api" : "https://lynote.xyz/gpt-api";
const PIXEL_FALLBACK_EVENT = "pixel-manager-server-fallback";

type PixelCommandEnvelope<T> = {
  data: T;
  connectionMode: "direct" | "server";
  node: string | null;
  latencyMs: number | null;
};

const isTauriRuntime = "__TAURI_INTERNALS__" in globalThis || "__TAURI__" in globalThis;
let pixelFallbackActive = false;

let pixelManagerKeyPromise: Promise<string> | null = null;

async function pixelManagerKey(): Promise<string> {
  const previewKey = import.meta.env.DEV ? import.meta.env.VITE_PIXEL_MANAGER_API_KEY : "";
  if (previewKey) return previewKey;
  if (!isTauriRuntime) return "";
  if (!pixelManagerKeyPromise) {
    pixelManagerKeyPromise = import("@tauri-apps/api/core")
      .then(({ invoke }) => invoke<string>("pixel_manager_api_key"))
      .catch((error) => {
        pixelManagerKeyPromise = null;
        if (import.meta.env.DEV) throw error;
        return "";
      });
  }
  return pixelManagerKeyPromise;
}

async function requestLocalPixel<T>(operation: string, targetId?: string, payload?: unknown, signal?: AbortSignal): Promise<T> {
  if (signal?.aborted) throw new DOMException("The operation was aborted", "AbortError");
  const { invoke } = await import("@tauri-apps/api/core");
  const response = await invoke<PixelCommandEnvelope<T>>("pixel_manager_request", {
    operation,
    targetId: targetId || null,
    payload: payload ?? {},
  });
  if (signal?.aborted) throw new DOMException("The operation was aborted", "AbortError");
  if (response.connectionMode === "server") {
    if (!pixelFallbackActive && typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(PIXEL_FALLBACK_EVENT));
    }
    pixelFallbackActive = true;
  } else {
    pixelFallbackActive = false;
  }
  return response.data;
}

async function requestPixelJson<T>(path: string, init?: RequestInit): Promise<T> {
  const key = await pixelManagerKey();
  const isFormData = init?.body instanceof FormData;
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(key ? { "X-91-Manager-Key": key } : {}),
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload && typeof payload === "object" && "detail" in payload ? String(payload.detail) : "";
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

async function downloadPixelExport(): Promise<PixelExportDownload> {
  const key = await pixelManagerKey();
  const response = await fetch(`${API_BASE}/pixel-manager/export`, {
    headers: { Accept: "application/json", ...(key ? { "X-91-Manager-Key": key } : {}) },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload && typeof payload === "object" && "detail" in payload ? String(payload.detail) : "";
    throw new Error(detail || `HTTP ${response.status}`);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plainName = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  return {
    blob: await response.blob(),
    fileName: encodedName ? decodeURIComponent(encodedName) : plainName || `pixel-accounts-${Date.now()}.json`,
    sourceCount: Number(response.headers.get("X-Pixel-Source-Count")) || 0,
    deduplicatedCount: Number(response.headers.get("X-Pixel-Deduplicated-Count")) || 0,
    duplicateCount: Number(response.headers.get("X-Pixel-Duplicate-Count")) || 0,
    batchCount: Number(response.headers.get("X-Pixel-Batch-Count")) || 0,
  };
}

async function downloadPixelExportJob(jobId: string): Promise<PixelExportDownload> {
  const key = await pixelManagerKey();
  const response = await fetch(`${API_BASE}/pixel-manager/export-jobs/${encodeURIComponent(jobId)}/download`, {
    headers: { Accept: "application/json", ...(key ? { "X-91-Manager-Key": key } : {}) },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload && typeof payload === "object" && "detail" in payload ? String(payload.detail) : "";
    throw new Error(detail || `HTTP ${response.status}`);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plainName = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  return {
    blob: await response.blob(),
    fileName: encodedName ? decodeURIComponent(encodedName) : plainName || `pixel-accounts-backup-${Date.now()}.json`,
    sourceCount: 0,
    deduplicatedCount: 0,
    duplicateCount: 0,
    batchCount: 0,
  };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export const api = {
  state: () => requestJson<ServerStateResponse>("/state"),
  refresh: () => requestJson<ServerRefreshResponse>("/refresh", { method: "POST" }),
  updateStoredState: (storedState: StoredState) =>
    requestJson<ServerRefreshResponse>("/stored-state", {
      method: "PUT",
      body: JSON.stringify({ storedState }),
    }),
  updatePoolState: (poolState: PoolAnalyzerState) =>
    requestJson<ServerRefreshResponse>("/pool-state", {
      method: "PUT",
      body: JSON.stringify({ poolState }),
    }),
  poolAnalytics: (groupName: string, days: number) =>
    requestJson<PoolAnalyticsResponse>(`/pool-analytics?groupName=${encodeURIComponent(groupName)}&days=${days}`),
  poolHistory: (groupName: string, cursor?: number | null, limit = 200) => {
    const params = new URLSearchParams({ groupName, limit: String(limit) });
    if (cursor !== null && cursor !== undefined) params.set("cursor", String(cursor));
    return requestJson<CursorPage<PoolSnapshot>>(`/pool-history?${params.toString()}`);
  },
  balanceHistory: (cursor?: number | null, limit = 200) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (cursor !== null && cursor !== undefined) params.set("cursor", String(cursor));
    return requestJson<CursorPage<Snapshot>>(`/balance-history?${params.toString()}`);
  },
  addCost: (addition: CostAddition) =>
    requestJson<ServerRefreshResponse>("/cost-additions", {
      method: "POST",
      body: JSON.stringify(addition),
    }),
  deleteCost: (additionId: string) =>
    requestJson<ServerRefreshResponse>(`/cost-additions/${encodeURIComponent(additionId)}`, { method: "DELETE" }),
  clearCosts: () => requestJson<ServerRefreshResponse>("/cost-additions", { method: "DELETE" }),
  balanceAccounts: () => requestJson<{ accounts: BalanceAccount[] }>("/balance-accounts"),
  updateBalanceAccounts: (accounts: BalanceAccount[]) =>
    requestJson<{ ok: boolean; count: number }>("/balance-accounts", {
      method: "PUT",
      body: JSON.stringify({ accounts }),
    }),
  poolCredentials: () => requestJson<{ credentials: PoolCredentials }>("/pool-credentials"),
  updatePoolCredentials: (credentials: PoolCredentials) =>
    requestJson<{ ok: boolean }>("/pool-credentials", {
      method: "PUT",
      body: JSON.stringify({ credentials }),
    }),
  withdrawalPreview: (draft: WithdrawalDraft) =>
    requestPixelJson<WithdrawalPlan>("/withdrawals/preview", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  withdrawals: () => requestPixelJson<{ job: WithdrawalJob | null }>("/withdrawals"),
  withdrawalHistory: (limit = 20, offset = 0) =>
    requestPixelJson<WithdrawalHistoryResponse>(`/withdrawals/history?limit=${limit}&offset=${offset}`),
  createWithdrawal: (draft: WithdrawalDraft) =>
    requestPixelJson<{ job: WithdrawalJob }>("/withdrawals", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  withdrawal: (jobId: string) => requestPixelJson<{ job: WithdrawalJob }>(`/withdrawals/${encodeURIComponent(jobId)}`),
  accelerateWithdrawal: (jobId: string) =>
    requestPixelJson<{ job: WithdrawalJob }>(`/withdrawals/${encodeURIComponent(jobId)}/accelerate`, { method: "POST" }),
  retryWithdrawal: (jobId: string) =>
    requestPixelJson<{ job: WithdrawalJob }>(`/withdrawals/${encodeURIComponent(jobId)}/retry`, { method: "POST" }),
  pixelTargets: () => isTauriRuntime
    ? requestLocalPixel<{ targets: PixelTarget[] }>("targets")
    : requestPixelJson<{ targets: PixelTarget[] }>("/pixel-manager/targets"),
  pixelAccounts: (targetId: string, page = 1, pageSize = 100, status = "", search = "") => {
    if (isTauriRuntime) return requestLocalPixel<PixelAccountPage>("accounts", targetId, { page, pageSize, status, search });
    const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
    if (status) params.set("status", status);
    if (search.trim()) params.set("search", search.trim());
    return requestPixelJson<PixelAccountPage>(`/pixel-manager/targets/${encodeURIComponent(targetId)}/accounts?${params.toString()}`);
  },
  pixelAccountUsage: (targetId: string, accountId: number, signal?: AbortSignal) => isTauriRuntime
    ? requestLocalPixel<PixelAccountUsage>("accountUsage", targetId, { accountId }, signal)
    : requestPixelJson<PixelAccountUsage>(
        `/pixel-manager/targets/${encodeURIComponent(targetId)}/accounts/${accountId}/usage?source=local`,
        { signal },
      ),
  pixelBulkTest: (targetId: string, accountIds: number[]) => isTauriRuntime
    ? requestLocalPixel<PixelBulkOperationResponse>("bulkTest", targetId, { accountIds })
    : requestPixelJson<PixelBulkOperationResponse>(`/pixel-manager/targets/${encodeURIComponent(targetId)}/accounts/bulk-test`, {
        method: "POST",
        body: JSON.stringify({ accountIds }),
      }),
  pixelBulkDelete: (targetId: string, accountIds: number[]) => isTauriRuntime
    ? requestLocalPixel<PixelBulkOperationResponse>("bulkDelete", targetId, { accountIds })
    : requestPixelJson<PixelBulkOperationResponse>(`/pixel-manager/targets/${encodeURIComponent(targetId)}/accounts/bulk-delete`, {
        method: "POST",
        body: JSON.stringify({ accountIds }),
      }),
  pixelBulkUpdate: (targetId: string, payload: PixelBulkUpdateRequest) => isTauriRuntime
    ? requestLocalPixel<PixelBulkOperationResponse>("bulkUpdate", targetId, payload)
    : requestPixelJson<PixelBulkOperationResponse>(`/pixel-manager/targets/${encodeURIComponent(targetId)}/accounts/bulk-update`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  pixelImport: (file: File, targetIds: string[]) => {
    const params = new URLSearchParams({ targetIds: JSON.stringify(targetIds), fileName: file.name });
    return requestPixelJson<{ job: PixelImportJob }>(`/pixel-manager/import?${params.toString()}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: file,
    });
  },
  pixelImportBatch: (files: File[], targetIds: string[]) => {
    const params = new URLSearchParams({ targetIds: JSON.stringify(targetIds) });
    const form = new FormData();
    files.forEach((file) => form.append("files", file, file.name));
    return requestPixelJson<{ job: PixelImportJob }>(`/pixel-manager/import-batch?${params.toString()}`, {
      method: "POST",
      body: form,
    });
  },
  pixelImportJob: (jobId: string) => requestPixelJson<{ job: PixelImportJob }>(`/pixel-manager/import-jobs/${encodeURIComponent(jobId)}`),
  acceleratePixelImport: (jobId: string) =>
    requestPixelJson<{ job: PixelImportJob }>(`/pixel-manager/import-jobs/${encodeURIComponent(jobId)}/accelerate`, { method: "POST" }),
  retryPixelImport: (jobId: string) =>
    requestPixelJson<{ job: PixelImportJob }>(`/pixel-manager/import-jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" }),
  pixelImportRecords: () => requestPixelJson<{ records: PixelImportRecord[] }>("/pixel-manager/import-records"),
  pixelRetryImportShare: (recordId: string, targetId: string, accountIds: number[]) =>
    requestPixelJson<{ record: PixelImportRecord; result: PixelShareResponse }>(
      `/pixel-manager/import-records/${encodeURIComponent(recordId)}/share`,
      {
        method: "POST",
        body: JSON.stringify({ targetId, accountIds }),
      },
    ),
  pixelDeleteImportRecord: (recordId: string) =>
    requestPixelJson<{ record: PixelImportRecord; result: unknown }>(
      `/pixel-manager/import-records/${encodeURIComponent(recordId)}/delete`,
      { method: "POST" },
    ),
  pixelRemoveImportRecord: (recordId: string) =>
    requestPixelJson<{ ok: boolean }>(`/pixel-manager/import-records/${encodeURIComponent(recordId)}`, { method: "DELETE" }),
  pixelShare: (targetId: string, accountIds: number[]) =>
    requestPixelJson<PixelShareResponse>(`/pixel-manager/targets/${encodeURIComponent(targetId)}/share`, {
      method: "POST",
      body: JSON.stringify({ accountIds }),
    }),
  pixelShareAll: (targetIds: string[], concurrency?: number) => isTauriRuntime
    ? requestLocalPixel<PixelShareAllResponse>("shareAll", undefined, { targetIds, ...(concurrency === undefined ? {} : { concurrency }) })
    : requestPixelJson<PixelShareAllResponse>("/pixel-manager/share-all", {
        method: "POST",
        body: JSON.stringify({ targetIds, ...(concurrency === undefined ? {} : { concurrency }) }),
      }),
  pixelExport: () => downloadPixelExport(),
  pixelExportJobCreate: (targetIds: string[]) =>
    requestPixelJson<{ job: PixelExportJob }>("/pixel-manager/export-jobs", {
      method: "POST",
      body: JSON.stringify({ deleteAllAndReimport: true, targetIds }),
    }),
  pixelExportJob: (jobId: string) => requestPixelJson<{ job: PixelExportJob }>(`/pixel-manager/export-jobs/${encodeURIComponent(jobId)}`),
  pixelExportJobDownload: (jobId: string) => downloadPixelExportJob(jobId),
};
