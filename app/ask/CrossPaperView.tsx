"use client";

/**
 * Asking one question of several papers at once.
 *
 * The selector is the whole difference from the single paper screen. Retrieval
 * runs each paper's own indexes and fuses the results, so an answer can cite
 * two papers in one sentence and each citation still resolves to a page in the
 * paper it came from.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { AskPanel } from "@/components/AskPanel";
import { BatchLens } from "@/components/BatchLens";
import {
  Button,
  Card,
  Empty,
  ErrorNotice,
  SectionTitle,
  Spinner,
  TextInput,
  cx,
} from "@/components/ui";
import { listComparisonLenses, listPapers } from "@/lib/api";
import type { LensGroup, Paper } from "@/lib/types";

export function CrossPaperView() {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [filter, setFilter] = useState("");
  const [comparisonLenses, setComparisonLenses] = useState<LensGroup[]>([]);
  const [tab, setTab] = useState<"ask" | "batch">("ask");

  useEffect(() => {
    // Comparison lenses only exist here, because comparing a paper with itself
    // is not a thing to offer on the single paper screen.
    listComparisonLenses().then(setComparisonLenses).catch(() => setComparisonLenses([]));
  }, []);

  useEffect(() => {
    listPapers({ status: "ready" })
      .then((next) => {
        setPapers(next);
        // Everything selected by default: the point of this screen is the
        // whole library, and narrowing is the deliberate act.
        setSelected(next.map((paper) => paper.id));
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return papers;
    return papers.filter((paper) => paper.title.toLowerCase().includes(needle));
  }, [papers, filter]);

  function toggle(id: number) {
    setSelected((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );
  }

  if (loading) {
    return (
      <div className="py-20 text-center">
        <Spinner label="Loading the library" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="py-8">
        <ErrorNotice error={error} />
      </div>
    );
  }

  if (papers.length === 0) {
    return (
      <div className="py-10">
        <Empty title="No papers are ready yet">
          <Link href="/" className="text-accent hover:underline">
            Upload one first
          </Link>
        </Empty>
      </div>
    );
  }

  return (
    <div className="py-5">
      <h1 className="text-lg font-semibold text-ink">Ask across papers</h1>
      <p className="mt-1 text-sm text-ink-faint">
        One question, answered from every selected paper, with each citation
        naming the paper and page it came from.
      </p>

      <Card className="my-5 p-3">
        <SectionTitle
          action={
            <div className="flex items-center gap-1.5">
              <TextInput
                type="search"
                value={filter}
                onChange={setFilter}
                placeholder="Filter"
                className="w-40"
              />
              <Button size="sm" onClick={() => setSelected(papers.map((p) => p.id))}>
                All
              </Button>
              <Button size="sm" onClick={() => setSelected([])}>
                None
              </Button>
            </div>
          }
        >
          {selected.length} of {papers.length} selected
        </SectionTitle>
        <div className="flex flex-wrap gap-1.5">
          {shown.map((paper) => {
            const on = selected.includes(paper.id);
            return (
              <button
                key={paper.id}
                onClick={() => toggle(paper.id)}
                title={paper.title}
                className={cx(
                  "max-w-xs truncate rounded-lg border px-2.5 py-1.5 text-xs transition-colors",
                  on
                    ? "border-accent bg-accent-soft text-accent-ink"
                    : "border-line text-ink-faint hover:bg-raised",
                )}
              >
                {paper.title || paper.filename}
              </button>
            );
          })}
        </div>
      </Card>

      {selected.length === 0 ? (
        <Empty title="Select at least one paper" />
      ) : (
        <>
          <div className="mb-4 flex gap-0.5 border-b border-line">
            {(
              [
                ["ask", "Ask and compare"],
                ["batch", "Run one analysis on each"],
              ] as ["ask" | "batch", string][]
            ).map(([id, label]) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                className={cx(
                  "-mb-px border-b-2 px-3 py-2 text-sm transition-colors",
                  tab === id
                    ? "border-accent font-medium text-ink"
                    : "border-transparent text-ink-muted hover:text-ink",
                )}
              >
                {label}
              </button>
            ))}
          </div>

          {tab === "ask" ? (
            <AskPanel
              paperIds={selected}
              title={`Ask ${selected.length} paper${selected.length === 1 ? "" : "s"}`}
              lensGroups={selected.length > 1 ? comparisonLenses : undefined}
            />
          ) : (
            <BatchLens paperIds={selected} />
          )}
        </>
      )}
    </div>
  );
}
