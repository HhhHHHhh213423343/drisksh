"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, CheckCircle2, Download, Loader2, Printer, Save, ShieldAlert } from "lucide-react";

type Report = {
  id: string; company_id: string; analysis_run_id: string; period: string; version: number;
  status: "generating" | "draft" | "in_review" | "approved" | "failed";
  title: string; summary: string; sections: Record<string, string>;
  evidence_snapshot?: { by_section?: Record<string, string[]>; monthly_metrics?: Record<string, unknown>[]; macro_context?: Record<string, unknown>[]; as_of_date?: string; period_status?: "month_to_date" | "closed_month" };
  section_order: string[]; section_labels: Record<string, string>;
  risk_sections: string[]; rating_levels: string[]; ratings: Record<string, Rating>; rating_boundary: string;
  rule_set_version: string; model_name: string; token_usage: Record<string, number>;
  generated_at: string; approved_by?: string; approved_at?: string;
};
type Rating = { level: string; data_pending: boolean; summary: string; basis: string; source?: string };
type Company = { id: string; name: string };

const LEVEL_STYLE: Record<string, string> = {
  "低风险": "bg-emerald-600 text-white", "较低风险": "bg-lime-500 text-[#13240a]", "中风险": "bg-amber-400 text-[#2b1d00]",
  "较高风险": "bg-orange-500 text-white", "高风险": "bg-red-600 text-white",
};
const SOURCE_LABEL: Record<string, string> = { system: "系统初评", llm: "模型初评", manual: "人工确认" };

// 法律、舆情板块用“【分组｜主体】”标题行按主体分段，阅读视图把标题行渲染为小标题。
function SectionBody({ text }: { text: string }) {
  if (!/^【[^】]+】\s*$/m.test(text)) return <p className="mt-3 whitespace-pre-wrap text-sm leading-8 text-[#59625a]">{text}</p>;
  const blocks: { heading: string; lines: string[] }[] = [];
  for (const line of text.split("\n")) {
    const match = line.trim().match(/^【([^】]+)】$/);
    if (match) blocks.push({ heading: match[1], lines: [] });
    else if (blocks.length) blocks[blocks.length - 1].lines.push(line);
    else if (line.trim()) blocks.push({ heading: "", lines: [line] });
  }
  return <div className="mt-3 divide-y divide-[#edf0ea] rounded-xl border border-[#edf0ea]">{blocks.map((block, index) => <div key={index} className="grid gap-2 p-4 md:grid-cols-[180px_1fr]"><p className="text-sm font-semibold text-[#313a32]">{block.heading.split("｜").map((part) => <span key={part} className="block">{part}</span>)}</p><p className="whitespace-pre-wrap text-sm leading-7 text-[#59625a]">{block.lines.join("\n").trim()}</p></div>)}</div>;
}

