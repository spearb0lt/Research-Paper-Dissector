"use client";

/**
 * All Views: everything the parser found, browsable by element, by section or
 * by page, and searchable across all of them at once.
 *
 * The search here is deliberately not retrieval. Retrieval ranks and returns
 * the best dozen; this is exhaustive and literal, because someone asking where
 * a term appears wants every occurrence, including the one in a table cell on
 * page 14 and the one OCR read out of a figure legend. The result says which
 * part of each element matched, so "found in a caption" and "found in a table
 * cell" are distinguishable without opening anything.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ShortcutHelp,
  focusListItem,
  useShortcuts,
  type Shortcut,
} from "@/components/Keyboard";

import {
  ElementCard,
  FigureImage,
  KIND_LABELS,
  SECTION_LABELS,
} from "@/components/ElementCard";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorNotice,
  KindDot,
  SectionTitle,
  Spinner,
  TextInput,
  cx,
} from "@/components/ui";
import { findEverywhere, listElements } from "@/lib/api";
import type { Element, FindMatch, FindResult } from "@/lib/types";

import { usePaper } from "../PaperChrome";

type Mode = "list" | "figures" | "tables" | "pages" | "sections";

const MODES: { id: Mode; label: string }[] = [
  { id: "list", label: "Everything" },
  { id: "figures", label: "Figures" },
  { id: "tables", label: "Tables" },
  { id: "sections", label: "By section" },
  { id: "pages", label: "By page" },
];

/** Render a snippet with the matched spans marked, without building HTML. */
function Snippet({ match }: { match: FindMatch }) {
  const snippet = match.snippets[0];
  if (!snippet) return null;
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  snippet.spans.forEach(([start, end], index) => {
    if (start > cursor) parts.push(snippet.text.slice(cursor, start));
    parts.push(
      <mark key={index} className="rounded bg-accent-soft px-0.5 text-accent-ink">
        {snippet.text.slice(start, end)}
      </mark>,
    );
    cursor = end;
  });
  parts.push(snippet.text.slice(cursor));
  return (
    <p className="text-[13px] leading-relaxed text-ink-muted">
      {snippet.prefix}
      {parts}
      {snippet.suffix}
    </p>
  );
}

