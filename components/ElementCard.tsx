"use client";

/**
 * Rendering one extracted element, whatever kind it is.
 *
 * A table is drawn from its `grid` rather than from the parser's HTML. The HTML
 * is kept for diagnosis and is never inserted into the page: injecting markup
 * that came out of a PDF is how a document viewer becomes an XSS vector, and
 * there is no upside because the grid renders identically.
 *
 * A table whose structure the fast tier inferred says so, and offers the
 * rendered image of the region, which is authoritative where the grid is a
 * guess. That pairing is the whole reason the parser renders table regions.
 */

import { useState } from "react";

import { blobUrl } from "@/lib/api";
import type { Element } from "@/lib/types";

import { ElementNotes } from "./Notes";
import { Badge, Button, Card, KindDot, Notice, cx } from "./ui";

export const KIND_LABELS: Record<string, string> = {
  title: "Title",
  authors: "Authors",
  abstract: "Abstract",
  heading: "Heading",
  paragraph: "Paragraph",
  list_item: "List item",
  table: "Table",
  figure: "Figure",
  caption: "Caption",
  formula: "Formula",
  code: "Code",
  footnote: "Footnote",
  reference: "Reference",
  form_field: "Form field",
  page_header: "Running head",
  page_footer: "Page footer",
  other: "Other",
};

export const SECTION_LABELS: Record<string, string> = {
  front: "Front matter",
  abstract: "Abstract",
  introduction: "Introduction",
  background: "Background",
  related_work: "Related work",
  methods: "Methods",
  data: "Data",
  experiments: "Experimental setup",
  results: "Results",
  discussion: "Discussion",
  limitations: "Limitations",
  conclusion: "Conclusion",
  ethics: "Ethics and impact",
  acknowledgements: "Acknowledgements",
  references: "References",
  appendix: "Appendix",
  unknown: "Unclassified",
};

export function TableGrid({
  grid,
  compact,
}: {
  grid: string[][];
  compact?: boolean;
}) {
  if (!grid.length) return null;
  const [header, ...rows] = grid;
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className={cx("w-full border-collapse", compact ? "text-[11px]" : "text-xs")}>
        <thead>
          <tr className="bg-raised">
            {header.map((cell, index) => (
              <th
                key={index}
                className="border-b border-line px-2 py-1.5 text-left font-semibold text-ink"
              >
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="even:bg-raised/40">
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className="border-b border-line px-2 py-1 align-top text-ink-muted"
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FigureImage({
  digest,
  alt,
  className,
}: {
  digest: string;
  alt: string;
  className?: string;
}) {
  if (!digest) return null;
  return (
    // The blob route is content addressed and already immutable, so next/image
    // would proxy it for no benefit and break the permanent cache header.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={blobUrl(digest)}
      alt={alt}
      loading="lazy"
      className={cx("rounded-lg border border-line bg-white", className)}
    />
  );
}

export function ElementBody({ element }: { element: Element }) {
  const [showImage, setShowImage] = useState(false);
  const confident = element.extra?.structure_confident !== false;

  if (element.kind === "figure") {
    return (
      <div>
        <FigureImage
          digest={element.image?.digest ?? ""}
          alt={element.caption || `Figure on page ${element.page}`}
          className="max-h-[420px] w-auto"
        />
        {element.image?.ocr_text ? (
          <details className="mt-2">
            <summary className="cursor-pointer text-xs text-ink-faint hover:text-ink">
              Text read from inside this figure
            </summary>
            <p className="mt-1 rounded-lg bg-raised p-2 text-xs leading-relaxed text-ink-muted">
              {element.image.ocr_text}
            </p>
          </details>
        ) : null}
      </div>
    );
  }

  if (element.kind === "table" && element.table) {
    return (
      <div>
        {!confident ? (
          <div className="mb-2">
            <Notice tone="warn" title="Column structure was inferred">
              This table has no ruling lines, so the columns were worked out from
              spacing. Check the numbers against the image before relying on them.
              {element.image?.digest ? (
                <Button
                  size="sm"
                  className="ml-2"
                  onClick={() => setShowImage((current) => !current)}
                >
                  {showImage ? "Hide" : "Show"} the original
                </Button>
              ) : null}
            </Notice>
          </div>
        ) : null}
        {showImage && element.image?.digest ? (
          <FigureImage
            digest={element.image.digest}
            alt="The table as printed"
            className="mb-2 max-h-[420px] w-auto"
          />
        ) : null}
        <TableGrid grid={element.table.grid} />
      </div>
    );
  }

  if (element.kind === "formula" || element.kind === "code") {
    return (
      <pre className="overflow-x-auto rounded-lg bg-raised p-2.5 font-mono text-xs text-ink-muted">
        {element.text}
      </pre>
    );
  }

  return (
    <p
      className={cx(
        "whitespace-pre-wrap text-[13px] leading-relaxed text-ink-muted",
        element.kind === "heading" && "font-semibold text-ink",
      )}
    >
      {element.text}
    </p>
  );
}

export function ElementCard({
  element,
  onOpenPage,
  highlight,
}: {
  element: Element;
  onOpenPage?: (page: number) => void;
  highlight?: boolean;
}) {
  return (
    <Card
      as="li"
      className={cx("p-3", highlight && "ring-2 ring-accent")}
      id={`element-${element.id}`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-faint">
        <KindDot kind={element.kind} />
        <span className="font-medium text-ink-muted">
          {element.label || KIND_LABELS[element.kind] || element.kind}
        </span>
        {element.page ? (
          onOpenPage ? (
            <button
              onClick={() => onOpenPage(element.page)}
              className="rounded px-1 hover:bg-raised hover:text-ink"
              title="Show this on the page"
            >
              page {element.page}
            </button>
          ) : (
            <span>page {element.page}</span>
          )
        ) : null}
        {element.section && element.section !== "unknown" ? (
          <Badge>{SECTION_LABELS[element.section] ?? element.section}</Badge>
        ) : null}
        {element.section_path?.length ? (
          <span className="truncate" title={element.section_path.join(" > ")}>
            {element.section_path.slice(-1)[0]}
          </span>
        ) : null}
      </div>

      {element.caption && element.kind !== "caption" ? (
        <p className="mb-2 text-xs italic leading-relaxed text-ink-faint">
          {element.caption}
        </p>
      ) : null}

      <ElementBody element={element} />

      <ElementNotes elementId={element.id} />
    </Card>
  );
}
