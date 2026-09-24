"use client";

import { ChangeEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Check, CheckCircle2, FileSearch,
  FileText, Loader2, Network, Play, RefreshCw, ShieldAlert, Upload,
  X, XCircle,
} from "lucide-react";

type AnalysisRun = {
  id: string; company_id: string; status: string; requested_period: string;
  current_stage: string; progress_current: number; progress_total: number;
  source_status: Record<string, { status?: string; message?: string; inserted_count?: number }>;
  summary: { rule_count?: number; rule_status_counts?: Record<string, number> };
  error_message?: string; created_at: string;
};
type Evaluation = {
  id: string; category: string;
  status: "hit" | "not_hit" | "insufficient_data" | "not_applicable";
  severity: string; rationale: string; evidence: { id?: string; title?: string; source_name?: string }[];
  reviewer_status?: string;
};
type DocumentItem = {
  id: string; filename: string; status: string; size_bytes: number; parse_error?: string;
  parse_metadata?: { fact_count?: number; auto_confirmed_count?: number }; created_at: string;
};
type Fact = {
  id: string; metric_code: string; label: string; value_numeric?: number | null;
  value_text: string; unit: string; currency: string; period: string;
  source_locator: string; confidence: number; status: "pending" | "confirmed" | "rejected";
};
type FactEdit = Partial<Pick<Fact, "metric_code" | "label" | "value_numeric" | "value_text" | "unit" | "currency" | "period">>;
type RelatedEntity = {
  id: string; name: string; relation_type: string; status: "candidate" | "confirmed" | "rejected";
  source: string;
};
type MonthlyReport = {
  id: string; period: string; version: number; status: string; title: string;
  summary: string; model_name: string; created_at: string;
};
type SettingsStatus = { llm_configured: boolean; llm_provider: string; llm_model: string; search_configured: boolean; company_profile_worker_enabled?: boolean; demo_mode?: boolean; demo_company_name?: string; report_as_of_date?: string };

