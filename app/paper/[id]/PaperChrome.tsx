"use client";

/**
 * The frame around every paper screen: title, tabs, and the paper itself.
 *
 * The paper is fetched once here and handed down through context, so switching
 * tabs does not refetch it and every tab agrees about what it is looking at.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { NotesProvider } from "@/components/Notes";
import { Badge, ErrorNotice, Notice, Spinner, cx } from "@/components/ui";
import { getPaper } from "@/lib/api";
import type { Paper } from "@/lib/types";

interface PaperContextValue {
  paper: Paper;
  elementCounts: Record<string, number>;
  sectionCounts: Record<string, number>;
  reload: () => void;
}

const PaperContext = createContext<PaperContextValue | null>(null);

export function usePaper(): PaperContextValue {
  const value = useContext(PaperContext);
  if (!value) throw new Error("usePaper must be used inside PaperChrome.");
  return value;
}

const TABS = [
  { segment: "", label: "Overview" },
  { segment: "elements", label: "All views" },
  { segment: "reader", label: "Reader" },
  { segment: "references", label: "References" },
  { segment: "notes", label: "Notes" },
  { segment: "ask", label: "Ask" },
  { segment: "analyse", label: "Analyse" },
];

export function PaperChrome({
  paperId,
  children,
}: {
  paperId: number;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const [state, setState] = useState<PaperContextValue | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    getPaper(paperId)
      .then((response) =>
        setState({
          paper: response.paper,
          elementCounts: response.element_counts,
          sectionCounts: response.section_counts,
          reload: load,
        }),
      )
      .catch(setError);
  }, [paperId]);

  useEffect(load, [load]);

  if (error) {
    return (
      <div className="py-8">
        <ErrorNotice error={error} />
        <Link href="/" className="mt-4 inline-block text-sm text-accent hover:underline">
          Back to the library
        </Link>
      </div>
    );
  }

  if (!state) {
    return (
      <div className="py-20 text-center">
        <Spinner label="Opening the paper" />
      </div>
    );
  }

  const { paper } = state;
  const base = `/paper/${paperId}`;

  return (
    <PaperContext.Provider value={state}>
      <NotesProvider paperId={paperId}>
      <div className="py-5">
        <div className="mb-3">
          <Link href="/" className="text-xs text-ink-faint hover:text-ink">
            ← Library
          </Link>
          <h1 className="mt-1 text-lg font-semibold leading-snug text-ink">
            {paper.title || paper.filename}
          </h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-faint">
            {paper.authors.length ? (
              <span className="max-w-xl truncate">{paper.authors.join(", ")}</span>
            ) : null}
            {paper.year ? <span>· {paper.year}</span> : null}
            <span>· {paper.num_pages} pages</span>
            {paper.arxiv_id ? (
              <a
                href={`https://arxiv.org/abs/${paper.arxiv_id}`}
                target="_blank"
                rel="noreferrer noopener"
                className="text-accent hover:underline"
              >
                arXiv:{paper.arxiv_id}
              </a>
            ) : null}
            {paper.doi ? (
              <a
                href={`https://doi.org/${paper.doi}`}
                target="_blank"
                rel="noreferrer noopener"
                className="text-accent hover:underline"
              >
                doi:{paper.doi}
              </a>
            ) : null}
            {!paper.dense_signature ? (
              <Badge tone="neutral" title="No vector index. Keyword search only.">
                keyword only
              </Badge>
            ) : null}
          </div>
        </div>

        {paper.status === "failed" ? (
          <div className="mb-4">
            <Notice tone="danger" title="This paper could not be parsed">
              {paper.status_detail}
            </Notice>
          </div>
        ) : null}

        <nav className="mb-5 flex gap-0.5 border-b border-line">
          {TABS.map((tab) => {
            const href = tab.segment ? `${base}/${tab.segment}` : base;
            const active = tab.segment
              ? pathname.startsWith(href)
              : pathname === base;
            return (
              <Link
                key={tab.segment || "overview"}
                href={href}
                className={cx(
                  "-mb-px border-b-2 px-3 py-2 text-sm transition-colors",
                  active
                    ? "border-accent font-medium text-ink"
                    : "border-transparent text-ink-muted hover:text-ink",
                )}
              >
                {tab.label}
              </Link>
            );
          })}
        </nav>

        {children}
      </div>
      </NotesProvider>
    </PaperContext.Provider>
  );
}
