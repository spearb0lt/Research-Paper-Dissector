import { NotesView } from "./NotesView";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <NotesView paperId={Number(id)} />;
}