export function ElementsView({ paperId }: { paperId: number }) {
  const router = useRouter();
  const params = useSearchParams();
  const { elementCounts } = usePaper();

  const [mode, setMode] = useState<Mode>(() => {
    const kind = params.get("kind");
    if (kind === "figure") return "figures";
    if (kind === "table") return "tables";
    if (params.get("section")) return "sections";
    return "list";
  });
  const [kindFilter, setKindFilter] = useState(params.get("kind") ?? "");
  const [sectionFilter, setSectionFilter] = useState(params.get("section") ?? "");
  const [elements, setElements] = useState<Element[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const [cursor, setCursor] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const [needle, setNeedle] = useState("");
  const [finding, setFinding] = useState(false);
  const [found, setFound] = useState<FindResult | null>(null);

  useEffect(() => {
    setLoading(true);
    listElements(paperId, { limit: 5000 })
      .then((response) => setElements(response.elements))
      .catch(setError)
      .finally(() => setLoading(false));
  }, [paperId]);

  const runFind = useCallback(async () => {
    const term = needle.trim();
    if (!term) {
      setFound(null);
      return;
    }
    setFinding(true);
    try {
      setFound(await findEverywhere([paperId], term, 400));
    } catch (exception) {
      setError(exception);
    } finally {
      setFinding(false);
    }
  }, [needle, paperId]);

  const figures = useMemo(() => elements.filter((e) => e.kind === "figure"), [elements]);
  const tables = useMemo(() => elements.filter((e) => e.kind === "table"), [elements]);

  const filtered = useMemo(() => {
    let out = elements;
    if (kindFilter) out = out.filter((e) => e.kind === kindFilter);
    if (sectionFilter) out = out.filter((e) => e.section === sectionFilter);
    return out;
  }, [elements, kindFilter, sectionFilter]);

  const byPage = useMemo(() => {
    const map = new Map<number, Element[]>();
    for (const element of elements) {
      const list = map.get(element.page) ?? [];
      list.push(element);
      map.set(element.page, list);
    }
    return [...map.entries()].sort((a, b) => a[0] - b[0]);
  }, [elements]);

  const bySection = useMemo(() => {
    const map = new Map<string, Element[]>();
    for (const element of elements) {
      const list = map.get(element.section) ?? [];
      list.push(element);
      map.set(element.section, list);
    }
    return [...map.entries()];
  }, [elements]);

  const openPage = useCallback(
    (page: number) => router.push(`/paper/${paperId}/reader?page=${page}`),
    [router, paperId],
  );

  // The list the keyboard walks, which is whichever one is on screen.
  const cursorList = useMemo(() => {
    if (mode === "figures") return figures;
    if (mode === "tables") return tables;
    return filtered;
  }, [mode, figures, tables, filtered]);

  const move = useCallback(
    (delta: number) => {
      setCursor((current) => {
        if (!cursorList.length) return current;
        const next = Math.max(0, Math.min(cursorList.length - 1, current + delta));
        const element = cursorList[next];
        if (element) focusListItem(`element-${element.id}`);
        return next;
      });
    },
    [cursorList],
  );

  const shortcuts: Shortcut[] = useMemo(
    () => [
      { keys: ["j"], label: "Next element", run: () => move(1) },
      { keys: ["k"], label: "Previous element", run: () => move(-1) },
      {
        keys: ["/"],
        label: "Focus the find box",
        run: () => searchRef.current?.focus(),
      },
      {
        keys: ["Enter"],
        label: "Open the current element on its page",
        run: () => {
          const element = cursorList[cursor];
          if (element) openPage(element.page);
        },
      },
      {
        keys: ["1", "2", "3", "4", "5"],
        label: "Switch view",
        run: (event: KeyboardEvent) => {
          const index = Number(event.key) - 1;
          const next = MODES[index];
          if (!next) return false;
          setMode(next.id);
          setKindFilter(
            next.id === "figures" ? "figure" : next.id === "tables" ? "table" : "",
          );
          setCursor(0);
        },
      },
    ],
    [move, cursorList, cursor, openPage],
  );
  useShortcuts(shortcuts);

  if (loading) {
    return (
      <div className="py-20 text-center">
        <Spinner label="Loading every element" />
      </div>
    );
  }

  return (
    <div>
      <ShortcutHelp shortcuts={shortcuts} />
      {/* Search across every element kind at once. */}
      <Card className="mb-4 p-3">
        <div className="flex flex-wrap items-end gap-2">
          <TextInput
            ref={searchRef}
            type="search"
            value={needle}
            onChange={setNeedle}
            onEnter={runFind}
            label="Find a word or value anywhere in this paper"
            placeholder="BLEU, 41.0, dropout, Figure 3, p<0.05"
            className="min-w-[280px] flex-1"
          />
          <Button variant="primary" onClick={runFind} disabled={finding}>
            {finding ? "Searching..." : "Find every occurrence"}
          </Button>
          {found ? (
            <Button
              variant="ghost"
              onClick={() => {
                setFound(null);
                setNeedle("");
              }}
            >
              Clear
            </Button>
          ) : null}
        </div>
        <p className="mt-1.5 text-xs text-ink-faint">
          Exhaustive and literal, across body text, headings, captions, table cells,
          references and any text read out of a figure.
        </p>
      </Card>

      {found ? (
        <section className="mb-6">
          <SectionTitle>
            {found.total} occurrence{found.total === 1 ? "" : "s"} of “{needle.trim()}”
          </SectionTitle>
          {found.total === 0 ? (
            <Empty title="Not found anywhere in this paper">
              Including table cells and figure text.
            </Empty>
          ) : (
            <>
              <div className="mb-3 flex flex-wrap gap-1.5">
                {Object.entries(found.by_kind).map(([kind, count]) => (
                  <Badge key={kind}>
                    {count} in {KIND_LABELS[kind]?.toLowerCase() ?? kind}
                  </Badge>
                ))}
              </div>
              <ul className="space-y-2">
                {found.matches.map((match) => (
                  <Card as="li" key={match.element_id} className="p-3">
                    <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-faint">
                      <KindDot kind={match.kind} />
                      <span className="font-medium text-ink-muted">
                        {match.label || KIND_LABELS[match.kind] || match.kind}
                      </span>
                      <button
                        onClick={() => openPage(match.page)}
                        className="rounded px-1 hover:bg-raised hover:text-ink"
                      >
                        page {match.page}
                      </button>
                      {match.section !== "unknown" ? (
                        <Badge>{SECTION_LABELS[match.section] ?? match.section}</Badge>
                      ) : null}
                      {/* Which part of the element matched, so a hit in a table
                          cell is distinguishable from one in its caption. */}
                      <span className="text-accent">in {match.where.join(", ")}</span>
                    </div>
                    <Snippet match={match} />
                    {match.image_digest ? (
                      <FigureImage
                        digest={match.image_digest}
                        alt={match.caption || "matched figure"}
                        className="mt-2 max-h-40 w-auto"
                      />
                    ) : null}
                  </Card>
                ))}
              </ul>
            </>
          )}
        </section>
      ) : null}

      <div className="mb-4 flex flex-wrap items-center gap-1">
        {MODES.map((entry) => (
          <button
            key={entry.id}
            onClick={() => {
              setMode(entry.id);
              setKindFilter(entry.id === "figures" ? "figure" : entry.id === "tables" ? "table" : "");
              setSectionFilter("");
            }}
            className={cx(
              "rounded-lg px-3 py-1.5 text-sm transition-colors",
              mode === entry.id
                ? "bg-accent text-white"
                : "border border-line text-ink-muted hover:bg-raised",
            )}
          >
            {entry.label}
            {entry.id === "figures" ? ` (${figures.length})` : null}
            {entry.id === "tables" ? ` (${tables.length})` : null}
          </button>
        ))}
      </div>

      {error ? <ErrorNotice error={error} /> : null}

      {mode === "figures" ? (
        figures.length === 0 ? (
          <Empty title="No figures were extracted from this paper" />
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {figures.map((figure) => (
              <Card as="li" key={figure.id} id={`element-${figure.id}`} className="p-3">
                <FigureImage
                  digest={figure.image?.digest ?? ""}
                  alt={figure.caption || `Figure on page ${figure.page}`}
                  className="max-h-72 w-full object-contain"
                />
                <div className="mt-2 flex items-center gap-2 text-[11px] text-ink-faint">
                  <span className="font-medium text-ink-muted">
                    {figure.label || "Figure"}
                  </span>
                  <button
                    onClick={() => openPage(figure.page)}
                    className="rounded px-1 hover:bg-raised hover:text-ink"
                  >
                    page {figure.page}
                  </button>
                  <span>
                    {figure.image?.width}×{figure.image?.height}
                  </span>
                  {String(figure.extra?.source ?? "") === "rendered" ? (
                    <Badge tone="neutral" title="Recovered by cropping the page, because this figure is drawn in vector primitives and has no embedded image.">
                      cropped
                    </Badge>
                  ) : null}
                </div>
                {figure.caption ? (
                  <p className="mt-1 text-xs leading-relaxed text-ink-muted">
                    {figure.caption}
                  </p>
                ) : null}
                {figure.image?.ocr_text ? (
                  <details className="mt-1.5">
                    <summary className="cursor-pointer text-[11px] text-ink-faint">
                      Text inside this figure
                    </summary>
                    <p className="mt-1 rounded bg-raised p-1.5 text-[11px] text-ink-muted">
                      {figure.image.ocr_text}
                    </p>
                  </details>
                ) : null}
              </Card>
            ))}
          </ul>
        )
      ) : null}

      {mode === "tables" ? (
        tables.length === 0 ? (
          <Empty title="No tables were extracted from this paper" />
        ) : (
          <ul className="space-y-4">
            {tables.map((table) => (
              <ElementCard key={table.id} element={table} onOpenPage={openPage} />
            ))}
          </ul>
        )
      ) : null}

      {mode === "sections" ? (
        <div className="space-y-6">
          {bySection
            .sort((a, b) => b[1].length - a[1].length)
            .map(([section, items]) => (
              <section key={section}>
                <SectionTitle>
                  {SECTION_LABELS[section] ?? section} · {items.length}
                </SectionTitle>
                <ul className="space-y-2">
                  {items.slice(0, 40).map((element) => (
                    <ElementCard key={element.id} element={element} onOpenPage={openPage} />
                  ))}
                </ul>
                {items.length > 40 ? (
                  <p className="mt-2 text-xs text-ink-faint">
                    and {items.length - 40} more in this section
                  </p>
                ) : null}
              </section>
            ))}
        </div>
      ) : null}

      {mode === "pages" ? (
        <div className="space-y-6">
          {byPage.map(([page, items]) => (
            <section key={page}>
              <SectionTitle
                action={
                  <Button size="sm" variant="ghost" onClick={() => openPage(page)}>
                    Open in the reader
                  </Button>
                }
              >
                Page {page} · {items.length} elements
              </SectionTitle>
              <ul className="space-y-2">
                {items.map((element) => (
                  <ElementCard key={element.id} element={element} onOpenPage={openPage} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      ) : null}

      {mode === "list" ? (
        <div>
          <div className="mb-3 flex flex-wrap gap-1.5">
            <button
              onClick={() => setKindFilter("")}
              className={cx(
                "rounded-md px-2 py-1 text-[11px]",
                !kindFilter ? "bg-raised font-medium text-ink" : "text-ink-faint hover:bg-raised",
              )}
            >
              all {elements.length}
            </button>
            {Object.entries(elementCounts)
              .sort((a, b) => b[1] - a[1])
              .map(([kind, count]) => (
                <button
                  key={kind}
                  onClick={() => setKindFilter(kind === kindFilter ? "" : kind)}
                  className={cx(
                    "inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px]",
                    kindFilter === kind
                      ? "bg-raised font-medium text-ink"
                      : "text-ink-faint hover:bg-raised",
                  )}
                >
                  <KindDot kind={kind} />
                  {KIND_LABELS[kind] ?? kind} {count}
                </button>
              ))}
          </div>
          {sectionFilter ? (
            <p className="mb-2 text-xs text-ink-faint">
              Showing {SECTION_LABELS[sectionFilter] ?? sectionFilter} only.{" "}
              <button onClick={() => setSectionFilter("")} className="text-accent hover:underline">
                Clear
              </button>
            </p>
          ) : null}
          <ul className="space-y-2">
            {filtered.slice(0, 400).map((element) => (
              <ElementCard key={element.id} element={element} onOpenPage={openPage} />
            ))}
          </ul>
          {filtered.length > 400 ? (
            <p className="mt-3 text-xs text-ink-faint">
              Showing the first 400 of {filtered.length}. Narrow by kind or section to see the rest.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