const RUN_STAGE: Record<string, string> = {
  queued: "等待开始", refresh_company_profile: "正在更新企业与财务信息",
  collect_public_sources: "正在更新公开信息", discover_related_entities: "正在整理监测范围",
  evaluate_rules: "正在形成风险结论", completed: "更新完成", failed: "更新失败",
};
const CATEGORY_ORDER = ["外部风险", "业务及经营风险", "财务风险", "公司治理风险", "内控风险", "法律风险", "品牌舆情风险", "其他风险"];
const CATEGORY_ACTIONS: Record<string, string> = {
  外部风险: "跟踪政策与行业变化，评估对经营和盈利的传导影响。",
  业务及经营风险: "核验客户、业务投放和回款变化，明确经营改善措施。",
  财务风险: "补充最新财务数据，持续跟踪盈利、现金流和偿债安排。",
  公司治理风险: "确认股权、高管及重大决策变化，完善治理事项留痕。",
  内控风险: "核验关键流程、职责分离和整改闭环情况。",
  法律风险: "跟进案件、处罚和执行进展，评估损失及回收影响。",
  品牌舆情风险: "关注负面信息传播及消费者反馈，准备回应与处置方案。",
  其他风险: "补充专项材料并确认风险责任人与跟踪时点。",
};
const SOURCE_LABELS: Record<string, string> = {
  qyyjt_company_profile: "企业预警通八模块",
  akshare: "公开财务数据",
  qichacha_api: "企业工商信息",
  public_connectors: "公司公告与新闻",
  authoritative_sources: "监管与权威来源",
  confirmed_related_entities: "关联主体信息",
};
const SOURCE_STATUS_LABELS: Record<string, string> = {
  success: "已更新", partial: "部分更新", failed: "更新失败",
  not_connected: "待补充", no_hit: "本期无新增",
};
const MANUAL_METRICS = [
  ["revenue", "营业收入"], ["net_profit", "净利润"], ["total_assets", "总资产"],
  ["net_assets", "净资产"], ["cash", "货币资金"], ["receivables", "应收款项"],
  ["operating_cashflow", "经营活动现金流量净额"], ["revenue_yoy", "营业收入同比"],
  ["net_profit_yoy", "净利润同比"], ["net_assets_yoy", "净资产同比"],
  ["operating_cashflow_yoy", "经营现金流同比"], ["top5_customer_ratio", "前五大客户占比"],
  ["top1_customer_ratio", "第一大客户占比"], ["cash_to_assets", "货币资金占总资产"],
  ["related_party_transaction_ratio", "关联交易占比"], ["valuation_yoy", "估值同比"],
  ["budget_completion", "预算完成率"], ["guarantees_to_equity", "担保余额占净资产"],
  ["litigation_amount_to_equity", "涉诉金额占净资产"], ["pledged_share_ratio", "股权质押比例"],
  ["frozen_share_ratio", "股权冻结比例"], ["employee_attrition_rate", "员工离职率"],
  ["executive_turnover_rate", "董监高离职率"], ["key_role_vacancy_months", "关键岗位缺位月数"],
  ["system_outage_days", "核心系统中断天数"], ["cash_months_to_maturity", "现金覆盖到期债务月数"],
  ["estimated_net_profit_impact", "政策变化预计净利润影响"], ["overseas_asset_impairment_expected", "境外资产减值预期"],
  ["disaster_loss_to_equity", "不可抗力损失占净资产"], ["capital_operation_amount_to_equity", "资本运作金额占净资产"],
  ["capital_operation_loss_to_equity", "资本运作损失占净资产"], ["core_revenue_yoy", "核心业务收入同比"],
  ["expense_growth_minus_revenue_growth", "费用与营收增速差"], ["related_party_fund_occupation_to_equity", "关联方资金占用占净资产"],
  ["related_party_fund_occupation_days", "关联方资金占用天数"], ["core_business_metric_yoy", "核心业务指标同比"],
  ["roe_change_pp", "ROE同比变动百分点"], ["accounting_change_profit_impact_abs", "会计变更净利润影响"],
  ["core_team_attrition_rate", "核心团队流失率"], ["legal_guarantees_to_equity", "法律担保余额占净资产"],
  ["overdue_90d_ratio_change_pp", "90天以上逾期占比变动百分点"],
  ["top_shareholder_change_pp_abs", "第一大股东持股变动百分点"],
  ["penalty_to_net_profit", "处罚金额占净利润"],
] as const;
const RATIO_METRIC_CODES = new Set([
  "cash_to_assets", "budget_completion", "estimated_net_profit_impact",
  "overseas_asset_impairment_expected", "disaster_loss_to_equity",
  "capital_operation_amount_to_equity", "capital_operation_loss_to_equity",
  "expense_growth_minus_revenue_growth", "accounting_change_profit_impact_abs",
  "penalty_to_net_profit",
]);
function currentMonth() { const now = new Date(); return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`; }
function dateTime(value?: string) { return value ? new Date(value).toLocaleString("zh-CN") : ""; }
function fileSize(bytes: number) { if (!bytes) return "手工录入"; return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`; }

