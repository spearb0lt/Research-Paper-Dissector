import { AskView } from "./AskView";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AskView paperIds={[Number(id)]} />;
}
