import { AnalyseView } from "./AnalyseView";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AnalyseView paperId={Number(id)} />;
}