export default function MonthlyReportEditor({ reportId, companyId }: { reportId: string; companyId?: string }) {
  const router = useRouter();
  const [report, setReport] = useState<Report | null>(null);
  const [company, setCompany] = useState<Company | null>(null);
  const [sections, setSections] = useState<Record<string, string>>({});
  const [summary, setSummary] = useState("");
  const [ratings, setRatings] = useState<Record<string, Rating>>({});
  const [boundary, setBoundary] = useState("");
  const [busy, setBusy] = useState("");

  function apply(payload: Report) {
    setReport(payload); setSections(payload.sections || {}); setSummary(payload.summary || "");
    setRatings(payload.ratings || {}); setBoundary(payload.rating_boundary || "");
  }
  function updateRating(key: string, patch: Partial<Rating>) {
    setRatings((before) => ({ ...before, [key]: { ...(before[key] ?? { level: "", data_pending: false, summary: "", basis: "" }), ...patch } }));
  }
  const [error, setError] = useState("");

  async function load() {
    const reportResponse = await fetch(`/api/v1/monthly-reports/${reportId}`, { cache: "no-store" });
    const reportPayload = await reportResponse.json();
    if (!reportResponse.ok) throw new Error(reportPayload.detail ?? "月报加载失败");
    apply(reportPayload);
    const id = companyId || reportPayload.company_id;
    const companyResponse = await fetch(`/api/v1/companies/${id}`, { cache: "no-store" });
    const companyPayload = await companyResponse.json();
    if (companyResponse.ok) setCompany(companyPayload);
  }
  useEffect(() => { void load().catch((reason) => setError(reason.message)); }, [reportId, companyId]);
  useEffect(() => {
    if (report?.status !== "generating") return;
    const timer = window.setInterval(() => void load().catch((reason) => setError(reason.message)), 1500);
    return () => window.clearInterval(timer);
  }, [report?.status]);

  const editable = report?.status === "draft" || report?.status === "in_review";
  const totalTokens = useMemo(() => Object.values(report?.token_usage || {}).reduce((sum, value) => sum + (typeof value === "number" ? value : 0), 0), [report]);

  async function save(nextStatus: "draft" | "in_review" = "in_review"): Promise<boolean> {
    if (!report) return false;
    setBusy("save"); setError("");
    try {
      const ratedOnly = Object.fromEntries(Object.entries(ratings).filter(([, item]) => item.level && item.basis.trim()).map(([key, item]) => [key, { level: item.level, data_pending: item.data_pending, summary: item.summary, basis: item.basis }]));
      const response = await fetch(`/api/v1/monthly-reports/${report.id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ summary, sections, ratings: ratedOnly, rating_boundary: boundary, status: nextStatus }) });
      const payload = await response.json(); if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "保存失败，请检查评级依据是否填写");
      apply(payload);
      return true;
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); return false; } finally { setBusy(""); }
  }
  async function approve() {
    if (!report) return;
    setBusy("approve"); setError("");
    try {
      if (editable && !(await save("in_review"))) return;
      const response = await fetch(`/api/v1/monthly-reports/${report.id}/approve`, { method: "POST" });
      const payload = await response.json(); if (!response.ok) throw new Error(payload.detail ?? "审批失败");
      apply(payload);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "审批失败"); } finally { setBusy(""); }
  }

  if (!report) return <main className="grid min-h-screen place-items-center bg-[#f4f7f1]"><div className="flex items-center gap-2 text-sm text-[#687168]">{error ? <ShieldAlert className="text-red-600" /> : <Loader2 className="animate-spin" />} {error || "正在加载月报"}</div></main>;
  if (report.status === "generating") return <main className="grid min-h-screen place-items-center bg-[#f4f7f1]"><div className="rounded-2xl border border-[#dbe3d6] bg-white p-8 text-center"><Loader2 className="mx-auto animate-spin text-[#6da522]" /><h1 className="mt-4 font-semibold">正在生成月报草稿</h1><p className="mt-2 text-sm text-[#6e766f]">规则结论、上传材料和上一期语言风格正在合并。</p></div></main>;

  return <main className="min-h-screen bg-[#f4f7f1] text-[#162018] print:bg-white">
    <header className="no-print sticky top-0 z-30 border-b border-[#dce3d7] bg-[#07110b] text-white"><div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-4"><button onClick={() => router.push(`/analysis/${companyId || report.company_id}?tab=monitoring&runId=${report.analysis_run_id}`)} className="flex items-center gap-2 text-sm"><ArrowLeft size={16} />返回投后监测</button><div className="flex gap-2">{editable && <button onClick={() => void save()} disabled={Boolean(busy)} className="flex items-center gap-2 rounded-xl border border-white/15 px-3 py-2 text-sm"><Save size={15} />保存审阅稿</button>}{editable && <button onClick={() => void approve()} disabled={Boolean(busy)} className="flex items-center gap-2 rounded-xl bg-[#78be20] px-3 py-2 text-sm font-semibold text-[#102006]"><CheckCircle2 size={15} />审批定稿</button>}<a href={`/api/v1/monthly-reports/${report.id}/export.xlsx`} className="flex items-center gap-2 rounded-xl border border-white/15 px-3 py-2 text-sm"><Download size={15} />Excel</a><button onClick={() => window.print()} disabled={report.status !== "approved"} title={report.status === "approved" ? "通过浏览器打印对话框另存为 PDF" : "请先审批定稿"} className="flex items-center gap-2 rounded-xl border border-white/15 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40"><Printer size={15} />导出 PDF</button></div></div></header>
    <article className="report-document mx-auto max-w-6xl px-5 py-8 print:max-w-none print:px-0 print:py-0">
      {error && <div className="no-print mb-5 flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"><ShieldAlert size={17} />{error}</div>}
      <section className="report-cover rounded-3xl border border-[#dbe3d6] bg-white p-7 md:p-10 print:border-0 print:p-0"><div className="flex flex-col justify-between gap-5 md:flex-row"><div><p className="text-sm font-semibold text-[#5c8d29]">{company?.name || "企业投后监测"}</p><h1 className="mt-2 text-3xl font-semibold">{report.title}</h1><p className="mt-3 text-sm text-[#737b73]">监测期间 {report.period}{report.evidence_snapshot?.period_status === "month_to_date" ? "（月度进行中）" : ""}　截止日 {report.evidence_snapshot?.as_of_date || "未标注"}　版本 v{report.version}　规则集 {report.rule_set_version}</p></div><div className={`report-status h-fit rounded-xl px-4 py-2 text-sm font-semibold ${report.status === "approved" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{report.status === "approved" ? `已审批 · ${report.approved_by}` : report.status}</div></div>
        <div className="mt-7"><h2 className="text-sm font-semibold text-[#687168]">管理层摘要</h2>{editable ? <textarea value={summary} onChange={(event) => setSummary(event.target.value)} rows={4} className="mt-2 w-full rounded-xl border border-[#dbe3d6] p-4 text-sm leading-7 outline-none focus:ring-2 focus:ring-[#78be20]" /> : <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-[#59625a]">{summary}</p>}</div>
      </section>
      <section className="report-section mt-6 rounded-2xl border border-[#dbe3d6] bg-white p-6 print:rounded-none print:border-x-0 print:px-0">
        <div className="flex flex-wrap items-baseline justify-between gap-2"><h2 className="text-lg font-semibold">风险概况</h2><p className="text-xs text-[#7a827a]">五档文字评级，不等同于发生概率；资料不足的板块标注“资料待补”</p></div>
        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">{(report.risk_sections || []).map((key) => { const rating = ratings[key]; return <div key={key} className="rounded-xl border border-[#e4e9df] p-3">
          <div className="flex items-center justify-between gap-2"><span className="text-sm font-semibold">{report.section_labels[key]}</span>{rating?.source && <span className="text-[10px] text-[#8a928a]">{SOURCE_LABEL[rating.source] || rating.source}</span>}</div>
          {editable ? <div className="mt-2 flex items-center gap-2"><select value={rating?.level || ""} onChange={(event) => updateRating(key, { level: event.target.value })} className={`rounded-lg px-2 py-1 text-xs font-semibold ${LEVEL_STYLE[rating?.level || ""] || "border border-[#d8dfd3]"}`}><option value="" disabled>未评级</option>{report.rating_levels.map((level) => <option key={level} value={level}>{level}</option>)}</select><label className="flex items-center gap-1 text-xs text-[#687168]"><input type="checkbox" checked={Boolean(rating?.data_pending)} onChange={(event) => updateRating(key, { data_pending: event.target.checked })} />资料待补</label></div>
            : <p className="mt-2"><span className={`rounded-lg px-2 py-1 text-xs font-semibold ${LEVEL_STYLE[rating?.level || ""] || "bg-[#eef1ec] text-[#687168]"}`}>{rating?.level || "未评级"}{rating?.data_pending ? "（资料待补）" : ""}</span></p>}
          {editable ? <textarea value={rating?.summary || ""} onChange={(event) => updateRating(key, { summary: event.target.value })} rows={2} maxLength={200} placeholder="简评" className="mt-2 w-full rounded-lg border border-[#dbe3d6] p-2 text-xs leading-5 outline-none focus:ring-2 focus:ring-[#78be20]" /> : <p className="mt-2 text-xs leading-5 text-[#59625a]">{rating?.summary}</p>}
        </div>; })}</div>
        <details className="mt-4 rounded-xl border border-[#e4e9df] p-3" open={editable}><summary className="cursor-pointer text-sm font-semibold">本期评级依据及评级边界</summary>
          <div className="mt-3 space-y-3">{(report.risk_sections || []).map((key) => <div key={key}><p className="text-xs font-semibold text-[#59625a]">{report.section_labels[key]}</p>{editable ? <textarea value={ratings[key]?.basis || ""} onChange={(event) => updateRating(key, { basis: event.target.value })} rows={3} placeholder="具体依据及评级原因（必填）" className="mt-1 w-full rounded-lg border border-[#dbe3d6] p-2 text-xs leading-5 outline-none focus:ring-2 focus:ring-[#78be20]" /> : <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-[#59625a]">{ratings[key]?.basis}</p>}</div>)}
            <div><p className="text-xs font-semibold text-[#59625a]">评级边界</p>{editable ? <textarea value={boundary} onChange={(event) => setBoundary(event.target.value)} rows={3} className="mt-1 w-full rounded-lg border border-[#dbe3d6] p-2 text-xs leading-5 outline-none focus:ring-2 focus:ring-[#78be20]" /> : <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-[#59625a]">{boundary}</p>}</div>
          </div>
        </details>
      </section>
      <div className="mt-6 space-y-4">{report.section_order.map((key) => { const evidenceIds = report.evidence_snapshot?.by_section?.[key] || []; const rating = ratings[key]; return <section key={key} className="report-section rounded-2xl border border-[#dbe3d6] bg-white p-6 print:rounded-none print:border-x-0 print:px-0"><h2 className="flex items-center gap-3 text-lg font-semibold">{report.section_labels[key] || key}{rating?.level && <span className={`rounded-lg px-2 py-0.5 text-xs font-semibold ${LEVEL_STYLE[rating.level] || ""}`}>{rating.level}{rating.data_pending ? "（资料待补）" : ""}</span>}</h2>{editable ? <textarea value={sections[key] || ""} onChange={(event) => setSections((before) => ({ ...before, [key]: event.target.value }))} rows={Math.min(16, Math.max(4, Math.ceil((sections[key]?.length || 0) / 90)))} className="mt-3 w-full rounded-xl border border-[#dbe3d6] p-4 text-sm leading-7 outline-none focus:ring-2 focus:ring-[#78be20]" /> : <SectionBody text={sections[key] || ""} />}{editable && (key === "legal" || key === "sentiment") && <p className="mt-2 text-[11px] text-[#858d85]">请保留“【分组｜主体】”标题行，导出 Excel 时按主体分行。</p>}{evidenceIds.length > 0 && <p className="evidence-index mt-3 border-t border-[#edf0ea] pt-3 text-[11px] leading-5 text-[#858d85]">证据编号：{evidenceIds.join("、")}</p>}</section>; })}</div>
      <footer className="report-footer mt-6 border-t border-[#dce3d7] py-5 text-xs text-[#7a827a]">生成方式：{report.model_name}　模型 Token：{totalTokens || 0}　生成时间：{new Date(report.generated_at).toLocaleString("zh-CN")}。本报告需结合原始证据使用。</footer>
    </article>
  </main>;
}
