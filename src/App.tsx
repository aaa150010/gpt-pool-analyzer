import { AnimatePresence, motion, useMotionValue, useSpring, useTransform } from "framer-motion";
import {
  Activity,
  Bell,
  Calculator,
  CheckCircle2,
  Database,
  History,
  Loader2,
  Mail,
  Plus,
  RefreshCw,
  Save,
  Server,
  Settings,
  Trash2,
  TrendingUp,
  Users,
  WalletCards,
} from "lucide-react";
import type React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
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
  PoolAnalyzerState,
  PoolCredentials,
  PoolMetricKey,
  PoolSnapshot,
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
  cost: 0,
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
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-20 border-b border-border bg-card/95 shadow-sm backdrop-blur">
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

      <main className="min-h-screen">
        <section className="space-y-4 p-4">
            {loading ? (
              <OverviewCardsSkeleton />
            ) : (
              <OverviewCards
                latestTotal={latestBalance?.total}
                cost={totalCost}
                costSummary={costSummary}
                net={latestBalance ? latestBalance.total - totalCost : undefined}
              />
            )}

          <AnimatePresence mode="wait">
            <motion.div
              key={view}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.18, ease: "easeOut" }}
            >
              {loading && <ViewSkeleton view={view} />}
              {!loading && view === "trends" && (
                <TrendsView stored={stored} pool={pool} selectedGroups={activeSelectedGroups} />
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
}: {
  stored: StoredState;
  pool: PoolAnalyzerState;
  selectedGroups: string[];
}) {
  const balanceRows = stored.history.map((item) => ({
    time: formatDateTime(item.date),
    total: Number(item.total.toFixed(2)),
    net: Number((item.total - stored.cost).toFixed(2)),
  }));
  return (
    <div className="space-y-4">
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
          <span className="text-xs font-bold text-muted-foreground">最多保留 24 小时</span>
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
        <PoolTable key={group} title={`${group} 历史`} rows={grouped[group] ?? []} />
      ))}
    </div>
  );
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
            <Input value={stored.cost ?? 0} onChange={(event) => updateStoredField({ cost: parseMoney(event.target.value) })} />
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
  const accountColumns = historyAccountColumns(stored.history);
  return (
    <div className="space-y-4">
      <DataTable
        title="余额历史"
        columns={["时间", "余额合计", "账号数", ...accountColumns]}
        rows={stored.history.map((item) => [
          formatDateTime(item.date),
          formatMoney(item.total),
          String(item.amounts.length),
          ...accountColumns.map((name, index) => formatMoney(balanceAmountForColumn(item, name, index))),
        ])}
      />
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
      description="每行一个账号：账号名 | BaseURL | API Key"
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
      description="账号密码保存到服务器，Mac 本机不再进钥匙串。"
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
        <Input value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} />
      </Field>
      <Field label="密码">
        <Input type="password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} />
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
      description="SMTP 授权码保存到服务器，用于掉号预警。"
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
        <Input value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} />
      </Field>
      <Field label="SMTP 授权码">
        <Input type="password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} />
      </Field>
      <Field label="预警收件邮箱">
        <Input value={form.recipient} onChange={(event) => setForm({ ...form, recipient: event.target.value })} />
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

function PoolTable({ title, rows }: { title: string; rows: PoolSnapshot[] }) {
  const tableRows = rows.map((row, index) => {
    const previous = index > 0 ? rows[index - 1] : undefined;
    return [
      formatDateTime(row.date),
      <TrendCell value={row.total} previous={previous?.total} />,
      <TrendCell value={row.remaining5h} previous={previous?.remaining5h} suffix={usageSuffix(row.utilization5h)} />,
      <TrendCell value={row.remaining7d} previous={previous?.remaining7d} suffix={usageSuffix(row.utilization7d)} />,
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
      columns={["时间", "总账号", "5h剩余", "7d剩余", "限流", "额度保护", "错误", "禁用", "状态"]}
      rows={tableRows}
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

function DataTable({ title, columns, rows }: { title: string; columns: string[]; rows: TableCell[][] }) {
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
        <span className="text-xs font-bold text-muted-foreground">{rows.length} 条</span>
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
  sub?: string;
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
    .filter((item) => item.name && item.baseURL && item.apiKey);
}
