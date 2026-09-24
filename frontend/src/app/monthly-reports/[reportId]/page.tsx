import MonthlyReportEditor from "@/components/MonthlyReportEditor";

export default async function MonthlyReportPage({ params, searchParams }: { params: Promise<{ reportId: string }>; searchParams: Promise<{ companyId?: string }> }) {
  const { reportId } = await params;
  const { companyId } = await searchParams;
  return <MonthlyReportEditor reportId={reportId} companyId={companyId} />;
}
