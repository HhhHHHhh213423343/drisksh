"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Activity, ArrowLeft, BarChart3, BookOpen, Building2, CheckCircle2,
  ChevronRight, ClipboardCheck, FileText, Gavel, Globe2, Loader2, LogOut, MessageSquareText,
  RefreshCw, Search, ShieldAlert, Sparkles, TrendingUp, Warehouse,
} from "lucide-react";
import CompanyProfilePanel from "./CompanyProfilePanel";
import MonitoringWorkspace from "./MonitoringWorkspace";

type Category = "macro" | "operations" | "finance" | "legal" | "brand";
type PlainValue = string | number | boolean | null | undefined;
type SectionItem = Record<string, PlainValue>;

type Metric = {
  key: string; label: string; value: string; unit: string; delta?: string;
  tone?: "positive" | "warning" | "negative" | "neutral"; description?: string;
};
type Series = { key: string; label: string; unit: string; points: { period: string; value: number }[] };
type Section = { key: string; title: string; kind: string; summary: string; items: SectionItem[] };
type Source = {
  title: string; source_name: string; source_url?: string; source_tag?: string;
  page_hint?: string; published_at?: string | null; severity?: string; sentiment?: string;
};
type MetricCoverage = {
  key: string; label: string;
  status: "available" | "partial" | "stale" | "no_hit" | "not_connected" | "fetch_failed" | "not_applicable";
  weight: number; reason?: string; source_ids?: string[]; as_of?: string | null; applicable: boolean;
};
type QualityComponents = { completeness: number; freshness: number; authority: number; corroboration: number };
type Preview = {
  company_name: string; category: Category; report_type: string; retrieval_stage: string;
  industry_template: string;
  summary: string; key_points: string[]; next_actions: string[]; sources: Source[];
  metrics: Metric[]; series: Series[]; sections: Section[];
  data_quality: {
    status: string; coverage_percent: number; evidence_count: number; warnings: string[]; updated_at?: string;
    boundary_summary?: string; components?: QualityComponents; metric_coverage?: MetricCoverage[];
  };
  freshness?: { updated_at?: string | null; stale: boolean; stale_after_hours: number };
  source_coverage?: { code?: string; source: string; status: string; authority?: string; points?: number; message?: string; last_checked_at?: string | null }[];
  generated_at: string;
};
type Company = { id: string; name: string; industry?: string | null; region?: string | null; description?: string; company_profile?: Record<string, unknown> };
type IngestionRun = {
  id: string; status: "queued" | "running" | "completed" | "partial" | "failed";
  progress_current: number; progress_total: number; inserted_count: number; updated_count: number;
  failed_sources: string[]; message: string;
};
type ReviewItem = {
  id: string; source_code: string; source_name: string; category: Category;
  query_url?: string; reason: string; priority: "high" | "normal" | "low";
  status: string; created_at: string;
};

const CATEGORY_CONFIG: Record<Category, { label: string; short: string; description: string; report: string; icon: typeof Globe2 }> = {
  macro: { label: "宏观环境及行业分析", short: "宏观与行业", description: "政策 / 经济 / 行业", report: "macro_environment_report", icon: Globe2 },
  operations: { label: "企业业务运营分析", short: "业务运营", description: "经营 / 供应链 / 对标", report: "business_operations_report", icon: Warehouse },
  finance: { label: "财务状况分析", short: "财务健康", description: "财报 / 趋势 / 情景", report: "financial_health_report", icon: BarChart3 },
  legal: { label: "法律及合规风险分析", short: "法律合规", description: "诉讼 / 监管 / 传导", report: "legal_risk_report", icon: Gavel },
  brand: { label: "品牌舆情分析", short: "品牌舆情", description: "媒体 / 情绪 / 议题", report: "brand_sentiment_report", icon: MessageSquareText },
};
const CATEGORIES = Object.keys(CATEGORY_CONFIG) as Category[];
const REVIEW_CATEGORY_LABELS: Record<Category, string> = {
  macro: "宏观行业", operations: "业务运营", finance: "财务", legal: "法律合规", brand: "品牌舆情",
};
const LABELS: Record<string, string> = {
  period: "报告期", revenue: "营业收入（亿元）", net_profit: "归母净利润（亿元）", net_margin: "净利率（%）",
  scenario: "情景", assumption: "假设", date: "日期", title: "事件", detail: "事件说明", severity: "重要性",
  sentiment: "情绪", source: "来源", count: "数量", ratio: "占比", stage: "阶段", value: "数值", label: "指标",
  category: "类别", impact: "影响", action: "建议动作", signal: "信号", risk: "风险", opportunity: "机会",
  event: "事件", channel: "传导渠道", company: "企业", direction: "影响方向", evidence: "证据",
};
const TEMPLATE_LABELS: Record<string, string> = {
  banking: "银行业模板", financial_services: "金融服务业模板",
  automotive: "汽车行业模板", manufacturing: "制造业模板",
  pharma: "医药行业模板", real_estate: "房地产行业模板",
  internet: "互联网与软件模板", energy: "能源行业模板",
  general: "通用模板",
};
const COVERAGE_STATUS: Record<MetricCoverage["status"], { label: string; className: string }> = {
  available: { label: "已覆盖", className: "bg-emerald-50 text-emerald-700" },
  partial: { label: "部分覆盖", className: "bg-amber-50 text-amber-700" },
  stale: { label: "数据过期", className: "bg-amber-50 text-amber-700" },
  no_hit: { label: "已检索无结果", className: "bg-slate-100 text-slate-600" },
  not_connected: { label: "未接入", className: "bg-slate-100 text-slate-600" },
  fetch_failed: { label: "抓取失败", className: "bg-red-50 text-red-700" },
  not_applicable: { label: "行业不适用", className: "bg-violet-50 text-violet-700" },
};

