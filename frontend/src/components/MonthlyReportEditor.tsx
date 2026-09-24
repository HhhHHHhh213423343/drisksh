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
  rule_set_version: string; model_name: string; token_usage: Record<string, number>;
  generated_at: string; approved_by?: string; approved_at?: string;
};
type Company = { id: string; name: string };

export default function MonthlyReportEditor({ reportId, companyId }: { reportId: string; companyId?: string }) {
  const router = useRouter();
  const [report, setReport] = useState<Report | null>(null);
  const [company, setCompany] = useState<Company | null>(null);
  const [sections, setSections] = useState<Record<string, string>>({});
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function load() {
    const reportResponse = await fetch(`/api/v1/monthly-reports/${reportId}`, { cache: "no-store" });
    const reportPayload = await reportResponse.json();
    if (!reportResponse.ok) throw new Error(reportPayload.detail ?? "月报加载失败");
    setReport(reportPayload); setSections(reportPayload.sections || {}); setSummary(reportPayload.summary || "");
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
      const response = await fetch(`/api/v1/monthly-reports/${report.id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ summary, sections, status: nextStatus }) });
      const payload = await response.json(); if (!response.ok) throw new Error(payload.detail ?? "保存失败");
      setReport(payload); setSections(payload.sections); setSummary(payload.summary);
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
      setReport(payload); setSections(payload.sections); setSummary(payload.summary);
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
      <div className="mt-6 space-y-4">{report.section_order.map((key) => { const evidenceIds = report.evidence_snapshot?.by_section?.[key] || []; return <section key={key} className="report-section rounded-2xl border border-[#dbe3d6] bg-white p-6 print:rounded-none print:border-x-0 print:px-0"><h2 className="text-lg font-semibold">{report.section_labels[key] || key}</h2>{editable ? <textarea value={sections[key] || ""} onChange={(event) => setSections((before) => ({ ...before, [key]: event.target.value }))} rows={Math.min(16, Math.max(4, Math.ceil((sections[key]?.length || 0) / 90)))} className="mt-3 w-full rounded-xl border border-[#dbe3d6] p-4 text-sm leading-7 outline-none focus:ring-2 focus:ring-[#78be20]" /> : <p className="mt-3 whitespace-pre-wrap text-sm leading-8 text-[#59625a]">{sections[key]}</p>}{evidenceIds.length > 0 && <p className="evidence-index mt-3 border-t border-[#edf0ea] pt-3 text-[11px] leading-5 text-[#858d85]">证据编号：{evidenceIds.join("、")}</p>}</section>; })}</div>
      <footer className="report-footer mt-6 border-t border-[#dce3d7] py-5 text-xs text-[#7a827a]">生成方式：{report.model_name}　模型 Token：{totalTokens || 0}　生成时间：{new Date(report.generated_at).toLocaleString("zh-CN")}。本报告需结合原始证据使用。</footer>
    </article>
  </main>;
}
