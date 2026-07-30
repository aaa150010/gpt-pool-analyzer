import { AnimatePresence, motion, useMotionValue, useSpring, useTransform } from "framer-motion";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import {
  Activity,
  AlertTriangle,
  Bell,
  Calculator,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  Download,
  FileJson,
  FolderTree,
  History,
  Loader2,
  Mail,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Server,
  Settings,
  Trash2,
  TrendingUp,
  Upload,
  Users,
  WalletCards,
  X,
} from "lucide-react";
import type React from "react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "./lib/api";
import type {
  BalanceAccount,
  CostAddition,
  DailyPoolUsage,
  DeathAnalysisDay,
  DeathTimelineHour,
  PoolAnalyticsResponse,
  PoolAnalyzerState,
  PoolForecast,
  PoolCredentials,
  PoolMetricKey,
  PoolSnapshot,
  PixelAccount,
  PixelAccountPage,
  PixelAccountUsage,
  PixelExportJob,
  PixelImportJob,
  PixelImportRecord,
  PixelImportTargetResult,
  PixelTarget,
  ServerStateResponse,
  SMTPSettings,
  StoredState,
} from "./lib/types";
import {
  cn,
  formatDateInput,
  formatDateTime,
  formatMoney,
  formatSignedMoney,
  latestByDate,
  parseMoney,
} from "./lib/utils";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./components/ui/card";
import { Checkbox } from "./components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "./components/ui/dialog";
import { Input } from "./components/ui/input";
import { Label } from "./components/ui/label";
import { Textarea } from "./components/ui/textarea";

const defaultStored: StoredState = {
  cost: 518,
  partnerCost: 0,
  withdrawalAmount: 0,
  useBaseDeduction: false,
  baseDeductionAmount: 0,
  history: [],
  costAdditions: [],
  settlement: {
    partnerName: "社会哥",
    partnerSharePercent: 40,
    payoutRatePercent: 85,
    withdrawals: {},
  },
};

const defaultPool: PoolAnalyzerState = {
  history: [],
  selectedGroups: ["PLUS共享号池", "K12共享号池", "TEAM共享号池"],
  availableGroups: ["PLUS共享号池", "K12共享号池", "TEAM共享号池"],
  pollingMinutes: 5,
  warningEmail: "",
};

const hiddenPoolGroups = new Set(["CLAUDE共享号池", "GROK共享号池", "CODEX【兜底】", "FREE共享号池", "PRO共享号池"]);

const navItems = [
  { key: "trends", label: "趋势分析", icon: TrendingUp },
  { key: "pools", label: "账号池分析", icon: Users },
  { key: "manager", label: "账号池管理", icon: FolderTree },
  { key: "cost", label: "成本计算", icon: Calculator },
  { key: "history", label: "成本历史", icon: History },
] as const;

type ViewKey = (typeof navItems)[number]["key"];
type TableCell = React.ReactNode;

const trendMetrics: { key: PoolMetricKey; label: string; color: string }[] = [
  { key: "total", label: "总账号", color: "#2563eb" },
  { key: "remaining5h", label: "5h剩余", color: "#059669" },
  { key: "remaining7d", label: "7d剩余", color: "#7c3aed" },
  { key: "error", label: "错误", color: "#dc2626" },
];

const chartTick = { fontSize: 10, fontWeight: 500, fill: "#64748b" };
const smallChartTick = { fontSize: 9, fontWeight: 500, fill: "#64748b" };
const chartAxisLine = { stroke: "#94a3b8", strokeWidth: 1 };
const chartTickLine = { stroke: "#cbd5e1", strokeWidth: 1 };
const legendStyle = { fontSize: 12, fontWeight: 600, color: "#475569", lineHeight: "16px" };
const tooltipStyle = {
  borderRadius: 8,
  border: "1px solid #dbe2ea",
  boxShadow: "0 10px 24px rgba(15, 23, 42, 0.10)",
  fontSize: 12,
  fontWeight: 600,
  lineHeight: "16px",
  padding: "8px 10px",
};
const tooltipLabelStyle = { fontSize: 12, fontWeight: 700, color: "#0f172a", marginBottom: 4 };
const tooltipItemStyle = { fontSize: 12, fontWeight: 600, lineHeight: "16px", padding: 0 };

export function App() {
  const [view, setView] = useState<ViewKey>("trends");
  const [serverState, setServerState] = useState<ServerStateResponse>({ initialized: false });
  const [stored, setStored] = useState<StoredState>(defaultStored);
  const [pool, setPool] = useState<PoolAnalyzerState>(defaultPool);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");
  const [dialog, setDialog] = useState<null | "addCost" | "costHistory" | "accounts" | "poolCredentials" | "smtp">(null);
  const editStartedAt = useRef(0);
  const saveTimer = useRef<number | null>(null);

  const applyState = useCallback((response: ServerStateResponse) => {
    setServerState(response);
    if (response.storedState) {
      setStored(normalizeStored(response.storedState));
    }
    if (response.poolState) {
      setPool(normalizePool(response.poolState));
    }
  }, []);

  const loadState = useCallback(
    async (manual = false) => {
      try {
        const response = await api.state();
        applyState(response);
        if (manual) setToast("已同步服务器最新数据");
      } catch (error) {
        setToast(error instanceof Error ? error.message : "服务器读取失败");
      } finally {
        setLoading(false);
      }
    },
    [applyState],
  );

  useEffect(() => {
    void loadState();
    const timer = window.setInterval(() => void loadState(), 60_000);
    return () => window.clearInterval(timer);
  }, [loadState]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 2600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const saveStoredDebounced = useCallback(
    (next: StoredState, delay = 700) => {
      editStartedAt.current = Date.now();
      setStored(next);
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(async () => {
        setSaving(true);
        try {
          const response = await api.updateStoredState(next);
          if (Date.now() - editStartedAt.current > delay && response.state) {
            applyState(response.state);
          }
          setToast("已保存到服务器");
        } catch (error) {
          setToast(error instanceof Error ? error.message : "保存失败");
        } finally {
          setSaving(false);
        }
      }, delay);
    },
    [applyState],
  );

  const latestBalance = useMemo(() => latestByDate(stored.history), [stored.history]);
  const availableGroups = visiblePoolGroups(pool.availableGroups?.length ? pool.availableGroups : defaultPool.availableGroups!);
  const selectedGroups = visiblePoolGroups(pool.selectedGroups?.length ? pool.selectedGroups : defaultPool.selectedGroups!).filter((group) =>
    availableGroups.includes(group),
  );
  const activeSelectedGroups = selectedGroups.length ? selectedGroups : availableGroups;
  const totalCost = stored.cost || 0;
  const partnerCost = stored.partnerCost || 0;
  const costSummary = totalCost + partnerCost;
  const withdrawal = stored.withdrawalAmount || 0;
  const settlementBase = withdrawal;
  const netOutcome = settlementBase - totalCost;
  const partnerReceivable = netOutcome >= 0 ? partnerCost + netOutcome * 0.4 : partnerCost + netOutcome / 2;
  const ownerReceivable = withdrawal - partnerReceivable;

  const updateStoredField = (patch: Partial<StoredState>) => {
    saveStoredDebounced({ ...stored, ...patch });
  };

  const refreshNow = async () => {
    setRefreshing(true);
    try {
      const response = await api.refresh();
      if (response.state) applyState(response.state);
      setToast("服务器已刷新");
    } catch (error) {
      setToast(error instanceof Error ? error.message : "刷新失败");
    } finally {
      setRefreshing(false);
    }
  };

  const savePoolState = async (nextPool: PoolAnalyzerState) => {
    const normalized = { ...nextPool, pollingMinutes: 5 };
    setPool(normalized);
    setSaving(true);
    try {
      const response = await api.updatePoolState(normalized);
      if (response.state) applyState(response.state);
      setToast("号池配置已保存");
    } catch (error) {
      setToast(error instanceof Error ? error.message : "号池配置保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-background text-foreground">
      <header className="z-20 shrink-0 border-b border-border bg-card/95 shadow-sm backdrop-blur">
        <div className="flex h-16 items-center justify-between px-6">
          <div className="flex items-center gap-4">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
              <Database className="h-5 w-5" />
            </div>
            <div>
              <div className="text-lg font-black">91</div>
              <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground">
                <Server className="h-3.5 w-3.5" />
                {serverState.initialized ? "服务器已接管数据" : "服务器未初始化"}
                <span className="text-border">|</span>
                {latestBalance ? `最近余额：${formatDateTime(latestBalance.date)}` : "等待服务器刷新数据"}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {saving && (
              <span className="flex items-center gap-1 text-xs font-bold text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                保存中
              </span>
            )}
            <Button variant="outline" onClick={() => void loadState(true)}>
              <RefreshCw className="h-4 w-4" />
              同步
            </Button>
            <Button onClick={() => void refreshNow()} disabled={refreshing}>
              {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Activity className="h-4 w-4" />}
              刷新服务器
            </Button>
          </div>
        </div>
        <nav className="flex h-11 items-stretch gap-6 border-t border-border px-6">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = view === item.key;
            return (
              <button
                key={item.key}
                className={cn(
                  "relative flex items-center gap-2 border-b-2 border-transparent px-1 text-sm font-black transition duration-200",
                  active ? "border-primary text-primary" : "text-muted-foreground hover:border-border hover:text-foreground",
                )}
                onClick={() => setView(item.key)}
              >
                <Icon className="h-4 w-4" />
                {item.label}
                {active && (
                  <motion.span
                    layoutId="top-tab-active"
                    className="absolute -bottom-0.5 left-0 right-0 h-0.5 rounded-full bg-primary"
                    transition={{ duration: 0.2 }}
                  />
                )}
              </button>
            );
          })}
        </nav>
      </header>

      <main className={cn("min-h-0 flex-1", view === "manager" ? "overflow-hidden" : "overflow-y-auto overscroll-contain [scrollbar-gutter:stable]")}>
        <section className={cn("p-4", view === "manager" ? "h-full min-h-0" : "space-y-4")}>
            {view !== "trends" && view !== "manager" && (
              loading ? (
                <OverviewCardsSkeleton />
              ) : (
                <OverviewCards
                  latestTotal={latestBalance?.total}
                  cost={totalCost}
                  costSummary={costSummary}
                  net={latestBalance ? latestBalance.total - totalCost : undefined}
                />
              )
            )}

          <AnimatePresence mode="wait">
            <motion.div
              key={view}
              className={cn(view === "manager" && "h-full min-h-0")}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18, ease: "easeOut" }}
            >
              {loading && <ViewSkeleton view={view} />}
              {!loading && view === "trends" && (
                <TrendsView
                  stored={stored}
                  pool={pool}
                  selectedGroups={activeSelectedGroups}
                  availableGroups={availableGroups}
                  onPoolChange={(next) => void savePoolState(next)}
                />
              )}
              {!loading && view === "pools" && (
                <PoolsView
                  pool={pool}
                  selectedGroups={activeSelectedGroups}
                  availableGroups={availableGroups}
                  onPoolChange={(next) => void savePoolState(next)}
                  onRefresh={() => void refreshNow()}
                  onOpenDialog={setDialog}
                />
              )}
              {!loading && view === "manager" && <PixelManagerView onToast={setToast} />}
              {!loading && view === "cost" && (
                <CostView
                  stored={stored}
                  latestBalance={latestBalance}
                  totalCost={totalCost}
                  netOutcome={netOutcome}
                  partnerReceivable={partnerReceivable}
                  ownerReceivable={ownerReceivable}
                  updateStoredField={updateStoredField}
                  onAddCost={() => setDialog("addCost")}
                  onHistory={() => setDialog("costHistory")}
                  onAccounts={() => setDialog("accounts")}
                  onPoolCredentials={() => setDialog("poolCredentials")}
                  onSmtp={() => setDialog("smtp")}
                />
              )}
              {!loading && view === "history" && <HistoryView stored={stored} />}
            </motion.div>
          </AnimatePresence>
        </section>
      </main>

      <AddCostDialog
        open={dialog === "addCost"}
        onOpenChange={(open) => setDialog(open ? "addCost" : null)}
        onSubmit={async (addition) => {
          const optimistic = {
            ...stored,
            cost: totalCost + addition.amount,
            costAdditions: [...(stored.costAdditions ?? []), addition],
          };
          setStored(optimistic);
          const response = await api.addCost(addition);
          if (response.state) applyState(response.state);
          setToast("累加成本已保存");
        }}
      />
      <CostHistoryDialog
        open={dialog === "costHistory"}
        additions={stored.costAdditions ?? []}
        onOpenChange={(open) => setDialog(open ? "costHistory" : null)}
        onClear={async () => {
          const response = await api.clearCosts();
          if (response.state) applyState(response.state);
          setToast("累加成本已清空");
        }}
      />
      <AccountsDialog open={dialog === "accounts"} onOpenChange={(open) => setDialog(open ? "accounts" : null)} onSaved={() => void refreshNow()} />
      <PoolCredentialsDialog
        open={dialog === "poolCredentials"}
        onOpenChange={(open) => setDialog(open ? "poolCredentials" : null)}
        onSaved={() => void refreshNow()}
      />
      <SmtpDialog open={dialog === "smtp"} onOpenChange={(open) => setDialog(open ? "smtp" : null)} />

      <AnimatePresence>
        {toast && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 10 }}
            className="fixed bottom-5 right-5 z-50 rounded-lg border border-border bg-card px-4 py-3 text-sm font-bold shadow-admin"
          >
            {toast}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function OverviewCards({
  latestTotal,
  cost,
  costSummary,
  net,
}: {
  latestTotal?: number;
  cost: number;
  costSummary: number;
  net?: number;
}) {
  const cards = [
    { title: "成本合计", value: costSummary, accent: "text-blue-600", sub: "星星 + 社会哥", digits: 2, signed: false },
    { title: "余额合计", value: latestTotal, accent: "text-emerald-600", sub: "服务器最近一次刷新", digits: 2, signed: false },
    { title: "扣后利润", value: net, accent: net !== undefined && net < 0 ? "text-rose-600" : "text-indigo-600", sub: "余额合计 - 总出资", digits: 2, signed: true },
    { title: "星星出资", value: cost, accent: "text-violet-600", sub: "含累加成本", digits: 2, signed: false },
  ];
  return (
    <div className="grid grid-cols-4 gap-3">
      {cards.map((card) => (
        <MetricTile key={card.title} {...card} size="large" />
      ))}
    </div>
  );
}

