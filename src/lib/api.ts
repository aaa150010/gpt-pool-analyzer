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
  PixelShareResponse,
  PixelTarget,
  PoolAnalyticsResponse,
  PoolAnalyzerState,
  PoolCredentials,
  PoolSnapshot,
  ServerRefreshResponse,
  ServerStateResponse,
  Snapshot,
  SMTPSettings,
  StoredState,
} from "./types";

const API_BASE = import.meta.env.DEV ? "/gpt-api" : "https://lynote.xyz/gpt-api";

let pixelManagerKeyPromise: Promise<string> | null = null;

async function pixelManagerKey(): Promise<string> {
  const previewKey = import.meta.env.DEV ? import.meta.env.VITE_PIXEL_MANAGER_API_KEY : "";
  if (previewKey) return previewKey;
  if (!pixelManagerKeyPromise) {
    pixelManagerKeyPromise = import("@tauri-apps/api/core")
      .then(({ invoke }) => invoke<string>("pixel_manager_api_key"))
      .catch((error) => {
        pixelManagerKeyPromise = null;
        throw error;
      });
  }
  return pixelManagerKeyPromise;
}

async function requestPixelJson<T>(path: string, init?: RequestInit): Promise<T> {
  const key = await pixelManagerKey();
  const isFormData = init?.body instanceof FormData;
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      "X-91-Manager-Key": key,
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
    headers: { Accept: "application/json", "X-91-Manager-Key": key },
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
    headers: { Accept: "application/json", "X-91-Manager-Key": key },
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
  smtpSettings: () => requestJson<{ settings: SMTPSettings }>("/smtp-settings"),
  updateSmtpSettings: (settings: SMTPSettings) =>
    requestJson<{ ok: boolean }>("/smtp-settings", {
      method: "PUT",
      body: JSON.stringify({ settings }),
    }),
  pixelTargets: () => requestPixelJson<{ targets: PixelTarget[] }>("/pixel-manager/targets"),
  pixelAccounts: (targetId: string, page = 1, pageSize = 20, status = "", search = "") => {
    const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
    if (status) params.set("status", status);
    if (search.trim()) params.set("search", search.trim());
    return requestPixelJson<PixelAccountPage>(`/pixel-manager/targets/${encodeURIComponent(targetId)}/accounts?${params.toString()}`);
  },
  pixelAccountUsage: (targetId: string, accountId: number, signal?: AbortSignal) =>
    requestPixelJson<PixelAccountUsage>(
      `/pixel-manager/targets/${encodeURIComponent(targetId)}/accounts/${accountId}/usage?source=local`,
      { signal },
    ),
  pixelBulkTest: (targetId: string, accountIds: number[]) =>
    requestPixelJson<PixelBulkOperationResponse>(`/pixel-manager/targets/${encodeURIComponent(targetId)}/accounts/bulk-test`, {
      method: "POST",
      body: JSON.stringify({ accountIds }),
    }),
  pixelBulkDelete: (targetId: string, accountIds: number[]) =>
    requestPixelJson<PixelBulkOperationResponse>(`/pixel-manager/targets/${encodeURIComponent(targetId)}/accounts/bulk-delete`, {
      method: "POST",
      body: JSON.stringify({ accountIds }),
    }),
  pixelBulkUpdate: (targetId: string, payload: PixelBulkUpdateRequest) =>
    requestPixelJson<PixelBulkOperationResponse>(`/pixel-manager/targets/${encodeURIComponent(targetId)}/accounts/bulk-update`, {
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
  pixelImportJob: (jobId: string) => requestPixelJson<{ job: PixelImportJob }>(`/pixel-manager/import-jobs/${encodeURIComponent(jobId)}`),
  pixelShare: (targetId: string, accountIds: number[]) =>
    requestPixelJson<PixelShareResponse>(`/pixel-manager/targets/${encodeURIComponent(targetId)}/share`, {
      method: "POST",
      body: JSON.stringify({ accountIds }),
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