function isCategory(value?: string): value is Category { return Boolean(value && CATEGORIES.includes(value as Category)); }
function dateLabel(value?: string | null) {
  if (!value) return "日期未提供";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value.slice(0, 10) : date.toLocaleDateString("zh-CN");
}
function toneClass(tone?: Metric["tone"]) {
  if (tone === "positive") return "text-emerald-700 bg-emerald-50";
  if (tone === "warning") return "text-amber-700 bg-amber-50";
  if (tone === "negative") return "text-red-700 bg-red-50";
  return "text-[#38551d] bg-[#f0f7e6]";
}
function displayValue(value: PlainValue) {
  if (value === null || value === undefined || value === "") return "待补充";
  if (typeof value === "number") return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
  return String(value);
}

function MiniChart({ series }: { series: Series }) {
  const width = 620; const height = 190; const pad = 22;
  const values = series.points.map((point) => point.value);
  const min = Math.min(...values); const max = Math.max(...values); const range = max - min || 1;
  const coords = series.points.map((point, index) => ({
    ...point,
    x: pad + (index * (width - pad * 2)) / Math.max(1, series.points.length - 1),
    y: height - pad - ((point.value - min) / range) * (height - pad * 2),
  }));
  const path = coords.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
  return (
    <div className="rounded-2xl border border-[#dfe5da] bg-white p-5">
      <div className="mb-4 flex items-start justify-between"><div><h3 className="font-semibold">{series.label}</h3><p className="mt-1 text-xs text-[#778078]">时间序列 · 单位：{series.unit}</p></div><TrendingUp size={18} className="text-[#78be20]" /></div>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-48 w-full overflow-visible" role="img" aria-label={`${series.label}趋势图`}>
        {[0.25, .5, .75].map((ratio) => <line key={ratio} x1={pad} x2={width-pad} y1={height*ratio} y2={height*ratio} stroke="#edf0ea" strokeWidth="1" />)}
        <path d={path} fill="none" stroke="#78be20" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
        {coords.map((point) => <g key={point.period}><circle cx={point.x} cy={point.y} r="5" fill="#fff" stroke="#5f9f16" strokeWidth="3" /><text x={point.x} y={height-2} textAnchor="middle" fontSize="10" fill="#788079">{point.period}</text></g>)}
      </svg>
      <div className="mt-1 flex justify-between text-xs text-[#7d847e]"><span>最低 {displayValue(min)} {series.unit}</span><span>最新 {displayValue(values.at(-1))} {series.unit}</span></div>
    </div>
  );
}

