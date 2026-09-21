"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AddByLink } from "@/components/AddByLink";
import { useConfig } from "@/components/ConfigProvider";
import { ReadStateBadge } from "@/components/Triage";
import { UploadPanel } from "@/components/UploadPanel";
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
  cx,
} from "@/components/ui";
import {
  createCollection,
  deleteCollection,
  deletePaper,
  exportLibraryUrl,
  listCollections,
  listPapers,
} from "@/lib/api";
import type { Collection, Paper, ReadState } from "@/lib/types";

const STATE_FILTERS: { id: ReadState | ""; label: string }[] = [
  { id: "", label: "All" },
  { id: "unread", label: "Unread" },
  { id: "reading", label: "Reading" },
  { id: "read", label: "Read" },
  { id: "rejected", label: "Rejected" },
];

function PaperCard({ paper, onDelete }: { paper: Paper; onDelete: (id: number) => void }) {
  const counts = paper.counts ?? {};
  const quality = paper.quality;

  return (
    <Card as="li" className="flex flex-col p-4 transition-shadow hover:shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <Link href={`/paper/${paper.id}`} className="min-w-0 flex-1">
          <h3 className="line-clamp-2 text-sm font-semibold leading-snug text-ink hover:text-accent">
            {paper.title || paper.filename}
          </h3>
        </Link>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <ReadStateBadge state={paper.read_state} />
          {paper.status !== "ready" ? (
            <Badge
              tone={paper.status === "failed" ? "danger" : "warn"}
              title={paper.status_detail}
            >
              {paper.status}
            </Badge>
          ) : null}
        </div>
      </div>

      {paper.authors.length ? (
        <p className="mt-1 line-clamp-1 text-xs text-ink-faint">
          {paper.authors.slice(0, 4).join(", ")}
          {paper.authors.length > 4 ? " and others" : ""}
        </p>
      ) : null}

      {/* The verdict displaces the abstract once there is one: it is what you
          actually wanted to remember about this paper. */}
      {paper.verdict ? (
        <p className="mt-2 border-l-2 border-accent pl-2 text-[13px] leading-relaxed text-ink">
          {paper.verdict}
        </p>
      ) : paper.abstract ? (
        <p className="mt-2 line-clamp-3 text-[13px] leading-relaxed text-ink-muted">
          {paper.abstract}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-faint">
        <span>{paper.num_pages} pages</span>
        {counts.figure ? <span>· {counts.figure} figures</span> : null}
        {counts.table ? <span>· {counts.table} tables</span> : null}
        {counts.formula ? <span>· {counts.formula} equations</span> : null}
        {paper.year ? <span>· {paper.year}</span> : null}
        {quality && quality.grade !== "good" ? (
          <Badge
            tone={quality.grade === "poor" ? "danger" : "warn"}
            title={quality.concerns.map((c) => c.message).join("\n")}
          >
            parse {quality.grade}
          </Badge>
        ) : null}
        {!paper.dense_signature ? (
          <Badge tone="neutral" title="Keyword search only. Re-index with embeddings for semantic search.">
            keyword only
          </Badge>
        ) : null}
      </div>

      <div className="mt-3 flex items-center gap-1.5 border-t border-line pt-3">
        <Link href={`/paper/${paper.id}`}>
          <Button size="sm" variant="primary">
            Open
          </Button>
        </Link>
        <Link href={`/paper/${paper.id}/elements`}>
          <Button size="sm">All views</Button>
        </Link>
        <Link href={`/paper/${paper.id}/ask`}>
          <Button size="sm">Ask</Button>
        </Link>
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          onClick={() => onDelete(paper.id)}
          title="Remove from the library"
        >
          Remove
        </Button>
      </div>
    </Card>
  );
}

export function LibraryView() {
  const router = useRouter();
  const { config, loading: configLoading, error: configError } = useConfig();

  const [papers, setPapers] = useState<Paper[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collectionId, setCollectionId] = useState<number | undefined>(undefined);
  const [readState, setReadState] = useState<ReadState | "">("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [filter, setFilter] = useState("");
  const [newCollection, setNewCollection] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([listPapers({ collection_id: collectionId }), listCollections()])
      .then(([nextPapers, nextCollections]) => {
        setPapers(nextPapers);
        setCollections(nextCollections);
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [collectionId]);

  useEffect(load, [load]);

  const remove = useCallback(async (id: number) => {
    if (!window.confirm("Remove this paper and everything extracted from it?")) return;
    try {
      await deletePaper(id);
      setPapers((current) => current.filter((paper) => paper.id !== id));
    } catch (exception) {
      setError(exception);
    }
  }, []);

  async function addCollection() {
    const name = newCollection.trim();
    if (!name) return;
    setCreating(true);
    try {
      const created = await createCollection(name);
      setNewCollection("");
      setCollections((current) => [...current, created]);
      setCollectionId(created.id);
    } catch (exception) {
      setError(exception);
    } finally {
      setCreating(false);
    }
  }

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return papers.filter((paper) => {
      if (readState && paper.read_state !== readState) return false;
      if (!needle) return true;
      return (
        paper.title.toLowerCase().includes(needle) ||
        paper.abstract.toLowerCase().includes(needle) ||
        paper.verdict.toLowerCase().includes(needle) ||
        paper.authors.join(" ").toLowerCase().includes(needle)
      );
    });
  }, [papers, filter, readState]);

  return (
    <div className="py-6">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-ink">{config?.app_name ?? "Dissect"}</h1>
        <p className="mt-1 text-sm text-ink-faint">
          {config?.tagline ?? "Reading a paper properly, without reading all of it."}
        </p>
      </div>

      {configError ? (
        <div className="mb-4">
          <Notice tone="danger" title="Could not reach the backend">
            {configError}. Start it with:{" "}
            <code className="rounded bg-raised px-1">npm run api</code>
          </Notice>
        </div>
      ) : null}

      {config && !config.storage.persistent ? (
        <div className="mb-4">
          <Notice tone="warn" title="Uploads will not survive a restart here">
            {config.storage.reason}
          </Notice>
        </div>
      ) : null}

      {collections.length > 0 ? (
        <div className="mb-4 flex flex-wrap items-center gap-1.5">
          <button
            onClick={() => setCollectionId(undefined)}
            className={cx(
              "rounded-lg px-2.5 py-1 text-xs transition-colors",
              collectionId === undefined
                ? "bg-accent text-white"
                : "border border-line text-ink-muted hover:bg-raised",
            )}
          >
            Everything
          </button>
          {collections.map((collection) => (
            <span key={collection.id} className="inline-flex items-center">
              <button
                onClick={() => setCollectionId(collection.id)}
                className={cx(
                  "rounded-lg px-2.5 py-1 text-xs transition-colors",
                  collectionId === collection.id
                    ? "bg-accent text-white"
                    : "border border-line text-ink-muted hover:bg-raised",
                )}
              >
                {collection.name}
                {collection.paper_count ? ` (${collection.paper_count})` : ""}
              </button>
              {collectionId === collection.id && collections.length > 1 ? (
                <button
                  onClick={async () => {
                    if (
                      !window.confirm(
                        `Delete "${collection.name}" and every paper in it?`,
                      )
                    )
                      return;
                    await deleteCollection(collection.id);
                    setCollectionId(undefined);
                    load();
                  }}
                  title="Delete this collection and its papers"
                  className="ml-1 text-xs text-ink-faint hover:text-danger"
                >
                  ×
                </button>
              ) : null}
            </span>
          ))}
          <span className="ml-1 inline-flex items-center gap-1">
            <input
              value={newCollection}
              onChange={(event) => setNewCollection(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void addCollection();
              }}
              placeholder="New collection"
              className="w-32 rounded-lg border border-line bg-surface px-2 py-1 text-xs"
            />
            {newCollection.trim() ? (
              <Button size="sm" onClick={addCollection} disabled={creating}>
                Add
              </Button>
            ) : null}
          </span>
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <div>
          <SectionTitle
            action={
              papers.length ? (
                <div className="flex items-center gap-1.5">
                  <a href={exportLibraryUrl(collectionId)} download>
                    <Button size="sm" variant="ghost">
                      Reading list
                    </Button>
                  </a>
                  <TextInput
                    type="search"
                    value={filter}
                    onChange={setFilter}
                    placeholder="Filter"
                    className="w-44"
                  />
                </div>
              ) : null
            }
          >
            {papers.length ? `${shown.length} of ${papers.length} papers` : "Library"}
          </SectionTitle>

          {papers.length > 1 ? (
            <div className="mb-3 flex flex-wrap gap-1">
              {STATE_FILTERS.map((entry) => {
                const count =
                  entry.id === ""
                    ? papers.length
                    : papers.filter((p) => p.read_state === entry.id).length;
                if (entry.id !== "" && count === 0) return null;
                return (
                  <button
                    key={entry.id || "all"}
                    onClick={() => setReadState(entry.id)}
                    className={cx(
                      "rounded-md px-2 py-1 text-[11px]",
                      readState === entry.id
                        ? "bg-raised font-medium text-ink"
                        : "text-ink-faint hover:bg-raised",
                    )}
                  >
                    {entry.label} {count}
                  </button>
                );
              })}
            </div>
          ) : null}

          {error ? <ErrorNotice error={error} /> : null}

          {loading || configLoading ? (
            <div className="py-10 text-center">
              <Spinner label="Loading the library" />
            </div>
          ) : shown.length === 0 ? (
            <Empty title={papers.length ? "Nothing matches" : "No papers yet"}>
              {papers.length
                ? "Try a different word or clear the filters."
                : "Add a PDF or paste an arXiv id. Everything works with no API key: extraction, browsing and search are all local."}
            </Empty>
          ) : (
            <ul className="grid items-start gap-3 sm:grid-cols-2">
              {shown.map((paper) => (
                <PaperCard key={paper.id} paper={paper} onDelete={remove} />
              ))}
            </ul>
          )}
        </div>

        <div className="lg:sticky lg:top-16 lg:self-start">
          <SectionTitle>Add a paper</SectionTitle>
          <UploadPanel
            collectionId={collectionId}
            onDone={(paperId) => {
              load();
              router.push(`/paper/${paperId}`);
            }}
            extra={
              <AddByLink
                collectionId={collectionId}
                onDone={(paperId) => {
                  load();
                  router.push(`/paper/${paperId}`);
                }}
              />
            }
          />

          {papers.length > 1 ? (
            <div className="mt-4">
              <Link href="/ask">
                <Button className="w-full">
                  Ask or compare across {papers.length} papers
                </Button>
              </Link>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
