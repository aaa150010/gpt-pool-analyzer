export type Snapshot = {
  date: string;
  total: number;
  amounts: number[];
  accounts?: string[];
};

export type CostAddition = {
  id: string;
  date: string;
  note: string;
  amount: number;
  createdAt: string;
};

export type SettlementState = {
  partnerName: string;
  partnerSharePercent: number;
  payoutRatePercent: number;
  withdrawals: Record<string, number>;
};

export type StoredState = {
  cost: number;
  partnerCost?: number;
  manualBaseTotal?: number;
  withdrawalAmount?: number;
  useBaseDeduction?: boolean;
  baseDeductionAmount?: number;
  initial?: Snapshot;
  history: Snapshot[];
  costAdditions?: CostAddition[];
  settlement?: SettlementState;
};

export type PoolSnapshot = {
  date: string;
  groupName: string;
  status: string;
  total: number;
  active: number;
  schedulable: number;
  remaining5h?: number | null;
  remaining7d?: number | null;
  utilization5h?: number | null;
  utilization7d?: number | null;
  concurrentAvailable: number;
  concurrentTotal: number;
  limited: number;
  quotaProtected: number;
  error: number;
  disabled: number;
};

export type PoolAnalyzerState = {
  history: PoolSnapshot[];
  selectedGroups?: string[];
  availableGroups?: string[];
  pollingMinutes?: number;
  warningEmail?: string;
  accessToken?: string;
  refreshToken?: string;
};

export type BalanceAccount = {
  name: string;
  baseURL: string;
  apiKey: string;
};

export type PoolCredentials = {
  email: string;
  password: string;
};

export type SMTPSettings = {
  host: string;
  port: number;
  username: string;
  password: string;
  recipient: string;
};

export type ServerStateResponse = {
  initialized: boolean;
  storedState?: StoredState;
  poolState?: PoolAnalyzerState;
  balanceAccounts?: BalanceAccount[];
};

export type ServerRefreshResponse = {
  ok: boolean;
  state?: ServerStateResponse;
};

export type PoolMetricKey =
  | "remaining5h"
  | "remaining7d"
  | "total"
  | "limited"
  | "quotaProtected"
  | "error"
  | "concurrentAvailable";
