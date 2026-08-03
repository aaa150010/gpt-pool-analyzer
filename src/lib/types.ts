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
  withdrawalAmountManual?: boolean;
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
  isCurrentHour: boolean;
  isComplete: boolean;
  lastSnapshotAt: string | null;
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
  currentHourNewErrors: number;
  currentHourRemovals: number;
  currentHourLikelyErrorDeaths: number;
  currentHourSampleCount: number;
  currentHourObservedMinutes: number;
  currentHourLastSnapshotAt: string | null;
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

export type WithdrawalMode = "cost" | "full";

export type WithdrawalItem = {
  itemId?: number;
  sequence: number;
  email: string;
  targetId?: string;
  owner: "owner" | "partner";
  ownerLabel: string;
  paymentMethod: "wechat" | "alipay";
  balance: number;
  amount: number;
  status: "queued" | "waiting" | "running" | "submitted" | "skipped" | "failed";
  statusLabel?: string;
  error?: string | null;
  submittedAt?: string | null;
  costRecoveredAmount?: number;
  costRecoveredAt?: string | null;
  remainingCostAfter?: number | null;
};

export type WithdrawalSettlement = {
  gross: number;
  cost: number;
  profit: number;
  ownerActual: number;
  partnerActual: number;
  ownerExpected: number;
  partnerExpected: number;
  partnerToOwner: number;
  ownerToPartner: number;
  roundingRemainder: number;
  costRecovery: number;
  unrecoveredCost: number;
};

export type WithdrawalPlan = {
  mode: WithdrawalMode;
  requestedAmount: number;
  totalAmount: number;
  cost: number;
  balanceSnapshotAt: string;
  balanceSnapshotTotal?: number | null;
  costHistory?: CostAddition[];
  costHistoryTotal?: number;
  settlement: WithdrawalSettlement;
  items: WithdrawalItem[];
};

export type WithdrawalJob = WithdrawalPlan & {
  jobId: string;
  status: "queued" | "waiting" | "running" | "completed" | "failed";
  currentSequence: number;
  nextRunAt: string | null;
  error: string | null;
  postWithdrawalCost?: number | null;
  postWithdrawalBalance?: number | null;
  discountedProfit?: number | null;
  costClearedAt?: string | null;
  costClearedAmount?: number;
  costSettlementStatus?: "pending" | "partial" | "cleared" | "already_cleared" | "not_recovered" | "not_applicable";
  createdAt: string;
  updatedAt: string;
};

