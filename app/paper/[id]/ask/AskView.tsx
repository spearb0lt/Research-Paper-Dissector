"use client";

import { AskPanel } from "@/components/AskPanel";

export function AskView({ paperIds }: { paperIds: number[] }) {
  return <AskPanel paperIds={paperIds} />;
}
