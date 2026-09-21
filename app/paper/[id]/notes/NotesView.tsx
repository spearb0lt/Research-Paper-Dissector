"use client";

import { useRouter } from "next/navigation";

import { NotesPanel } from "@/components/Notes";

export function NotesView({ paperId }: { paperId: number }) {
  const router = useRouter();
  return (
    <div className="max-w-3xl">
      <NotesPanel
        onOpenElement={(_elementId, page) =>
          router.push(`/paper/${paperId}/reader?page=${page || 1}`)
        }
      />
    </div>
  );
}
