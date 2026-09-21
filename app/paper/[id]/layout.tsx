import { PaperChrome } from "./PaperChrome";

export default async function PaperLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <PaperChrome paperId={Number(id)}>{children}</PaperChrome>;
}
