import { ReferencesView } from "./ReferencesView";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ReferencesView paperId={Number(id)} />;
}
