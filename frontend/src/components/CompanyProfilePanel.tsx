"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Database,
  Download,
  Loader2,
  RefreshCw,
} from "lucide-react";

type ProfileSection = { title: string; columns: string[]; rows: unknown[][]; kind?: string; text?: string; emphasis?: boolean };
type ProfileModule = {
  key: string; title: string; status: string; source_url: string;
  captured_at: string; page_count: number; row_count: number; sections: ProfileSection[];
};
type ProfileRun = {
  id: string; status: string; progress_current: number; progress_total: number;
  current_module: string; error_code: string; error_message: string;
  module_statuses: Record<string, string>; created_at: string; updated_at: string;
};
type ProfileSnapshot = {
  id: string; captured_at: string; sop_version: string; excel_filename: string;
  normalized_data: { modules?: Record<string, ProfileModule> };
};
type ProfileState = {
  enabled: boolean; company_name: string; qyyjt_company_code: string;
  active_run?: ProfileRun | null; latest_run?: ProfileRun | null;
  latest_snapshot?: ProfileSnapshot | null;
};

const MODULE_ORDER = [
  "overview", "penalties", "dynamic_monitor", "business_changes",
  "executives", "shareholders", "investments", "subsidiaries",
  "judgments", "court_hearings", "case_filings", "enforcement",
];
const MODULE_LABELS: Record<string, string> = {
  overview: "企业速览", penalties: "监管处罚", dynamic_monitor: "动态监测",
  business_changes: "工商变更", executives: "高管信息", shareholders: "股东信息",
  investments: "对外投资企业", subsidiaries: "控股子公司",
  judgments: "裁判文书", court_hearings: "开庭公告", case_filings: "立案信息", enforcement: "被执行人",
};
const RUNNING = new Set(["queued", "running"]);

function dateTime(value?: string | null) {
  if (!value) return "尚未采集";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
}

