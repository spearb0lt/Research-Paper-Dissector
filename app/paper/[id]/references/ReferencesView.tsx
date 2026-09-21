"use client";

/**
 * The reference list, with somewhere to look and something to add.
 *
 * View and Add are deliberately two buttons rather than one. Fetching every
 * cited paper automatically would fill a library with things nobody chose, so
 * the flow is: look at it, decide, then add it. View opens the landing page in
 * a new tab, and Add appears only where a freely downloadable PDF actually
 * exists, because offering a download that leads to a paywall is worse than
 * not offering one.
 *
 * Resolution against arXiv and Crossref is a button rather than automatic: it
 * is one network request per unresolved entry, and a reference list is long.
 * Whatever an entry prints itself, an arXiv id or a DOI, is already resolved
 * when the page loads, because that costs nothing.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorNotice,
  Notice,
  SectionTitle,
  Spinner,
  TextInput,
} from "@/components/ui";
import { addReference, listReferences } from "@/lib/api";
import type { Reference } from "@/lib/types";

function ReferenceRow({
  reference,
  paperId,
  onAdded,
}: {
  reference: Reference;
  paperId: number;
  onAdded: (newPaperId: number) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [added, setAdded] = useState<number | null>(reference.in_library);

  const resolved = reference.resolved;
  const title = resolved?.title || reference.title || "";
  const authors = resolved?.authors?.length ? resolved.authors : reference.authors;
  const year = resolved?.year || reference.year;

  async function add() {
    setAdding(true);
    setError(null);
    try {
      const response = await addReference(paperId, reference.element_id);
      setAdded(response.paper.id);
      onAdded(response.paper.id);
    } catch (exception) {
      setError(exception);
    } finally {
      setAdding(false);
    }
  }

  return (
    <Card as="li" className="p-3">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 min-w-[2rem] text-[11px] font-medium text-ink-faint">
          {reference.number !== null ? `[${reference.number}]` : "-"}
        </span>

        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium leading-snug text-ink">
            {title || reference.text.slice(0, 120)}
          </p>
          {authors.length || year ? (
            <p className="mt-0.5 text-[11px] text-ink-faint">
              {authors.slice(0, 4).join(", ")}
              {authors.length > 4 ? " and others" : ""}
              {year ? ` · ${year}` : ""}
              {resolved?.venue ? ` · ${resolved.venue}` : ""}
            </p>
          ) : null}

          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {resolved?.source ? (
              <Badge
                tone={resolved.source === "printed" ? "neutral" : "accent"}
                title={
                  resolved.source === "printed"
                    ? "The entry printed an identifier, so no lookup was needed."
                    : `Matched on ${resolved.source} with ${Math.round(resolved.confidence * 100)}% title overlap.`
                }
              >
                {resolved.source}
              </Badge>
            ) : null}
            {resolved?.arxiv_id ? <Badge>arXiv:{resolved.arxiv_id}</Badge> : null}
            {resolved?.doi ? (
              <Badge title={resolved.doi}>doi</Badge>
            ) : null}
            <span className="text-[11px] text-ink-faint">page {reference.page}</span>
          </div>

          {error ? (
            <div className="mt-2">
              <ErrorNotice error={error} />
            </div>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-col gap-1.5">
          {resolved?.view_url ? (
            <a href={resolved.view_url} target="_blank" rel="noreferrer noopener">
              <Button size="sm" className="w-full">
                View
              </Button>
            </a>
          ) : (
            <Button size="sm" disabled title="Nothing to open: this entry has no resolvable link.">
              View
            </Button>
          )}

          {added ? (
            <Link href={`/paper/${added}`}>
              <Button size="sm" variant="ghost" className="w-full">
                In library
              </Button>
            </Link>
          ) : reference.can_add ? (
            <Button size="sm" variant="primary" onClick={add} disabled={adding}>
              {adding ? "Adding..." : "Add"}
            </Button>
          ) : (
            <Button
              size="sm"
              disabled
              title="No freely downloadable PDF was found. Open it with View and upload the file if you have access."
            >
              Add
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}

export function ReferencesView({ paperId }: { paperId: number }) {
  const [references, setReferences] = useState<Reference[]>([]);
  const [loading, setLoading] = useState(true);
  const [resolving, setResolving] = useState(false);
  const [resolved, setResolved] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [filter, setFilter] = useState("");

  const load = useCallback(
    (online: boolean) => {
      const setter = online ? setResolving : setLoading;
      setter(true);
      listReferences(paperId, online)
        .then((next) => {
          setReferences(next);
          if (online) setResolved(true);
        })
        .catch(setError)
        .finally(() => setter(false));
    },
    [paperId],
  );

  useEffect(() => load(false), [load]);

  const shown = references.filter((reference) => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return true;
    return (
      (reference.title || "").toLowerCase().includes(needle) ||
      reference.text.toLowerCase().includes(needle) ||
      (reference.authors || []).join(" ").toLowerCase().includes(needle)
    );
  });

  const addable = references.filter((r) => r.can_add).length;
  const unresolved = references.filter((r) => !r.resolved).length;

  if (loading) {
    return (
      <div className="py-20 text-center">
        <Spinner label="Loading the reference list" />
      </div>
    );
  }

  if (references.length === 0) {
    return (
      <Empty title="No references were recognised in this paper">
        The reference section may use a format the splitter did not recognise,
        or the paper may not have one. In-text citations will not resolve.
      </Empty>
    );
  }

  return (
    <div>
      <Card className="mb-4 p-3">
        <div className="flex flex-wrap items-end gap-2">
          <TextInput
            type="search"
            value={filter}
            onChange={setFilter}
            label={`${references.length} references, ${addable} with a downloadable PDF`}
            placeholder="Filter by title or author"
            className="min-w-[240px] flex-1"
          />
          <Button onClick={() => load(true)} disabled={resolving || unresolved === 0}>
            {resolving
              ? "Looking up..."
              : unresolved === 0
                ? "All resolved"
                : `Look up ${unresolved} against arXiv and Crossref`}
          </Button>
        </div>
        <p className="mt-1.5 text-xs text-ink-faint">
          Entries printing an arXiv id or a DOI resolved instantly. Looking up
          the rest makes one request each, so it is a button rather than
          automatic.
        </p>
      </Card>

      {error ? (
        <div className="mb-4">
          <ErrorNotice error={error} />
        </div>
      ) : null}

      {resolved ? (
        <div className="mb-4">
          <Notice tone="neutral">
            Resolved against arXiv and Crossref. A match needs 82 percent title
            overlap, so an entry that stayed unresolved found nothing close
            enough rather than nothing at all.
          </Notice>
        </div>
      ) : null}

      <SectionTitle>
        {shown.length === references.length
          ? "Reference list"
          : `${shown.length} of ${references.length}`}
      </SectionTitle>

      <ul className="space-y-2">
        {shown.map((reference) => (
          <ReferenceRow
            key={reference.element_id}
            reference={reference}
            paperId={paperId}
            onAdded={() => load(resolved)}
          />
        ))}
      </ul>
    </div>
  );
}
