"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Building2, CheckCircle2, Clock3, Search, ShieldCheck } from "lucide-react";

type Company = {
  id: string;
  name: string;
  industry?: string | null;
  region?: string | null;
  description?: string;
  company_profile?: {
    akshare_profile?: { stock_code?: string; stock_name?: string; status?: string };
    qyyjt_profile?: { enabled?: boolean };
    monitoring_parent_id?: string;
  };
};

type CompanySuggestion = {
  name: string;
  company_code?: string;
};

function stockCode(company: Company) {
  return company.company_profile?.akshare_profile?.stock_code ?? "";
}

function companyRoute(company: Company) {
  return `/analysis/${company.id}?tab=monitoring`;
}

function localMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export default function HomeSearch() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [companies, setCompanies] = useState<Company[]>([]);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [runStage, setRunStage] = useState("");
  const [error, setError] = useState("");
  const [suggestion, setSuggestion] = useState<CompanySuggestion | null>(null);

  useEffect(() => {
    fetch("/api/v1/companies", { cache: "no-store" })
      .then(async (companiesResponse) => {
        const companyPayload = await companiesResponse.json();
        if (!companiesResponse.ok) throw new Error(companyPayload.detail ?? "无法读取企业列表");
        return companyPayload;
      })
      .then((items: Company[]) => setCompanies(items.filter((item) => !item.company_profile?.monitoring_parent_id)))
      .catch(() => setCompanies([]));
  }, []);

  useEffect(() => {
    if (!busy) { setElapsed(0); return; }
    const startedAt = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  const candidates = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return companies.slice(0, 6);
    return companies.filter((company) =>
      company.name.toLowerCase().includes(normalized) || stockCode(company).includes(normalized),
    ).slice(0, 6);
  }, [companies, query]);

  async function runSearch(rawInput: string) {
    const input = rawInput?.trim() || "";
    if (!input) { setError("请输入公司名称、股票简称或 6 位股票代码"); return; }
    setBusy(true);
    setError("");
    setSuggestion(null);
    setRunStage("creating");
    try {
      const isStockCode = /^\d{6}$/.test(input);
      const response = await fetch("/api/v1/analysis-runs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          company_name: input,
          stock_code: isStockCode ? input : "",
          requested_period: localMonth(),
          max_results_per_source: 8,
          max_documents: 60,
          lookback_days: 30,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(typeof payload.detail === "string" ? payload.detail : "企业检索失败");
      }
      const company = payload.company;
      const id = company?.id;
      const runId = payload.analysis_run?.id;
      if (!id) throw new Error("后端未返回企业 ID");
      if (!runId) throw new Error("后端未返回分析任务 ID");
      let run = payload.analysis_run;
      for (let attempt = 0; attempt < 300 && ["queued", "running"].includes(run.status); attempt += 1) {
        setRunStage(run.current_stage || run.status);
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        const statusResponse = await fetch(`/api/v1/analysis-runs/${runId}`, { cache: "no-store" });
        run = await statusResponse.json();
        if (!statusResponse.ok) throw new Error(run.detail ?? "分析进度读取失败");
      }
      if (run.status === "failed") throw new Error(run.error_message || "实时分析失败");
      if (run.status !== "completed") {
        router.push(`/analysis/${id}?tab=monitoring&runId=${runId}`);
        return;
      }
      router.push(`/analysis/${id}?tab=monitoring&runId=${runId}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "企业检索失败");
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await runSearch(query);
  }

  async function logout() {
    await fetch("/api/v1/auth/logout", { method: "POST" });
    window.location.href = "/login";
  }

  const progressText = runStage === "refresh_company_profile" ? "正在实时识别企业并刷新财务数据"
    : runStage === "collect_public_sources" ? "正在抓取公开网页、公告与监管来源"
      : runStage === "discover_related_entities" ? "正在整理关联主体候选"
        : runStage === "evaluate_rules" ? "正在形成风险结论"
          : elapsed < 8 ? "正在准备分析" : "信息更新较慢，分析仍在继续";

  return (
    <main className="min-h-[100dvh] bg-[#07110b] text-white">
      <div className="mx-auto flex min-h-[100dvh] max-w-7xl flex-col px-6 py-8 lg:px-12">
        <header className="flex items-center justify-between border-b border-white/10 pb-6">
          <div className="flex items-center gap-4">
            <div className="grid h-12 w-12 place-items-center rounded-xl bg-white text-2xl font-black text-[#172019]">D.</div>
            <div><p className="text-xs font-semibold tracking-[.28em] text-[#85c82b]">RISK INTELLIGENCE</p><h1 className="text-xl font-semibold">D.Risk AI</h1></div>
          </div>
          <div className="hidden items-center gap-4 text-sm text-white/60 sm:flex"><span className="flex items-center gap-2"><ShieldCheck size={18} className="text-[#85c82b]" />公开数据　证据可追溯　缺失不臆测</span><button onClick={() => void logout()} className="rounded-lg border border-white/15 px-3 py-1.5 text-xs hover:bg-white/5">退出</button></div>
        </header>

        <section className="grid flex-1 items-center gap-14 py-14 lg:grid-cols-[1.08fr_.92fr]">
          <div>
            <p className="mb-5 text-sm font-semibold tracking-[.3em] text-[#85c82b]">ENTERPRISE RISK WORKSPACE</p>
            <h2 className="max-w-3xl text-5xl font-semibold leading-[1.08] tracking-tight lg:text-7xl">从一家企业开始，<br /><span className="text-[#9bdb45]">让风险有据可查。</span></h2>
            <p className="mt-7 max-w-2xl text-lg leading-8 text-white/60">输入公司名称或股票代码，联动分析流程，并保留每条结论的数据来源与覆盖质量。</p>
          </div>

          <div className="rounded-3xl border border-white/10 bg-white/[.06] p-7 shadow-2xl backdrop-blur">
            <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-2xl bg-[#85c82b] text-[#07110b]"><Building2 /></div>
            <h3 className="text-2xl font-semibold">智能检索企业</h3>
            <p className="mt-2 text-sm leading-6 text-white/55">输入公司名称或股票代码，获取本月风险结论、重点事项和投后建议。</p>

            <form onSubmit={submit} className="mt-7">
              <label className="mb-2 block text-sm font-medium text-white/80" htmlFor="company-search">公司名称或股票代码</label>
              <div className="flex rounded-2xl bg-white p-2 text-[#172019] focus-within:ring-2 focus-within:ring-[#85c82b]">
                <Search className="ml-3 mt-3 shrink-0 text-black/40" size={20} />
                <input id="company-search" value={query} onChange={(event) => { setQuery(event.target.value); setSuggestion(null); setError(""); }} disabled={busy} className="min-w-0 flex-1 bg-transparent px-3 py-3 outline-none placeholder:text-black/45" placeholder="例如：工商银行 / 601398" />
                <button disabled={busy} className="flex shrink-0 items-center gap-2 rounded-xl bg-[#78be20] px-5 font-semibold text-[#102006] active:scale-[.98] disabled:opacity-60">{busy ? "检索中" : "开始分析"}<ArrowRight size={17} /></button>
              </div>
              <p className="mt-2 text-xs text-white/45">精确股票代码可以减少同名公司或简称匹配误差。</p>
            </form>

            {busy && <div className="mt-5 rounded-2xl border border-[#85c82b]/25 bg-[#85c82b]/10 p-4" aria-live="polite">
              <div className="flex items-center justify-between gap-3"><span className="flex items-center gap-2 text-sm font-medium text-[#b8e47c]"><Clock3 size={16} />{progressText}</span><span className="text-xs text-white/45">{elapsed} 秒</span></div>
              <div className="mt-3 h-1 overflow-hidden rounded-full bg-white/10"><div className="loading-bar h-full w-2/3 rounded-full bg-[#85c82b]" /></div>
            </div>}
            {error && <p className="mt-4 rounded-xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</p>}
            {suggestion && <button type="button" onClick={() => void runSearch(suggestion.name)} disabled={busy} className="mt-3 flex w-full items-center justify-between rounded-xl border border-[#85c82b]/40 bg-[#85c82b]/10 px-4 py-3 text-left text-sm transition hover:bg-[#85c82b]/15 disabled:opacity-60"><span><span className="block text-xs text-white/50">你是否要分析</span><span className="mt-1 block font-semibold text-[#c9ed98]">{suggestion.name}</span></span><ArrowRight size={17} className="shrink-0 text-[#85c82b]" /></button>}

            <div className="mt-7 border-t border-white/10 pt-5">
              <div className="flex items-center justify-between"><h4 className="text-sm font-semibold">{query ? "匹配的已分析企业" : "最近分析企业"}</h4><span className="text-xs text-white/40">{candidates.length} 家</span></div>
              {candidates.length > 0 ? <div className="mt-3 grid gap-2 sm:grid-cols-2">{candidates.map((company) => <button key={company.id} onClick={() => router.push(companyRoute(company))} className="group flex items-center justify-between rounded-xl border border-white/10 bg-white/[.04] px-3 py-3 text-left transition hover:border-[#85c82b]/50 hover:bg-white/[.07] active:scale-[.99]"><span className="min-w-0"><span className="block truncate text-sm font-medium">{company.name}</span><span className="mt-1 block text-xs text-white/40">{stockCode(company) || company.industry || "基础档案已入库"}</span></span><CheckCircle2 size={16} className="ml-2 shrink-0 text-[#85c82b]" /></button>)}</div> : <p className="mt-3 rounded-xl border border-dashed border-white/15 px-4 py-4 text-sm text-white/45">没有本地候选，提交后将执行智能检索。</p>}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