export default function MonitoringWorkspace({ companyId, companyName, initialRunId, onStatus }: { companyId: string; companyName: string; initialRunId?: string; onStatus: (message: string) => void }) {
  const router = useRouter();
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [entities, setEntities] = useState<RelatedEntity[]>([]);
  const [reports, setReports] = useState<MonthlyReport[]>([]);
  const [facts, setFacts] = useState<Fact[]>([]);
  const [factEdits, setFactEdits] = useState<Record<string, FactEdit>>({});
  const [selectedDocument, setSelectedDocument] = useState<string>("");
  const [period, setPeriod] = useState(currentMonth());
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [settingsStatus, setSettingsStatus] = useState<SettingsStatus | null>(null);
  const [entityName, setEntityName] = useState("");
  const [relationType, setRelationType] = useState("subsidiary");
  const [manualMetric, setManualMetric] = useState("revenue");
  const [manualValue, setManualValue] = useState("");

  const fetchJson = useCallback(async (url: string, init?: RequestInit) => {
    const response = await fetch(url, { cache: "no-store", ...init });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail ?? "请求失败");
    return payload;
  }, []);

  const loadDocuments = useCallback(async () => setDocuments(await fetchJson(`/api/v1/companies/${companyId}/documents`)), [companyId, fetchJson]);
  const loadEntities = useCallback(async () => setEntities(await fetchJson(`/api/v1/companies/${companyId}/related-entities`)), [companyId, fetchJson]);
  const loadReports = useCallback(async () => setReports(await fetchJson(`/api/v1/companies/${companyId}/monthly-reports`)), [companyId, fetchJson]);
  const loadEvaluations = useCallback(async (runId: string) => { const payload = await fetchJson(`/api/v1/analysis-runs/${runId}/risk-summary`); setEvaluations(payload.findings || []); }, [fetchJson]);

  const loadRun = useCallback(async (runId: string) => {
    const payload: AnalysisRun = await fetchJson(`/api/v1/analysis-runs/${runId}`);
    setRun(payload);
    if (payload.requested_period) setPeriod(payload.requested_period);
    if (payload.status === "completed") await Promise.all([loadEvaluations(runId), loadEntities()]);
    return payload;
  }, [fetchJson, loadEntities, loadEvaluations]);

  useEffect(() => {
    Promise.all([loadDocuments(), loadEntities(), loadReports(), fetchJson("/api/v1/settings/status").then((settings: SettingsStatus) => { setSettingsStatus(settings); if (settings.report_as_of_date) setPeriod(settings.report_as_of_date.slice(0, 7)); })]).catch((reason) => setError(reason.message));
    if (initialRunId) void loadRun(initialRunId).catch((reason) => setError(reason.message));
    else void fetchJson(`/api/v1/companies/${companyId}/analysis-runs?limit=1`).then((items: AnalysisRun[]) => items[0] ? loadRun(items[0].id) : undefined).catch((reason) => setError(reason.message));
  }, [companyId, initialRunId, fetchJson, loadDocuments, loadEntities, loadReports, loadRun]);

  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return;
    const timer = window.setInterval(() => void loadRun(run.id).catch((reason) => setError(reason.message)), 1800);
    return () => window.clearInterval(timer);
  }, [run, loadRun]);

  const counts = useMemo(() => evaluations.reduce<Record<string, number>>((result, item) => { result[item.status] = (result[item.status] || 0) + 1; return result; }, {}), [evaluations]);
  const riskHits = useMemo(() => evaluations.filter((item) => item.status === "hit"), [evaluations]);
  const importantHits = useMemo(() => riskHits.filter((item) => ["important", "high", "critical"].includes(item.severity)), [riskHits]);
  const missingCount = counts.insufficient_data || 0;
  const coveragePercent = evaluations.length ? Math.round(((evaluations.length - missingCount) / evaluations.length) * 100) : 0;
  const categoryOverview = useMemo(() => CATEGORY_ORDER.map((category) => {
    const items = evaluations.filter((item) => item.category === category);
    const hits = items.filter((item) => item.status === "hit");
    const missing = items.filter((item) => item.status === "insufficient_data");
    return {
      category,
      status: hits.length ? "attention" : missing.length ? "missing" : items.length ? "clear" : "empty",
      count: hits.length,
      summary: hits[0]?.rationale || (missing.length ? "部分资料待补充" : items.length ? "本期暂未发现已确认异常" : "尚未形成结论"),
    };
  }), [evaluations]);
  const missingCategoryCount = categoryOverview.filter((item) => item.status === "missing").length;
  const latestReport = reports[0];
  const resultHeadline = importantHits.length ? `本月有${importantHits.length}项事项需要重点关注` : riskHits.length ? `本月识别到${riskHits.length}项风险提示` : evaluations.length ? "本月暂未发现已确认的重大风险" : "等待生成本月监测结果";
  const actionCategories = useMemo(() => Array.from(new Set(riskHits.map((item) => item.category))).slice(0, 4), [riskHits]);
  const displayActionCategories = actionCategories.length ? actionCategories : categoryOverview.filter((item) => item.status === "missing").slice(0, 4).map((item) => item.category);

  async function startAnalysis() {
    setBusy("analysis"); setError("");
    try {
      const payload = await fetchJson("/api/v1/analysis-runs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ company_name: companyName, requested_period: period, max_results_per_source: 8, max_documents: 60, lookback_days: 30 }) });
      setEvaluations([]); setRun(payload.analysis_run); onStatus("正在更新企业信息并形成本月风险结论");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "实时分析启动失败"); } finally { setBusy(""); }
  }

  async function requestCompanyProfile() {
    setBusy("company-profile"); setError("");
    try {
      const payload = await fetchJson(`/api/v1/companies/${companyId}/company-profile/runs`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ force: true }) });
      const status = payload.run?.status || (payload.snapshot ? "completed" : "queued");
      onStatus(status === "completed" ? "已复用最新企业预警通快照" : "采集任务已创建，请在 Mac 运行单次 Worker 完成上传");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法创建企业预警通采集任务"); } finally { setBusy(""); }
  }

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    setBusy("upload"); setError("");
    try {
      const body = new FormData(); body.append("file", file); if (run?.id) body.append("analysis_run_id", run.id);
      const payload = await fetchJson(`/api/v1/companies/${companyId}/documents`, { method: "POST", body });
      await loadDocuments();
      if (payload.duplicate) { onStatus("该文件已经上传过，已复用原解析结果"); return; }
      onStatus("材料已上传，正在解析字段和来源位置");
      for (let attempt = 0; attempt < 90; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1200));
        const list: DocumentItem[] = await fetchJson(`/api/v1/companies/${companyId}/documents`);
        setDocuments(list);
        const current = list.find((item) => item.id === payload.document.id);
        if (current && ["parsed", "failed"].includes(current.status)) { if (current.status === "parsed") { setSelectedDocument(current.id); setFactEdits({}); setFacts(await fetchJson(`/api/v1/documents/${current.id}/extracted-facts`)); } break; }
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "材料上传失败"); } finally { setBusy(""); }
  }

  async function openFacts(documentId: string) {
    setSelectedDocument(documentId); setFactEdits({}); setFacts(await fetchJson(`/api/v1/documents/${documentId}/extracted-facts`));
  }

  async function decideFact(fact: Fact, status: "confirmed" | "rejected") {
    if (!selectedDocument) return;
    setBusy(`fact-${fact.id}`);
    try {
      const next = await fetchJson(`/api/v1/documents/${selectedDocument}/confirm-facts`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ decisions: [{ fact_id: fact.id, status, ...(factEdits[fact.id] || {}) }] }) });
      setFacts(next); setFactEdits((before) => { const copy = { ...before }; delete copy[fact.id]; return copy; }); onStatus(status === "confirmed" ? "信息已确认，更新分析后将计入本月结论" : "识别结果已排除");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "事实确认失败"); } finally { setBusy(""); }
  }

  async function decideEntity(entity: RelatedEntity, status: "confirmed" | "rejected") {
    setBusy(`entity-${entity.id}`);
    try {
      const next = await fetchJson(`/api/v1/companies/${companyId}/related-entities`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ entities: [{ id: entity.id, name: entity.name, relation_type: entity.relation_type, status }] }) });
      setEntities(next); onStatus(status === "confirmed" ? "已纳入关联监测清单" : "已排除该关联候选");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "关联主体更新失败"); } finally { setBusy(""); }
  }

  async function addEntity() {
    if (!entityName.trim()) return;
    setBusy("entity-add");
    try {
      const next = await fetchJson(`/api/v1/companies/${companyId}/related-entities`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ entities: [{ name: entityName.trim(), relation_type: relationType, status: "confirmed" }] }) });
      setEntities(next); setEntityName(""); onStatus("已加入监测范围，下次更新分析时将同步纳入");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "关联主体添加失败"); } finally { setBusy(""); }
  }

  async function addManualFact() {
    if (!manualValue.trim()) return;
    const selected = MANUAL_METRICS.find(([code]) => code === manualMetric);
    const raw = manualValue.trim();
    const parsed = Number(raw.replace(/,/g, "").replace("%", ""));
    if (!Number.isFinite(parsed)) { setError("手工补录数值格式不正确"); return; }
    const isPercent = raw.includes("%") || manualMetric.endsWith("_ratio") || manualMetric.endsWith("_yoy") || manualMetric.endsWith("_to_equity") || RATIO_METRIC_CODES.has(manualMetric);
    const numericValue = isPercent && (raw.includes("%") || Math.abs(parsed) > 1) ? parsed / 100 : parsed;
    const isAmount = ["revenue", "net_profit", "total_assets", "net_assets", "cash", "receivables", "operating_cashflow"].includes(manualMetric);
    setBusy("manual-fact");
    try {
      const fact = await fetchJson(`/api/v1/companies/${companyId}/manual-facts`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ metric_code: manualMetric, label: selected?.[1] || manualMetric, value_numeric: numericValue, value_text: raw, unit: isPercent ? "%" : isAmount ? "元" : "", currency: isAmount ? "CNY" : "", period }) });
      setManualValue(""); await loadDocuments(); setSelectedDocument(fact.document_id); setFacts(await fetchJson(`/api/v1/documents/${fact.document_id}/extracted-facts`)); onStatus("财务指标已补录，更新分析后将计入本月结论");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "手工补录失败"); } finally { setBusy(""); }
  }

  async function createReport() {
    if (!run || run.status !== "completed") { setError("请先完成一次实时分析"); return; }
    setBusy("report"); setError("");
    try {
      const payload = await fetchJson("/api/v1/monthly-reports", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ company_id: companyId, period, analysis_run_id: run.id }) });
      let report: MonthlyReport = payload.report;
      for (let attempt = 0; attempt < 120 && report.status === "generating"; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1200));
        report = await fetchJson(`/api/v1/monthly-reports/${report.id}`);
      }
      await loadReports();
      if (report.status === "failed") throw new Error(report.summary || "月报生成失败");
      router.push(`/monthly-reports/${report.id}?companyId=${companyId}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "月报生成失败"); } finally { setBusy(""); }
  }

  return <div className="space-y-6">
    {error && <div className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"><ShieldAlert size={17} />{error}</div>}

    <section className="rounded-3xl border border-[#dfe5da] bg-white p-6 md:p-8">
      <div className="flex flex-col justify-between gap-6 lg:flex-row lg:items-start">
        <div className="max-w-3xl">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-[#eef7e2] px-3 py-1.5 text-xs font-semibold text-[#4c7e18]">本月投后监测</span>
            {settingsStatus?.llm_configured && <span className="rounded-full bg-emerald-50 px-3 py-1.5 text-xs text-emerald-700">智能分析已启用</span>}
          </div>
          <h2 className="mt-4 text-3xl font-semibold leading-tight tracking-tight">{resultHeadline}</h2>
          <p className="mt-3 text-sm leading-7 text-[#69716a]">{run?.status === "completed" ? "结果已更新至 " + dateTime(run.created_at) + "，可继续补充材料或生成月报。" : run ? RUN_STAGE[run.current_stage] || "正在更新分析" : "选择月份后开始分析，系统将形成风险结论和建议动作。"}</p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs font-medium text-[#69716a]">监测月份<input type="month" value={period} onChange={(event) => setPeriod(event.target.value)} className="mt-1 block rounded-xl border border-[#d7dfd2] px-3 py-2.5 text-sm text-[#172019] outline-none focus:ring-2 focus:ring-[#78be20]" /></label>
          {settingsStatus?.company_profile_worker_enabled && <button onClick={requestCompanyProfile} disabled={Boolean(busy)} className="flex items-center gap-2 whitespace-nowrap rounded-xl border border-[#b9d593] px-4 py-3 text-sm font-semibold text-[#467419] disabled:opacity-50">{busy === "company-profile" ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}预采集企业预警通</button>}
          <button onClick={startAnalysis} disabled={Boolean(busy)} className="flex items-center gap-2 whitespace-nowrap rounded-xl bg-[#78be20] px-4 py-3 text-sm font-semibold text-[#102006] active:scale-[.98] disabled:opacity-50">{busy === "analysis" ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}{run ? "更新分析" : "开始分析"}</button>
        </div>
      </div>

      {run && ["queued", "running"].includes(run.status) && <div className="mt-6 rounded-2xl bg-[#f4f8ef] p-4"><div className="flex items-center justify-between gap-3 text-sm"><span className="flex items-center gap-2 font-semibold text-[#4f771f]"><Loader2 size={16} className="animate-spin" />{RUN_STAGE[run.current_stage] || "正在更新"}</span><span className="text-xs text-[#788275]">{run.progress_current}/{run.progress_total}</span></div><progress value={run.progress_current} max={run.progress_total || 5} className="mt-3 h-2 w-full accent-[#78be20]" /></div>}

      {evaluations.length > 0 && <div className="mt-7 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <div className="border-l-2 border-red-400 pl-4"><p className="text-xs text-[#7a827a]">风险提示</p><p className="mt-1 text-3xl font-semibold">{riskHits.length}</p></div>
        <div className="border-l-2 border-amber-400 pl-4"><p className="text-xs text-[#7a827a]">重点关注</p><p className="mt-1 text-3xl font-semibold">{importantHits.length}</p></div>
        <div className="border-l-2 border-[#78be20] pl-4"><p className="text-xs text-[#7a827a]">资料覆盖</p><p className="mt-1 text-3xl font-semibold">{coveragePercent}%</p></div>
        <div className="border-l-2 border-slate-300 pl-4"><p className="text-xs text-[#7a827a]">待完善板块</p><p className="mt-1 text-3xl font-semibold">{missingCategoryCount}</p></div>
      </div>}
    </section>

    <div className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(300px,.55fr)]">
      <section className="rounded-2xl border border-[#dfe5da] bg-white p-6">
        <div className="flex items-center justify-between gap-3"><h3 className="text-lg font-semibold">本月重点关注</h3>{riskHits.length > 0 && <span className="text-xs text-[#7a827a]">按重要程度展示</span>}</div>
        {riskHits.length > 0 ? <div className="mt-4 divide-y divide-[#edf0ea]">{riskHits.slice(0, 5).map((item) => <article key={item.id} className="py-4 first:pt-1"><div className="flex items-start justify-between gap-4"><div><span className="text-xs font-semibold text-red-700">{item.category}</span><h4 className="mt-1 font-semibold">{item.evidence[0]?.title || item.category + "提示"}</h4></div>{["important", "high", "critical"].includes(item.severity) && <span className="shrink-0 rounded-full bg-red-50 px-2.5 py-1 text-[11px] font-semibold text-red-700">重点</span>}</div><p className="mt-2 text-sm leading-6 text-[#626b62]">{item.rationale === "规则阈值已触发。" ? "相关指标已达到预警条件，建议结合原始材料进一步核验。" : item.rationale}</p>{item.evidence[0]?.source_name && <p className="mt-2 text-xs text-[#8a918a]">来源：{item.evidence[0].source_name}</p>}</article>)}</div> : <div className="mt-5 rounded-xl bg-[#f5f8f1] px-5 py-8 text-center"><CheckCircle2 className="mx-auto text-[#6da522]" /><p className="mt-3 font-medium">本月暂无已确认的重大风险事项</p><p className="mt-1 text-sm text-[#747d74]">{missingCount ? "仍有部分资料待补充，结论将在更新后同步调整。" : "建议继续按月更新经营与财务信息。"}</p></div>}
      </section>

      <section className="rounded-2xl border border-[#dfe5da] bg-white p-6">
        <h3 className="text-lg font-semibold">建议动作</h3>
        <div className="mt-4 space-y-4">{displayActionCategories.map((category, index) => <div key={category} className="flex gap-3"><span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[#eef6e4] text-xs font-semibold text-[#5c931d]">{index + 1}</span><div><p className="text-sm font-medium">{category}</p><p className="mt-1 text-sm leading-6 text-[#69716a]">{CATEGORY_ACTIONS[category]}</p></div></div>)}</div>
        {!actionCategories.length && !missingCount && <p className="mt-4 text-sm leading-6 text-[#69716a]">维持常规跟踪，按月更新经营、财务和重大事项信息。</p>}
      </section>
    </div>

    <section className="rounded-2xl border border-[#dfe5da] bg-white p-6">
      <div><h3 className="text-lg font-semibold">风险板块概览</h3><p className="mt-1 text-sm text-[#747d74]">按投后管理板块汇总结论，点击更新分析后同步刷新。</p></div>
      <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">{categoryOverview.map((item) => <article key={item.category} className="rounded-xl border border-[#e4e9df] p-4"><div className="flex items-center justify-between gap-3"><h4 className="font-semibold">{item.category}</h4><span className={item.status === "attention" ? "rounded-full bg-red-50 px-2.5 py-1 text-[11px] font-semibold text-red-700" : item.status === "missing" ? "rounded-full bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-700" : item.status === "clear" ? "rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-700" : "rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600"}>{item.status === "attention" ? "需关注" : item.status === "missing" ? "待补资料" : item.status === "clear" ? "暂未发现异常" : "待分析"}</span></div><p className="mt-3 line-clamp-3 text-sm leading-6 text-[#69716a]">{item.summary}</p>{item.count > 0 && <p className="mt-3 text-xs font-medium text-red-700">{item.count}项风险提示</p>}</article>)}</div>
    </section>

    <section className="rounded-2xl border border-[#dfe5da] bg-white p-6">
      <div className="flex flex-col justify-between gap-5 md:flex-row md:items-center"><div><h3 className="text-lg font-semibold">月度投后监测报告</h3><p className="mt-1 text-sm text-[#747d74]">基于本月结论生成草稿，审阅后可导出 Excel 或打印为 PDF。</p></div><button onClick={createReport} disabled={busy === "report" || run?.status !== "completed"} className="flex items-center justify-center gap-2 whitespace-nowrap rounded-xl bg-[#78be20] px-4 py-3 text-sm font-semibold text-[#102006] active:scale-[.98] disabled:opacity-50">{busy === "report" ? <Loader2 size={16} className="animate-spin" /> : <FileText size={16} />}生成{period}月报</button></div>
      {latestReport ? <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">{reports.slice(0, 6).map((report) => <button key={report.id} onClick={() => router.push("/monthly-reports/" + report.id + "?companyId=" + companyId)} className="rounded-xl border border-[#e1e7dd] p-4 text-left transition hover:border-[#9ec574]"><div className="flex items-center justify-between gap-3"><span className="text-xs font-semibold text-[#588c20]">{report.period} · v{report.version}</span><span className="text-xs text-[#7a827a]">{report.status === "approved" ? "已审批" : report.status === "in_review" ? "审阅中" : report.status === "draft" ? "草稿" : "生成中"}</span></div><p className="mt-3 font-medium">{report.title}</p><p className="mt-2 line-clamp-2 text-xs leading-5 text-[#747d74]">{report.summary || "正在整理报告内容"}</p></button>)}</div> : <p className="mt-5 rounded-xl bg-[#f7f9f4] px-4 py-5 text-sm text-[#747d74]">完成分析后，可在此生成首份月报。</p>}
    </section>

    <div className="grid gap-4 lg:grid-cols-2">
      <details className="group rounded-2xl border border-[#dfe5da] bg-white">
        <summary className="flex cursor-pointer list-none items-center justify-between px-5 py-4"><span><b className="block">补充公司材料</b><span className="mt-1 block text-xs text-[#7a827a]">财务报表、经营台账及其他证明材料</span></span><span className="text-xs text-[#6b8f43]">{documents.length}份</span></summary>
        <div className="border-t border-[#edf0ea] p-5">
          <div className="flex flex-wrap gap-2"><label className="cursor-pointer rounded-xl border border-[#b9d593] px-3 py-2 text-sm font-semibold text-[#467419]"><input type="file" onChange={upload} disabled={Boolean(busy)} className="hidden" accept=".xlsx,.xls,.csv,.pdf,.docx,.txt,.png,.jpg,.jpeg" />{busy === "upload" ? "处理中" : "上传文件"}</label><select value={manualMetric} onChange={(event) => setManualMetric(event.target.value)} className="rounded-xl border border-[#d8dfd3] bg-white px-2 py-2 text-xs">{MANUAL_METRICS.map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select><input value={manualValue} onChange={(event) => setManualValue(event.target.value)} placeholder="手工补录数值" className="min-w-32 flex-1 rounded-xl border border-[#d8dfd3] px-3 py-2 text-sm" /><button onClick={() => void addManualFact()} disabled={!manualValue.trim() || busy === "manual-fact"} className="rounded-xl border border-[#b9d593] px-3 py-2 text-xs font-semibold text-[#467419] disabled:opacity-50">补录指标</button></div>
          <div className="mt-4 space-y-2">{documents.slice(0, 8).map((document) => <button key={document.id} onClick={() => document.status === "parsed" && void openFacts(document.id)} className={selectedDocument === document.id ? "flex w-full items-center justify-between rounded-xl border border-[#91bd5d] bg-[#f3f8ed] px-3 py-3 text-left" : "flex w-full items-center justify-between rounded-xl border border-[#e4e9df] px-3 py-3 text-left"}><span className="min-w-0"><span className="block truncate text-sm font-medium">{document.filename}</span><span className="mt-1 block text-xs text-[#808880]">{fileSize(document.size_bytes)} · {document.status === "parsed" ? "已识别" : document.status === "failed" ? "处理失败" : "处理中"}</span></span>{["queued", "parsing"].includes(document.status) ? <Loader2 size={16} className="animate-spin" /> : <FileSearch size={16} className="text-[#6da522]" />}</button>)}</div>
          {selectedDocument && facts.length > 0 && <div className="mt-5 border-t border-[#edf0ea] pt-4"><p className="text-sm font-semibold">识别结果</p><div className="mt-3 space-y-2">{facts.slice(0, 20).map((fact) => { const edit = factEdits[fact.id] || {}; return <div key={fact.id} className="grid gap-2 rounded-xl bg-[#f8faf6] p-3 md:grid-cols-[1fr_150px_110px_auto] md:items-center"><div><p className="text-sm font-medium">{fact.label}</p><p className="text-xs text-[#858d85]">{fact.metric_code} · {fact.source_locator}</p></div>{fact.status === "pending" ? <input value={edit.value_text ?? (fact.value_text || String(fact.value_numeric ?? ""))} onChange={(event) => { const raw = event.target.value; const parsed = Number(raw.replace(/,/g, "").replace("%", "")); const numeric = raw.includes("%") ? parsed / 100 : parsed; setFactEdits((before) => ({ ...before, [fact.id]: { ...before[fact.id], value_text: raw, value_numeric: Number.isFinite(numeric) ? numeric : undefined } })); }} className="rounded-lg border border-[#dfe5da] px-2 py-1.5 text-sm" /> : <span className="text-sm">{fact.value_text || fact.value_numeric}{fact.unit}</span>}{fact.status === "pending" ? <input value={edit.period ?? fact.period} onChange={(event) => setFactEdits((before) => ({ ...before, [fact.id]: { ...before[fact.id], period: event.target.value } }))} placeholder="YYYY-MM" className="rounded-lg border border-[#dfe5da] px-2 py-1.5 text-sm" /> : <span className="text-xs text-[#747d74]">{fact.period || "期间未识别"}</span>}<div>{fact.status === "pending" ? <span className="flex gap-1"><button title="确认" onClick={() => void decideFact(fact, "confirmed")} className="rounded-lg bg-emerald-50 p-2 text-emerald-700"><Check size={14} /></button><button title="排除" onClick={() => void decideFact(fact, "rejected")} className="rounded-lg bg-red-50 p-2 text-red-700"><X size={14} /></button></span> : <span className="text-xs text-[#747d74]">{fact.status === "confirmed" ? "已确认" : "已排除"}</span>}</div></div>; })}</div></div>}
        </div>
      </details>

      <details className="group rounded-2xl border border-[#dfe5da] bg-white">
        <summary className="flex cursor-pointer list-none items-center justify-between px-5 py-4"><span><b className="block">监测主体范围</b><span className="mt-1 block text-xs text-[#7a827a]">子公司、股东、重要客户和合作机构</span></span><span className="text-xs text-[#6b8f43]">{entities.filter((item) => item.status === "confirmed").length}家已确认</span></summary>
        <div className="border-t border-[#edf0ea] p-5">
          <div className="flex gap-2"><input value={entityName} onChange={(event) => setEntityName(event.target.value)} placeholder="关联主体名称" className="min-w-0 flex-1 rounded-xl border border-[#d8dfd3] px-3 py-2 text-sm" /><select value={relationType} onChange={(event) => setRelationType(event.target.value)} className="rounded-xl border border-[#d8dfd3] px-2 py-2 text-xs"><option value="subsidiary">子公司</option><option value="shareholder">股东</option><option value="customer">重要客户</option><option value="guarantor">担保机构</option><option value="partner">合作方</option><option value="other">其他</option></select><button onClick={() => void addEntity()} disabled={!entityName.trim() || busy === "entity-add"} className="rounded-xl border border-[#b9d593] px-3 py-2 text-sm font-semibold text-[#467419] disabled:opacity-50">添加</button></div>
          <div className="mt-4 space-y-2">{entities.slice(0, 12).map((entity) => <div key={entity.id} className="flex items-center justify-between gap-3 rounded-xl border border-[#e4e9df] px-3 py-3"><span className="min-w-0"><span className="block truncate text-sm font-medium">{entity.name}</span><span className="mt-1 block text-xs text-[#808880]">{entity.relation_type} · {entity.status === "confirmed" ? "已纳入" : entity.status === "candidate" ? "待确认" : "已排除"}</span></span>{entity.status === "candidate" && <span className="flex gap-1"><button title="确认纳入" onClick={() => void decideEntity(entity, "confirmed")} className="rounded-lg bg-emerald-50 p-2 text-emerald-700"><Check size={15} /></button><button title="排除" onClick={() => void decideEntity(entity, "rejected")} className="rounded-lg bg-red-50 p-2 text-red-700"><X size={15} /></button></span>}</div>)}</div>
          {!entities.length && <p className="mt-4 rounded-xl bg-[#f7f9f4] px-4 py-5 text-center text-sm text-[#7a827a]">尚未添加关联监测主体</p>}
        </div>
      </details>
    </div>

    <details className="rounded-xl border border-[#e4e9df] bg-white">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-medium text-[#687168]">数据更新说明</summary>
      <div className="border-t border-[#edf0ea] px-4 py-4 text-xs leading-6 text-[#747d74]">
        <p>企业信息、公开披露和已上传材料共同构成本次分析依据。未取得的数据不会被视为零风险。</p>
        {run && <p className="mt-2">最近更新：{dateTime(run.created_at)}。{Object.entries(run.source_status || {}).map(([key, value]) => (SOURCE_LABELS[key] || key) + "：" + (SOURCE_STATUS_LABELS[value.status || ""] || "待确认")).join("；")}</p>}
      </div>
    </details>
  </div>;
}
