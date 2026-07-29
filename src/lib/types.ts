export type Snapshot = {
  date: string;
  total: number;
  amounts: number[];
  accounts?: string[];
};

export type CursorPage<T> = {
  items: T[];
  nextCursor: number | null;
  hasMore: boolean;
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
  capacity5h?: number | null;
  capacity7d?: number | null;
  remainingCapacity5h?: number | null;
  remainingCapacity7d?: number | null;
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
  analyticsGroup?: string;
};

export type DailyPoolUsage = {
  date: string;
  dayType: "workday" | "nonWorkday";
  isComplete: boolean;
  estimated5h: number | null;
  estimated7d: number | null;
  accountDecrease: number;
  accountIncrease: number;
  netAccountChange: number;
  sampleCount: number;
  coverage: number;
};

export type PoolForecast = {
  date: string;
  dayType: "workday" | "nonWorkday";
  estimated5h: number | null;
  upper5h: number | null;
  estimated7d: number | null;
  upper7d: number | null;
  accountDecrease: number | null;
  upperAccountDecrease: number | null;
  confidence: "low" | "medium" | "high" | "insufficient";
  sampleCount: number;
};

export type RollingPoolForecast = {
  estimated5h: number | null;
  upper5h: number | null;
  estimated7d: number | null;
  upper7d: number | null;
  accountDecrease: number | null;
  upperAccountDecrease: number | null;
  confidence: PoolForecast["confidence"];
};

export type PoolRecommendation = {
  replenish: number | null;
  horizonHours: number;
  gap5h: number | null;
  gap7d: number | null;
  accountGap: number | null;
};

export type PoolRisk = {
  level: "low" | "medium" | "high" | "insufficient";
  reasons: string[];
};

export type DeathAnalysisDay = {
  date: string;
  dayType: "workday" | "nonWorkday";
  isComplete: boolean;
  estimated5h: number | null;
  estimated7d: number | null;
  newErrors: number;
  endingErrors: number | null;
  inferredAccountRemovals: number;
  likelyErrorDeaths: number;
  autoDeletionCandidates: number;
  manualOrUnmatchedCandidates: number;
  otherRemovalCandidates: number;
  accountAdditions: number;
  sampleCount: number;
  coverage: number;
  observedHours: number;
};

export type DeathAnalysisHour = {
  hour: number;
  label: string;
  newErrors: number;
  inferredAccountRemovals: number;
  likelyErrorDeaths: number;
  autoDeletionCandidates: number;
  manualOrUnmatchedCandidates: number;
  otherRemovalCandidates: number;
  accountAdditions: number;
  observedDays: number;
  observedHours: number;
  coverage: number;
  errorRatePercent: number | null;
  removalRatePercent: number | null;
  likelyErrorDeathRatePercent: number | null;
};

export type DeathTimelineHour = {
  date: string;
  hour: number;
  label: string;
  newErrors: number;
  endingErrors: number | null;
  inferredAccountRemovals: number;
  likelyErrorDeaths: number;
  autoDeletionCandidates: number;
  manualOrUnmatchedCandidates: number;
  otherRemovalCandidates: number;
  accountAdditions: number;
  sampleCount: number;
  observed: boolean;
  observedMinutes: number;
  coverage: number;
  errorRatePercent: number | null;
  removalRatePercent: number | null;
};

export type RecentErrorWindow = {
  minutes: number;
  startErrors: number | null;
  endErrors: number | null;
  netIncrease: number;
  positiveSteps: number;
  decreaseSteps: number;
  isContinuouslyRising: boolean;
  sampleCount: number;
  observedMinutes: number;
  confidence: PoolForecast["confidence"];
};

export type PoolDeathAnalysis = {
  windowDays: number;
  windowStart: string;
  windowEnd: string;
  timezone: string;
  snapshotCount: number;
  firstSnapshotAt: string | null;
  lastSnapshotAt: string | null;
  autoDeletionDelayHours: number;
  method: string;
  limitations: string[];
  daily: DeathAnalysisDay[];
  hourly: DeathAnalysisHour[];
  timeline: DeathTimelineHour[];
  recentErrorTrend: {
    currentErrors: number | null;
    signalLevel: PoolRisk["level"];
    isContinuouslyRising: boolean;
    window30m: RecentErrorWindow;
    window60m: RecentErrorWindow;
  };
  upcoming24hAutoDeletions: Array<{
    start: string;
    localHour: number;
    estimatedCount: number;
  }>;
  peakHours: number[];
  suggestedReplenishmentHours: number[];
};

export type ReplenishmentTimingRisk = {
  level: PoolRisk["level"];
  action: "avoid" | "caution" | "suitable" | "insufficient";
  evaluatedHour: number;
  hourLabel: string;
  confidence: PoolForecast["confidence"];
  newErrors: number;
  inferredAccountRemovals: number;
  errorRatePercent: number | null;
  removalRatePercent: number | null;
  autoDeletionCandidates: number;
  dueNextHour: number;
  recentErrorSignalLevel: PoolRisk["level"];
  reasons: string[];
  peakHours: number[];
  suggestedHours: number[];
};

export type PoolAnalyticsResponse = {
  groupName: string;
  timezone: string;
  generatedAt: string;
  calendarFallback: boolean;
  current?: PoolSnapshot | null;
  daily: DailyPoolUsage[];
  forecasts: {
    tomorrow: PoolForecast;
    nextThreeDays?: PoolForecast[];
    nextWorkday: PoolForecast;
    nextNonWorkday: PoolForecast;
    rolling24h: RollingPoolForecast;
  };
  recommendation: PoolRecommendation;
  risk: PoolRisk;
  deathAnalysis?: PoolDeathAnalysis;
  replenishmentTimingRisk?: ReplenishmentTimingRisk;
  dataCoverage: {
    daysRequested: number;
    completeDays: number;
    eligibleDays: number;
    firstDate?: string | null;
  };
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
