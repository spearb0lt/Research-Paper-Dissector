import { OverviewView } from "./OverviewView";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <OverviewView paperId={Number(id)} />;
}