export type WithdrawalHistoryResponse = {
  jobs: WithdrawalJob[];
  total: number;
  limit: number;
  offset: number;
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

export type PixelTarget = {
  id: string;
  email: string;
  connected: boolean;
  accountCount: number | null;
  lastCheckedAt: string | null;
  error: string | null;
};

export type PixelAccount = {
  id: number;
  name: string;
  platform: string;
  accountLevel: string;
  type: string;
  shareMode: string;
  shareStatus: string;
  concurrency: number;
  currentConcurrency: number;
  priority: number;
  status: string;
  schedulable: boolean;
  credentialsStatus: string;
  errorMessage: string;
  errorSince: string | null;
  expiresAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  codex5hLimitPercent: number | null;
  codex7dLimitPercent: number | null;
  rateLimitedAt: string | null;
  rateLimitResetAt: string | null;
  codexQuotaProtectionReason: string | null;
  codexQuotaProtectionResetAt: string | null;
  groups: Array<{ id: number; name: string }>;
};

export type PixelAccountPage = {
  items: PixelAccount[];
  total: number;
  page: number;
  pageSize: number;
  pages: number;
  target: PixelTarget;
};

export type PixelAccountUsage = {
  accountId: number;
  codex5hLimitPercent: number | null;
  codex7dLimitPercent: number | null;
};

export type PixelBulkOperationResponse = {
  ok: boolean;
  success: number;
  failed: number;
  successIds?: number[];
  failedIds?: number[];
  concurrencyById?: Record<string, number>;
  message?: string | null;
};

export type PixelBulkUpdateRequest = {
  accountIds: number[];
  makePublic: boolean;
  concurrency?: number;
};

export type PixelImportTargetResult = {
  targetId: string;
  email: string;
  generatedFileName: string;
  sourceCount: number;
  created: number;
  updated: number;
  failed: number;
  shared: number;
  shareFailed: number;
  failedShareIds: number[];
  concurrencyById: Record<string, number>;
  importErrors: Array<{ index: string; name: string; message: string }>;
  generatedNames: string[];
  status: "success" | "partial" | "failed";
  message: string;
};

export type PixelImportResponse = {
  ok: boolean;
  sourceFileName: string;
  sourceCount: number;
  results: PixelImportTargetResult[];
};

export type PixelImportJob = {
  jobId: string;
  status: "queued" | "running" | "completed" | "failed";
  phase: "queued" | "processing" | "waiting" | "completed" | "failed";
  createdAt: string;
  updatedAt: string;
  sourceFileName: string;
  sourceFileNames: string[];
  sourceCount: number;
  currentTargetId: string | null;
  completedTargets: number;
  totalTargets: number;
  waitSeconds: number;
  nextRunAt?: string | null;
  results: PixelImportTargetResult[];
  error: string | null;
};

export type PixelDeleteTargetResult = {
  targetId: string;
  email: string;
  total: number;
  deleted: number;
  failed: number;
  failedIds: number[];
  status: "success" | "partial" | "failed";
  message: string;
};

export type PixelImportRecordTarget = {
  targetId: string;
  email: string;
  generatedFileName: string;
  sourceCount: number;
  created: number;
  updated: number;
  failed: number;
  shared: number;
  shareFailed: number;
  importErrors: Array<{ index: string; name: string; message: string }>;
  status: "success" | "partial" | "failed";
  message: string;
  generatedNames: string[];
};

export type PixelImportRecordDeleteResult = {
  targetId: string;
  email: string;
  requested: number;
  matched: number;
  deleted: number;
  failed: number;
  deletedNames: string[];
  missingNames: string[];
  ambiguousNames: string[];
  failedIds: number[];
  status: "success" | "partial" | "failed";
  message: string;
};

export type PixelImportRecord = {
  recordId: string;
  createdAt: string;
  sourceFileName: string;
  sourceFileNames: string[];
  sourceCount: number;
  targetCount: number;
  targets: PixelImportRecordTarget[];
  deleteStatus: "active" | "partial" | "deleted";
  deletedAt: string | null;
  lastDeleteResults: PixelImportRecordDeleteResult[];
};

export type PixelExportJob = {
  jobId: string;
  status: "queued" | "running" | "completed" | "failed";
  phase: "queued" | "exporting" | "backing_up" | "deleting" | "importing" | "waiting" | "completed" | "failed";
  createdAt: string;
  updatedAt: string;
  mode: "export_delete_reimport";
  currentTargetId: string | null;
  completedTargets: number;
  totalTargets: number;
  waitSeconds: number;
  backupFileName: string | null;
  export: {
    sourceCount: number;
    deduplicatedCount: number;
    duplicateCount: number;
    batchCount: number;
  } | null;
  deleteResults: PixelDeleteTargetResult[];
  results: PixelImportTargetResult[];
  error: string | null;
};

export type PixelShareResponse = {
  ok: boolean;
  success: number;
  failed: number;
  successIds: number[];
  failedIds: number[];
  concurrencyById: Record<string, number>;
};

export type PixelShareAllTargetResult = {
  targetId: string;
  email: string;
  total: number;
  shared: number;
  failed: number;
  failedIds: number[];
  concurrencyById: Record<string, number>;
  status: "success" | "partial" | "failed";
  message: string;
};

export type PixelShareAllResponse = {
  ok: boolean;
  status: "success" | "partial" | "failed";
  totalTargets: number;
  total: number;
  shared: number;
  failed: number;
  results: PixelShareAllTargetResult[];
  message: string;
};

export type PixelExportDownload = {
  blob: Blob;
  fileName: string;
  sourceCount: number;
  deduplicatedCount: number;
  duplicateCount: number;
  batchCount: number;
};

export type PoolMetricKey =
  | "remaining5h"
  | "remaining7d"
  | "total"
  | "limited"
  | "quotaProtected"
  | "error"
  | "concurrentAvailable";