function SectionView({ section }: { section: Section }) {
  if (!section.items.length) return null;
  const keys = Array.from(new Set(section.items.flatMap((item) => Object.keys(item)))).slice(0, 7);

  if (section.kind === "timeline") return (
    <article className="rounded-2xl border border-[#dfe5da] bg-white p-6">
      <h3 className="text-lg font-semibold">{section.title}</h3><p className="mt-1 text-sm text-[#6e766f]">{section.summary}</p>
      <div className="mt-6 space-y-1">
        {section.items.map((item, index) => <div key={index} className="relative grid grid-cols-[92px_18px_1fr] gap-3 pb-6">
          <span className="pt-0.5 text-xs text-[#788079]">{displayValue(item.date ?? item.period)}</span>
          <div className="relative"><span className="absolute left-1/2 top-1.5 z-10 h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-[#78be20] ring-4 ring-[#eef6e4]" />{index < section.items.length-1 && <span className="absolute left-1/2 top-3 h-[calc(100%+12px)] w-px bg-[#dce8cf]" />}</div>
          <div><p className="font-medium">{displayValue(item.title ?? item.label ?? item.event)}</p><p className="mt-1 text-sm leading-6 text-[#69716a]">{displayValue(item.detail ?? item.description ?? item.impact)}</p></div>
        </div>)}
      </div>
    </article>
  );

  if (section.kind === "distribution" || section.kind === "radar") return (
    <article className="rounded-2xl border border-[#dfe5da] bg-white p-6">
      <h3 className="text-lg font-semibold">{section.title}</h3><p className="mt-1 text-sm text-[#6e766f]">{section.summary}</p>
      <div className="mt-6 space-y-4">{section.items.map((item, index) => {
        const label = displayValue(item.label ?? item.source ?? item.category ?? item.name ?? `维度 ${index+1}`);
        const raw = Number(item.ratio ?? item.score ?? item.value ?? item.count ?? 0); const percent = raw <= 1 ? raw * 100 : Math.min(raw, 100);
        return <div key={index}><div className="mb-1.5 flex justify-between text-sm"><span>{label}</span><span className="font-semibold">{displayValue(item.ratio ?? item.score ?? item.value ?? item.count)}</span></div><div className="h-2.5 rounded-full bg-[#edf0ea]"><div className="h-full rounded-full bg-[#78be20]" style={{ width: `${Math.max(3, percent)}%` }} /></div></div>;
      })}</div>
    </article>
  );

  if (section.kind === "table") return (
    <article className="overflow-hidden rounded-2xl border border-[#dfe5da] bg-white">
      <div className="p-6"><h3 className="text-lg font-semibold">{section.title}</h3><p className="mt-1 text-sm text-[#6e766f]">{section.summary}</p></div>
      <div className="overflow-x-auto"><table className="w-full min-w-[620px] border-collapse text-sm"><thead className="bg-[#f5f7f2] text-left text-xs text-[#6b746c]"><tr>{keys.map((key) => <th key={key} className="border-y border-[#e4e8e0] px-5 py-3 font-medium">{LABELS[key] ?? key}</th>)}</tr></thead><tbody>{section.items.map((item,index)=><tr key={index} className="border-b border-[#edf0ea] last:border-0">{keys.map((key)=><td key={key} className="px-5 py-3.5">{displayValue(item[key])}</td>)}</tr>)}</tbody></table></div>
    </article>
  );

  return (
    <article className="rounded-2xl border border-[#dfe5da] bg-white p-6">
      <h3 className="text-lg font-semibold">{section.title}</h3><p className="mt-1 text-sm text-[#6e766f]">{section.summary}</p>
      <div className="mt-5 grid gap-3 md:grid-cols-2">{section.items.map((item,index)=><div key={index} className="rounded-xl border border-[#e4e9df] bg-[#fafbf8] p-4">{keys.map((key)=><div key={key} className="mb-2 last:mb-0"><span className="mr-2 text-xs text-[#7a827b]">{LABELS[key] ?? key}</span><span className="text-sm font-medium">{displayValue(item[key])}</span></div>)}</div>)}</div>
    </article>
  );
}

function ReportPanel({ companyId, companyName, previews, current, onStatus }: { companyId: string; companyName: string; previews: Partial<Record<Category, Preview>>; current: Preview | null; onStatus: (message: string) => void }) {
  const router = useRouter();
  const [selected, setSelected] = useState<Set<Category>>(new Set(CATEGORIES));
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ current: number; total: number; label: string } | null>(null);

  async function savePreview(preview: Preview) {
    const response = await fetch("/api/v1/analysis-reports", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ company_id: companyId, report_type: preview.report_type, title: `${companyName}｜${CATEGORY_CONFIG[preview.category].label}`, summary: preview.summary, snapshot: { analysis: preview, evidence: preview.sources, data_quality: preview.data_quality }, model_name: "structured-agent-v1" }) });
    const payload = await response.json(); if (!response.ok) throw new Error(payload.detail ?? "报告保存失败"); return payload;
  }
  async function saveCurrent() {
    if (!current) return; setBusy(true);
    try {
      const report = await savePreview(current);
      onStatus(`${CATEGORY_CONFIG[current.category].short}报告已落库，正在打开`);
      router.push(`/reports/${report.id}?companyId=${companyId}&fromTab=${current.category}`);
    } catch (reason) { onStatus(reason instanceof Error ? reason.message : "报告保存失败"); } finally { setBusy(false); }
  }
  async function compose() {
    const categories = Array.from(selected);
    const total = categories.length + 1;
    setBusy(true); setProgress({ current: 0, total, label: "准备生成任务" });
    try {
      for (let index = 0; index < categories.length; index += 1) {
        const category = categories[index];
        setProgress({ current: index, total, label: `正在整理${CATEGORY_CONFIG[category].short}报告` });
        let preview = previews[category];
        if (!preview) { const response = await fetch(`/api/v1/agent-analysis/preview?company_id=${companyId}&category=${category}`, { cache: "no-store" }); preview = await response.json(); if (!response.ok) throw new Error(`无法加载${CATEGORY_CONFIG[category].short}`); }
        if (!preview) throw new Error(`无法加载${CATEGORY_CONFIG[category].short}`);
        await savePreview(preview);
        setProgress({ current: index + 1, total, label: `${CATEGORY_CONFIG[category].short}报告已保存` });
      }
      setProgress({ current: categories.length, total, label: "正在组装综合风险报告" });
      const response = await fetch("/api/v1/analysis-reports/compose", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ company_id: companyId, report_types: Array.from(selected).map((category) => CATEGORY_CONFIG[category].report), title: `${companyName}综合风险分析报告` }) });
      const payload = await response.json(); if (!response.ok) throw new Error(payload.detail ?? "综合报告生成失败");
      setProgress({ current: total, total, label: "综合报告已生成，正在打开" });
      onStatus(`综合报告已生成，包含 ${selected.size} 个 Agent 的证据快照`);
      router.push(`/reports/${payload.id}?companyId=${companyId}&fromTab=${current?.category ?? "macro"}`);
    } catch (reason) {
      onStatus(reason instanceof Error ? reason.message : "综合报告生成失败");
      setProgress(null);
    } finally { setBusy(false); }
  }

  return <aside className="rounded-2xl border border-[#dfe5da] bg-white p-5">
    <div className="flex items-center gap-2"><FileText size={18} className="text-[#629e1c]" /><h3 className="font-semibold">分析报告</h3></div>
    <p className="mt-2 text-xs leading-5 text-[#737a74]">选择要纳入综合报告的 Agent。报告会保存当前指标、时间线、来源和数据质量快照。</p>
    <div className="mt-4 space-y-2">{CATEGORIES.map((category)=><label key={category} className="flex cursor-pointer items-center gap-2 rounded-lg bg-[#f7f9f4] px-3 py-2 text-xs"><input type="checkbox" checked={selected.has(category)} onChange={() => setSelected((before) => { const next = new Set(before); next.has(category) ? next.delete(category) : next.add(category); return next; })} className="accent-[#78be20]" />{CATEGORY_CONFIG[category].short}</label>)}</div>
    {progress && <div className="mt-4 rounded-xl border border-[#d8e7c7] bg-[#f3f8ed] p-3" aria-live="polite">
      <div className="flex items-center justify-between gap-2 text-xs"><span className="font-medium text-[#4f771f]">{progress.label}</span><span className="text-[#788275]">{progress.current}/{progress.total}</span></div>
      <progress value={progress.current} max={progress.total} className="mt-2 h-2 w-full accent-[#78be20]" />
    </div>}
    <button onClick={saveCurrent} disabled={busy || !current} className="mt-4 w-full rounded-xl border border-[#b9d593] px-3 py-2.5 text-sm font-semibold text-[#467419] disabled:opacity-50">保存当前 Agent</button>
    <button onClick={compose} disabled={busy || !selected.size} className="mt-2 flex w-full items-center justify-center gap-2 rounded-xl bg-[#78be20] px-3 py-2.5 text-sm font-semibold text-[#13220b] active:scale-[.98] disabled:opacity-50">{busy && <Loader2 size={15} className="animate-spin" />}{busy ? "正在生成" : "生成综合报告"}</button>
    <button onClick={() => router.push(`/reports?companyId=${companyId}`)} disabled={busy} className="mt-2 w-full rounded-xl px-3 py-2.5 text-sm font-medium text-[#687268] hover:bg-[#f5f7f2] disabled:opacity-50">查看历史报告</button>
  </aside>;
}

