"use client";

/**
 * The page reader: the real page, with every extracted element drawn over it.
 *
 * Three views of the same page, because they answer different questions.
 *
 * **Page** renders the page server side and draws a box over every element.
 * This is how you check a parse: a table whose columns are wrong, a figure
 * cropped badly, a heading that was missed, all become obvious the moment the
 * boxes are drawn. It is also what makes a citation verifiable rather than
 * merely plausible.
 *
 * **Original** embeds the real PDF. Selectable text, native search, native
 * zoom, and no server cost at all. The overlay is impossible here: boxes can be
 * drawn on top of an iframe but not aligned to it, because the browser's viewer
 * owns its own scroll and zoom and will not report either.
 *
 * **Elements** lists what was extracted in reading order, which is the view
 * that works when a page render fails.
 *
 * Boxes are positioned in percentages of the page's own point dimensions, so
 * the overlay stays correct at any width without recomputing anything.
 */

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ShortcutHelp, useShortcuts, type Shortcut } from "@/components/Keyboard";

import { KIND_LABELS, SECTION_LABELS } from "@/components/ElementCard";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorNotice,
  KindDot,
  Notice,
  Spinner,
  Toggle,
  cx,
} from "@/components/ui";
import { blobUrl, listElements, pageImageUrl } from "@/lib/api";
import type { Element } from "@/lib/types";

import { usePaper } from "../PaperChrome";

type Mode = "page" | "original" | "elements";

const OVERLAY: Record<string, string> = {
  figure: "border-kind-figure bg-kind-figure/10",
  table: "border-kind-table bg-kind-table/10",
  formula: "border-kind-formula bg-kind-formula/10",
  reference: "border-kind-reference bg-kind-reference/10",
  heading: "border-accent bg-accent/10",
};

