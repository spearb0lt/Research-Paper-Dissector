"use client";

/**
 * Rendering a cited answer, and the evidence behind it.
 *
 * The markdown renderer here is small and deliberate rather than a library. It
 * handles exactly what the prompts ask models to produce, headings, lists,
 * tables, bold and code, and it builds React elements rather than HTML
 * strings. Nothing model written is ever passed to dangerouslySetInnerHTML,
 * which is the only way to be sure a model cannot inject markup into the page.
 *
 * Citation markers are turned into buttons that scroll to the evidence, so a
 * claim can always be traced to the excerpt and the page it came from.
 */

import { Fragment, type ReactNode } from "react";

import { blobUrl } from "@/lib/api";
import type { Citation, Hit, SearchResult } from "@/lib/types";

import { SECTION_LABELS } from "./ElementCard";
import { Badge, Card, KindDot, Notice, cx } from "./ui";

/** Inline markup: bold, code, and citation markers. */
function inline(text: string, onCite?: (n: number) => void): ReactNode[] {
  const out: ReactNode[] = [];
  let cursor = 0;
  let key = 0;

  // Bold and code first, then citations inside whatever is left as plain text.
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[\d+(?:\s*,\s*\d+)*\])/g;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > cursor) out.push(text.slice(cursor, index));
    const token = match[0];

    if (token.startsWith("**")) {
      out.push(
        <strong key={key++} className="font-semibold text-ink">
          {token.slice(2, -2)}
        </strong>,
      );
    } else if (token.startsWith("`")) {
      out.push(<code key={key++}>{token.slice(1, -1)}</code>);
    } else {
      const numbers = token
        .slice(1, -1)
        .split(",")
        .map((part) => Number(part.trim()))
        .filter((value) => Number.isFinite(value));
      out.push(
        <span key={key++} className="whitespace-nowrap">
          {numbers.map((n, position) => (
            <Fragment key={n}>
              {position > 0 ? <span className="text-ink-faint">,</span> : null}
              <button
                onClick={() => onCite?.(n)}
                title={`Jump to excerpt ${n}`}
                className="mx-px rounded bg-accent-soft px-1 align-super text-[10px] font-medium text-accent-ink hover:bg-accent hover:text-white"
              >
                {n}
              </button>
            </Fragment>
          ))}
        </span>,
      );
    }
    cursor = index + token.length;
  }
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}

function MarkdownTable({ rows, onCite }: { rows: string[][]; onCite?: (n: number) => void }) {
  const [header, ...body] = rows;
  return (
    <table>
      <thead>
        <tr>
          {header.map((cell, index) => (
            <th key={index}>{inline(cell, onCite)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {body.map((row, rowIndex) => (
          <tr key={rowIndex}>
            {row.map((cell, cellIndex) => (
              <td key={cellIndex}>{inline(cell, onCite)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function splitRow(line: string): string[] {
  return line
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());
}

export function Markdown({
  text,
  onCite,
}: {
  text: string;
  onCite?: (n: number) => void;
}) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  let key = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    // A table is a header row, a separator row of dashes, then body rows.
    if (
      line.trim().startsWith("|") &&
      index + 1 < lines.length &&
      /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[index + 1])
    ) {
      const rows: string[][] = [splitRow(line)];
      index += 2;
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        rows.push(splitRow(lines[index]));
        index += 1;
      }
      blocks.push(<MarkdownTable key={key++} rows={rows} onCite={onCite} />);
      continue;
    }

    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      const Tag = (["h1", "h2", "h3"] as const)[heading[1].length - 1];
      blocks.push(<Tag key={key++}>{inline(heading[2], onCite)}</Tag>);
      index += 1;
      continue;
    }

    if (/^\s*[-*•]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*•]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*•]\s+/, ""));
        index += 1;
      }
      blocks.push(
        <ul key={key++}>
          {items.map((item, position) => (
            <li key={position}>{inline(item, onCite)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+[.)]\s+/, ""));
        index += 1;
      }
      blocks.push(
        <ol key={key++}>
          {items.map((item, position) => (
            <li key={position}>{inline(item, onCite)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    // Everything else is a paragraph, running until a blank line.
    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() && !/^[#|]|^\s*[-*•\d]/.test(lines[index])) {
      paragraph.push(lines[index]);
      index += 1;
    }
    if (paragraph.length) {
      blocks.push(<p key={key++}>{inline(paragraph.join(" "), onCite)}</p>);
    } else {
      blocks.push(<p key={key++}>{inline(line, onCite)}</p>);
      index += 1;
    }
  }

  return <div className="prose-answer text-[14px] text-ink-muted">{blocks}</div>;
}

export function EvidenceCard({
  citation,
  hit,
  highlighted,
}: {
  citation: Citation;
  hit?: Hit;
  highlighted?: boolean;
}) {
  const text = hit?.text ?? "";
  return (
    <Card
      as="li"
      id={`evidence-${citation.n}`}
      className={cx(
        "scroll-mt-20 p-3 transition-shadow",
        highlighted && "ring-2 ring-accent",
      )}
    >
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-faint">
        <span className="grid h-4 w-4 place-items-center rounded bg-accent-soft text-[10px] font-semibold text-accent-ink">
          {citation.n}
        </span>
        <KindDot kind={citation.kind} />
        <span className="font-medium text-ink-muted">
          {citation.label || citation.kind}
        </span>
        {citation.page ? <span>page {citation.page}</span> : null}
        {citation.section && citation.section !== "unknown" ? (
          <Badge>{SECTION_LABELS[citation.section] ?? citation.section}</Badge>
        ) : null}
        {/* Which legs found it, so a result is explainable rather than magic. */}
        {hit
          ? Object.entries(hit.legs).map(([leg, info]) =>
              // A label pin is an exact lookup rather than a ranking, so it
              // says so instead of showing a meaningless rank of one.
              leg === "label" ? (
                <Badge key={leg} tone="accent" title="Named directly in the question">
                  named
                </Badge>
              ) : (
                <span key={leg} title={`${leg} rank ${info.rank}, score ${info.score}`}>
                  {leg} #{info.rank}
                </span>
              ),
            )
          : null}
      </div>

      {citation.image_digest ? (
        // eslint-disable-next-line @next/next/no-img-element -- content addressed
        <img
          src={blobUrl(citation.image_digest)}
          alt={citation.label || "retrieved figure"}
          loading="lazy"
          className="mb-2 max-h-56 rounded-lg border border-line bg-white"
        />
      ) : null}

      {text ? (
        <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-ink-muted">
          {text.length > 900 ? `${text.slice(0, 900)}...` : text}
        </p>
      ) : null}
    </Card>
  );
}

export function RetrievalSummary({ retrieval }: { retrieval: SearchResult | null }) {
  if (!retrieval) return null;
  const skipped = Object.entries(retrieval.legs_skipped);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-faint">
        <span>Retrieved by</span>
        {retrieval.legs_used.map((leg) => (
          <Badge key={leg} tone="accent">
            {leg} ×{(retrieval.weights[leg] ?? 1).toFixed(2)}
          </Badge>
        ))}
        {retrieval.rerank_used ? <Badge tone="good">reranked</Badge> : null}
        <span>from {retrieval.candidate_count} candidates</span>
      </div>
      {skipped.length ? (
        <Notice tone="warn" title="Some retrieval was skipped">
          <ul className="list-disc space-y-0.5 pl-4">
            {skipped.map(([leg, reason]) => (
              <li key={leg}>
                <span className="font-medium">{leg}</span>: {reason}
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}
    </div>
  );
}