function valueText(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

function statusText(run?: ProfileRun | null) {
  if (!run) return "暂无进行中的采集";
  if (run.status === "queued") return "等待 Windows 采集节点";
  if (run.status === "running") return `正在采集${run.current_module ? `：${MODULE_LABELS[run.current_module] ?? run.current_module}` : ""}`;
  if (run.status === "waiting_for_login") return "企业预警通登录已失效";
  if (run.status === "waiting_for_captcha") return "等待人工完成验证码";
  if (run.status === "completed") return "采集完成";
  if (run.status === "failed") return "本次采集失败";
  return run.status;
}

export default function CompanyProfilePanel({ companyId, companyName, onStatus }: { companyId: string; companyName: string; onStatus: (message: string) => void }) {
  const [state, setState] = useState<ProfileState | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    const response = await fetch(`/api/v1/companies/${companyId}/company-profile`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail ?? "企业全景加载失败");
    setState(payload);
    return payload as ProfileState;
  }

  async function refresh() {
    setRefreshing(true); setError("");
    try {
      const response = await fetch(`/api/v1/companies/${companyId}/company-profile/runs`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ force: true }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "无法创建企业全景采集任务");
      await load();
      onStatus("企业全景采集任务已创建，等待 Windows 采集节点领取");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法创建企业全景采集任务");
    } finally { setRefreshing(false); }
  }

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        await load();
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "企业全景加载失败");
      } finally { if (!cancelled) setLoading(false); }
    }
    void poll();
    const timer = setInterval(() => { void poll(); }, 2500);
    return () => { cancelled = true; clearInterval(timer); };
  }, [companyId]);

  const currentRun = state?.active_run ?? state?.latest_run ?? null;
  const snapshot = state?.latest_snapshot ?? null;
  const modules = useMemo(() => {
    const source = snapshot?.normalized_data?.modules ?? {};
    return MODULE_ORDER.map((key) => source[key]).filter(Boolean);
  }, [snapshot]);
  const needsAction = currentRun && ["waiting_for_login", "waiting_for_captcha", "failed"].includes(currentRun.status);

  if (loading && !state) return <div className="space-y-4"><div className="loading-bar h-40 rounded-3xl bg-[#e7ece2]" /><div className="loading-bar h-64 rounded-3xl bg-[#e7ece2]" /></div>;
  if (!state?.enabled) return <section className="rounded-3xl border border-[#dfe5da] bg-white p-8"><h2 className="text-xl font-semibold">企业全景暂未启用</h2><p className="mt-3 text-sm text-[#69716a]">轻量版目前仅为上海携程金融信息服务有限公司开放。</p></section>;

  return <div className="space-y-5">
    {error && <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>}
    <section className="rounded-3xl border border-[#dfe5da] bg-white p-6 md:p-8">
      <div className="flex flex-col justify-between gap-5 lg:flex-row lg:items-start">
        <div><div className="flex items-center gap-2 text-sm font-semibold text-[#5c8d29]"><Database size={17} />企业预警通企业全景</div><h2 className="mt-2 text-2xl font-semibold">{companyName}</h2><p className="mt-3 text-sm leading-6 text-[#6e766f]">八个核心模块完整通过后生成正式快照，司法模块随快照一并展示。最近 24 小时内重复查询直接复用。</p></div>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => void refresh()} disabled={refreshing || Boolean(currentRun && RUNNING.has(currentRun.status))} className="flex items-center gap-2 rounded-xl border border-[#b9d593] px-4 py-2.5 text-sm font-semibold text-[#467419] disabled:opacity-50">{refreshing ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}重新采集</button>
          <a aria-disabled={!snapshot} href={snapshot ? `/api/v1/companies/${companyId}/company-profile/export` : undefined} className={`flex items-center gap-2 rounded-xl bg-[#78be20] px-4 py-2.5 text-sm font-semibold text-[#13220b] ${snapshot ? "active:scale-[.98]" : "pointer-events-none opacity-40"}`}><Download size={16} />导出 Excel</a>
        </div>
      </div>
      <div className={`mt-6 rounded-2xl border p-4 ${needsAction ? "border-amber-200 bg-amber-50" : currentRun && RUNNING.has(currentRun.status) ? "border-[#cfe3b7] bg-[#f3f8ed]" : "border-[#e1e7dd] bg-[#f8faf6]"}`}>
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center"><span className="flex items-center gap-2 text-sm font-semibold">{needsAction ? <AlertTriangle size={17} className="text-amber-700" /> : currentRun && RUNNING.has(currentRun.status) ? <Loader2 size={17} className="animate-spin text-[#649d20]" /> : <CheckCircle2 size={17} className="text-[#649d20]" />}{statusText(currentRun)}</span><span className="text-xs text-[#788078]">最近完整快照：{dateTime(snapshot?.captured_at)}</span></div>
        {currentRun && RUNNING.has(currentRun.status) && <><progress value={currentRun.progress_current} max={currentRun.progress_total || 8} className="mt-3 h-2 w-full accent-[#78be20]" /><p className="mt-2 text-xs text-[#6f7c69]">{currentRun.progress_current}/{currentRun.progress_total || 8} 个模块</p></>}
        {needsAction && <div className="mt-3"><p className="text-sm leading-6 text-amber-900">{currentRun?.error_message || "请在 Windows 采集节点恢复企业预警通登录后重新采集。"}</p><button onClick={() => void refresh()} disabled={refreshing} className="mt-3 rounded-lg bg-amber-700 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">已处理，重新采集</button></div>}
      </div>
    </section>

    {snapshot && modules.length > 0 ? <section className="space-y-4">
      {modules.map((module, moduleIndex) => <details key={module.key} open={moduleIndex === 0} className="group overflow-hidden rounded-2xl border border-[#dfe5da] bg-white">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 md:px-6"><div><h3 className="font-semibold">{module.title ?? MODULE_LABELS[module.key]}</h3><p className="mt-1 text-xs text-[#788078]">{module.row_count ?? 0} 条 · {module.page_count ?? 1} 页 · {dateTime(module.captured_at)}</p></div><ChevronDown size={18} className="text-[#649d20] transition group-open:rotate-180" /></summary>
        <div className="border-t border-[#e8ece5] p-5 md:p-6">
          <div className="space-y-6">{(module.sections ?? []).map((section, sectionIndex) => section.kind === "note" ? <div key={`${section.title}-${sectionIndex}`} className={`rounded-xl px-4 py-3 text-sm leading-6 ${section.emphasis ? "bg-[#eaf6f0] font-semibold text-[#166a45]" : "border border-[#e3e8df] text-[#49534d]"}`}>{section.text ?? section.title}</div> : <div key={`${section.title}-${sectionIndex}`}><h4 className="mb-3 text-sm font-semibold text-[#436d1d]">{section.title}</h4><div className="overflow-x-auto rounded-xl border border-[#e3e8df]"><table className="w-full min-w-[680px] border-collapse text-sm"><thead className="bg-[#eaf6f0] text-left text-xs"><tr>{section.columns.map((column) => <th key={column} className="border-b border-[#d7e2da] px-4 py-3 font-semibold whitespace-nowrap">{column}</th>)}</tr></thead><tbody>{section.rows.length ? section.rows.map((row, rowIndex) => <tr key={rowIndex} className="border-b border-[#edf0ea] last:border-0 odd:bg-white even:bg-[#f8fbf8]">{section.columns.map((_, columnIndex) => <td key={columnIndex} className="max-w-[460px] whitespace-pre-wrap px-4 py-3 align-top leading-6">{valueText(row[columnIndex])}</td>)}</tr>) : <tr><td colSpan={Math.max(1, section.columns.length)} className="px-4 py-8 text-center text-sm text-[#8a918a]">页面未显示记录</td></tr>}</tbody></table></div></div>)}</div>
          {module.source_url && <a className="mt-5 block truncate text-xs text-[#5c8d29] hover:underline" href={module.source_url} target="_blank" rel="noreferrer">来源：{module.source_url}</a>}
        </div>
      </details>)}
    </section> : <section className="rounded-2xl border border-dashed border-[#ccd7c4] bg-white px-6 py-12 text-center"><Database className="mx-auto text-[#8bb75d]" /><h3 className="mt-4 font-semibold">等待第一份完整企业全景</h3><p className="mt-2 text-sm text-[#747d75]">Windows 采集节点完成八个模块后，这里会显示页面数据并开放 Excel 下载。</p></section>}
  </div>;
}