export function ReaderView({ paperId }: { paperId: number }) {
  const params = useSearchParams();
  const { paper } = usePaper();

  const [page, setPage] = useState(() => {
    const requested = Number(params.get("page") ?? 1);
    return Number.isFinite(requested) && requested > 0 ? requested : 1;
  });
  const [mode, setMode] = useState<Mode>("page");
  const [elements, setElements] = useState<Element[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [showOverlay, setShowOverlay] = useState(true);
  const [selected, setSelected] = useState<number | null>(null);
  const [pageFailed, setPageFailed] = useState(false);
  const [pageLoading, setPageLoading] = useState(true);
  const topRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLoading(true);
    listElements(paperId, { limit: 5000 })
      .then((response) => setElements(response.elements))
      .catch(setError)
      .finally(() => setLoading(false));
  }, [paperId]);

  const geometry = useMemo(
    () => paper.pages.find((entry) => entry.number === page),
    [paper.pages, page],
  );

  const onPage = useMemo(
    () => elements.filter((element) => element.page === page),
    [elements, page],
  );
  const withBoxes = useMemo(() => onPage.filter((e) => e.bbox), [onPage]);

  const go = useCallback(
    (next: number) => {
      const clamped = Math.max(1, Math.min(paper.num_pages || 1, next));
      setPage(clamped);
      setSelected(null);
      setPageFailed(false);
      setPageLoading(true);
      topRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    },
    [paper.num_pages],
  );

  const shortcuts: Shortcut[] = useMemo(
    () => [
      { keys: ["ArrowLeft", "h"], label: "Previous page", run: () => go(page - 1) },
      { keys: ["ArrowRight", "l"], label: "Next page", run: () => go(page + 1) },
      { keys: ["g"], label: "First page", run: () => go(1) },
      { keys: ["G"], label: "Last page", run: () => go(paper.num_pages) },
      {
        keys: ["o"],
        label: "Toggle the extraction overlay",
        run: () => setShowOverlay((current) => !current),
      },
      {
        keys: ["v"],
        label: "Cycle page, original PDF, elements",
        run: () =>
          setMode((current) =>
            current === "page" ? "original" : current === "original" ? "elements" : "page",
          ),
      },
    ],
    [go, page, paper.num_pages],
  );
  useShortcuts(shortcuts);

  // The next page is requested as soon as this one settles, so turning a page
  // is instant after the first. A render is 38ms and is cached by digest, so
  // this costs one request and never repeats.
  useEffect(() => {
    if (mode !== "page" || page >= (paper.num_pages || 0)) return;
    const image = new window.Image();
    image.src = pageImageUrl(paperId, page + 1);
  }, [mode, page, paperId, paper.num_pages]);

  if (loading) {
    return (
      <div className="py-20 text-center">
        <Spinner label="Loading the page" />
      </div>
    );
  }
  if (error) return <ErrorNotice error={error} />;

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
      <ShortcutHelp shortcuts={shortcuts} />
      <div>
        <Card className="mb-3 flex flex-wrap items-center gap-2 p-2.5">
          <Button size="sm" onClick={() => go(page - 1)} disabled={page <= 1}>
            ← Prev
          </Button>
          <span className="text-sm text-ink-muted">
            <input
              type="number"
              value={page}
              min={1}
              max={paper.num_pages}
              onChange={(event) => go(Number(event.target.value))}
              className="w-14 rounded border border-line bg-surface px-1.5 py-0.5 text-center text-sm"
            />
            <span className="ml-1">of {paper.num_pages}</span>
          </span>
          <Button size="sm" onClick={() => go(page + 1)} disabled={page >= paper.num_pages}>
            Next →
          </Button>

          <div className="ml-2 flex overflow-hidden rounded-lg border border-line">
            {(
              [
                ["page", "Page"],
                ["original", "Original PDF"],
                ["elements", "Elements"],
              ] as [Mode, string][]
            ).map(([id, label]) => (
              <button
                key={id}
                onClick={() => setMode(id)}
                className={cx(
                  "px-2.5 py-1 text-xs transition-colors",
                  mode === id
                    ? "bg-accent text-white"
                    : "text-ink-muted hover:bg-raised",
                )}
              >
                {label}
              </button>
            ))}
          </div>

          {mode === "page" ? (
            <Toggle
              checked={showOverlay}
              onChange={setShowOverlay}
              label="Show what was extracted"
            />
          ) : null}

          <a
            href={blobUrl(paper.blob_digest, "pdf")}
            target="_blank"
            rel="noreferrer noopener"
            className="ml-auto text-xs text-accent hover:underline"
          >
            Open in a new tab
          </a>
        </Card>

        {mode === "original" ? (
          <div>
            <object
              data={`${blobUrl(paper.blob_digest, "pdf")}#page=${page}`}
              type="application/pdf"
              className="h-[80vh] w-full rounded-lg border border-line bg-white"
            >
              {/* Every current browser embeds a PDF, but a locked down one or a
                  mobile browser may not, so the fallback is a real link rather
                  than an empty frame. */}
              <div className="p-6 text-center text-sm text-ink-muted">
                This browser will not embed a PDF.{" "}
                <a
                  href={blobUrl(paper.blob_digest, "pdf")}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-accent hover:underline"
                >
                  Open it in a new tab
                </a>
                .
              </div>
            </object>
            <p className="mt-1.5 text-[11px] text-ink-faint">
              The real PDF, with selectable text and the browser&apos;s own search.
              The extraction overlay is only available on the Page view: boxes
              cannot be aligned to an embedded viewer, which controls its own
              scroll and zoom.
            </p>
          </div>
        ) : null}

        {mode === "page" ? (
          <div ref={topRef}>
            {pageFailed ? (
              <Notice tone="warn" title="This page could not be rendered">
                Switch to Original PDF or Elements. The PDF may be unusual, or
                this deployment may have lost the stored file.
              </Notice>
            ) : (
              <div className="relative inline-block w-full">
                {pageLoading ? (
                  <div className="absolute inset-0 grid place-items-center">
                    <Spinner label="Rendering" />
                  </div>
                ) : null}
                {/* eslint-disable-next-line @next/next/no-img-element -- rendered
                    server side and content addressed; next/image would proxy it
                    for no benefit. */}
                <img
                  key={page}
                  src={pageImageUrl(paperId, page)}
                  alt={`Page ${page}`}
                  onLoad={() => setPageLoading(false)}
                  onError={() => {
                    setPageLoading(false);
                    setPageFailed(true);
                  }}
                  className={cx(
                    "w-full rounded-lg border border-line bg-white transition-opacity",
                    pageLoading && "opacity-40",
                  )}
                />
                {showOverlay && geometry
                  ? withBoxes.map((element) => {
                      const box = element.bbox!;
                      return (
                        <button
                          key={element.id}
                          onClick={() => setSelected(element.id)}
                          title={`${KIND_LABELS[element.kind] ?? element.kind}${element.label ? `: ${element.label}` : ""}`}
                          className={cx(
                            "absolute rounded border-2 transition-opacity hover:opacity-100",
                            OVERLAY[element.kind] ?? "border-kind-text bg-kind-text/5",
                            selected === element.id
                              ? "opacity-100 ring-2 ring-accent"
                              : "opacity-45",
                          )}
                          style={{
                            left: `${(box.x0 / geometry.width) * 100}%`,
                            top: `${(box.y0 / geometry.height) * 100}%`,
                            width: `${((box.x1 - box.x0) / geometry.width) * 100}%`,
                            height: `${((box.y1 - box.y0) / geometry.height) * 100}%`,
                          }}
                        />
                      );
                    })
                  : null}
              </div>
            )}
            {showOverlay && !pageFailed ? (
              <p className="mt-1.5 text-[11px] text-ink-faint">
                {withBoxes.length} extracted element
                {withBoxes.length === 1 ? "" : "s"} outlined. Click one to select
                it. If a box is in the wrong place, the extraction is wrong there.
              </p>
            ) : null}
          </div>
        ) : null}

        {mode === "elements" ? (
          <ul className="space-y-2">
            {onPage.length === 0 ? (
              <Empty title="Nothing was extracted from this page" />
            ) : null}
            {onPage.map((element) => (
              <Card
                as="li"
                key={element.id}
                id={`reader-${element.id}`}
                className={cx("p-3", selected === element.id && "ring-2 ring-accent")}
              >
                <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-ink-faint">
                  <KindDot kind={element.kind} />
                  <span className="font-medium text-ink-muted">
                    {element.label || KIND_LABELS[element.kind] || element.kind}
                  </span>
                  {element.section !== "unknown" ? (
                    <Badge>{SECTION_LABELS[element.section] ?? element.section}</Badge>
                  ) : null}
                </div>
                {element.image?.digest ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={blobUrl(element.image.digest)}
                    alt={element.caption || "figure"}
                    loading="lazy"
                    className="max-h-80 rounded border border-line bg-white"
                  />
                ) : null}
                {element.text ? (
                  <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink-muted">
                    {element.text}
                  </p>
                ) : null}
              </Card>
            ))}
          </ul>
        ) : null}
      </div>

      <aside className="lg:sticky lg:top-16 lg:self-start">
        <Card className="p-3">
          <h2 className="mb-2 text-sm font-semibold text-ink">
            {onPage.length} element{onPage.length === 1 ? "" : "s"} on this page
          </h2>
          {onPage.length === 0 ? (
            <Empty title="Nothing here" />
          ) : (
            <ul className="max-h-[68vh] space-y-1 overflow-y-auto">
              {onPage.map((element) => (
                <li key={element.id}>
                  <button
                    onClick={() => {
                      setSelected(element.id);
                      if (mode === "elements") {
                        document
                          .getElementById(`reader-${element.id}`)
                          ?.scrollIntoView({ behavior: "smooth", block: "center" });
                      }
                    }}
                    className={cx(
                      "flex w-full items-start gap-1.5 rounded px-1.5 py-1 text-left text-xs",
                      selected === element.id
                        ? "bg-accent-soft text-accent-ink"
                        : "text-ink-muted hover:bg-raised",
                    )}
                  >
                    <KindDot kind={element.kind} />
                    <span className="min-w-0 flex-1">
                      <span className="font-medium">
                        {element.label || KIND_LABELS[element.kind] || element.kind}
                      </span>
                      {element.text ? (
                        <span className="ml-1 text-ink-faint">
                          {element.text.slice(0, 54)}
                          {element.text.length > 54 ? "..." : ""}
                        </span>
                      ) : null}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {geometry?.needs_ocr ? (
          <div className="mt-3">
            <Notice tone="warn" title="This page has no text layer">
              It is an image of a page. Nothing on it is searchable unless the
              paper is re-parsed with OCR turned on.
            </Notice>
          </div>
        ) : null}
      </aside>
    </div>
  );
}
