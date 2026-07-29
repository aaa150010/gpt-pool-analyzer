import type {
  BalanceAccount,
  CostAddition,
  CursorPage,
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
};
