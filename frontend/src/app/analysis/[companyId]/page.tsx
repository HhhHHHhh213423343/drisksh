import AnalysisPlatform from "@/components/AnalysisPlatform";

export default async function AnalysisPage({
  params,
  searchParams,
}: {
  params: Promise<{ companyId: string }>;
  searchParams: Promise<{ tab?: string; runId?: string }>;
}) {
  const { companyId } = await params;
  const { tab, runId } = await searchParams;
  return <AnalysisPlatform companyId={companyId} initialCategory={tab} initialRunId={runId} />;
}