export default function AnalysisPlatform({ companyId, initialCategory, initialRunId }: { companyId: string; initialCategory?: string; initialRunId?: string }) {
  const router = useRouter();
  const [showMonitoring, setShowMonitoring] = useState(initialCategory === "monitoring" || (!isCategory(initialCategory) && initialCategory !== "profile"));
  const [showProfile, setShowProfile] = useState(initialCategory === "profile");
  const [profileWorkerEnabled, setProfileWorkerEnabled] = useState(false);
  const [category, setCategory] = useState<Category>(isCategory(initialCategory) ? initialCategory : "macro");
  const [company, setCompany] = useState<Company | null>(null);
  const [previews, setPreviews] = useState<Partial<Record<Category, Preview>>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refreshRun, setRefreshRun] = useState<IngestionRun | null>(null);
  const [authoritativeRefreshing, setAuthoritativeRefreshing] = useState(false);
  const [reviewItems, setReviewItems] = useState<ReviewItem[]>([]);
  const preview = previews[category] ?? null;

  async function load(categoryToLoad: Category, force = false) {
    if (!force && previews[categoryToLoad]) return;
    setLoading(true); setError("");
    try {
      const response = await fetch(`/api/v1/agent-analysis/preview?company_id=${companyId}&category=${categoryToLoad}`, { cache: "no-store" });
      const payload = await response.json(); if (!response.ok) throw new Error(payload.detail ?? "Agent 分析加载失败");
      setPreviews((before) => ({ ...before, [categoryToLoad]: payload }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Agent 分析加载失败"); } finally { setLoading(false); }
  }
  async function loadCompany() {
    const [companyResponse, settingsResponse] = await Promise.all([
      fetch(`/api/v1/companies/${companyId}`, { cache: "no-store" }),
      fetch("/api/v1/settings/status", { cache: "no-store" }),
    ]);
    const payload = await companyResponse.json();
    const settings = await settingsResponse.json();
    if (!companyResponse.ok) throw new Error(payload.detail ?? "企业档案加载失败");
    setCompany(payload);
    if (settingsResponse.ok) {
      setProfileWorkerEnabled(Boolean(settings.company_profile_worker_enabled));
    }
  }
  async function loadReviewQueue() {
    const response = await fetch(`/api/v1/authoritative-ingestion/review-queue?company_id=${companyId}&review_status=pending&limit=100`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail ?? "人工核验队列加载失败");
    setReviewItems(Array.isArray(payload) ? payload : []);
  }
  async function refreshMacro() {
    if (refreshRun && ["queued", "running"].includes(refreshRun.status)) return;
    setError("");
    try {
      const response = await fetch(`/api/v1/companies/${companyId}/macro-industry/refresh`, { method: "POST" });
      let run: IngestionRun = await response.json();
      if (!response.ok) throw new Error((run as unknown as { detail?: string }).detail ?? "无法启动宏观与行业数据刷新");
      setRefreshRun(run);
      for (let attempt = 0; attempt < 180 && ["queued", "running"].includes(run.status); attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        const statusResponse = await fetch(`/api/v1/ingestion-runs/${run.id}`, { cache: "no-store" });
        run = await statusResponse.json();
        if (!statusResponse.ok) throw new Error((run as unknown as { detail?: string }).detail ?? "刷新进度读取失败");
        setRefreshRun(run);
      }
      if (run.status === "failed") throw new Error(run.message || "宏观与行业数据刷新失败");
      if (["completed", "partial"].includes(run.status)) {
        await Promise.all([load("macro", true), loadCompany()]);
        setNotice(run.status === "partial" ? `${run.message}，页面已展示可用结果` : run.message);
      } else {
        throw new Error("刷新任务等待超时，可稍后重新进入页面查看结果");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "宏观与行业数据刷新失败");
    }
  }
  async function refreshAuthoritative() {
    if (authoritativeRefreshing) return;
    setAuthoritativeRefreshing(true); setError("");
    try {
      const response = await fetch(`/api/v1/authoritative-ingestion/companies/${companyId}/run?max_documents=60`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "权威数据源刷新失败");
      await Promise.all([load(category, true), loadReviewQueue()]);
      setNotice(`权威数据源刷新完成：核验 ${payload.document_count ?? 0} 份材料，新增或更新 ${payload.ingested_event_count ?? 0} 条跨维度证据，${payload.manual_review_count ?? 0} 项进入人工核验`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "权威数据源刷新失败");
    } finally { setAuthoritativeRefreshing(false); }
  }
  useEffect(() => { void loadCompany().catch((reason) => setError(reason.message)); }, [companyId]);
  useEffect(() => { void loadReviewQueue().catch((reason) => setError(reason.message)); }, [companyId]);
  useEffect(() => { if (!showProfile && !showMonitoring) void load(category); }, [category, showProfile, showMonitoring]);
  useEffect(() => { if (!notice) return; const timer = window.setTimeout(() => setNotice(""), 5000); return () => window.clearTimeout(timer); }, [notice]);
  const qualityLabel = useMemo(() => preview?.data_quality.status === "complete" ? "数据较完整" : preview?.data_quality.status === "partial" ? "部分数据可用" : "数据待补充", [preview]);

  function changeCategory(next: Category) { setShowMonitoring(false); setShowProfile(false); setCategory(next); router.replace(`/analysis/${companyId}?tab=${next}`, { scroll: false }); }
  function openMonitoring() { setShowMonitoring(true); setShowProfile(false); router.replace(`/analysis/${companyId}?tab=monitoring${initialRunId ? `&runId=${initialRunId}` : ""}`, { scroll: false }); }
  function openProfile() { setShowMonitoring(false); setShowProfile(true); router.replace(`/analysis/${companyId}?tab=profile`, { scroll: false }); }
  async function logout() { await fetch("/api/v1/auth/logout", { method: "POST" }); window.location.href = "/login"; }

  return <div className="min-h-screen lg:grid lg:grid-cols-[286px_1fr]">
    <aside className="bg-[#06110a] px-4 py-5 text-white lg:fixed lg:inset-y-0 lg:w-[286px] lg:overflow-y-auto">
      <button onClick={() => router.push("/")} className="flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left"><div className="grid h-11 w-11 place-items-center rounded-xl bg-white text-xl font-black text-[#172019]">D.</div><div><p className="text-[10px] font-semibold tracking-[.24em] text-[#86c82d]">RISK INTELLIGENCE</p><p className="font-semibold">D.Risk AI</p></div></button>
      <div className="my-5 border-l-2 border-[#78be20] pl-4 text-xs leading-5 text-white/55">企业投后风险监测与月报</div>
      <p className="px-2 py-3 text-[11px] font-semibold tracking-[.14em] text-[#78be20]">投后管理</p>
      <button onClick={openMonitoring} className={`mb-3 flex w-full items-center gap-3 rounded-xl border px-3 py-3 text-left transition ${showMonitoring ? "border-[#5f941c] bg-[#14240e] text-white" : "border-transparent text-white/65 hover:bg-white/5"}`}><span className={`grid h-9 w-9 place-items-center rounded-lg ${showMonitoring ? "bg-[#78be20] text-[#102006]" : "bg-white/[.07] text-[#78be20]"}`}><ClipboardCheck size={18} /></span><span><b className="block text-sm font-medium">投后监测</b><small className="text-[11px] text-white/40">风险结论 · 补充材料 · 月报</small></span></button>
      {profileWorkerEnabled && <><p className="px-2 py-3 text-[11px] font-semibold tracking-[.14em] text-[#78be20]">企业信息</p><button onClick={openProfile} className={`mb-3 flex w-full items-center gap-3 rounded-xl border px-3 py-3 text-left transition ${showProfile ? "border-[#5f941c] bg-[#14240e] text-white" : "border-transparent text-white/65 hover:bg-white/5"}`}><span className={`grid h-9 w-9 place-items-center rounded-lg ${showProfile ? "bg-[#78be20] text-[#102006]" : "bg-white/[.07] text-[#78be20]"}`}><Building2 size={18} /></span><span><b className="block text-sm font-medium">企业全景</b><small className="text-[11px] text-white/40">企业预警通八模块</small></span></button></>}
      <p className="px-2 py-3 text-[11px] font-semibold tracking-[.14em] text-[#78be20]">专项分析</p>
      <nav className="space-y-2"><button onClick={()=>router.push("/")} className="mb-3 flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left text-sm text-white/65 hover:bg-white/5"><Search size={18} />重新检索企业</button>{CATEGORIES.map((item)=>{const config=CATEGORY_CONFIG[item]; const Icon=config.icon; const active=!showMonitoring && !showProfile && item===category; return <button key={item} onClick={()=>changeCategory(item)} className={`flex w-full items-center gap-3 rounded-xl border px-3 py-3 text-left transition ${active ? "border-[#5f941c] bg-[#14240e] text-white" : "border-transparent text-white/65 hover:bg-white/5"}`}><span className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg ${active ? "bg-[#78be20] text-[#102006]" : "bg-white/[.07] text-[#78be20]"}`}><Icon size={18} /></span><span><b className="block text-sm font-medium">{config.label}</b><small className="text-[11px] text-white/40">{config.description}</small></span></button>})}</nav>
      <button onClick={() => router.push(`/reports?companyId=${companyId}`)} className="mt-5 flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left text-sm text-white/65 hover:bg-white/5"><FileText size={18} className="text-[#78be20]" />历史分析报告</button>
      <p className="mt-3 px-2 py-3 text-[11px] font-semibold tracking-[.14em] text-[#78be20]">知识问答</p><div className="flex items-center gap-3 rounded-xl px-3 py-3 text-sm text-white/55"><BookOpen size={18} className="text-[#78be20]" />企业知识库</div>
      <button onClick={() => void logout()} className="mt-4 flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left text-sm text-white/55 hover:bg-white/5"><LogOut size={18} className="text-[#78be20]" />退出内部账号</button>
    </aside>

    <main className="min-w-0 lg:col-start-2">
      <header className="sticky top-0 z-30 border-b border-[#dfe5da] bg-[#f6f8f3]/95 px-5 py-4 backdrop-blur md:px-8"><div className="mx-auto flex max-w-[1500px] items-center justify-between gap-4"><div className="min-w-0"><div className="flex items-center gap-2 text-xs font-semibold text-[#5e8f27]"><Building2 size={15} />{showMonitoring ? "投后监测" : showProfile ? "企业全景" : CATEGORY_CONFIG[category].short}</div><h1 className="mt-1 truncate text-xl font-semibold">{company?.name ?? preview?.company_name ?? "企业分析加载中"}</h1></div><div className="hidden items-center gap-3 md:flex"><span className="rounded-full border border-[#cfdbc3] bg-white px-3 py-1.5 text-xs text-[#687168]">{company?.industry || "行业待识别"} · {company?.region || "地区待识别"}</span>{!showMonitoring && !showProfile && <button onClick={()=>category === "macro" ? void refreshMacro() : void refreshAuthoritative()} disabled={authoritativeRefreshing || Boolean(refreshRun && ["queued", "running"].includes(refreshRun.status))} className="grid h-9 w-9 place-items-center rounded-full border border-[#d8ded3] bg-white disabled:opacity-50" title={category === "macro" ? "刷新宏观与行业数据" : "刷新权威数据源并重新分析"}>{(authoritativeRefreshing && category !== "macro") || (refreshRun && ["queued", "running"].includes(refreshRun.status) && category === "macro") ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}</button>}</div></div></header>

      <div className="mx-auto max-w-[1500px] px-5 py-7 md:px-8">
        {notice && <div className="mb-5 flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800"><CheckCircle2 size={17} />{notice}</div>}
        {error && <div className="mb-5 flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"><ShieldAlert size={17} />{error}</div>}
        {showMonitoring ? <MonitoringWorkspace companyId={companyId} companyName={company?.name ?? "目标公司"} initialRunId={initialRunId} onStatus={setNotice} /> : showProfile ? <CompanyProfilePanel companyId={companyId} companyName={company?.name ?? "上海携程金融信息服务有限公司"} onStatus={setNotice} /> : <>
        {category === "macro" && refreshRun && ["queued", "running"].includes(refreshRun.status) && <section className="mb-5 rounded-2xl border border-[#cfe3b7] bg-[#f3f8ed] px-5 py-4" aria-live="polite"><div className="flex items-center justify-between gap-3 text-sm"><span className="flex items-center gap-2 font-semibold text-[#4f771f]"><Loader2 size={16} className="animate-spin" />{refreshRun.message || "正在刷新宏观与行业数据"}</span><span className="text-xs text-[#6f7c69]">{refreshRun.progress_current}/{refreshRun.progress_total}</span></div><progress value={refreshRun.progress_current} max={refreshRun.progress_total} className="mt-3 h-2 w-full accent-[#78be20]" /></section>}
        {loading && !preview ? <div className="space-y-5"><div className="loading-bar h-36 rounded-2xl bg-[#e7ece2]" /><div className="grid gap-4 md:grid-cols-3">{[1,2,3].map((x)=><div key={x} className="loading-bar h-28 rounded-2xl bg-[#e7ece2]" />)}</div></div> : preview && <>
          <section className="rounded-3xl border border-[#dfe5da] bg-white p-6 md:p-8"><div className="flex flex-col justify-between gap-5 md:flex-row md:items-start"><div className="max-w-4xl"><div className="mb-3 flex flex-wrap items-center gap-2"><span className="inline-flex items-center gap-2 rounded-full bg-[#eef7e2] px-3 py-1.5 text-xs font-semibold text-[#4c7e18]"><Sparkles size={14} />{CATEGORY_CONFIG[category].label} Agent</span><span className="rounded-full border border-[#dce7d2] bg-white px-3 py-1.5 text-xs text-[#63715e]">{TEMPLATE_LABELS[preview.industry_template] ?? preview.industry_template}</span></div><h2 className="text-2xl font-semibold leading-tight md:text-3xl">{preview.summary}</h2><p className="mt-4 text-sm leading-7 text-[#69716a]">检索阶段：{preview.retrieval_stage} · 生成时间：{dateLabel(preview.generated_at)}</p></div><div className="min-w-[190px] rounded-2xl bg-[#f5f8f1] p-4"><p className="text-xs text-[#747d75]">证据覆盖率</p><p className="mt-1 text-3xl font-semibold text-[#4f8619]">{preview.data_quality.coverage_percent}%</p><p className="mt-1 text-xs text-[#747d75]">{qualityLabel} · {preview.data_quality.evidence_count} 条证据</p><p className="mt-2 text-[10px] leading-4 text-[#899087]">完整度40% · 时效20% · 权威25% · 交叉验证15%</p></div></div></section>

          {(preview.data_quality.boundary_summary || preview.data_quality.warnings.length > 0) && <section className="mt-5 rounded-2xl border border-amber-200 bg-amber-50 px-5 py-4"><div className="flex flex-col justify-between gap-3 md:flex-row md:items-start"><div><h3 className="flex items-center gap-2 text-sm font-semibold text-amber-800"><ShieldAlert size={17} />数据边界</h3>{preview.data_quality.boundary_summary && <p className="mt-2 text-sm leading-6 text-amber-900">{preview.data_quality.boundary_summary}</p>}<ul className="mt-2 space-y-1 text-sm leading-6 text-amber-800/85">{preview.data_quality.warnings.map((warning)=><li key={warning}>· {warning}</li>)}</ul></div>{preview.data_quality.components && <div className="grid shrink-0 grid-cols-2 gap-2 text-xs sm:grid-cols-4 md:grid-cols-2"><span className="rounded-lg bg-white/70 px-2.5 py-2">完整度 <b>{preview.data_quality.components.completeness}%</b></span><span className="rounded-lg bg-white/70 px-2.5 py-2">时效性 <b>{preview.data_quality.components.freshness}%</b></span><span className="rounded-lg bg-white/70 px-2.5 py-2">权威性 <b>{preview.data_quality.components.authority}%</b></span><span className="rounded-lg bg-white/70 px-2.5 py-2">交叉验证 <b>{preview.data_quality.components.corroboration}%</b></span></div>}</div></section>}

          {preview.source_coverage && preview.source_coverage.length > 0 && <section className="mt-5 rounded-2xl border border-[#dfe5da] bg-white px-5 py-4"><div className="flex flex-wrap items-center gap-2"><span className="mr-2 text-xs font-semibold text-[#626c62]">数据源状态</span>{preview.source_coverage.map((source)=>{ const label = source.status === "success" ? source.points ? `已入库 ${source.points} 条` : "已连接" : source.status === "no_hit" ? "已检索无结果" : source.status === "failed" ? "抓取失败" : source.status === "partial" ? "部分可用" : "未接入"; return <span key={source.code ?? source.source} title={source.message || undefined} className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${source.status === "success" ? "bg-emerald-50 text-emerald-700" : source.status === "failed" ? "bg-red-50 text-red-700" : source.status === "no_hit" ? "bg-slate-100 text-slate-600" : "bg-amber-50 text-amber-700"}`}>{source.source} · {label}</span>;})}</div><p className="mt-3 text-[11px] leading-5 text-[#7b837b]">“已检索无结果”表示连接器成功执行但未命中；“未接入”表示尚不能据此判断企业没有相关风险。</p></section>}

          <section className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{preview.metrics.map((metric)=><article key={metric.key} className="rounded-2xl border border-[#dfe5da] bg-white p-5"><div className="flex items-start justify-between"><p className="text-sm font-medium text-[#687168]">{metric.label}</p><Activity size={16} className="text-[#78be20]" /></div><p className="mt-4 text-3xl font-semibold tracking-tight">{metric.value}<span className="ml-1 text-base font-medium text-[#667066]">{metric.unit}</span></p>{(metric.description || metric.delta) && <p className={`mt-3 inline-block rounded-lg px-2.5 py-1 text-xs ${toneClass(metric.tone)}`}>{metric.delta || metric.description}</p>}</article>)}</section>

          {preview.data_quality.metric_coverage && preview.data_quality.metric_coverage.length > 0 && <section className="mt-5 rounded-2xl border border-[#dfe5da] bg-white p-5"><div className="flex items-center justify-between"><h3 className="font-semibold">指标覆盖明细</h3><span className="text-[11px] text-[#7b837b]">按 Excel 分析维度逐项核验</span></div><div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{preview.data_quality.metric_coverage.map((item)=>{ const status = COVERAGE_STATUS[item.status]; return <div key={item.key} className="rounded-xl border border-[#e5e9e1] px-3.5 py-3"><div className="flex items-start justify-between gap-2"><span className="text-sm font-medium">{item.label}</span><span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${status.className}`}>{status.label}</span></div>{item.reason && <p className="mt-2 text-[11px] leading-5 text-[#7a827b]">{item.reason}</p>}<p className="mt-1 text-[10px] text-[#9a9f99]">数据日期：{dateLabel(item.as_of)}</p></div>;})}</div></section>}

          <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_310px]"><div className="min-w-0 space-y-5">
            {preview.series.length > 0 && <section className="grid gap-5 2xl:grid-cols-2">{preview.series.map((series)=><MiniChart key={series.key} series={series} />)}</section>}
            {preview.sections.map((section)=><SectionView key={section.key} section={section} />)}
            <section className="grid gap-5 lg:grid-cols-2"><article className="rounded-2xl border border-[#dfe5da] bg-white p-6"><h3 className="font-semibold">Agent 关键判断</h3><ul className="mt-4 space-y-3">{preview.key_points.map((point,index)=><li key={index} className="flex gap-3 text-sm leading-6 text-[#59615a]"><ChevronRight size={16} className="mt-1 shrink-0 text-[#78be20]" />{point}</li>)}</ul></article><article className="rounded-2xl border border-[#dfe5da] bg-white p-6"><h3 className="font-semibold">建议后续动作</h3><ol className="mt-4 space-y-3">{preview.next_actions.map((action,index)=><li key={index} className="flex gap-3 text-sm leading-6 text-[#59615a]"><span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-[#eef6e4] text-xs font-semibold text-[#5c931d]">{index+1}</span>{action}</li>)}</ol></article></section>
          </div><aside className="space-y-5">
            {reviewItems.length > 0 && <section className="rounded-2xl border border-amber-200 bg-amber-50 p-5"><div className="flex items-center justify-between"><h3 className="flex items-center gap-2 font-semibold text-amber-900"><ShieldAlert size={17} />人工核验队列</h3><span className="rounded-full bg-white/80 px-2 py-1 text-[11px] font-semibold text-amber-800">{reviewItems.length} 项</span></div><p className="mt-2 text-[11px] leading-5 text-amber-800/80">受验证码、登录或授权限制的数据源不会被视为“零风险”，已转为原文核验任务。</p><div className="mt-4 space-y-2">{reviewItems.slice(0, 6).map((item)=><a key={item.id} href={item.query_url || undefined} target={item.query_url ? "_blank" : undefined} rel="noreferrer" className="block rounded-xl border border-amber-200 bg-white/80 p-3 hover:border-amber-400"><div className="flex items-start justify-between gap-2"><span className="text-xs font-semibold text-amber-900">{item.source_name}</span><span className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-800">{REVIEW_CATEGORY_LABELS[item.category] ?? item.category}</span></div><p className="mt-1.5 line-clamp-2 text-[11px] leading-5 text-amber-900/70">{item.reason}</p></a>)}</div>{reviewItems.length > 6 && <p className="mt-3 text-center text-[11px] text-amber-800">另有 {reviewItems.length - 6} 项待核验</p>}</section>}
            <section className="rounded-2xl border border-[#dfe5da] bg-white p-5"><div className="flex items-center justify-between"><h3 className="font-semibold">证据来源</h3><span className="rounded-full bg-[#edf6e3] px-2 py-1 text-[11px] font-semibold text-[#588c20]">{preview.sources.length} 条</span></div><div className="mt-4 space-y-3">{preview.sources.map((source,index)=><a key={`${source.title}-${index}`} href={source.source_url || undefined} target={source.source_url ? "_blank" : undefined} rel="noreferrer" className="block rounded-xl border border-[#e3e8df] p-3 transition hover:border-[#9ec574]"><div className="flex items-start justify-between gap-2"><span className="rounded bg-[#f0f6e8] px-1.5 py-0.5 text-[10px] font-semibold text-[#5e8c2e]">{source.source_tag || "公开来源"}</span><span className="text-[10px] text-[#8a918a]">{dateLabel(source.published_at)}</span></div><p className="mt-2 line-clamp-3 text-xs font-medium leading-5">{source.title}</p><p className="mt-2 text-[10px] text-[#858c85]">{source.source_name}{source.page_hint ? ` · ${source.page_hint}` : ""}</p></a>)}</div></section><ReportPanel companyId={companyId} companyName={company?.name ?? preview.company_name} previews={previews} current={preview} onStatus={setNotice} /></aside></div>
        </>}</>}
      </div>
    </main>
  </div>;
}
