"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useConfig } from "@/components/ConfigProvider";
import { FigureImage, SECTION_LABELS } from "@/components/ElementCard";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorNotice,
  Notice,
  SectionTitle,
  Spinner,
  Toggle,
  cx,
} from "@/components/ui";
import { QualityPanel, TriagePanel } from "@/components/Triage";
import {
  exportBibtexUrl,
  exportMarkdownUrl,
  exportTableUrl,
  listElements,
  reindexPaper,
} from "@/lib/api";
import type { Element } from "@/lib/types";

import { usePaper } from "./PaperChrome";

/** The composition bar: what this paper is made of, at a glance. */
function CompositionBar({ counts }: { counts: Record<string, number> }) {
  const interesting: [string, string, string][] = [
    ["paragraph", "Text", "bg-kind-text"],
    ["figure", "Figures", "bg-kind-figure"],
    ["table", "Tables", "bg-kind-table"],
    ["formula", "Formulas", "bg-kind-formula"],
    ["reference", "References", "bg-kind-reference"],
  ];
  const present = interesting.filter(([key]) => (counts[key] ?? 0) > 0);
  const total = present.reduce((sum, [key]) => sum + (counts[key] ?? 0), 0);
  if (!total) return null;

  return (
    <div>
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-raised">
        {present.map(([key, , colour]) => (
          <div
            key={key}
            className={colour}
            style={{ width: `${((counts[key] ?? 0) / total) * 100}%` }}
            title={`${counts[key]} ${key}`}
          />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink-faint">
        {present.map(([key, label, colour]) => (
          <span key={key} className="inline-flex items-center gap-1">
            <span className={cx("h-2 w-2 rounded-full", colour)} />
            {counts[key]} {label.toLowerCase()}
          </span>
        ))}
      </div>
    </div>
  );
}

export function OverviewView({ paperId }: { paperId: number }) {
  const { paper, elementCounts, sectionCounts, reload } = usePaper();
  const { can, why, prefs } = useConfig();
  const [figures, setFigures] = useState<Element[]>([]);
  const [tables, setTables] = useState<Element[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [reindexing, setReindexing] = useState(false);
  const [dense, setDense] = useState(Boolean(paper.dense_signature));

  useEffect(() => {
    setLoading(true);
    Promise.all([
      listElements(paperId, { kind: "figure", limit: 6 }),
      listElements(paperId, { kind: "table", limit: 4 }),
    ])
      .then(([figureResponse, tableResponse]) => {
        setFigures(figureResponse.elements);
        setTables(tableResponse.elements);
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [paperId]);

  async function runReindex() {
    setReindexing(true);
    setError(null);
    try {
      await reindexPaper(paperId, { dense, embedding_provider: prefs.embedder });
      reload();
    } catch (exception) {
      setError(exception);
    } finally {
      setReindexing(false);
    }
  }

  const orderedSections = Object.entries(sectionCounts)
    .filter(([section]) => section !== "unknown")
    .sort((a, b) => b[1] - a[1]);

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
      <div className="space-y-6">
        {paper.abstract ? (
          <section>
            <SectionTitle>Abstract</SectionTitle>
            <Card className="p-4">
              <p className="text-[13px] leading-relaxed text-ink-muted">{paper.abstract}</p>
            </Card>
          </section>
        ) : null}

        {paper.warnings?.length ? (
          <Notice tone="warn" title="Notes from the parser">
            <ul className="list-disc space-y-0.5 pl-4">
              {paper.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </Notice>
        ) : null}

        <section>
          <SectionTitle
            action={
              <Link href={`/paper/${paperId}/elements?kind=figure`}>
                <Button size="sm" variant="ghost">
                  All {elementCounts.figure ?? 0} figures
                </Button>
              </Link>
            }
          >
            Figures
          </SectionTitle>
          {loading ? (
            <Spinner label="Loading figures" />
          ) : figures.length === 0 ? (
            <Empty title="No figures were extracted">
              {can("deep_parse")
                ? "This paper may genuinely have none."
                : `Vector drawn charts are recovered by cropping, which is approximate. ${why("deep_parse")}`}
            </Empty>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {figures.map((figure) => (
                <Link
                  key={figure.id}
                  href={`/paper/${paperId}/elements?kind=figure#element-${figure.id}`}
                  className="group"
                >
                  <FigureImage
                    digest={figure.image?.digest ?? ""}
                    alt={figure.caption || `Figure on page ${figure.page}`}
                    className="h-32 w-full object-contain transition-shadow group-hover:shadow-md"
                  />
                  <p className="mt-1 line-clamp-2 text-[11px] text-ink-faint">
                    {figure.label || `page ${figure.page}`}
                    {figure.caption ? ` · ${figure.caption}` : ""}
                  </p>
                </Link>
              ))}
            </div>
          )}
        </section>

        <section>
          <SectionTitle
            action={
              <Link href={`/paper/${paperId}/elements?kind=table`}>
                <Button size="sm" variant="ghost">
                  All {elementCounts.table ?? 0} tables
                </Button>
              </Link>
            }
          >
            Tables
          </SectionTitle>
          {tables.length === 0 ? (
            <Empty title="No tables were extracted" />
          ) : (
            <ul className="space-y-2">
              {tables.map((table) => (
                <Card as="li" key={table.id} className="p-3">
                  <div className="flex items-center gap-2 text-[11px] text-ink-faint">
                    <span className="font-medium text-ink-muted">
                      {table.label || "Table"}
                    </span>
                    <span>page {table.page}</span>
                    <span>
                      {table.table?.num_rows}×{table.table?.num_cols}
                    </span>
                    {table.extra?.structure_confident === false ? (
                      <Badge tone="warn">structure inferred</Badge>
                    ) : null}
                  </div>
                  {table.caption ? (
                    <p className="mt-1 line-clamp-2 text-xs italic text-ink-faint">
                      {table.caption}
                    </p>
                  ) : null}
                  <a
                    href={exportTableUrl(paperId, table.id)}
                    download
                    className="mt-1 inline-block text-[11px] text-accent hover:underline"
                  >
                    Download as CSV
                  </a>
                </Card>
              ))}
            </ul>
          )}
        </section>
      </div>

      <aside className="space-y-5 lg:sticky lg:top-16 lg:self-start">
        <TriagePanel paper={paper} onChange={() => reload()} />

        <QualityPanel quality={paper.quality} />

        <Card className="p-4">
          <SectionTitle>What is in it</SectionTitle>
          <CompositionBar counts={elementCounts} />
        </Card>

        {orderedSections.length ? (
          <Card className="p-4">
            <SectionTitle>Sections found</SectionTitle>
            <ul className="space-y-1">
              {orderedSections.map(([section, count]) => (
                <li key={section}>
                  <Link
                    href={`/paper/${paperId}/elements?section=${section}`}
                    className="flex items-center justify-between rounded px-1.5 py-1 text-[13px] text-ink-muted hover:bg-raised hover:text-ink"
                  >
                    <span>{SECTION_LABELS[section] ?? section}</span>
                    <span className="text-[11px] text-ink-faint">{count}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        <Card className="p-4">
          <SectionTitle>Index</SectionTitle>
          <dl className="space-y-1.5 text-[13px]">
            <div className="flex justify-between">
              <dt className="text-ink-faint">Parser</dt>
              <dd className="text-ink-muted">{paper.parser || "unknown"}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-faint">Parse time</dt>
              <dd className="text-ink-muted">{paper.parse_seconds.toFixed(1)}s</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-faint">Chunk size</dt>
              <dd className="text-ink-muted">
                {paper.chunk_tokens || "-"} tokens
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-ink-faint">Vectors</dt>
              <dd className="truncate text-right text-ink-muted" title={paper.dense_signature}>
                {paper.dense_signature ? paper.dense_signature.split(":")[1] : "none"}
              </dd>
            </div>
          </dl>

          <div className="mt-3 space-y-2 border-t border-line pt-3">
            <Toggle
              checked={dense}
              onChange={setDense}
              label="Vector index"
              hint="Local encoder. Off leaves keyword search, which is faster to build."
            />
            <Button
              size="sm"
              onClick={runReindex}
              disabled={reindexing}
              className="w-full"
            >
              {reindexing ? "Rebuilding..." : "Rebuild the index"}
            </Button>
            <p className="text-[11px] text-ink-faint">
              Rebuilds chunks and indexes from the stored elements. The PDF is not
              read again. Cached analyses are cleared, because the excerpts they
              cited no longer exist under those numbers.
            </p>
          </div>
        </Card>

        <Card className="p-4">
          <SectionTitle>Export</SectionTitle>
          <div className="flex flex-wrap gap-1.5">
            <a href={exportMarkdownUrl(paperId)} download>
              <Button size="sm">Analyses as Markdown</Button>
            </a>
            <a href={exportBibtexUrl(paperId)} download>
              <Button size="sm">BibTeX</Button>
            </a>
          </div>
          <p className="mt-2 text-[11px] text-ink-faint">
            Citations in the Markdown are resolved from excerpt numbers to page
            references, because an excerpt number means nothing once the answer
            has left the screen it was written on.
          </p>
        </Card>

        {error ? <ErrorNotice error={error} /> : null}
      </aside>
    </div>
  );
}