function TrendsView({
  stored,
  pool,
  selectedGroups,
  availableGroups,
  onPoolChange,
}: {
  stored: StoredState;
  pool: PoolAnalyzerState;
  selectedGroups: string[];
  availableGroups: string[];
  onPoolChange: (pool: PoolAnalyzerState) => void;
}) {
  const balanceRows = stored.history.map((item) => ({
    time: formatDateTime(item.date),
    total: Number(item.total.toFixed(2)),
    net: Number((item.total - stored.cost).toFixed(2)),
  }));
  return (
    <div className="space-y-4">
      <PoolOperationsDashboard
        pool={pool}
        availableGroups={availableGroups}
        preferredGroup={selectedGroups[0]}
        onPoolChange={onPoolChange}
      />
      <Card>
        <CardHeader>
          <CardTitle>账号池趋势</CardTitle>
          <span className="text-xs font-bold text-muted-foreground">{selectedGroups.join("、")}</span>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3">
          {trendMetrics.map((metric) => (
            <PoolMetricChart key={metric.key} pool={pool} selectedGroups={selectedGroups} metric={metric} />
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>余额走势</CardTitle>
          <span className="text-xs font-bold text-muted-foreground">趋势窗口：最近 24 小时</span>
        </CardHeader>
        <CardContent>
          <div className="h-[360px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={balanceRows}>
                <defs>
                  <linearGradient id="balanceFill" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor="#10b981" stopOpacity={0.26} />
                    <stop offset="100%" stopColor="#10b981" stopOpacity={0.04} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="time" minTickGap={36} tick={chartTick} axisLine={chartAxisLine} tickLine={chartTickLine} />
                <YAxis width={50} tick={chartTick} axisLine={chartAxisLine} tickLine={chartTickLine} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle} />
                <Legend wrapperStyle={legendStyle} iconSize={7} />
                <Area type="monotone" dataKey="total" name="余额合计" stroke="#10b981" strokeWidth={2.3} fill="url(#balanceFill)" />
                <Line type="monotone" dataKey="net" name="扣后利润" stroke="#4f46e5" strokeWidth={2} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function PoolOperationsDashboard({
  pool,
  availableGroups,
  preferredGroup,
  onPoolChange,
}: {
  pool: PoolAnalyzerState;
  availableGroups: string[];
  preferredGroup?: string;
  onPoolChange: (pool: PoolAnalyzerState) => void;
}) {
  const fallbackGroup = preferredGroup && availableGroups.includes(preferredGroup) ? preferredGroup : availableGroups[0] ?? "PLUS共享号池";
  const analyticsGroup = availableGroups.includes(pool.analyticsGroup ?? "") ? pool.analyticsGroup! : fallbackGroup;
  const [days, setDays] = useState<7 | 30 | 90>(7);
  const [analytics, setAnalytics] = useState<PoolAnalyticsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const latestSnapshotDate = useMemo(
    () => pool.history.filter((row) => row.groupName === analyticsGroup).at(-1)?.date,
    [analyticsGroup, pool.history],
  );

  useEffect(() => {
    if (!analyticsGroup) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    void api
      .poolAnalytics(analyticsGroup, days)
      .then((response) => {
        if (!cancelled) setAnalytics(response);
      })
      .catch((reason) => {
        if (!cancelled) {
          setAnalytics(null);
          setError(reason instanceof Error ? reason.message : "运营分析加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [analyticsGroup, days, latestSnapshotDate]);

  const setAnalyticsGroup = (group: string) => {
    if (!group || group === analyticsGroup) return;
    onPoolChange({ ...pool, analyticsGroup: group });
  };

  return (
    <Card>
      <CardHeader className="border-b border-border">
        <div>
          <CardTitle>账号池每日运营看板</CardTitle>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <select
            aria-label="分析账号池"
            value={analyticsGroup}
            onChange={(event) => setAnalyticsGroup(event.target.value)}
            className="h-9 rounded-md border border-border bg-background px-2.5 text-sm font-bold text-foreground outline-none focus:ring-2 focus:ring-ring"
          >
            {availableGroups.map((group) => (
              <option key={group} value={group}>
                {group}
              </option>
            ))}
          </select>
          <div className="flex h-9 overflow-hidden rounded-md border border-border bg-background" aria-label="统计范围">
            {([7, 30, 90] as const).map((range) => (
              <button
                key={range}
                onClick={() => setDays(range)}
                className={cn(
                  "min-w-11 border-l border-border px-2 text-xs font-black first:border-l-0",
                  days === range ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {range}天
              </button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent className="pt-4">
        {loading && !analytics && <DashboardLoading />}
        {!loading && error && (
          <div className="flex min-h-32 items-center gap-3 rounded-md border border-rose-200 bg-rose-50 px-4 text-sm font-bold text-rose-700">
            <AlertTriangle className="h-5 w-5 shrink-0" />
            {error}
          </div>
        )}
        {analytics && <PoolOperationsContent analytics={analytics} loading={loading} />}
      </CardContent>
    </Card>
  );
}

function DashboardLoading() {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-4 gap-3">
        {Array.from({ length: 4 }).map((_, index) => (
          <SkeletonLine key={index} className="h-20 rounded-md" />
        ))}
      </div>
      <SkeletonLine className="h-[270px] rounded-md" />
    </div>
  );
}

function PoolOperationsContent({ analytics, loading }: { analytics: PoolAnalyticsResponse; loading: boolean }) {
  const latestDaily = analytics.daily.at(-1);
  const coverage = analytics.dataCoverage;
  const nextThreeDays = analytics.forecasts.nextThreeDays?.length === 3
    ? analytics.forecasts.nextThreeDays
    : [analytics.forecasts.tomorrow, analytics.forecasts.nextWorkday, analytics.forecasts.nextNonWorkday];
  const riskStyle = {
    low: "border-emerald-200 bg-emerald-50 text-emerald-700",
    medium: "border-amber-200 bg-amber-50 text-amber-700",
    high: "border-rose-200 bg-rose-50 text-rose-700",
    insufficient: "border-slate-200 bg-slate-50 text-slate-600",
  }[analytics.risk.level];
  return (
    <div className={cn("space-y-4", loading && "opacity-60")}>
      <div className="grid grid-cols-4 gap-3">
        <MetricTile title="当前总账号" value={analytics.current?.total} accent="text-blue-600" size="normal" sub={analytics.groupName} />
        <MetricTile title="今日估算 5h 消耗" value={latestDaily?.estimated5h} accent="text-emerald-600" digits={1} size="normal" />
        <MetricTile title="今日估算 7d 消耗" value={latestDaily?.estimated7d} accent="text-violet-600" digits={1} size="normal" />
        <MetricTile
          title={`建议补号（未来 ${analytics.recommendation.horizonHours}h）`}
          value={analytics.recommendation.replenish}
          accent="text-blue-700"
          size="normal"
          suffix=" 个"
          sub={(
            <span className="inline-flex items-center gap-1.5">
              <span>建议置信度</span>
              <span className={cn("rounded border px-1.5 py-0.5 text-[10px] font-black leading-none", confidenceBadgeClass(analytics.forecasts.rolling24h.confidence))}>
                {confidenceLabel(analytics.forecasts.rolling24h.confidence)}
              </span>
            </span>
          )}
        />
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)_300px] gap-3">
        <div className="min-w-0 rounded-md border border-border bg-background p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <div className="text-[13px] font-bold text-foreground">每日消耗与掉号趋势</div>
            <span className="text-xs font-semibold text-muted-foreground">
              完整 {coverage.completeDays}/{coverage.daysRequested} 天，合格样本 {coverage.eligibleDays} 天
            </span>
          </div>
          <div className="h-[270px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={buildOperationsChartRows(analytics.daily, analytics.forecasts)}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="date" minTickGap={28} tick={smallChartTick} axisLine={chartAxisLine} tickLine={chartTickLine} />
                <YAxis yAxisId="usage" width={42} tick={smallChartTick} axisLine={chartAxisLine} tickLine={chartTickLine} />
                <YAxis yAxisId="accounts" orientation="right" width={42} tick={smallChartTick} axisLine={chartAxisLine} tickLine={chartTickLine} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle} />
                <Legend wrapperStyle={legendStyle} iconSize={7} />
                <Bar yAxisId="accounts" dataKey="accountDecrease" name="账号减少" fill="#f97316" barSize={12} radius={[2, 2, 0, 0]} />
                <Line yAxisId="usage" type="monotone" dataKey="estimated5h" name="5h 估算消耗" stroke="#059669" strokeWidth={2} dot={false} connectNulls />
                <Line yAxisId="usage" type="monotone" dataKey="estimated7d" name="7d 估算消耗" stroke="#7c3aed" strokeWidth={2} dot={false} connectNulls />
                <Line yAxisId="usage" type="monotone" dataKey="forecast5h" name="5h 预测" stroke="#059669" strokeWidth={1.8} strokeDasharray="5 4" dot={{ r: 3 }} connectNulls />
                <Line yAxisId="usage" type="monotone" dataKey="forecast7d" name="7d 预测" stroke="#7c3aed" strokeWidth={1.8} strokeDasharray="5 4" dot={{ r: 3 }} connectNulls />
                <Line yAxisId="accounts" type="monotone" dataKey="forecastDecrease" name="掉号预测" stroke="#f97316" strokeWidth={1.8} strokeDasharray="5 4" dot={{ r: 3 }} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className={cn("rounded-md border p-3", riskStyle)}>
          <div className="flex items-center gap-2 text-sm font-black">
            <AlertTriangle className="h-4 w-4" />
            当前不补号风险：{riskLabel(analytics.risk.level)}
          </div>
          <div className="mt-2 space-y-1.5 text-xs font-semibold leading-5">
            {analytics.risk.reasons.length ? analytics.risk.reasons.map((reason, index) => <p key={index}>{reason}</p>) : <p>暂未发现明显风险。</p>}
          </div>
          <div className="mt-4 border-t border-current/15 pt-3 text-xs font-semibold leading-5">
            <div>5h 额度缺口：{formatMoney(analytics.recommendation.gap5h, 1)}</div>
            <div>7d 额度缺口：{formatMoney(analytics.recommendation.gap7d, 1)}</div>
            <div>预计 24h 账号减少：{formatMoney(analytics.forecasts.rolling24h.accountDecrease, 0)}</div>
          </div>
        </div>
      </div>

      {analytics.deathAnalysis && analytics.replenishmentTimingRisk && (
        <DeathTimingAnalysis
          analysis={analytics.deathAnalysis}
          timing={analytics.replenishmentTimingRisk}
          recommendation={analytics.recommendation.replenish}
          capacityRisk={analytics.risk.level}
        />
      )}

      <div className="grid grid-cols-3 gap-3">
        {nextThreeDays.map((forecast, index) => (
          <ForecastSummary key={`${forecast.date}-${index}`} title={["明天", "后天", "大后天"][index]} forecast={forecast} />
        ))}
      </div>

      <DailyUsageTable
        rows={analytics.daily}
        deathDays={analytics.deathAnalysis?.daily}
        timezone={analytics.timezone}
        calendarFallback={analytics.calendarFallback}
      />
    </div>
  );
}

function DeathTimingAnalysis({
  analysis,
  timing,
  recommendation,
  capacityRisk,
}: {
  analysis: NonNullable<PoolAnalyticsResponse["deathAnalysis"]>;
  timing: NonNullable<PoolAnalyticsResponse["replenishmentTimingRisk"]>;
  recommendation: number | null;
  capacityRisk: PoolAnalyticsResponse["risk"]["level"];
}) {
  const timeline = new Map(analysis.timeline.map((item) => [`${item.date}-${item.hour}`, item]));
  const maxRemovals = Math.max(...analysis.timeline.map((item) => item.inferredAccountRemovals), 1);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const recent = analysis.recentErrorTrend;
  const totals = analysis.daily.reduce(
    (sum, row) => ({
      newErrors: sum.newErrors + row.newErrors,
      removals: sum.removals + row.inferredAccountRemovals,
      automatic: sum.automatic + row.autoDeletionCandidates,
      manual: sum.manual + row.manualOrUnmatchedCandidates,
    }),
    { newErrors: 0, removals: 0, automatic: 0, manual: 0 },
  );
  const timingStyle = {
    low: "border-emerald-200 bg-emerald-50 text-emerald-700",
    medium: "border-amber-200 bg-amber-50 text-amber-700",
    high: "border-rose-200 bg-rose-50 text-rose-700",
    insufficient: "border-slate-200 bg-slate-50 text-slate-600",
  }[timing.level];
  const decision = replenishmentDecision(recommendation, timing.action, capacityRisk);
  const currentDate = analysis.daily.at(-1)?.date;
  const heatTargets = analysis.daily.flatMap((day) => Array.from({ length: 24 }, (_, hour) => ({
    date: day.date,
    hour,
    cell: timeline.get(`${day.date}-${hour}`),
    current: day.date === currentDate && hour === timing.evaluatedHour,
  })));
  const currentTarget = heatTargets.find((target) => target.current);
  const currentHourNewErrors = timing.currentHourNewErrors ?? currentTarget?.cell?.newErrors ?? 0;
  const currentHourRemovals = timing.currentHourRemovals ?? currentTarget?.cell?.inferredAccountRemovals ?? 0;
  const currentHourLikelyErrorDeaths = timing.currentHourLikelyErrorDeaths ?? currentTarget?.cell?.likelyErrorDeaths ?? 0;
  const currentHourSampleCount = timing.currentHourSampleCount ?? currentTarget?.cell?.sampleCount ?? 0;
  const currentHourObservedMinutes = timing.currentHourObservedMinutes ?? currentTarget?.cell?.observedMinutes ?? 0;
  const currentHourLastSnapshotAt = timing.currentHourLastSnapshotAt ?? currentTarget?.cell?.lastSnapshotAt ?? analysis.lastSnapshotAt;
  const activeSelectedKey = selectedKey ?? (currentTarget ? heatCellKey(currentTarget.date, currentTarget.hour) : null);
  const selected = activeSelectedKey ? heatTargets.find((target) => heatCellKey(target.date, target.hour) === activeSelectedKey) : undefined;
  const hovered = hoveredKey ? heatTargets.find((target) => heatCellKey(target.date, target.hour) === hoveredKey) : undefined;
  const detailTarget = hovered ?? selected;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_300px] gap-3">
      <div className="min-w-0 rounded-md border border-border bg-background p-3">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <div className="text-[13px] font-bold text-foreground">近 7 天杀号时段</div>
            <div className="mt-0.5 text-xs font-semibold text-muted-foreground">基于 {analysis.snapshotCount} 条快照的新增错误与账号删除推断</div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 text-xs font-bold text-muted-foreground">
            <span className="h-2.5 w-2.5 bg-orange-100" />
            <span className="h-2.5 w-2.5 bg-orange-300" />
            <span className="h-2.5 w-2.5 bg-orange-600" />
            删除热度
            <span className="ml-1 h-1.5 w-1.5 rounded-full bg-rose-600" />
            新增错误
          </div>
        </div>

        <div className="mb-3 grid grid-cols-4 gap-3 border-b border-border pb-3 text-xs font-semibold text-muted-foreground">
          <DeathStat label="当前错误" value={formatMoney(recent.currentErrors, 0)} />
          <DeathStat label="近 30 分钟" value={formatSignedCount(recent.window30m.netIncrease)} alert={recent.window30m.netIncrease > 0} />
          <DeathStat label="近 60 分钟" value={formatSignedCount(recent.window60m.netIncrease)} alert={recent.window60m.netIncrease > 0} />
          <DeathStat label="连续上升" value={recent.isContinuouslyRising ? "是" : "否"} alert={recent.isContinuouslyRising} />
        </div>

        <TooltipPrimitive.Provider delayDuration={120} skipDelayDuration={60}>
          <div className="overflow-x-auto">
            <div className="grid min-w-[750px] grid-cols-[54px_repeat(24,minmax(22px,1fr))_44px] gap-1">
            <div className="self-end pb-1 text-[10px] font-bold text-muted-foreground">日期</div>
            {Array.from({ length: 24 }, (_, hour) => (
              <div key={hour} className="pb-1 text-center text-[9px] font-bold text-muted-foreground">{String(hour).padStart(2, "0")}</div>
            ))}
            <div className="self-end pb-1 text-right text-[10px] font-bold text-muted-foreground">删除</div>
            {analysis.daily.flatMap((day) => {
              const cells = Array.from({ length: 24 }, (_, hour) => timeline.get(`${day.date}-${hour}`));
              return [
                <div key={`${day.date}-date`} className="flex h-7 items-center text-[10px] font-black text-muted-foreground">
                  {formatDailyDate(day.date)}
                </div>,
                ...cells.map((cell, hour) => (
                  <DeathHeatCell
                    key={`${day.date}-${hour}`}
                    date={day.date}
                    hour={hour}
                    cell={cell}
                    maxRemovals={maxRemovals}
                    current={day.date === currentDate && hour === timing.evaluatedHour}
                    selected={activeSelectedKey === heatCellKey(day.date, hour)}
                    onSelect={() => setSelectedKey(heatCellKey(day.date, hour))}
                    onHover={(active) => setHoveredKey(active ? heatCellKey(day.date, hour) : null)}
                  />
                )),
                <div key={`${day.date}-total`} className="flex h-7 items-center justify-end text-[10px] font-black text-orange-700">
                  {formatMoney(day.inferredAccountRemovals, 0)}
                </div>,
              ];
            })}
            </div>
          </div>
        </TooltipPrimitive.Provider>

        <DeathHeatDetail
          target={detailTarget}
          source={hovered ? "悬停详情" : selectedKey ? "已选时段" : "当前时段"}
          lastSnapshotAt={analysis.lastSnapshotAt}
        />

        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 border-t border-border pt-3 text-xs font-semibold text-muted-foreground">
          <span>7 天新增错误 <strong className="text-rose-600">{formatMoney(totals.newErrors, 0)}</strong></span>
          <span>账号净删除下界 <strong className="text-orange-600">{formatMoney(totals.removals, 0)}</strong></span>
          <span>24h 自动删除候选 <strong className="text-violet-600">{formatMoney(totals.automatic, 0)}</strong></span>
          <span>人工/未匹配删除 <strong className="text-blue-600">{formatMoney(totals.manual, 0)}</strong></span>
        </div>
      </div>

      <div className={cn("rounded-md border p-3", timingStyle)}>
        <div className="flex items-center gap-2 text-sm font-black">
          <Clock3 className="h-4 w-4" />
          现在补号风险：{riskLabel(timing.level)}
        </div>
        <div className="mt-3 text-base font-black leading-6">{decision.title}</div>
        <div className="mt-1 text-xs font-semibold leading-5">{decision.detail}</div>
        <div className="mt-3 space-y-1.5 text-xs font-semibold leading-5">
          {timing.reasons.map((reason, index) => <p key={index}>{reason}</p>)}
        </div>
        <div className="mt-4 border-t border-current/15 pt-3 text-xs font-semibold leading-5">
          <div>当前时段：{timing.hourLabel}</div>
          <div>实时更新至：{formatDateTime(currentHourLastSnapshotAt)}</div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <div className="rounded border border-current/15 bg-white/45 px-2 py-1.5">
              <div className="text-[10px]">本小时已删除</div>
              <div className="text-lg font-black text-orange-700">{formatMoney(currentHourRemovals, 0)}</div>
            </div>
            <div className="rounded border border-current/15 bg-white/45 px-2 py-1.5">
              <div className="text-[10px]">本小时新增错误</div>
              <div className="text-lg font-black text-rose-700">{formatMoney(currentHourNewErrors, 0)}</div>
            </div>
          </div>
          <div className="mt-2">其中疑似错误后死亡：{formatMoney(currentHourLikelyErrorDeaths, 0)}</div>
          <div>有效采样：{currentHourSampleCount} 条 · 覆盖 {formatMoney(currentHourObservedMinutes, 0)} 分钟</div>
          <div className="mt-2 border-t border-current/15 pt-2 font-black">近 7 天相同时段累计</div>
          <div>新增错误：{formatMoney(timing.newErrors, 0)}</div>
          <div>账号删除：{formatMoney(timing.inferredAccountRemovals, 0)}</div>
          <div>时机置信度：{confidenceLabel(timing.confidence)}</div>
          <div>历史高发：{formatHourList(timing.peakHours)}</div>
          <div>建议时段：{formatHourList(timing.suggestedHours)}</div>
        </div>
      </div>
    </div>
  );
}

function DeathStat({ label, value, alert = false }: { label: string; value: string; alert?: boolean }) {
  return (
    <div>
      <div>{label}</div>
      <div className={cn("mt-1 text-base font-black", alert ? "text-rose-600" : "text-foreground")}>{value}</div>
    </div>
  );
}

function heatCellKey(date: string, hour: number) {
  return `${date}-${hour}`;
}

function deathHourLabel(hour: number) {
  return `${String(hour).padStart(2, "0")}:00-${String((hour + 1) % 24).padStart(2, "0")}:00`;
}

function deathCellAriaLabel(date: string, hour: number, cell: DeathTimelineHour | undefined, current: boolean) {
  const prefix = `${date} ${cell?.label ?? deathHourLabel(hour)}`;
  if (!cell?.observed) return `${prefix}，无有效采样，点击查看说明`;
  return `${prefix}，账号删除 ${formatMoney(cell.inferredAccountRemovals, 0)}，新增错误 ${formatMoney(cell.newErrors, 0)}，时末错误 ${formatMoney(cell.endingErrors, 0)}，采样 ${cell.sampleCount} 条，覆盖 ${formatMoney(cell.coverage * 100, 0)}%${current ? "，当前小时进行中" : ""}，点击固定详情`;
}

function DeathHeatCell({
  date,
  hour,
  cell,
  maxRemovals,
  current,
  selected,
  onSelect,
  onHover,
}: {
  date: string;
  hour: number;
  cell?: DeathTimelineHour;
  maxRemovals: number;
  current: boolean;
  selected: boolean;
  onSelect: () => void;
  onHover: (active: boolean) => void;
}) {
  const className = !cell?.observed
    ? "bg-slate-100 text-slate-400"
    : cell.inferredAccountRemovals <= 0
      ? "bg-emerald-50 text-emerald-700"
      : cell.inferredAccountRemovals / maxRemovals >= 0.75
        ? "bg-orange-600 text-white"
        : cell.inferredAccountRemovals / maxRemovals >= 0.45
          ? "bg-orange-300 text-orange-950"
          : "bg-orange-100 text-orange-800";
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>
        <button
          type="button"
          aria-label={deathCellAriaLabel(date, hour, cell, current)}
          aria-pressed={selected}
          onClick={onSelect}
          onMouseEnter={() => onHover(true)}
          onMouseLeave={() => onHover(false)}
          onFocus={() => onHover(true)}
          onBlur={() => onHover(false)}
          className={cn(
            "relative flex h-7 min-w-0 items-center justify-center text-[10px] font-black outline-none transition-[box-shadow,transform] hover:z-10 hover:scale-110 focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1",
            className,
            current && "ring-2 ring-blue-600 ring-offset-1",
            selected && "z-10 ring-2 ring-slate-950 ring-offset-1",
          )}
        >
          {!cell?.observed ? "--" : cell.inferredAccountRemovals || ""}
          {cell?.observed && cell.newErrors > 0 && <span aria-label={`新增错误 ${formatMoney(cell.newErrors, 0)}`} className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-rose-600 ring-1 ring-white" />}
        </button>
      </TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side="top"
          sideOffset={7}
          collisionPadding={12}
          className="z-50 max-w-64 rounded-md border border-slate-700 bg-slate-950 px-2.5 py-2 text-[11px] font-semibold leading-4 text-slate-100 shadow-xl"
        >
          <div className="font-black text-white">{date} {cell?.label ?? deathHourLabel(hour)}</div>
          {!cell?.observed ? (
            <div className="mt-1 text-slate-300">无有效采样</div>
          ) : (
            <>
              <div className="mt-1">账号删除 <strong className="text-orange-300">{formatMoney(cell.inferredAccountRemovals, 0)}</strong> · 新增错误 <strong className="text-rose-300">{formatMoney(cell.newErrors, 0)}</strong></div>
              <div className="text-slate-300">{cell.sampleCount} 条快照 · 覆盖 {formatMoney(cell.observedMinutes, 0)} 分钟（{formatMoney(cell.coverage * 100, 0)}%）</div>
              {current && <div className="mt-1 font-bold text-blue-300">当前小时进行中，数字会继续累计</div>}
            </>
          )}
          <TooltipPrimitive.Arrow className="fill-slate-950" />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

function DeathHeatDetail({
  target,
  source,
  lastSnapshotAt,
}: {
  target?: { date: string; hour: number; cell?: DeathTimelineHour; current: boolean };
  source: string;
  lastSnapshotAt: string | null;
}) {
  if (!target) {
    return (
      <div className="mt-3 rounded-md border border-dashed border-border bg-muted/35 px-3 py-2 text-xs font-semibold text-muted-foreground">
        悬停或点击任意时段格查看新增错误、删除推断、采样和覆盖详情。
      </div>
    );
  }
  const { cell, date, hour, current } = target;
  const label = cell?.label ?? deathHourLabel(hour);
  return (
    <div className={cn("mt-3 rounded-md border px-3 py-2.5", cell?.observed ? "border-slate-200 bg-slate-50" : "border-slate-200 bg-slate-50/60")}>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="text-xs font-black text-foreground">{source}：{date} {label}</div>
        {current && <span className="rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10px] font-black text-blue-700">当前小时进行中</span>}
      </div>
      {current && <div className="mt-1 text-[10px] font-semibold text-muted-foreground">本地最后快照更新：{formatDateTime(lastSnapshotAt)}</div>}
      {!cell?.observed ? (
        <p className="mt-1.5 text-xs font-semibold leading-5 text-muted-foreground">该时段没有有效快照或覆盖区间，不能据此判断是否存在杀号。</p>
      ) : (
        <>
          <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-3 lg:grid-cols-4">
            <DeathDetailMetric label="账号删除" value={formatMoney(cell.inferredAccountRemovals, 0)} tone="text-orange-700" />
            <DeathDetailMetric label="新增错误" value={formatMoney(cell.newErrors, 0)} tone="text-rose-700" />
            <DeathDetailMetric label="时末错误" value={formatMoney(cell.endingErrors, 0)} tone="text-rose-700" />
            <DeathDetailMetric label="采样数" value={`${cell.sampleCount} 条`} />
            <DeathDetailMetric label="覆盖时长" value={`${formatMoney(cell.observedMinutes, 0)} 分钟`} />
            <DeathDetailMetric label="覆盖率" value={`${formatMoney(cell.coverage * 100, 0)}%`} />
            <DeathDetailMetric label="错误后死亡推断" value={formatMoney(cell.likelyErrorDeaths, 0)} tone="text-orange-700" />
            <DeathDetailMetric label="24h 自动删除候选" value={formatMoney(cell.autoDeletionCandidates, 0)} tone="text-violet-700" />
            <DeathDetailMetric label="人工/未匹配删除" value={formatMoney(cell.manualOrUnmatchedCandidates, 0)} tone="text-blue-700" />
            <DeathDetailMetric label="其他删除候选" value={formatMoney(cell.otherRemovalCandidates, 0)} />
            <DeathDetailMetric label="账号增加" value={formatMoney(cell.accountAdditions, 0)} tone="text-emerald-700" />
          </div>
          {current && <p className="mt-2 text-xs font-semibold leading-5 text-blue-700">本小时尚未结束，以上是已收到快照的累计值；每次轮询后会继续更新，不会等到整点。</p>}
        </>
      )}
    </div>
  );
}

function DeathDetailMetric({ label, value, tone = "text-foreground" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-bold text-muted-foreground">{label}</div>
      <div className={cn("mt-0.5 truncate text-sm font-black", tone)}>{value}</div>
    </div>
  );
}

function ForecastSummary({ title, forecast }: { title: string; forecast: PoolForecast }) {
  return (
    <div className="rounded-md border border-border bg-background px-3 py-2.5 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-black text-foreground">{title}</span>
        <span className="inline-flex items-center gap-1.5 text-xs font-bold text-muted-foreground">
          <span>{formatDailyDate(forecast.date)}</span>
          <span className={cn("rounded border px-1.5 py-0.5 text-[10px] font-black leading-none", dayTypeBadgeClass(forecast.dayType))}>
            {forecast.dayType === "workday" ? "工作日" : "非工作日"}
          </span>
        </span>
      </div>
      <div className="mt-2 grid grid-cols-3 divide-x divide-border">
        <ForecastMetric label="5h" value={formatMoney(forecast.estimated5h, 1)} className="text-emerald-700" />
        <ForecastMetric label="7d" value={formatMoney(forecast.estimated7d, 1)} className="text-violet-700" />
        <ForecastMetric label="掉号" value={formatMoney(forecast.accountDecrease, 0)} className="text-orange-700" />
      </div>
      <div className="mt-2 flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
        <span>预测置信度</span>
        <span className={cn("rounded border px-1.5 py-0.5 text-[10px] font-black leading-none", confidenceBadgeClass(forecast.confidence))}>
          {confidenceLabel(forecast.confidence)}
        </span>
        <span>· {forecast.sampleCount} 个样本日</span>
      </div>
    </div>
  );
}

function ForecastMetric({ label, value, className }: { label: string; value: string; className: string }) {
  return (
    <div className="min-w-0 px-2 first:pl-0 last:pr-0">
      <div className={cn("text-[11px] font-black", className)}>{label}</div>
      <div className={cn("mt-0.5 truncate text-base font-black leading-none", className)}>{value}</div>
    </div>
  );
}

function DailyUsageTable({
  rows,
  deathDays,
  timezone,
  calendarFallback,
}: {
  rows: DailyPoolUsage[];
  deathDays?: DeathAnalysisDay[];
  timezone: string;
  calendarFallback: boolean;
}) {
  const errorsByDate = new Map((deathDays ?? []).map((row) => [row.date, row.newErrors]));
  const tableRows = rows.map((row, index) => {
    const previous = index ? rows[index - 1] : undefined;
    const currentErrors = errorsByDate.get(row.date);
    const previousErrors = previous ? errorsByDate.get(previous.date) : undefined;
    return [
      formatDailyDate(row.date),
      <span className={cn("inline-flex rounded border px-1.5 py-0.5 text-[10px] font-black leading-none", dayTypeBadgeClass(row.dayType))}>
        {row.dayType === "workday" ? "工作日" : "非工作日"}
      </span>,
      <UsageTrendCell value={row.estimated5h} previous={previous?.estimated5h} digits={1} tone="emerald" />,
      <UsageTrendCell value={row.estimated7d} previous={previous?.estimated7d} digits={1} tone="violet" />,
      <UsageTrendCell value={currentErrors} previous={previousErrors} tone="rose" />,
      <UsageTrendCell value={row.accountDecrease} previous={previous?.accountDecrease} tone="orange" />,
      <UsageTrendCell value={row.accountIncrease} previous={previous?.accountIncrease} tone="blue" />,
      <UsageTrendCell value={row.netAccountChange} previous={previous?.netAccountChange} signed tone={row.netAccountChange < 0 ? "rose" : "emerald"} />,
      `${formatMoney(row.coverage * 100, 0)}% (${row.sampleCount})`,
      row.isComplete ? <span className="text-emerald-600">完整</span> : <span className="text-amber-600">未完整</span>,
    ];
  });
  return (
    <DataTable
      title="每日对比"
      columns={["日期", "类型", "5h 消耗", "7d 消耗", "新增错误", "账号减少", "账号增加", "净变动", "覆盖率", "状态"]}
      rows={tableRows}
      subtitle={`${timezone}${calendarFallback ? " · 节假日按周末规则估算" : ""}`}
    />
  );
}

function UsageTrendCell({
  value,
  previous,
  digits = 0,
  signed = false,
  tone = "default",
}: {
  value?: number | null;
  previous?: number | null;
  digits?: number;
  signed?: boolean;
  tone?: "default" | "emerald" | "violet" | "rose" | "orange" | "blue";
}) {
  if (value === null || value === undefined) return <span className="font-black text-muted-foreground">--</span>;
  const delta = previous === null || previous === undefined ? null : value - previous;
  const toneClass = {
    default: "text-foreground",
    emerald: "text-emerald-700",
    violet: "text-violet-700",
    rose: "text-rose-700",
    orange: "text-orange-700",
    blue: "text-blue-700",
  }[tone];
  return (
    <span className="inline-flex items-center gap-1 font-black">
      <span className={toneClass}>{signed ? `${value >= 0 ? "+" : "-"}${formatMoney(Math.abs(value), digits)}` : formatMoney(value, digits)}</span>
      {delta !== null && delta !== 0 && <span className={cn("text-xs", delta > 0 ? "text-rose-600" : "text-emerald-600")}>{delta > 0 ? `↑${formatMoney(delta, digits)}` : `↓${formatMoney(Math.abs(delta), digits)}`}</span>}
    </span>
  );
}

function buildOperationsChartRows(daily: DailyPoolUsage[], forecasts: PoolAnalyticsResponse["forecasts"]) {
  const rows: Array<{
    date: string;
    estimated5h?: number | null;
    estimated7d?: number | null;
    accountDecrease?: number | null;
    forecast5h?: number | null;
    forecast7d?: number | null;
    forecastDecrease?: number | null;
  }> = daily.map((item) => ({
    date: formatDailyDate(item.date),
    estimated5h: item.estimated5h,
    estimated7d: item.estimated7d,
    accountDecrease: item.accountDecrease,
  }));
  const last = rows.at(-1);
  if (last) {
    last.forecast5h = last.estimated5h;
    last.forecast7d = last.estimated7d;
    last.forecastDecrease = last.accountDecrease;
  }
  const uniqueForecasts = new Map<string, PoolForecast>();
  const forecastRows = forecasts.nextThreeDays?.length
    ? forecasts.nextThreeDays
    : [forecasts.tomorrow, forecasts.nextWorkday, forecasts.nextNonWorkday];
  for (const forecast of forecastRows) {
    uniqueForecasts.set(forecast.date, forecast);
  }
  for (const forecast of uniqueForecasts.values()) {
    rows.push({
      date: formatDailyDate(forecast.date),
      forecast5h: forecast.estimated5h,
      forecast7d: forecast.estimated7d,
      forecastDecrease: forecast.accountDecrease,
    });
  }
  return rows;
}

function formatDailyDate(value: string) {
  return value.length >= 10 ? value.slice(5, 10) : value;
}

function riskLabel(level: PoolAnalyticsResponse["risk"]["level"]) {
  return { low: "低", medium: "中", high: "高", insufficient: "数据不足" }[level];
}

function replenishmentDecision(
  amount: number | null,
  action: NonNullable<PoolAnalyticsResponse["replenishmentTimingRisk"]>["action"],
  capacityRisk: PoolAnalyticsResponse["risk"]["level"],
) {
  if (amount === null) return { title: "建议补号：数据不足", detail: "先继续积累快照，不做大批量补号。" };
  const title = `建议补 ${formatMoney(amount, 0)} 个号`;
  if (amount <= 0) return { title, detail: "当前容量无缺口，现在无需补号。" };
  if (action === "avoid") {
    return {
      title,
      detail: capacityRisk === "high"
        ? "当前缺口也是高风险；先小批应急，在建议时段补足。"
        : "当前处于高杀号时段，不建议现在批量补。",
    };
  }
  if (action === "caution") return { title, detail: "建议分批补入，每批后等待两次刷新再继续。" };
  if (action === "suitable") return { title, detail: "当前时段可按建议数量补入。" };
  return { title, detail: "补号时机样本不足，建议先小批补入并观察错误变化。" };
}

function formatSignedCount(value: number) {
  if (value === 0) return "0";
  return `${value > 0 ? "+" : "-"}${formatMoney(Math.abs(value), 0)}`;
}

function formatHourList(hours: number[]) {
  if (!hours.length) return "--";
  const ordered = [...new Set(hours)].sort((left, right) => left - right);
  const ranges: Array<[number, number]> = [];
  for (const hour of ordered) {
    const current = ranges.at(-1);
    if (current && hour === current[1] + 1) current[1] = hour;
    else ranges.push([hour, hour]);
  }
  return ranges
    .map(([start, end]) => `${String(start).padStart(2, "0")}:00-${String((end + 1) % 24).padStart(2, "0")}:00`)
    .join("、");
}

function confidenceLabel(level: PoolForecast["confidence"]) {
  return { high: "高", medium: "中", low: "低", insufficient: "不足" }[level];
}

function confidenceBadgeClass(level: PoolForecast["confidence"]) {
  return {
    high: "border-emerald-200 bg-emerald-50 text-emerald-700",
    medium: "border-blue-200 bg-blue-50 text-blue-700",
    low: "border-amber-200 bg-amber-50 text-amber-700",
    insufficient: "border-slate-200 bg-slate-50 text-slate-600",
  }[level];
}

function dayTypeBadgeClass(dayType: PoolForecast["dayType"]) {
  return dayType === "workday"
    ? "border-blue-200 bg-blue-50 text-blue-700"
    : "border-amber-200 bg-amber-50 text-amber-700";
}

function PoolMetricChart({
  pool,
  selectedGroups,
  metric,
}: {
  pool: PoolAnalyzerState;
  selectedGroups: string[];
  metric: { key: PoolMetricKey; label: string; color: string };
}) {
  const poolRows = buildPoolChartRows(pool.history, selectedGroups, metric.key);
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="mb-2 text-[13px] font-bold text-foreground">{metric.label}</div>
      <div className="h-[240px]">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={poolRows}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="time" minTickGap={38} tick={smallChartTick} axisLine={chartAxisLine} tickLine={chartTickLine} />
            <YAxis width={42} tick={smallChartTick} axisLine={chartAxisLine} tickLine={chartTickLine} />
            <Tooltip contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle} />
            <Legend wrapperStyle={legendStyle} iconSize={7} />
            {selectedGroups.map((group, index) => (
              <Line
                key={group}
                type="monotone"
                dataKey={group}
                name={group}
                stroke={index === 0 ? metric.color : ["#f97316", "#14b8a6", "#64748b"][index % 3]}
                strokeWidth={2}
                dot={false}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function PoolsView({
  pool,
  selectedGroups,
  availableGroups,
  onPoolChange,
  onRefresh,
  onOpenDialog,
}: {
  pool: PoolAnalyzerState;
  selectedGroups: string[];
  availableGroups: string[];
  onPoolChange: (pool: PoolAnalyzerState) => void;
  onRefresh: () => void;
  onOpenDialog: (dialog: "poolCredentials" | "smtp") => void;
}) {
  const grouped = useMemo(() => groupPoolRows(pool.history), [pool.history]);
  const toggleGroup = (group: string) => {
    const next = selectedGroups.includes(group) ? selectedGroups.filter((item) => item !== group) : [...selectedGroups, group];
    if (!next.length) return;
    onPoolChange({ ...pool, selectedGroups: next });
  };
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex items-center justify-between gap-3 p-3">
          <div className="flex flex-wrap items-center gap-3">
            {availableGroups.map((group) => (
              <label key={group} className="flex h-9 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm font-bold">
                <Checkbox checked={selectedGroups.includes(group)} onCheckedChange={() => toggleGroup(group)} />
                {group}
              </label>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={onRefresh}>
              <RefreshCw className="h-4 w-4" />
              刷新
            </Button>
            <Button variant="outline" onClick={() => onOpenDialog("poolCredentials")}>
              <Settings className="h-4 w-4" />
              接口账号
            </Button>
            <Button variant="outline" onClick={() => onOpenDialog("smtp")}>
              <Bell className="h-4 w-4" />
              预警设置
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 gap-3">
        {selectedGroups.map((group) => {
          const rows = grouped[group] ?? [];
          const latest = rows.at(-1);
          return <PoolSummaryCard key={group} group={group} latest={latest} />;
        })}
      </div>

      {selectedGroups.map((group) => (
        <PagedPoolTable key={group} group={group} stateRows={grouped[group] ?? []} />
      ))}
    </div>
  );
}

function PixelManagerView({ onToast }: { onToast: (message: string) => void }) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [targets, setTargets] = useState<PixelTarget[]>([]);
  const [activeTargetId, setActiveTargetId] = useState("");
  const [targetsLoading, setTargetsLoading] = useState(true);
  const [targetsError, setTargetsError] = useState("");
  const [accounts, setAccounts] = useState<PixelAccountPage | null>(null);
  const [accountsLoading, setAccountsLoading] = useState(false);
  const [accountsError, setAccountsError] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [accountSearchInput, setAccountSearchInput] = useState("");
  const [accountSearch, setAccountSearch] = useState("");
  const [accountStatus, setAccountStatus] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [selectedTargetIds, setSelectedTargetIds] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);
  const [results, setResults] = useState<PixelImportTargetResult[]>([]);
  const [importJob, setImportJob] = useState<PixelImportJob | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportDeleteAndReimport, setExportDeleteAndReimport] = useState(false);
  const [exportSelectedTargetIds, setExportSelectedTargetIds] = useState<Set<string>>(new Set());
  const [exportJob, setExportJob] = useState<PixelExportJob | null>(null);
  const [retryingTargetId, setRetryingTargetId] = useState("");
  const [exporting, setExporting] = useState(false);
  const [importRecords, setImportRecords] = useState<PixelImportRecord[]>([]);
  const [importRecordsOpen, setImportRecordsOpen] = useState(false);
  const [importRecordsLoading, setImportRecordsLoading] = useState(false);
  const [deletingRecordId, setDeletingRecordId] = useState("");
  const [deleteRecord, setDeleteRecord] = useState<PixelImportRecord | null>(null);
  const exportBackupDownloadedRef = useRef<Set<string>>(new Set());
  const accountsRequestSequence = useRef(0);
  const [selectedAccountIds, setSelectedAccountIds] = useState<Set<number>>(new Set());
  const [bulkAction, setBulkAction] = useState<"test" | "update" | "delete" | "">("");
  const [bulkEditOpen, setBulkEditOpen] = useState(false);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [bulkDeleteAccountIds, setBulkDeleteAccountIds] = useState<number[]>([]);
  const [bulkMakePublic, setBulkMakePublic] = useState(false);
  const [bulkConcurrency, setBulkConcurrency] = useState("");
  const [targetCountsRefreshing, setTargetCountsRefreshing] = useState(false);
  const loadAccountsRef = useRef<() => Promise<void>>(async () => undefined);
  const refreshAllTargetCountsRef = useRef<(targetList?: PixelTarget[], options?: { silent?: boolean }) => Promise<void>>(async () => undefined);

  const beginAccountsTransition = useCallback(() => {
    accountsRequestSequence.current += 1;
    setAccounts(null);
    setAccountsError("");
    setAccountsLoading(true);
    setSelectedAccountIds(new Set());
  }, []);

  useEffect(() => {
    const normalizedSearch = accountSearchInput.trim();
    if (normalizedSearch === accountSearch) return;
    const timer = window.setTimeout(() => {
      beginAccountsTransition();
      setPage(1);
      setAccountSearch(normalizedSearch);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [accountSearch, accountSearchInput, beginAccountsTransition]);

  const loadTargets = useCallback(async () => {
    setTargetsLoading(true);
    setTargetsError("");
    try {
      const response = await api.pixelTargets();
      setTargets(response.targets);
      setActiveTargetId((current) => (response.targets.some((target) => target.id === current) ? current : response.targets[0]?.id ?? ""));
      return response.targets;
    } catch (error) {
      setTargetsError(error instanceof Error ? error.message : "平台账号读取失败");
      return [];
    } finally {
      setTargetsLoading(false);
    }
  }, []);

  const refreshAllTargetCounts = useCallback(async (targetList = targets, options: { silent?: boolean } = {}) => {
    if (targetCountsRefreshing || !targetList.length) return;
    setTargetCountsRefreshing(true);
    try {
      const results = await Promise.all(targetList.map(async (target) => {
        try {
          return { targetId: target.id, response: await api.pixelAccounts(target.id, 1, 1), error: "" };
        } catch (error) {
          return { targetId: target.id, response: null, error: error instanceof Error ? error.message : "账号数量刷新失败" };
        }
      }));
      const resultByTarget = new Map(results.map((result) => [result.targetId, result]));
      const checkedAt = new Date().toISOString();
      setTargets((current) => current.map((target) => {
        const result = resultByTarget.get(target.id);
        if (!result) return target;
        if (result.response) {
          return { ...target, connected: true, accountCount: result.response.total, lastCheckedAt: checkedAt, error: null };
        }
        return { ...target, connected: false, lastCheckedAt: checkedAt, error: result.error };
      }));
      const activeResult = resultByTarget.get(activeTargetId)?.response;
      if (activeResult && !accountSearch && !accountStatus && accounts) {
        setAccounts((current) => current ? {
          ...current,
          total: activeResult.total,
          pages: Math.max(Math.ceil(activeResult.total / current.pageSize), 1),
        } : current);
      }
      const succeeded = results.filter((result) => result.response).length;
      const failed = results.length - succeeded;
      if (!options.silent) onToast(`七个平台数量刷新完成：成功 ${succeeded} 个${failed ? `，失败 ${failed} 个` : ""}`);
    } finally {
      setTargetCountsRefreshing(false);
    }
  }, [accountSearch, accountStatus, accounts, activeTargetId, onToast, targetCountsRefreshing, targets]);

  const loadAccounts = useCallback(async () => {
    if (!activeTargetId) {
      accountsRequestSequence.current += 1;
      setAccounts(null);
      setAccountsLoading(false);
      setSelectedAccountIds(new Set());
      return;
    }
    const requestSequence = ++accountsRequestSequence.current;
    setAccountsLoading(true);
    setAccountsError("");
    setAccounts(null);
    setSelectedAccountIds(new Set());
    try {
      const response = await api.pixelAccounts(activeTargetId, page, pageSize, accountStatus, accountSearch);
      if (requestSequence !== accountsRequestSequence.current) return;
      setAccounts(response);
      setTargets((current) =>
        current.map((target) =>
          target.id === activeTargetId
            ? {
                ...target,
                connected: true,
                accountCount: accountStatus || accountSearch ? target.accountCount : response.total,
                lastCheckedAt: new Date().toISOString(),
                error: null,
              }
            : target,
        ),
      );
    } catch (error) {
      if (requestSequence !== accountsRequestSequence.current) return;
      const message = error instanceof Error ? error.message : "账号列表读取失败";
      setAccountsError(message);
      setAccounts(null);
      setTargets((current) =>
        current.map((target) => (target.id === activeTargetId ? { ...target, connected: false, error: message } : target)),
      );
    } finally {
      if (requestSequence === accountsRequestSequence.current) setAccountsLoading(false);
    }
  }, [accountSearch, accountStatus, activeTargetId, page, pageSize]);

  useEffect(() => {
    loadAccountsRef.current = loadAccounts;
  }, [loadAccounts]);

  useEffect(() => {
    refreshAllTargetCountsRef.current = refreshAllTargetCounts;
  }, [refreshAllTargetCounts]);

  useEffect(() => {
    void loadTargets();
  }, [loadTargets]);

  useEffect(() => {
    const jobId = window.localStorage.getItem("pixelImportJobId");
    if (!jobId) return;
    void api.pixelImportJob(jobId).then(({ job }) => {
      setImportJob(job);
      setResults(job.results);
      setImporting(job.status === "queued" || job.status === "running");
      if (job.status === "completed" || job.status === "failed") {
        window.localStorage.removeItem("pixelImportJobId");
      }
      if (job.status === "completed") {
        void loadTargets().then((targetList) => refreshAllTargetCountsRef.current(targetList, { silent: true }));
        void loadAccountsRef.current();
      }
    }).catch(() => window.localStorage.removeItem("pixelImportJobId"));
  }, [loadTargets]);

  useEffect(() => {
    const jobId = window.localStorage.getItem("pixelExportJobId");
    if (!jobId) return;
    void api.pixelExportJob(jobId).then(({ job }) => {
      setExportJob(job);
      setExporting(job.status === "queued" || job.status === "running");
      if (job.status === "completed" || job.status === "failed") {
        window.localStorage.removeItem("pixelExportJobId");
      }
      if (job.status === "completed") {
        void loadTargets().then((targetList) => refreshAllTargetCountsRef.current(targetList, { silent: true }));
        void loadAccountsRef.current();
      }
    }).catch(() => window.localStorage.removeItem("pixelExportJobId"));
  }, [loadTargets]);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    if (!importJob || importJob.status === "completed" || importJob.status === "failed") return;
    let cancelled = false;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const response = await api.pixelImportJob(importJob.jobId);
        if (cancelled) return;
        setImportJob(response.job);
        setResults(response.job.results);
        if (response.job.status === "completed") {
          setImporting(false);
          window.localStorage.removeItem("pixelImportJobId");
          const succeeded = response.job.results.filter((item) => item.status === "success").length;
          const partial = response.job.results.filter((item) => item.status === "partial").length;
          onToast(`导入完成：成功 ${succeeded} 个平台${partial ? `，部分成功 ${partial} 个` : ""}`);
          const targetList = await loadTargets();
          await refreshAllTargetCountsRef.current(targetList, { silent: true });
          await loadAccountsRef.current();
          return;
        }
        if (response.job.status === "failed") {
          setImporting(false);
          window.localStorage.removeItem("pixelImportJobId");
          onToast(response.job.error || "批量导入失败");
          return;
        }
      } catch (error) {
        if (!cancelled) onToast(error instanceof Error ? error.message : "导入进度读取失败");
      }
      if (!cancelled) timer = window.setTimeout(() => void poll(), 2_000);
    };
    timer = window.setTimeout(() => void poll(), 1_000);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [importJob?.jobId]);

  useEffect(() => {
    if (!exportJob || exportJob.status === "completed" || exportJob.status === "failed") return;
    let cancelled = false;
    let timer: number | null = null;
    const poll = async () => {
      try {
        const response = await api.pixelExportJob(exportJob.jobId);
        if (cancelled) return;
        setExportJob(response.job);
        if (response.job.status === "completed") {
          setExporting(false);
          window.localStorage.removeItem("pixelExportJobId");
          if (!exportBackupDownloadedRef.current.has(response.job.jobId)) {
            exportBackupDownloadedRef.current.add(response.job.jobId);
            try {
              downloadPixelFile(await api.pixelExportJobDownload(response.job.jobId));
            } catch (error) {
              onToast(error instanceof Error ? error.message : "备份文件自动下载失败");
            }
          }
          const targetList = await loadTargets();
          await refreshAllTargetCountsRef.current(targetList, { silent: true });
          await loadAccountsRef.current();
          const exported = response.job.export?.deduplicatedCount ?? 0;
          onToast(`汇总整理完成：备份 ${exported} 个账号，已重新导入 ${response.job.results.length} 个平台`);
          return;
        }
        if (response.job.status === "failed") {
          setExporting(false);
          window.localStorage.removeItem("pixelExportJobId");
          onToast(response.job.error || "汇总整理任务失败");
          return;
        }
      } catch (error) {
        if (!cancelled) onToast(error instanceof Error ? error.message : "汇总整理进度读取失败");
      }
      if (!cancelled) timer = window.setTimeout(() => void poll(), 2_000);
    };
    timer = window.setTimeout(() => void poll(), 1_000);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [exportJob?.jobId]);

  const openImport = () => {
    if (!selectedFile || !targets.length) return;
    setSelectedTargetIds(new Set(targets.map((target) => target.id)));
    setImportOpen(true);
  };

  const downloadPixelFile = (download: { blob: Blob; fileName: string }) => {
    const url = URL.createObjectURL(download.blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = download.fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
  };

  const openExport = () => {
    if (!targets.length) return;
    setExportDeleteAndReimport(false);
    setExportSelectedTargetIds(new Set(targets.map((target) => target.id)));
    setExportOpen(true);
  };

  const openImportRecords = async () => {
    setImportRecordsOpen(true);
    setImportRecordsLoading(true);
    try {
      const response = await api.pixelImportRecords();
      setImportRecords(response.records);
    } catch (error) {
      onToast(error instanceof Error ? error.message : "导入记录读取失败");
    } finally {
      setImportRecordsLoading(false);
    }
  };

  const runDeleteImportRecord = async () => {
    if (!deleteRecord) return;
    setDeletingRecordId(deleteRecord.recordId);
    try {
      const response = await api.pixelDeleteImportRecord(deleteRecord.recordId);
      setImportRecords((current) => current.map((item) => item.recordId === response.record.recordId ? response.record : item));
      setDeleteRecord(null);
      const targetList = await loadTargets();
      await refreshAllTargetCountsRef.current(targetList, { silent: true });
      await loadAccountsRef.current();
      onToast(response.record.deleteStatus === "deleted" ? "导入记录账号已全部删除" : "导入记录删除完成，但存在未处理账号");
    } catch (error) {
      onToast(error instanceof Error ? error.message : "导入记录删除失败");
    } finally {
      setDeletingRecordId("");
    }
  };

  const exportAllAccounts = async () => {
    setExporting(true);
    try {
      const download = await api.pixelExport();
      downloadPixelFile(download);
      const total = download.sourceCount || download.deduplicatedCount + download.duplicateCount;
      const batchText = download.batchCount ? `，已按 100 个一组生成 ${download.batchCount} 组` : "";
      onToast(`汇总导出 ${download.deduplicatedCount || total} 个账号${download.duplicateCount ? `，已去重 ${download.duplicateCount} 个` : ""}${batchText}`);
    } catch (error) {
      onToast(error instanceof Error ? error.message : "账号汇总导出失败");
    } finally {
      setExporting(false);
    }
  };

  const runExportAction = async () => {
    if (!exportDeleteAndReimport) {
      setExportOpen(false);
      await exportAllAccounts();
      return;
    }
    if (!exportSelectedTargetIds.size) {
      onToast("请选择重新导入的平台账号");
      return;
    }
    setExporting(true);
    try {
      const response = await api.pixelExportJobCreate([...exportSelectedTargetIds]);
      setExportJob(response.job);
      window.localStorage.setItem("pixelExportJobId", response.job.jobId);
      setExportOpen(false);
      onToast("汇总整理任务已开始：先导出备份，再删除，再重新导入");
    } catch (error) {
      onToast(error instanceof Error ? error.message : "汇总整理任务启动失败");
      setExporting(false);
    }
  };

  const toggleExportTarget = (targetId: string) => {
    setExportSelectedTargetIds((current) => {
      const next = new Set(current);
      if (next.has(targetId)) next.delete(targetId);
      else next.add(targetId);
      return next;
    });
  };

  const toggleImportTarget = (targetId: string) => {
    setSelectedTargetIds((current) => {
      const next = new Set(current);
      if (next.has(targetId)) next.delete(targetId);
      else next.add(targetId);
      return next;
    });
  };

  const runImport = async () => {
    if (!selectedFile || !selectedTargetIds.size) return;
    setImporting(true);
    try {
      const response = await api.pixelImport(selectedFile, [...selectedTargetIds]);
      setImportJob(response.job);
      window.localStorage.setItem("pixelImportJobId", response.job.jobId);
      setResults(response.job.results);
      setImportOpen(false);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      onToast(`导入任务已开始，将依次处理 ${response.job.totalTargets} 个平台账号`);
    } catch (error) {
      onToast(error instanceof Error ? error.message : "批量导入失败");
      setImporting(false);
    }
  };

  const retryShare = async (result: PixelImportTargetResult) => {
    if (!result.failedShareIds.length) return;
    setRetryingTargetId(result.targetId);
    try {
      const response = await api.pixelShare(result.targetId, result.failedShareIds);
      setResults((current) =>
        current.map((item) => {
          if (item.targetId !== result.targetId) return item;
          const shared = item.shared + response.success;
          const shareFailed = response.failed;
          const status = shareFailed === 0 && item.failed === 0 ? "success" : "partial";
          return {
            ...item,
            shared,
            shareFailed,
            failedShareIds: response.failedIds,
            status,
            message: shareFailed ? `仍有 ${shareFailed} 个账号未开启公共共享` : "公共共享已全部开启",
          };
        }),
      );
      onToast(response.failed ? `仍有 ${response.failed} 个账号共享失败` : "公共共享重试成功");
      await loadAccounts();
    } catch (error) {
      onToast(error instanceof Error ? error.message : "公共共享重试失败");
    } finally {
      setRetryingTargetId("");
    }
  };

  const toggleSelectedAccount = (accountId: number) => {
    setSelectedAccountIds((current) => {
      const next = new Set(current);
      if (next.has(accountId)) next.delete(accountId);
      else next.add(accountId);
      return next;
    });
  };

  const toggleCurrentPageAccounts = () => {
    if (!accounts) return;
    const pageIds = accounts.items.map((account) => account.id);
    const allSelected = pageIds.length > 0 && pageIds.every((accountId) => selectedAccountIds.has(accountId));
    setSelectedAccountIds(allSelected ? new Set() : new Set(pageIds));
  };

  const runBulkTest = async () => {
    if (!activeTargetId || !selectedAccountIds.size) return;
    const accountIds = [...selectedAccountIds];
    setBulkAction("test");
    try {
      const response = await api.pixelBulkTest(activeTargetId, accountIds);
      onToast(`连接测试完成：成功 ${response.success} 个${response.failed ? `，失败 ${response.failed} 个` : ""}`);
      await loadAccounts();
    } catch (error) {
      onToast(error instanceof Error ? error.message : "批量测试连接失败");
    } finally {
      setBulkAction("");
    }
  };

  const openBulkEdit = () => {
    if (!selectedAccountIds.size) return;
    setBulkMakePublic(false);
    setBulkConcurrency("");
    setBulkEditOpen(true);
  };

  const runBulkUpdate = async () => {
    if (!activeTargetId || !selectedAccountIds.size) return;
    const concurrency = bulkConcurrency.trim() ? Number(bulkConcurrency) : undefined;
    if (concurrency !== undefined && (!Number.isInteger(concurrency) || concurrency < 3 || concurrency > 50)) {
      onToast("并发数必须是 3 到 50 的整数");
      return;
    }
    if (!bulkMakePublic && concurrency === undefined) return;
    const accountIds = [...selectedAccountIds];
    setBulkAction("update");
    try {
      const response = await api.pixelBulkUpdate(activeTargetId, {
        accountIds,
        makePublic: bulkMakePublic,
        ...(concurrency === undefined ? {} : { concurrency }),
      });
      setBulkEditOpen(false);
      onToast(`批量编辑完成：成功 ${response.success} 个${response.failed ? `，失败 ${response.failed} 个` : ""}`);
      await loadAccounts();
    } catch (error) {
      onToast(error instanceof Error ? error.message : "批量编辑失败");
    } finally {
      setBulkAction("");
    }
  };

  const runBulkDelete = async () => {
    if (!activeTargetId || !bulkDeleteAccountIds.length) return;
    const accountIds = [...bulkDeleteAccountIds];
    setBulkAction("delete");
    try {
      const response = await api.pixelBulkDelete(activeTargetId, accountIds);
      await loadAccounts();
      setBulkDeleteOpen(false);
      setBulkDeleteAccountIds([]);
      onToast(`批量删除完成：成功 ${response.success} 个${response.failed ? `，失败 ${response.failed} 个` : ""}`);
    } catch (error) {
      onToast(error instanceof Error ? error.message : "批量删除失败");
    } finally {
      setBulkAction("");
    }
  };

  const openBulkDelete = () => {
    if (!selectedAccountIds.size) return;
    setBulkDeleteAccountIds([...selectedAccountIds]);
    setBulkDeleteOpen(true);
  };

  const activeTarget = targets.find((target) => target.id === activeTargetId);
  const bulkConcurrencyValue = bulkConcurrency.trim() ? Number(bulkConcurrency) : undefined;
  const bulkConcurrencyValid = bulkConcurrencyValue === undefined
    || (Number.isInteger(bulkConcurrencyValue) && bulkConcurrencyValue >= 3 && bulkConcurrencyValue <= 50);
  const bulkEditReady = bulkMakePublic || bulkConcurrencyValue !== undefined;

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-hidden">
      <Card className="shrink-0">
        <CardContent className="flex min-h-20 items-center justify-between gap-4 p-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md bg-blue-50 text-blue-600">
              <FileJson className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <div className="text-sm font-black text-foreground">导入账号 JSON</div>
              <div className={cn("truncate text-xs font-semibold", selectedFile ? "text-emerald-700" : "text-muted-foreground")}>
                {selectedFile ? `${selectedFile.name} · ${formatFileSize(selectedFile.size)}` : "尚未选择文件"}
              </div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
            />
            <Button variant="outline" disabled={importing} onClick={() => fileInputRef.current?.click()}>
              <FileJson className="h-4 w-4" />
              选择 JSON
            </Button>
            <Button disabled={importing || !selectedFile || !targets.length} onClick={openImport}>
              <Upload className="h-4 w-4" />
              上传到平台
            </Button>
            <Button variant="outline" disabled={importing || targetsLoading} onClick={() => void openImportRecords()}>
              <History className="h-4 w-4" />
              导入记录
            </Button>
            <Button variant="outline" disabled={exporting || importing || targetsLoading || !targets.length} onClick={openExport}>
              {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              {exporting ? "正在处理" : "汇总导出"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)] gap-4">
        <Card className="flex h-full min-h-0 flex-col overflow-hidden">
          <CardHeader className="shrink-0 border-b border-border px-4 py-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <FolderTree className="h-4 w-4 text-blue-600" />
              平台账号
            </CardTitle>
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-black text-muted-foreground">{targets.length || 7} 个</span>
              <button
                type="button"
                title="刷新全部账号数量"
                aria-label="刷新七个平台账号数量"
                disabled={targetsLoading || targetCountsRefreshing || !targets.length || Boolean(bulkAction)}
                className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
                onClick={() => void refreshAllTargetCounts()}
              >
                {targetCountsRefreshing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              </button>
            </div>
          </CardHeader>
          <CardContent className="min-h-0 flex-1 overflow-y-auto p-2">
            {targetsLoading && (
              <div className="space-y-2 p-1">
                {Array.from({ length: 7 }).map((_, index) => <SkeletonLine key={index} className="h-14 w-full rounded-md" />)}
              </div>
            )}
            {!targetsLoading && targetsError && (
              <div className="m-1 rounded-md border border-rose-200 bg-rose-50 p-3 text-xs font-bold text-rose-700">{targetsError}</div>
            )}
            {!targetsLoading && targets.map((target, index) => {
              const active = target.id === activeTargetId;
              return (
                <button
                  key={target.id}
                  disabled={Boolean(bulkAction)}
                  className={cn(
                    "mb-1 flex h-14 w-full items-center gap-2 rounded-md border px-2.5 text-left transition last:mb-0 disabled:cursor-wait disabled:opacity-60",
                    active ? "border-blue-200 bg-blue-50 text-blue-950" : "border-transparent hover:border-border hover:bg-muted",
                  )}
                  onClick={() => {
                    if (target.id === activeTargetId) return;
                    beginAccountsTransition();
                    setPage(1);
                    setActiveTargetId(target.id);
                  }}
                >
                  <span className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded text-xs font-black", active ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600")}>
                    {index + 1}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-black">{target.email}</span>
                    <span className="mt-0.5 flex items-center gap-1.5 text-[11px] font-bold text-muted-foreground">
                      <span className={cn("h-1.5 w-1.5 rounded-full", target.connected ? "bg-emerald-500" : target.error ? "bg-rose-500" : "bg-slate-300")} />
                      {target.connected ? "已连接" : target.error ? "连接失败" : "待连接"}
                      {target.accountCount !== null && <span>· {target.accountCount} 个</span>}
                    </span>
                  </span>
                </button>
              );
            })}
          </CardContent>
        </Card>

        <Card className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
          <CardHeader className="shrink-0 border-b border-border px-4 py-3">
            <div className="min-w-0">
              <CardTitle className="truncate text-base">{activeTarget?.email || "账号列表"}</CardTitle>
              <div className="mt-0.5 text-xs font-bold text-muted-foreground">
                {accounts ? `共 ${accounts.total} 个账号` : accountsLoading ? "正在连接平台" : "等待平台数据"}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="icon" title="刷新账号列表" disabled={!activeTargetId || accountsLoading || Boolean(bulkAction)} onClick={() => void loadAccounts()}>
                <RefreshCw className={cn("h-4 w-4", accountsLoading && "animate-spin")} />
              </Button>
            </div>
          </CardHeader>
          <CardContent className="flex min-h-0 flex-1 flex-col p-0">
            <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
              <div className="relative w-[210px] shrink-0">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  aria-label="搜索账号"
                  value={accountSearchInput}
                  placeholder="搜索账号名称"
                  disabled={!activeTargetId || Boolean(bulkAction)}
                  onChange={(event) => setAccountSearchInput(event.target.value)}
                  className="h-8 pl-8 pr-8 text-xs font-bold"
                />
                {accountSearchInput && (
                  <button
                    type="button"
                    title="清空搜索"
                    aria-label="清空搜索"
                    disabled={Boolean(bulkAction)}
                    className="absolute right-1.5 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                    onClick={() => setAccountSearchInput("")}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
              <select
                aria-label="账号状态筛选"
                value={accountStatus}
                disabled={!activeTargetId || accountsLoading || Boolean(bulkAction)}
                onChange={(event) => {
                  beginAccountsTransition();
                  setPage(1);
                  setAccountStatus(event.target.value);
                }}
                className="h-8 w-[128px] shrink-0 rounded-md border border-border bg-background px-2 text-xs font-bold outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
              >
                <option value="">全部状态</option>
                <option value="active">正常</option>
                <option value="codex_quota_protected">限额保护中</option>
                <option value="rate_limited">限流中</option>
                <option value="error">错误</option>
              </select>
              <span className={cn("mr-auto whitespace-nowrap text-xs font-black", selectedAccountIds.size ? "text-blue-700" : "text-muted-foreground")}>已选 {selectedAccountIds.size}</span>
              <div className="flex shrink-0 items-center gap-2">
                <Button variant="outline" size="sm" disabled={!selectedAccountIds.size || accountsLoading || Boolean(bulkAction)} onClick={() => void runBulkTest()}>
                  {bulkAction === "test" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Activity className="h-3.5 w-3.5" />}
                  批量测试连接
                </Button>
                <Button variant="outline" size="sm" disabled={!selectedAccountIds.size || accountsLoading || Boolean(bulkAction)} onClick={openBulkEdit}>
                  {bulkAction === "update" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Settings className="h-3.5 w-3.5" />}
                  批量编辑
                </Button>
                <Button variant="destructive" size="sm" disabled={!selectedAccountIds.size || accountsLoading || Boolean(bulkAction)} onClick={openBulkDelete}>
                  <Trash2 className="h-3.5 w-3.5" />
                  批量删除
                </Button>
              </div>
            </div>
            {accountsLoading && (
              <div className="min-h-0 flex-1 space-y-2 overflow-hidden p-4">
                {Array.from({ length: 8 }).map((_, index) => <SkeletonLine key={index} className="h-10 w-full rounded-md" />)}
              </div>
            )}
            {!accountsLoading && accountsError && (
              <div className="flex min-h-0 flex-1 items-center justify-center p-6">
                <div className="max-w-md rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-center text-sm font-bold text-rose-700">
                  {accountsError}
                </div>
              </div>
            )}
            {accounts && !accountsLoading && (
              <PixelAccountsTable
                key={`${activeTargetId}-${accounts.page}-${accounts.pageSize}`}
                targetId={activeTargetId}
                items={accounts.items}
                selectedIds={selectedAccountIds}
                onToggle={toggleSelectedAccount}
                onToggleAll={toggleCurrentPageAccounts}
              />
            )}
            <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-t border-border px-4">
              <span className="text-xs font-bold text-muted-foreground">
                {accounts ? `共 ${accounts.total} 个 · 第 ${accounts.page} / ${Math.max(accounts.pages, 1)} 页` : accountsLoading ? "正在加载账号" : "暂无分页数据"}
              </span>
              <div className="flex items-center gap-2">
                <select
                  aria-label="每页数量"
                  value={pageSize}
                  disabled={accountsLoading || Boolean(bulkAction)}
                  onChange={(event) => {
                    beginAccountsTransition();
                    setPage(1);
                    setPageSize(Number(event.target.value));
                  }}
                  className="h-8 rounded-md border border-border bg-background px-2 text-xs font-bold outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                >
                  {[20, 50, 100].map((size) => <option key={size} value={size}>{size} 条/页</option>)}
                </select>
                <Button
                  variant="outline"
                  size="icon"
                  title="上一页"
                  disabled={!accounts || page <= 1 || accountsLoading || Boolean(bulkAction)}
                  onClick={() => {
                    beginAccountsTransition();
                    setPage((value) => Math.max(value - 1, 1));
                  }}
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  title="下一页"
                  disabled={!accounts || page >= accounts.pages || accountsLoading || Boolean(bulkAction)}
                  onClick={() => {
                    beginAccountsTransition();
                    setPage((value) => value + 1);
                  }}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {importJob && (importJob.status === "queued" || importJob.status === "running") && (
        <div className="fixed bottom-4 right-4 z-40 w-[min(560px,calc(100vw-32px))] shadow-admin">
          <PixelImportProgress job={importJob} targets={targets} />
        </div>
      )}
      {exportJob && (exportJob.status === "queued" || exportJob.status === "running") && (
        <div className="fixed bottom-4 right-4 z-40 w-[min(620px,calc(100vw-32px))] shadow-admin">
          <PixelExportProgress job={exportJob} targets={targets} />
        </div>
      )}
      {results.length > 0 && importJob?.status === "completed" && (
        <Dialog open onOpenChange={(open) => !open && setResults([])}>
          <DialogContent className="max-h-[min(720px,calc(100vh-48px))] max-w-5xl overflow-auto">
            <DialogHeader>
              <DialogTitle>最近一次导入结果</DialogTitle>
              <DialogDescription>已完成 {results.length} 个平台账号的导入与公共共享处理。</DialogDescription>
            </DialogHeader>
            <PixelImportResults results={results} retryingTargetId={retryingTargetId} onRetry={retryShare} />
          </DialogContent>
        </Dialog>
      )}
      {exportJob?.status === "completed" && (
        <Dialog open onOpenChange={(open) => !open && setExportJob(null)}>
          <DialogContent className="max-h-[min(760px,calc(100vh-48px))] max-w-5xl overflow-auto">
            <DialogHeader>
              <DialogTitle>汇总整理完成</DialogTitle>
              <DialogDescription>
                已先保存备份 {exportJob.backupFileName || "JSON"}，再删除七个平台账号，并按选择的平台重新导入。
              </DialogDescription>
            </DialogHeader>
            <div className="flex justify-end">
              <Button
                variant="outline"
                size="sm"
                onClick={async () => {
                  try {
                    downloadPixelFile(await api.pixelExportJobDownload(exportJob.jobId));
                    onToast("备份文件已下载");
                  } catch (error) {
                    onToast(error instanceof Error ? error.message : "备份文件下载失败");
                  }
                }}
              >
                <Download className="h-3.5 w-3.5" />
                下载备份
              </Button>
            </div>
            <PixelExportResults job={exportJob} retryingTargetId={retryingTargetId} onRetry={retryShare} />
          </DialogContent>
        </Dialog>
      )}

      <PixelImportRecordsDialog
        open={importRecordsOpen}
        records={importRecords}
        loading={importRecordsLoading}
        deletingRecordId={deletingRecordId}
        onOpenChange={setImportRecordsOpen}
        onDelete={setDeleteRecord}
      />

      <Dialog open={Boolean(deleteRecord)} onOpenChange={(open) => !open && !deletingRecordId && setDeleteRecord(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>确认删除导入账号</DialogTitle>
            <DialogDescription>
              将删除“{deleteRecord?.sourceFileName || "JSON 文件"}”在 {deleteRecord?.targetCount || 0} 个平台中记录的随机邮箱账号，只匹配本次导入名称。
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-3 text-sm font-bold text-rose-700">
            改名、找不到或重复名称的账号会跳过并报告，不会删除其他账号。
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="outline" disabled={Boolean(deletingRecordId)} onClick={() => setDeleteRecord(null)}>取消</Button>
            <Button variant="destructive" disabled={Boolean(deletingRecordId)} onClick={() => void runDeleteImportRecord()}>
              {deletingRecordId ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
              {deletingRecordId ? "正在删除" : "确认删除"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={exportOpen} onOpenChange={(open) => !exporting && setExportOpen(open)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>汇总导出</DialogTitle>
            <DialogDescription>默认只下载去重后的 JSON，并在文件里按 100 个账号一组生成分组。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <label className={cn(
              "flex items-start gap-3 rounded-md border px-3 py-3 text-sm font-bold",
              exportDeleteAndReimport ? "border-rose-200 bg-rose-50 text-rose-800" : "border-border bg-background",
            )}>
              <Checkbox checked={exportDeleteAndReimport} onCheckedChange={(checked) => setExportDeleteAndReimport(checked === true)} />
              <span>
                <span className="block font-black">删除七个平台全部账号，并用本次汇总重新导入</span>
                <span className="mt-1 block text-xs font-bold text-muted-foreground">
                  服务器会先完整导出、去重、保存 600 权限备份；任一导出或备份失败都会停止，不会删除。
                </span>
              </span>
            </label>
            {exportDeleteAndReimport && (
              <div>
                <div className="mb-2 text-xs font-black text-muted-foreground">选择重新导入到哪些平台账号</div>
                <div className="grid max-h-[300px] grid-cols-2 gap-2 overflow-auto">
                  {targets.map((target, index) => (
                    <label key={target.id} className="flex h-12 items-center gap-2.5 rounded-md border border-border bg-background px-3 text-sm font-bold">
                      <Checkbox checked={exportSelectedTargetIds.has(target.id)} onCheckedChange={() => toggleExportTarget(target.id)} />
                      <span className="flex h-6 w-6 items-center justify-center rounded bg-slate-100 text-[11px] font-black text-slate-600">{index + 1}</span>
                      <span className="truncate">{target.email}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
          <div className="mt-5 flex items-center justify-between">
            {exportDeleteAndReimport ? (
              <button
                className="text-xs font-black text-blue-600 hover:text-blue-700"
                onClick={() => setExportSelectedTargetIds((current) => current.size === targets.length ? new Set() : new Set(targets.map((target) => target.id)))}
              >
                {exportSelectedTargetIds.size === targets.length ? "取消全选" : "全部选择"}
              </button>
            ) : <span />}
            <div className="flex gap-2">
              <Button variant="outline" disabled={exporting} onClick={() => setExportOpen(false)}>取消</Button>
              <Button
                variant={exportDeleteAndReimport ? "destructive" : "default"}
                disabled={exporting || (exportDeleteAndReimport && !exportSelectedTargetIds.size)}
                onClick={() => void runExportAction()}
              >
                {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : exportDeleteAndReimport ? <Trash2 className="h-4 w-4" /> : <Download className="h-4 w-4" />}
                {exporting ? "正在处理" : exportDeleteAndReimport ? `确认整理并导入 ${exportSelectedTargetIds.size} 个` : "只导出下载"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={importOpen} onOpenChange={(open) => !importing && setImportOpen(open)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>选择上传账号</DialogTitle>
            <DialogDescription>{selectedFile?.name || "JSON 文件"} · 已选 {selectedTargetIds.size} / {targets.length} 个平台账号</DialogDescription>
          </DialogHeader>
          <div className="grid max-h-[360px] grid-cols-2 gap-2 overflow-auto">
            {targets.map((target, index) => (
              <label key={target.id} className="flex h-12 items-center gap-2.5 rounded-md border border-border bg-background px-3 text-sm font-bold">
                <Checkbox checked={selectedTargetIds.has(target.id)} onCheckedChange={() => toggleImportTarget(target.id)} />
                <span className="flex h-6 w-6 items-center justify-center rounded bg-slate-100 text-[11px] font-black text-slate-600">{index + 1}</span>
                <span className="truncate">{target.email}</span>
              </label>
            ))}
          </div>
          <div className="mt-5 flex items-center justify-between">
            <button
              className="text-xs font-black text-blue-600 hover:text-blue-700"
              onClick={() => setSelectedTargetIds((current) => current.size === targets.length ? new Set() : new Set(targets.map((target) => target.id)))}
            >
              {selectedTargetIds.size === targets.length ? "取消全选" : "全部选择"}
            </button>
            <div className="flex gap-2">
              <Button variant="outline" disabled={importing} onClick={() => setImportOpen(false)}>取消</Button>
              <Button disabled={importing || !selectedTargetIds.size} onClick={() => void runImport()}>
                {importing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                {importing ? "正在导入" : `确认上传 ${selectedTargetIds.size} 个`}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={bulkEditOpen} onOpenChange={(open) => !bulkAction && setBulkEditOpen(open)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>批量编辑账号</DialogTitle>
            <DialogDescription>将设置应用到已选择的 {selectedAccountIds.size} 个账号；未填写的项目保持不变。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <label className="flex h-12 items-center gap-3 rounded-md border border-border px-3 text-sm font-black">
              <Checkbox checked={bulkMakePublic} onCheckedChange={(checked) => setBulkMakePublic(checked === true)} />
              <span>设为公共账号池</span>
            </label>
            <div className="space-y-1.5">
              <Label htmlFor="pixel-bulk-concurrency">并发数</Label>
              <Input
                id="pixel-bulk-concurrency"
                type="number"
                min={3}
                max={50}
                step={1}
                value={bulkConcurrency}
                placeholder="留空则不修改（3-50）"
                onChange={(event) => setBulkConcurrency(event.target.value)}
              />
              {!bulkConcurrencyValid && <div className="text-xs font-bold text-rose-600">请输入 3 到 50 的整数</div>}
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="outline" disabled={bulkAction === "update"} onClick={() => setBulkEditOpen(false)}>取消</Button>
            <Button disabled={!bulkEditReady || !bulkConcurrencyValid || bulkAction === "update"} onClick={() => void runBulkUpdate()}>
              {bulkAction === "update" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {bulkAction === "update" ? "正在保存" : "确认修改"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog
        open={bulkDeleteOpen}
        onOpenChange={(open) => {
          if (bulkAction) return;
          setBulkDeleteOpen(open);
          if (!open) setBulkDeleteAccountIds([]);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>确认批量删除</DialogTitle>
            <DialogDescription>
              将从 {activeTarget?.email || "当前平台账号"} 永久删除已选择的 {bulkDeleteAccountIds.length} 个账号，此操作无法撤销。
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-3 text-sm font-bold text-rose-700">
            只会删除当前表格中已勾选的账号，不会影响其他平台账号。
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="outline" disabled={bulkAction === "delete"} onClick={() => setBulkDeleteOpen(false)}>取消</Button>
            <Button
              variant="destructive"
              className="min-w-[116px] disabled:opacity-100"
              aria-busy={bulkAction === "delete"}
              disabled={!bulkDeleteAccountIds.length || bulkAction === "delete"}
              onClick={() => void runBulkDelete()}
            >
              {bulkAction === "delete" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
              {bulkAction === "delete" ? "正在删除" : `删除 ${bulkDeleteAccountIds.length} 个`}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

type PixelUsageLoadState =
  | { status: "loading" }
  | { status: "ready"; usage: PixelAccountUsage }
  | { status: "error" };

function PixelAccountsTable({
  targetId,
  items,
  selectedIds,
  onToggle,
  onToggleAll,
}: {
  targetId: string;
  items: PixelAccount[];
  selectedIds: Set<number>;
  onToggle: (accountId: number) => void;
  onToggleAll: () => void;
}) {
  const [usageStates, setUsageStates] = useState<Record<number, PixelUsageLoadState>>({});
  const usageStatesRef = useRef<Record<number, PixelUsageLoadState>>({});
  const usageQueueRef = useRef<number[]>([]);
  const activeUsageRequestsRef = useRef(0);
  const usageControllersRef = useRef<Map<number, AbortController>>(new Map());
  const mountedRef = useRef(true);
  const [usageQueueVersion, setUsageQueueVersion] = useState(0);

  useEffect(() => () => {
    mountedRef.current = false;
    usageQueueRef.current = [];
    usageControllersRef.current.forEach((controller) => controller.abort());
    usageControllersRef.current.clear();
  }, []);

  useEffect(() => {
    while (activeUsageRequestsRef.current < 5 && usageQueueRef.current.length > 0) {
      const accountId = usageQueueRef.current.shift();
      if (accountId === undefined) break;
      const controller = new AbortController();
      usageControllersRef.current.set(accountId, controller);
      activeUsageRequestsRef.current += 1;
      void api.pixelAccountUsage(targetId, accountId, controller.signal)
        .then((usage) => {
          if (!mountedRef.current || controller.signal.aborted) return;
          const next = { ...usageStatesRef.current, [accountId]: { status: "ready", usage } as PixelUsageLoadState };
          usageStatesRef.current = next;
          setUsageStates(next);
        })
        .catch(() => {
          if (!mountedRef.current || controller.signal.aborted) return;
          const next = { ...usageStatesRef.current, [accountId]: { status: "error" } as PixelUsageLoadState };
          usageStatesRef.current = next;
          setUsageStates(next);
        })
        .finally(() => {
          usageControllersRef.current.delete(accountId);
          activeUsageRequestsRef.current = Math.max(activeUsageRequestsRef.current - 1, 0);
          if (mountedRef.current) setUsageQueueVersion((value) => value + 1);
        });
    }
  }, [targetId, usageQueueVersion]);

  const requestUsage = useCallback((accountId: number) => {
    if (usageStatesRef.current[accountId]) return;
    const next = { ...usageStatesRef.current, [accountId]: { status: "loading" } as PixelUsageLoadState };
    usageStatesRef.current = next;
    setUsageStates(next);
    usageQueueRef.current.push(accountId);
    setUsageQueueVersion((value) => value + 1);
  }, []);

  if (!items.length) {
    return <div className="flex min-h-0 flex-1 items-center justify-center text-sm font-bold text-muted-foreground">当前账号没有已导入数据</div>;
  }
  const selectedOnPage = items.filter((account) => selectedIds.has(account.id)).length;
  const allSelected = selectedOnPage === items.length;
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="w-full min-w-[1158px] table-fixed text-left text-xs">
        <thead className="sticky top-0 z-10 bg-muted text-[11px] font-black text-muted-foreground">
          <tr>
            <th className="w-[48px] px-3 py-2.5 text-center">
              <Checkbox
                aria-label="选择当前页全部账号"
                checked={allSelected ? true : selectedOnPage > 0 ? "indeterminate" : false}
                onCheckedChange={onToggleAll}
              />
            </th>
            <th className="w-[220px] px-3 py-2.5">账号名称</th>
            <th className="w-[92px] px-3 py-2.5">等级</th>
            <th className="w-[92px] px-3 py-2.5">状态</th>
            <th className="w-[104px] px-3 py-2.5">调度</th>
            <th className="w-[118px] px-3 py-2.5">公共共享</th>
            <th className="w-[82px] px-3 py-2.5">5h</th>
            <th className="w-[82px] px-3 py-2.5">7d</th>
            <th className="w-[100px] px-3 py-2.5">并发</th>
            <th className="w-[220px] px-3 py-2.5">错误</th>
          </tr>
        </thead>
        <tbody>
          {items.map((account) => (
            <PixelAccountRow
              key={account.id}
              account={account}
              selected={selectedIds.has(account.id)}
              usageState={usageStates[account.id]}
              onToggle={onToggle}
              onVisible={requestUsage}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PixelAccountRow({
  account,
  selected,
  usageState,
  onToggle,
  onVisible,
}: {
  account: PixelAccount;
  selected: boolean;
  usageState?: PixelUsageLoadState;
  onToggle: (accountId: number) => void;
  onVisible: (accountId: number) => void;
}) {
  const rowRef = useRef<HTMLTableRowElement>(null);
  const effectiveStatus = pixelEffectiveStatus(account);

  useEffect(() => {
    const row = rowRef.current;
    if (!row) return;
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      onVisible(account.id);
      observer.disconnect();
    }, { rootMargin: "80px 0px" });
    observer.observe(row);
    return () => observer.disconnect();
  }, [account.id, onVisible]);

  return (
    <tr ref={rowRef} className={cn("border-t border-border align-middle hover:bg-muted/50", selected && "bg-blue-50/70")}>
      <td className="px-3 py-2.5 text-center">
        <Checkbox aria-label={`选择账号 ${account.name || account.id}`} checked={selected} onCheckedChange={() => onToggle(account.id)} />
      </td>
      <td className="px-3 py-2.5">
        <div className="truncate font-black text-foreground" title={account.name}>{account.name || "-"}</div>
        <div className="mt-0.5 text-[11px] font-bold uppercase text-muted-foreground">{account.platform || "openai"}</div>
      </td>
      <td className="px-3 py-2.5"><StatusPill tone="blue">{(account.accountLevel || "plus").toUpperCase()}</StatusPill></td>
      <td className="px-3 py-2.5">
        <StatusPill tone={effectiveStatus === "active" ? "green" : effectiveStatus === "error" ? "red" : effectiveStatus === "codex_quota_protected" ? "blue" : effectiveStatus === "rate_limited" ? "amber" : "gray"}>
          {pixelStatusLabel(effectiveStatus)}
        </StatusPill>
      </td>
      <td className="px-3 py-2.5"><StatusPill tone={account.schedulable ? "green" : "amber"}>{account.schedulable ? "可调度" : "不可调度"}</StatusPill></td>
      <td className="px-3 py-2.5"><StatusPill tone={account.shareMode === "public" && ["approved", "active", ""].includes(account.shareStatus || "") ? "green" : account.shareMode === "public" ? "amber" : "gray"}>{pixelShareLabel(account)}</StatusPill></td>
      <td className="px-3 py-2.5"><PixelUsageValue state={usageState} window="5h" /></td>
      <td className="px-3 py-2.5"><PixelUsageValue state={usageState} window="7d" /></td>
      <td className="px-3 py-2.5 font-black">{account.currentConcurrency} / {account.concurrency}</td>
      <td className="px-3 py-2.5"><div className={cn("truncate font-semibold", account.errorMessage ? "text-rose-600" : "text-muted-foreground")} title={account.errorMessage}>{account.errorMessage || "-"}</div></td>
    </tr>
  );
}

function PixelUsageValue({ state, window }: { state?: PixelUsageLoadState; window: "5h" | "7d" }) {
  if (!state || state.status === "loading") {
    return <Loader2 className={cn("h-3.5 w-3.5 animate-spin", window === "5h" ? "text-emerald-600" : "text-violet-600")} />;
  }
  if (state.status === "error") {
    return <span className="font-bold text-rose-600" title="额度读取失败">失败</span>;
  }
  const value = window === "5h" ? state.usage.codex5hLimitPercent : state.usage.codex7dLimitPercent;
  return <span className={cn("font-black", window === "5h" ? "text-emerald-700" : "text-violet-700")}>{formatPercent(value)}</span>;
}

function PixelImportProgress({ job, targets }: { job: PixelImportJob; targets: PixelTarget[] }) {
  const currentTarget = targets.find((target) => target.id === job.currentTargetId);
  const completed = Math.min(job.completedTargets, job.totalTargets);
  const percent = job.totalTargets ? Math.round((completed / job.totalTargets) * 100) : 0;
  const failed = job.status === "failed";
  const finished = job.status === "completed";
  const waiting = job.phase === "waiting";
  const label = failed
    ? job.error || "导入任务失败"
    : finished
      ? "全部平台账号处理完成"
      : waiting
        ? `等待 ${Math.round(job.waitSeconds)} 秒后处理 ${currentTarget?.email || "下一个平台账号"}`
        : `正在处理 ${currentTarget?.email || "平台账号"}`;
  return (
    <Card className={cn(failed ? "border-rose-200" : finished ? "border-emerald-200" : waiting ? "border-amber-200" : "border-blue-200")}>
      <CardContent className="flex min-h-20 items-center gap-4 p-4">
        <div className={cn(
          "flex h-10 w-10 shrink-0 items-center justify-center rounded-md",
          failed ? "bg-rose-50 text-rose-600" : finished ? "bg-emerald-50 text-emerald-600" : waiting ? "bg-amber-50 text-amber-600" : "bg-blue-50 text-blue-600",
        )}>
          {failed ? <AlertTriangle className="h-5 w-5" /> : finished ? <CheckCircle2 className="h-5 w-5" /> : <Loader2 className="h-5 w-5 animate-spin" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <div className="truncate text-sm font-black">{label}</div>
            <div className="shrink-0 text-xs font-black text-muted-foreground">{completed} / {job.totalTargets}</div>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
            <div
              className={cn("h-full rounded-full transition-all duration-500", failed ? "bg-rose-500" : finished ? "bg-emerald-500" : waiting ? "bg-amber-500" : "bg-blue-500")}
              style={{ width: `${finished ? 100 : percent}%` }}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function PixelImportRecordsDialog({
  open,
  records,
  loading,
  deletingRecordId,
  onOpenChange,
  onDelete,
}: {
  open: boolean;
  records: PixelImportRecord[];
  loading: boolean;
  deletingRecordId: string;
  onOpenChange: (open: boolean) => void;
  onDelete: (record: PixelImportRecord) => void;
}) {
  const [expandedRecordId, setExpandedRecordId] = useState("");
  const statusLabel = (status: PixelImportRecord["deleteStatus"]) => {
    if (status === "deleted") return "已删除";
    if (status === "partial") return "部分处理";
    return "未删除";
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[min(760px,calc(100vh-48px))] w-[min(calc(100vw-48px),1260px)] overflow-auto">
        <DialogHeader>
          <DialogTitle>导入记录</DialogTitle>
          <DialogDescription>每条记录只对应当次选择的平台和生成的随机邮箱，记录会永久保留。</DialogDescription>
        </DialogHeader>
        {loading ? (
          <div className="flex min-h-32 items-center justify-center text-sm font-bold text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />正在读取记录
          </div>
        ) : records.length === 0 ? (
          <div className="flex min-h-32 items-center justify-center text-sm font-bold text-muted-foreground">暂无导入记录</div>
        ) : (
          <div className="overflow-auto rounded-md border border-border">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead className="bg-muted text-[11px] font-black text-muted-foreground">
                <tr>
                  <th className="w-8 px-3 py-2.5" />
                  <th className="px-3 py-2.5">导入时间</th>
                  <th className="px-3 py-2.5">JSON 文件</th>
                  <th className="px-3 py-2.5">平台</th>
                  <th className="px-3 py-2.5">账号数</th>
                  <th className="px-3 py-2.5">删除状态</th>
                  <th className="w-[130px] px-3 py-2.5">操作</th>
                </tr>
              </thead>
              <tbody>
                {records.map((record) => {
                  const expanded = expandedRecordId === record.recordId;
                  return (
                    <Fragment key={record.recordId}>
                    <tr className="border-t border-border align-top">
                    <td className="px-3 py-3">
                      <button
                        type="button"
                        title="查看随机邮箱"
                        aria-label="查看随机邮箱"
                        className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition hover:bg-muted hover:text-foreground"
                        onClick={() => setExpandedRecordId(expanded ? "" : record.recordId)}
                      >
                        <ChevronRight className={cn("h-4 w-4 transition-transform", expanded && "rotate-90")} />
                      </button>
                    </td>
                    <td className="px-3 py-3 font-bold whitespace-nowrap">{formatDateTime(record.createdAt)}</td>
                    <td className="max-w-[260px] px-3 py-3 font-black"><div className="truncate" title={record.sourceFileName}>{record.sourceFileName}</div></td>
                    <td className="px-3 py-3 font-black">{record.targetCount}</td>
                    <td className="px-3 py-3 font-black">{record.targets.reduce((total, target) => total + target.generatedNames.length, 0)}</td>
                    <td className="px-3 py-3">
                      <StatusPill tone={record.deleteStatus === "deleted" ? "green" : record.deleteStatus === "partial" ? "amber" : "gray"}>
                        {statusLabel(record.deleteStatus)}
                      </StatusPill>
                    </td>
                    <td className="px-3 py-3">
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={record.deleteStatus === "deleted" || Boolean(deletingRecordId)}
                        onClick={() => onDelete(record)}
                      >
                        {deletingRecordId === record.recordId ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                        {record.deleteStatus === "partial" ? "重试删除" : "删除账号"}
                      </Button>
                    </td>
                    </tr>
                    {expanded && (
                      <tr className="border-t border-border bg-muted/30">
                        <td colSpan={7} className="px-4 py-3">
                          <div className="grid gap-3 md:grid-cols-2">
                            {record.targets.map((target) => (
                              <div key={target.targetId} className="rounded-md border border-border bg-background p-3">
                                <div className="flex items-center justify-between gap-3 text-xs font-black">
                                  <span className="truncate">{target.email}</span>
                                  <span className="shrink-0 text-muted-foreground">{target.generatedNames.length} 个随机邮箱</span>
                                </div>
                                <div className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-all rounded bg-muted p-2 font-mono text-[11px] text-muted-foreground">
                                  {target.generatedNames.length ? target.generatedNames.join("\n") : "没有实际新增账号"}
                                </div>
                                {record.lastDeleteResults.find((item) => item.targetId === target.targetId)?.message && (
                                  <div className="mt-2 text-[11px] font-bold text-amber-700">
                                    {record.lastDeleteResults.find((item) => item.targetId === target.targetId)?.message}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function PixelExportProgress({ job, targets }: { job: PixelExportJob; targets: PixelTarget[] }) {
  const currentTarget = targets.find((target) => target.id === job.currentTargetId);
  const completed = Math.min(job.completedTargets, job.totalTargets);
  const percent = job.totalTargets ? Math.round((completed / job.totalTargets) * 100) : 0;
  const phaseLabel = {
    queued: "准备汇总整理",
    exporting: "正在导出七个平台账号",
    backing_up: "正在保存服务器备份",
    deleting: `正在清空 ${currentTarget?.email || "平台账号"}`,
    importing: `正在重新导入 ${currentTarget?.email || "平台账号"}`,
    waiting: `等待 ${Math.round(job.waitSeconds)} 秒后处理 ${currentTarget?.email || "下一个平台账号"}`,
    completed: "汇总整理完成",
    failed: job.error || "汇总整理失败",
  }[job.phase];
  const exported = job.export?.deduplicatedCount;
  return (
    <Card className={cn(job.phase === "failed" ? "border-rose-200" : job.phase === "waiting" ? "border-amber-200" : "border-blue-200")}>
      <CardContent className="flex min-h-20 items-center gap-4 p-4">
        <div className={cn(
          "flex h-10 w-10 shrink-0 items-center justify-center rounded-md",
          job.phase === "failed" ? "bg-rose-50 text-rose-600" : job.phase === "waiting" ? "bg-amber-50 text-amber-600" : "bg-blue-50 text-blue-600",
        )}>
          {job.phase === "failed" ? <AlertTriangle className="h-5 w-5" /> : <Loader2 className="h-5 w-5 animate-spin" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <div className="truncate text-sm font-black">{phaseLabel}</div>
            <div className="shrink-0 text-xs font-black text-muted-foreground">{completed} / {job.totalTargets}</div>
          </div>
          <div className="mt-1 text-xs font-bold text-muted-foreground">
            {exported ? `已备份 ${exported} 个账号 · ${job.export?.batchCount || 0} 个 100 分组` : "导出成功后才会进入删除阶段"}
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
            <div className={cn("h-full rounded-full transition-all duration-500", job.phase === "waiting" ? "bg-amber-500" : "bg-blue-500")} style={{ width: `${percent}%` }} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function PixelExportResults({
  job,
  retryingTargetId,
  onRetry,
}: {
  job: PixelExportJob;
  retryingTargetId: string;
  onRetry: (result: PixelImportTargetResult) => Promise<void>;
}) {
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="grid gap-3 p-4 sm:grid-cols-4">
          <div><div className="text-xs font-bold text-muted-foreground">导出账号</div><div className="text-lg font-black text-blue-700">{job.export?.sourceCount ?? 0}</div></div>
          <div><div className="text-xs font-bold text-muted-foreground">去重后</div><div className="text-lg font-black text-emerald-700">{job.export?.deduplicatedCount ?? 0}</div></div>
          <div><div className="text-xs font-bold text-muted-foreground">重复剔除</div><div className="text-lg font-black text-amber-700">{job.export?.duplicateCount ?? 0}</div></div>
          <div><div className="text-xs font-bold text-muted-foreground">100 分组</div><div className="text-lg font-black text-violet-700">{job.export?.batchCount ?? 0}</div></div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="border-b border-border">
          <CardTitle>删除结果</CardTitle>
          <span className="text-xs font-black text-muted-foreground">七个平台账号</span>
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-auto">
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead className="bg-muted text-[11px] font-black text-muted-foreground">
                <tr><th className="px-3 py-2.5">平台账号</th><th className="px-3 py-2.5">状态</th><th className="px-3 py-2.5">原数量</th><th className="px-3 py-2.5">已删除</th><th className="px-3 py-2.5">失败</th><th className="px-3 py-2.5">说明</th></tr>
              </thead>
              <tbody>
                {job.deleteResults.map((result) => (
                  <tr key={result.targetId} className="border-t border-border">
                    <td className="px-3 py-3 font-black">{result.email}</td>
                    <td className="px-3 py-3"><StatusPill tone={result.status === "success" ? "green" : result.status === "partial" ? "amber" : "red"}>{result.status === "success" ? "已清空" : result.status === "partial" ? "部分失败" : "失败"}</StatusPill></td>
                    <td className="px-3 py-3 font-black">{result.total}</td>
                    <td className="px-3 py-3 font-black text-emerald-700">{result.deleted}</td>
                    <td className={cn("px-3 py-3 font-black", result.failed ? "text-rose-600" : "text-slate-400")}>{result.failed}</td>
                    <td className="px-3 py-3"><div className="truncate font-bold text-muted-foreground" title={result.message}>{result.message || "-"}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
      <PixelImportResults results={job.results} retryingTargetId={retryingTargetId} onRetry={onRetry} />
    </div>
  );
}

function PixelImportResults({
  results,
  retryingTargetId,
  onRetry,
}: {
  results: PixelImportTargetResult[];
  retryingTargetId: string;
  onRetry: (result: PixelImportTargetResult) => Promise<void>;
}) {
  return (
    <Card>
      <CardHeader className="border-b border-border">
        <CardTitle>最近一次导入结果</CardTitle>
        <span className="text-xs font-black text-muted-foreground">{results.length} 个平台账号</span>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-auto">
          <table className="w-full min-w-[980px] text-left text-xs">
            <thead className="bg-muted text-[11px] font-black text-muted-foreground">
              <tr>
                <th className="px-3 py-2.5">平台账号</th>
                <th className="px-3 py-2.5">状态</th>
                <th className="px-3 py-2.5">生成文件</th>
                <th className="px-3 py-2.5">创建</th>
                <th className="px-3 py-2.5">更新</th>
                <th className="px-3 py-2.5">导入失败</th>
                <th className="px-3 py-2.5">已公共共享</th>
                <th className="px-3 py-2.5">共享失败</th>
                <th className="px-3 py-2.5">结果</th>
                <th className="w-[110px] px-3 py-2.5">操作</th>
              </tr>
            </thead>
            <tbody>
              {results.map((result) => (
                <tr key={result.targetId} className="border-t border-border">
                  <td className="px-3 py-3 font-black">{result.email}</td>
                  <td className="px-3 py-3"><StatusPill tone={result.status === "success" ? "green" : result.status === "partial" ? "amber" : "red"}>{result.status === "success" ? "成功" : result.status === "partial" ? "部分成功" : "失败"}</StatusPill></td>
                  <td className="px-3 py-3 font-mono text-[11px] font-bold text-slate-500">{result.generatedFileName || "-"}</td>
                  <td className="px-3 py-3 font-black text-blue-600">{result.created}</td>
                  <td className="px-3 py-3 font-black text-violet-600">{result.updated}</td>
                  <td className={cn("px-3 py-3 font-black", result.failed ? "text-rose-600" : "text-slate-400")}>{result.failed}</td>
                  <td className="px-3 py-3 font-black text-emerald-600">{result.shared}</td>
                  <td className={cn("px-3 py-3 font-black", result.shareFailed ? "text-rose-600" : "text-slate-400")}>{result.shareFailed}</td>
                  <td className="max-w-[260px] px-3 py-3"><div className={cn("truncate font-bold", result.status === "failed" ? "text-rose-600" : "text-muted-foreground")} title={result.message}>{result.message || "-"}</div></td>
                  <td className="px-3 py-3">
                    <Button variant="outline" size="sm" disabled={!result.failedShareIds.length || retryingTargetId === result.targetId} onClick={() => void onRetry(result)}>
                      {retryingTargetId === result.targetId ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
                      重试共享
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function StatusPill({ children, tone }: { children: React.ReactNode; tone: "green" | "red" | "amber" | "blue" | "gray" }) {
  const styles = {
    green: "border-emerald-200 bg-emerald-50 text-emerald-700",
    red: "border-rose-200 bg-rose-50 text-rose-700",
    amber: "border-amber-200 bg-amber-50 text-amber-700",
    blue: "border-blue-200 bg-blue-50 text-blue-700",
    gray: "border-slate-200 bg-slate-50 text-slate-600",
  };
  return <span className={cn("inline-flex min-h-6 items-center rounded border px-2 text-[11px] font-black", styles[tone])}>{children}</span>;
}

function pixelStatusLabel(status: string) {
  if (status === "active") return "正常";
  if (status === "codex_quota_protected") return "限额保护中";
  if (status === "rate_limited") return "限流中";
  if (status === "temp_unschedulable") return "临时暂停";
  if (status === "unschedulable") return "不可调度";
  if (status === "error") return "错误";
  if (status === "inactive") return "停用";
  if (status === "disabled") return "禁用";
  if (status === "paused") return "暂停";
  return status || "未知";
}

function pixelEffectiveStatus(account: PixelAccount) {
  if (account.status === "error") return "error";
  const now = Date.now();
  const rateLimitResetAt = account.rateLimitResetAt ? Date.parse(account.rateLimitResetAt) : Number.NaN;
  if (Number.isFinite(rateLimitResetAt) && rateLimitResetAt > now) return "rate_limited";
  const quotaResetAt = account.codexQuotaProtectionResetAt ? Date.parse(account.codexQuotaProtectionResetAt) : Number.NaN;
  if (account.codexQuotaProtectionReason && (!Number.isFinite(quotaResetAt) || quotaResetAt > now)) {
    return "codex_quota_protected";
  }
  return account.status;
}

function pixelShareLabel(account: PixelAccount) {
  if (account.shareMode !== "public") return "私有";
  if (account.shareStatus === "pending") return "公共 · 审核中";
  if (account.shareStatus === "suspended") return "公共 · 暂停";
  return "公共 · 已开启";
}

function formatPercent(value: number | null) {
  return value === null || !Number.isFinite(value) ? "-" : `${Math.round(value)}%`;
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function CostView({
  stored,
  latestBalance,
  totalCost,
  netOutcome,
  partnerReceivable,
  ownerReceivable,
  updateStoredField,
  onAddCost,
  onHistory,
  onAccounts,
  onPoolCredentials,
  onSmtp,
}: {
  stored: StoredState;
  latestBalance?: { total: number; amounts: number[]; accounts?: string[]; date: string };
  totalCost: number;
  netOutcome: number;
  partnerReceivable: number;
  ownerReceivable: number;
  updateStoredField: (patch: Partial<StoredState>) => void;
  onAddCost: () => void;
  onHistory: () => void;
  onAccounts: () => void;
  onPoolCredentials: () => void;
  onSmtp: () => void;
}) {
  const rows = latestBalance?.amounts.map((amount, index) => ({
    account: latestBalance.accounts?.[index] || `账号${index + 1}`,
    current: amount,
  })) ?? [];
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="grid grid-cols-[repeat(4,minmax(120px,1fr))_auto] items-end gap-3 p-4">
          <Field label="星星出资">
            <div className="flex h-9 items-center rounded-md border border-border bg-muted px-3 text-sm font-black">
              {formatMoney(totalCost)}
            </div>
          </Field>
          <Field label="社会哥出资">
            <Input value={stored.partnerCost ?? 0} onChange={(event) => updateStoredField({ partnerCost: parseMoney(event.target.value) })} />
          </Field>
          <Field label="本次提现">
            <Input value={stored.withdrawalAmount ?? ""} onChange={(event) => updateStoredField({ withdrawalAmount: parseMoney(event.target.value) })} />
          </Field>
          <Field label="最近余额">
            <div className="flex h-9 items-center rounded-md border border-border bg-muted px-3 text-sm font-black">
              <AnimatedNumber value={latestBalance?.total} digits={2} />
            </div>
          </Field>
          <div className="flex flex-wrap justify-end gap-2">
            <Button onClick={onAddCost}>
              <Plus className="h-4 w-4" />
              累加成本
            </Button>
            <Button variant="outline" onClick={onHistory}>
              <History className="h-4 w-4" />
              累加历史
            </Button>
            <Button variant="outline" onClick={onAccounts}>
              <WalletCards className="h-4 w-4" />
              账号配置
            </Button>
            <Button variant="outline" onClick={onPoolCredentials}>
              <Settings className="h-4 w-4" />
              接口账号
            </Button>
            <Button variant="outline" onClick={onSmtp}>
              <Mail className="h-4 w-4" />
              预警设置
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-4 gap-3">
        <MetricTile title="总出资" value={totalCost} accent="text-blue-600" sub="星星出资合计" size="normal" digits={2} suffix=" 元" />
        <MetricTile title="当前提现利润" value={netOutcome} accent={netOutcome < 0 ? "text-rose-600" : "text-indigo-600"} signed sub="本次提现 - 总出资" size="normal" digits={2} suffix=" 元" />
        <MetricTile title="社会哥应收" value={partnerReceivable} accent="text-orange-600" sub="按结算比例计算" size="normal" digits={2} suffix=" 元" />
        <MetricTile title="星星应收" value={ownerReceivable} accent="text-violet-600" sub="本次提现剩余" size="normal" digits={2} suffix=" 元" />
      </div>

      <Card className="bg-indigo-50/55">
        <CardContent className="grid grid-cols-[110px_1fr] gap-x-4 gap-y-2.5 p-3 text-sm font-bold text-foreground">
          <span className="text-blue-700">本次提现</span>
          <span>本次提现金额 = {formatMoney(stored.withdrawalAmount)}</span>
          <span className="text-blue-700">当前提现利润</span>
          <span>本次提现 {formatMoney(stored.withdrawalAmount)} - 总出资 {formatMoney(totalCost)} = {formatSignedMoney(netOutcome)}</span>
          <span className="text-blue-700">结算结果</span>
          <span>社会哥应收 {formatMoney(partnerReceivable)}；星星应收 {formatMoney(ownerReceivable)}</span>
        </CardContent>
      </Card>

      <DataTable
        title="账号余额"
        columns={["账号", "当前余额"]}
        rows={rows.map((row) => [
          row.account,
          formatMoney(row.current),
        ])}
      />
    </div>
  );
}

function HistoryView({ stored }: { stored: StoredState }) {
  return (
    <div className="space-y-4">
      <PagedBalanceHistory stateRows={stored.history} />
      <DataTable
        title="累加成本历史"
        columns={["日期", "金额", "备注", "创建时间"]}
        rows={(stored.costAdditions ?? []).map((item) => [
          formatDateTime(item.date),
          formatSignedMoney(item.amount),
          item.note || "-",
          formatDateTime(item.createdAt),
        ])}
      />
    </div>
  );
}

function AddCostDialog({
  open,
  onOpenChange,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (addition: CostAddition) => Promise<void>;
}) {
  const [date, setDate] = useState(formatDateInput());
  const [note, setNote] = useState("");
  const [amount, setAmount] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (open) {
      setDate(formatDateInput());
      setNote("");
      setAmount("");
    }
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>累加成本</DialogTitle>
          <DialogDescription>确认后会追加到星星出资，并同步服务器。</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Field label="日期">
            <Input type="date" value={date} onChange={(event) => setDate(event.target.value)} />
          </Field>
          <Field label="备注">
            <Input value={note} onChange={(event) => setNote(event.target.value)} placeholder="例如：续费、补号、人工成本" />
          </Field>
          <Field label="金额">
            <Input value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="输入金额" />
          </Field>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            disabled={saving || parseMoney(amount) === 0}
            onClick={async () => {
              setSaving(true);
              await onSubmit({
                id: crypto.randomUUID(),
                date: new Date(`${date}T00:00:00+08:00`).toISOString(),
                note: note.trim(),
                amount: parseMoney(amount),
                createdAt: new Date().toISOString(),
              });
              setSaving(false);
              onOpenChange(false);
            }}
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
            确定
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function CostHistoryDialog({
  open,
  additions,
  onOpenChange,
  onClear,
}: {
  open: boolean;
  additions: CostAddition[];
  onOpenChange: (open: boolean) => void;
  onClear: () => Promise<void>;
}) {
  const [clearing, setClearing] = useState(false);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>累加成本历史</DialogTitle>
          <DialogDescription>合计 {formatSignedMoney(additions.reduce((sum, item) => sum + item.amount, 0))} 元</DialogDescription>
        </DialogHeader>
        <div className="max-h-[360px] overflow-auto rounded-md border border-border">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 bg-muted text-xs font-black text-muted-foreground">
              <tr>
                <th className="px-3 py-2">日期</th>
                <th className="px-3 py-2">金额</th>
                <th className="px-3 py-2">备注</th>
                <th className="px-3 py-2">创建时间</th>
              </tr>
            </thead>
            <tbody>
              {additions.map((item) => (
                <tr key={item.id} className="border-t border-border">
                  <td className="px-3 py-2 font-semibold">{formatDateTime(item.date)}</td>
                  <td className="px-3 py-2 font-black">{formatSignedMoney(item.amount)}</td>
                  <td className="px-3 py-2">{item.note || "-"}</td>
                  <td className="px-3 py-2 text-muted-foreground">{formatDateTime(item.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
          <Button
            variant="destructive"
            disabled={!additions.length || clearing}
            onClick={async () => {
              setClearing(true);
              await onClear();
              setClearing(false);
            }}
          >
            <Trash2 className="h-4 w-4" />
            一键清空
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function AccountsDialog({ open, onOpenChange, onSaved }: { open: boolean; onOpenChange: (open: boolean) => void; onSaved: () => void }) {
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!open) return;
    api.balanceAccounts().then((response) => {
      setText(response.accounts.map((item) => `${item.name} | ${item.baseURL} | ${item.apiKey}`).join("\n"));
    });
  }, [open]);
  return (
    <ConfigDialog
      open={open}
      title="余额账号配置"
      description="每行一个账号：账号名 | BaseURL | API Key；已有账号的 API Key 留空表示不修改。"
      saving={saving}
      onOpenChange={onOpenChange}
      onSave={async () => {
        setSaving(true);
        await api.updateBalanceAccounts(parseAccounts(text));
        setSaving(false);
        onSaved();
        onOpenChange(false);
      }}
    >
      <Textarea value={text} onChange={(event) => setText(event.target.value)} className="h-72 font-mono" />
    </ConfigDialog>
  );
}

function PoolCredentialsDialog({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<PoolCredentials>({ email: "", password: "" });
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!open) return;
    api.poolCredentials().then((response) => setForm(response.credentials));
  }, [open]);
  return (
    <ConfigDialog
      open={open}
      title="平台接口账号"
      description="密码保存在服务器且不会回显；留空表示不修改。"
      saving={saving}
      onOpenChange={onOpenChange}
      onSave={async () => {
        setSaving(true);
        await api.updatePoolCredentials(form);
        setSaving(false);
        onSaved();
        onOpenChange(false);
      }}
    >
      <Field label="邮箱">
        <Input
          value={form.email}
          placeholder="已保存，留空不修改"
          onChange={(event) => setForm({ ...form, email: event.target.value })}
        />
      </Field>
      <Field label="密码">
        <Input
          type="password"
          value={form.password}
          placeholder="已保存，留空不修改"
          onChange={(event) => setForm({ ...form, password: event.target.value })}
        />
      </Field>
    </ConfigDialog>
  );
}

function SmtpDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [form, setForm] = useState<SMTPSettings>({ host: "smtp.qq.com", port: 465, username: "", password: "", recipient: "" });
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!open) return;
    api.smtpSettings().then((response) => setForm(response.settings));
  }, [open]);
  return (
    <ConfigDialog
      open={open}
      title="预警邮箱设置"
      description="SMTP 授权码保存在服务器且不会回显；留空表示不修改。"
      saving={saving}
      onOpenChange={onOpenChange}
      onSave={async () => {
        setSaving(true);
        await api.updateSmtpSettings(form);
        setSaving(false);
        onOpenChange(false);
      }}
    >
      <div className="grid grid-cols-2 gap-3">
        <Field label="SMTP Host">
          <Input value={form.host} onChange={(event) => setForm({ ...form, host: event.target.value })} />
        </Field>
        <Field label="端口">
          <Input value={form.port} onChange={(event) => setForm({ ...form, port: Number(event.target.value) || 465 })} />
        </Field>
      </div>
      <Field label="发件邮箱">
        <Input
          value={form.username}
          placeholder="已保存，留空不修改"
          onChange={(event) => setForm({ ...form, username: event.target.value })}
        />
      </Field>
      <Field label="SMTP 授权码">
        <Input
          type="password"
          value={form.password}
          placeholder="已保存，留空不修改"
          onChange={(event) => setForm({ ...form, password: event.target.value })}
        />
      </Field>
      <Field label="预警收件邮箱">
        <Input
          value={form.recipient}
          placeholder="已保存，留空不修改"
          onChange={(event) => setForm({ ...form, recipient: event.target.value })}
        />
      </Field>
    </ConfigDialog>
  );
}

function ConfigDialog({
  open,
  title,
  description,
  saving,
  onOpenChange,
  onSave,
  children,
}: {
  open: boolean;
  title: string;
  description: string;
  saving: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: () => Promise<void>;
  children: React.ReactNode;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">{children}</div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button disabled={saving} onClick={() => void onSave()}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            保存
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function PoolSummaryCard({ group, latest }: { group: string; latest?: PoolSnapshot }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{group}</CardTitle>
        <span className="rounded bg-emerald-50 px-2 py-1 text-xs font-black text-emerald-700">{latest?.status || "暂无"}</span>
      </CardHeader>
      <CardContent className="grid grid-cols-3 gap-2.5">
        <MetricTile title="总账号" value={latest?.total} accent="text-blue-600" size="compact" />
        <MetricTile title="5h剩余" value={latest?.remaining5h} accent="text-emerald-600" size="compact" />
        <MetricTile title="7d剩余" value={latest?.remaining7d} accent="text-violet-600" size="compact" />
      </CardContent>
    </Card>
  );
}

function PagedPoolTable({ group, stateRows }: { group: string; stateRows: PoolSnapshot[] }) {
  const [loadedRows, setLoadedRows] = useState<PoolSnapshot[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoadedRows([]);
    setNextCursor(null);
    setHasMore(false);
    setError("");
    setLoading(true);
    void api
      .poolHistory(group)
      .then((page) => {
        if (cancelled) return;
        setLoadedRows(page.items);
        setNextCursor(page.nextCursor);
        setHasMore(page.hasMore);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "账号池历史加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [group]);

  const loadOlder = () => {
    if (loading || !hasMore || nextCursor === null) return;
    setLoading(true);
    setError("");
    void api
      .poolHistory(group, nextCursor)
      .then((page) => {
        setLoadedRows((current) => mergeHistoryRows(page.items, current));
        setNextCursor(page.nextCursor);
        setHasMore(page.hasMore);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "更早历史加载失败"))
      .finally(() => setLoading(false));
  };

  const rows = useMemo(() => mergeHistoryRows(loadedRows, stateRows), [loadedRows, stateRows]);
  return <PoolTable title={`${group} 历史`} rows={rows} footer={<HistoryPagination loading={loading} hasMore={hasMore} error={error} onLoadOlder={loadOlder} />} />;
}

function PagedBalanceHistory({ stateRows }: { stateRows: StoredState["history"] }) {
  const [loadedRows, setLoadedRows] = useState<StoredState["history"]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoadedRows([]);
    setNextCursor(null);
    setHasMore(false);
    setError("");
    setLoading(true);
    void api
      .balanceHistory()
      .then((page) => {
        if (cancelled) return;
        setLoadedRows(page.items);
        setNextCursor(page.nextCursor);
        setHasMore(page.hasMore);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "余额历史加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadOlder = () => {
    if (loading || !hasMore || nextCursor === null) return;
    setLoading(true);
    setError("");
    void api
      .balanceHistory(nextCursor)
      .then((page) => {
        setLoadedRows((current) => mergeHistoryRows(page.items, current));
        setNextCursor(page.nextCursor);
        setHasMore(page.hasMore);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "更早历史加载失败"))
      .finally(() => setLoading(false));
  };

  const rows = useMemo(() => mergeHistoryRows(loadedRows, stateRows), [loadedRows, stateRows]);
  const accountColumns = historyAccountColumns(rows);
  return (
    <DataTable
      title="余额历史"
      columns={["时间", "余额合计", "账号数", ...accountColumns]}
      rows={rows.map((item) => [
        formatDateTime(item.date),
        formatMoney(item.total),
        String(item.amounts.length),
        ...accountColumns.map((name, index) => formatMoney(balanceAmountForColumn(item, name, index))),
      ])}
      footer={<HistoryPagination loading={loading} hasMore={hasMore} error={error} onLoadOlder={loadOlder} />}
    />
  );
}

function HistoryPagination({
  loading,
  hasMore,
  error,
  onLoadOlder,
}: {
  loading: boolean;
  hasMore: boolean;
  error: string;
  onLoadOlder: () => void;
}) {
  return (
    <div className="flex min-h-9 items-center justify-between gap-3 pt-3">
      <span className={cn("text-xs font-bold", error ? "text-rose-600" : "text-muted-foreground")}>
        {error || (hasMore ? "当前展示最新一页" : "已加载全部历史")}
      </span>
      {hasMore && (
        <Button variant="outline" size="sm" disabled={loading} onClick={onLoadOlder}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <History className="h-3.5 w-3.5" />}
          加载更早
        </Button>
      )}
    </div>
  );
}

function mergeHistoryRows<T extends { date: string }>(primary: T[], secondary: T[]) {
  const byDate = new Map<string, T>();
  for (const row of primary) byDate.set(row.date, row);
  for (const row of secondary) byDate.set(row.date, row);
  return Array.from(byDate.values()).sort((left, right) => new Date(left.date).getTime() - new Date(right.date).getTime());
}

function PoolTable({ title, rows, footer }: { title: string; rows: PoolSnapshot[]; footer?: React.ReactNode }) {
  const tableRows = rows.map((row, index) => {
    const previous = index > 0 ? rows[index - 1] : undefined;
    return [
      formatDateTime(row.date),
      <TrendCell value={row.total} previous={previous?.total} />,
      <TrendCell value={row.remaining5h} previous={previous?.remaining5h} suffix={usageSuffix(row.utilization5h)} />,
      <TrendCell value={row.remaining7d} previous={previous?.remaining7d} suffix={usageSuffix(row.utilization7d)} />,
      <TrendCell value={row.concurrentAvailable} previous={previous?.concurrentAvailable} />,
      <TrendCell value={row.limited} previous={previous?.limited} inverse />,
      <TrendCell value={row.quotaProtected} previous={previous?.quotaProtected} inverse />,
      <TrendCell value={row.error} previous={previous?.error} inverse />,
      <TrendCell value={row.disabled} previous={previous?.disabled} inverse />,
      row.status || "--",
    ];
  });
  return (
    <DataTable
      title={title}
      columns={["时间", "总账号", "5h剩余", "7d剩余", "并发可用", "限流", "额度保护", "错误", "禁用", "状态"]}
      rows={tableRows}
      footer={footer}
    />
  );
}

function TrendCell({
  value,
  previous,
  suffix,
  inverse = false,
}: {
  value?: number | null;
  previous?: number | null;
  suffix?: string;
  inverse?: boolean;
}) {
  if (value === null || value === undefined) {
    return <span className="font-black text-muted-foreground">--{suffix ? ` ${suffix}` : ""}</span>;
  }
  const delta = previous === null || previous === undefined ? 0 : value - previous;
  const changed = delta !== 0;
  const isGood = inverse ? delta < 0 : delta > 0;
  const color = !changed ? "text-muted-foreground" : isGood ? "text-emerald-600" : "text-rose-600";
  return (
    <span className="inline-flex items-center gap-1 font-black">
      <span>{value}</span>
      {changed && <span className={cn("text-xs", color)}>{delta > 0 ? `↑${delta}` : `↓${Math.abs(delta)}`}</span>}
      {!changed && previous !== undefined && <span className="text-xs text-muted-foreground">无变化</span>}
      {suffix && <span className="text-xs font-bold text-muted-foreground">{suffix}</span>}
    </span>
  );
}

function usageSuffix(value?: number | null) {
  return value === null || value === undefined ? undefined : `${formatMoney(value, 1)}%`;
}

function DataTable({
  title,
  columns,
  rows,
  subtitle,
  footer,
}: {
  title: string;
  columns: string[];
  rows: TableCell[][];
  subtitle?: string;
  footer?: React.ReactNode;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior: rows.length > 80 ? "auto" : "smooth" });
  }, [rows.length, title]);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <span className="text-xs font-bold text-muted-foreground">{subtitle ?? `${rows.length} 条`}</span>
      </CardHeader>
      <CardContent>
        <div ref={scrollRef} className="max-h-[360px] overflow-auto rounded-md border border-border will-change-scroll">
          <table className="w-full min-w-max text-left text-sm">
            <thead className="sticky top-0 z-[1] bg-muted text-xs font-black text-muted-foreground">
              <tr>
                {columns.map((column) => (
                  <th key={column} className="whitespace-nowrap px-3 py-2">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${title}-${index}`} className="border-t border-border transition hover:bg-muted/55">
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className="whitespace-nowrap px-3 py-2 font-semibold text-foreground">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
              {!rows.length && (
                <tr>
                  <td colSpan={columns.length} className="px-3 py-10 text-center text-sm font-bold text-muted-foreground">
                    暂无数据
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {footer}
      </CardContent>
    </Card>
  );
}

function AnimatedNumber({
  value,
  digits = 0,
  signed = false,
  suffix = "",
  className,
}: {
  value?: number | null;
  digits?: number;
  signed?: boolean;
  suffix?: string;
  className?: string;
}) {
  const source = useMotionValue(value ?? 0);
  const spring = useSpring(source, { stiffness: 110, damping: 24, mass: 0.8 });
  const formatted = useTransform(spring, (latest) => {
    if (value === null || value === undefined || Number.isNaN(value)) return "--";
    const prefix = signed && latest >= 0 ? "+" : "";
    return `${prefix}${formatMoney(latest, digits)}${suffix}`;
  });

  useEffect(() => {
    source.set(value ?? 0);
  }, [source, value]);

  return <motion.span className={className}>{formatted}</motion.span>;
}

function MetricTile({
  title,
  value,
  accent = "text-foreground",
  signed = false,
  sub,
  digits = 0,
  suffix = "",
  size = "normal",
}: {
  title: string;
  value?: number | null;
  accent?: string;
  signed?: boolean;
  sub?: React.ReactNode;
  digits?: number;
  suffix?: string;
  size?: "compact" | "normal" | "large";
}) {
  const numberClass = {
    compact: "text-[26px]",
    normal: "text-[26px]",
    large: "text-[28px]",
  }[size];
  return (
    <div
      className={cn(
        "rounded-md border border-border bg-card shadow-sm",
        size === "compact" ? "min-h-[68px] px-3 py-1.5" : "min-h-[80px] px-3.5 py-2",
      )}
    >
      <div className="flex items-center gap-2 text-xs font-black text-muted-foreground">
        <span className={cn("h-2 w-2 shrink-0 rounded-full bg-current", accent)} />
        {title}
      </div>
      <AnimatedNumber className={cn("mt-1 block font-black leading-none", numberClass, accent)} value={value} digits={digits} signed={signed} suffix={suffix} />
      {sub && <div className="mt-1 text-xs font-bold text-muted-foreground">{sub}</div>}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function OverviewCardsSkeleton() {
  return (
    <div className="grid grid-cols-4 gap-3">
      {Array.from({ length: 4 }).map((_, index) => (
        <SkeletonMetricTile key={index} size="large" />
      ))}
    </div>
  );
}

function ViewSkeleton({ view }: { view: ViewKey }) {
  if (view === "manager") {
    return (
      <div className="flex h-full min-h-0 flex-col gap-4 overflow-hidden">
        <Card className="shrink-0"><CardContent className="flex h-20 items-center justify-between p-4"><SkeletonLine className="h-11 w-56" /><div className="flex gap-2"><SkeletonLine className="h-9 w-24 rounded-md" /><SkeletonLine className="h-9 w-28 rounded-md" /></div></CardContent></Card>
        <div className="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)] gap-4">
          <Card className="flex min-h-0 flex-col overflow-hidden"><CardHeader className="shrink-0"><SkeletonLine className="h-5 w-24" /></CardHeader><CardContent className="min-h-0 flex-1 space-y-2 overflow-hidden">{Array.from({ length: 7 }).map((_, index) => <SkeletonLine key={index} className="h-14 w-full rounded-md" />)}</CardContent></Card>
          <SkeletonTable rows={9} />
        </div>
      </div>
    );
  }

  if (view === "pools") {
    return (
      <div className="space-y-4">
        <Card>
          <CardContent className="flex items-center justify-between gap-3 p-3">
            <div className="flex gap-3">
              <SkeletonLine className="h-9 w-36 rounded-md" />
              <SkeletonLine className="h-9 w-36 rounded-md" />
              <SkeletonLine className="h-9 w-36 rounded-md" />
            </div>
            <div className="flex gap-2">
              <SkeletonLine className="h-9 w-20 rounded-md" />
              <SkeletonLine className="h-9 w-24 rounded-md" />
              <SkeletonLine className="h-9 w-24 rounded-md" />
            </div>
          </CardContent>
        </Card>
        <div className="grid grid-cols-2 gap-3">
          {Array.from({ length: 2 }).map((_, index) => (
            <Card key={index}>
              <CardHeader>
                <SkeletonLine className="h-5 w-32" />
                <SkeletonLine className="h-7 w-16 rounded" />
              </CardHeader>
              <CardContent className="grid grid-cols-3 gap-2.5">
                <SkeletonMetricTile size="compact" />
                <SkeletonMetricTile size="compact" />
                <SkeletonMetricTile size="compact" />
              </CardContent>
            </Card>
          ))}
        </div>
        <SkeletonTable />
      </div>
    );
  }

  if (view === "cost") {
    return (
      <div className="space-y-4">
        <Card>
          <CardContent className="grid grid-cols-[repeat(4,minmax(120px,1fr))_auto] items-end gap-3 p-4">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="space-y-2">
                <SkeletonLine className="h-4 w-20" />
                <SkeletonLine className="h-9 w-full rounded-md" />
              </div>
            ))}
            <div className="flex justify-end gap-2">
              <SkeletonLine className="h-9 w-24 rounded-md" />
              <SkeletonLine className="h-9 w-24 rounded-md" />
            </div>
          </CardContent>
        </Card>
        <div className="grid grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, index) => (
            <SkeletonMetricTile key={index} />
          ))}
        </div>
        <SkeletonTable />
      </div>
    );
  }

  if (view === "history") {
    return (
      <div className="space-y-4">
        <SkeletonTable />
        <SkeletonTable rows={4} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <SkeletonLine className="h-5 w-28" />
          <SkeletonLine className="h-4 w-48" />
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="rounded-md border border-border bg-background p-3">
              <SkeletonLine className="mb-3 h-4 w-16" />
              <SkeletonLine className="h-[220px] w-full rounded-md" />
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <SkeletonLine className="h-5 w-24" />
          <SkeletonLine className="h-4 w-28" />
        </CardHeader>
        <CardContent>
          <SkeletonLine className="h-[320px] w-full rounded-md" />
        </CardContent>
      </Card>
    </div>
  );
}

function SkeletonMetricTile({ size = "normal" }: { size?: "compact" | "normal" | "large" }) {
  return (
    <div className={cn("rounded-md border border-border bg-card shadow-sm", size === "compact" ? "min-h-[68px] px-3 py-1.5" : "min-h-[80px] px-3.5 py-2")}>
      <div className="flex items-center gap-2">
        <SkeletonLine className="h-2 w-2 rounded-full" />
        <SkeletonLine className="h-3.5 w-20" />
      </div>
      <SkeletonLine className={cn("mt-1.5 h-7", size === "large" ? "w-32" : "w-24")} />
      {size !== "compact" && <SkeletonLine className="mt-1.5 h-3.5 w-28" />}
    </div>
  );
}

function SkeletonTable({ rows = 6 }: { rows?: number }) {
  return (
    <Card>
      <CardHeader>
        <SkeletonLine className="h-5 w-24" />
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {Array.from({ length: rows }).map((_, index) => (
            <SkeletonLine key={index} className="h-9 w-full rounded-md" />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function SkeletonLine({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-slate-200/80", className)} />;
}

function normalizeStored(value: StoredState): StoredState {
  return {
    ...defaultStored,
    ...value,
    history: value.history ?? [],
    costAdditions: value.costAdditions ?? [],
  };
}

function normalizePool(value: PoolAnalyzerState): PoolAnalyzerState {
  const selectedGroups = visiblePoolGroups(value.selectedGroups?.length ? value.selectedGroups : defaultPool.selectedGroups!);
  const availableGroups = visiblePoolGroups(value.availableGroups?.length ? value.availableGroups : defaultPool.availableGroups!);
  return {
    ...defaultPool,
    ...value,
    history: value.history ?? [],
    selectedGroups: selectedGroups.length ? selectedGroups : defaultPool.selectedGroups,
    availableGroups: availableGroups.length ? availableGroups : defaultPool.availableGroups,
    pollingMinutes: 5,
  };
}

function visiblePoolGroups(groups: string[]) {
  return groups.filter((group) => group && !hiddenPoolGroups.has(group));
}

function groupPoolRows(history: PoolSnapshot[]) {
  return history
    .slice()
    .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())
    .reduce<Record<string, PoolSnapshot[]>>((groups, item) => {
      groups[item.groupName] ??= [];
      groups[item.groupName].push(item);
      return groups;
    }, {});
}

function buildPoolChartRows(history: PoolSnapshot[], selectedGroups: string[], metric: PoolMetricKey) {
  const byTime = new Map<string, Record<string, string | number | null>>();
  for (const item of history.filter((row) => selectedGroups.includes(row.groupName))) {
    const key = formatDateTime(item.date);
    const row = byTime.get(key) ?? { time: key };
    row[item.groupName] = item[metric] ?? null;
    byTime.set(key, row);
  }
  return Array.from(byTime.values());
}

function historyAccountColumns(history: StoredState["history"]) {
  const names: string[] = [];
  for (const item of history) {
    const itemNames = item.accounts?.length ? item.accounts : item.amounts.map((_, index) => `账号${index + 1}`);
    for (const name of itemNames) {
      if (name && !names.includes(name)) names.push(name);
    }
  }
  return names;
}

function balanceAmountForColumn(item: StoredState["history"][number], columnName: string, fallbackIndex: number) {
  const names = item.accounts?.length ? item.accounts : item.amounts.map((_, index) => `账号${index + 1}`);
  const matchedIndex = names.indexOf(columnName);
  return item.amounts[matchedIndex >= 0 ? matchedIndex : fallbackIndex];
}

function parseAccounts(text: string): BalanceAccount[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line.split("|").map((part) => part.trim());
      return { name: parts[0] ?? "", baseURL: parts[1] ?? "", apiKey: parts[2] ?? "" };
    })
    .filter((item) => item.name && item.baseURL);
}
