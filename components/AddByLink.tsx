"use client";

/**
 * Adding a paper from an arXiv id, a DOI or a URL.
 *
 * Every paper you meet is a link, and requiring the file on disk first was the
 * largest piece of friction in the tool. What is pasted is identified as you
 * type, without downloading anything, so the box can say what it thinks it has
 * before you commit to fetching it.
 */

import { useCallback, useEffect, useState } from "react";

import { fetchPaper, identifySource } from "@/lib/api";
import type { Identified } from "@/lib/types";

import { useConfig } from "./ConfigProvider";
import { Badge, Button, ErrorNotice, Spinner, TextInput } from "./ui";

export function AddByLink({
  collectionId,
  onDone,
}: {
  collectionId?: number;
  onDone: (paperId: number) => void;
}) {
  const { prefs } = useConfig();
  const [source, setSource] = useState("");
  const [identified, setIdentified] = useState<Identified | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // Identify as the user types, debounced. It is a local parse on the server
  // with no network of its own, so it is cheap enough to run on every pause.
  useEffect(() => {
    const value = source.trim();
    if (value.length < 4) {
      setIdentified(null);
      return;
    }
    const timer = window.setTimeout(() => {
      identifySource(value)
        .then(setIdentified)
        .catch(() => setIdentified(null));
    }, 350);
    return () => window.clearTimeout(timer);
  }, [source]);

  const add = useCallback(async () => {
    const value = source.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetchPaper(value, {
        collection_id: collectionId,
        parse_mode: prefs.parseMode,
        dense: prefs.dense,
        ocr: prefs.ocr,
      });
      setSource("");
      setIdentified(null);
      onDone(response.paper.id);
    } catch (exception) {
      setError(exception);
    } finally {
      setBusy(false);
    }
  }, [source, collectionId, prefs, onDone]);

  return (
    <div>
      <div className="flex flex-wrap items-end gap-2">
        <TextInput
          value={source}
          onChange={setSource}
          onEnter={add}
          label="Or paste an arXiv id, a DOI or a link"
          placeholder="2407.01449"
          className="min-w-[200px] flex-1"
        />
        <Button variant="primary" onClick={add} disabled={busy || !source.trim()}>
          {busy ? "Fetching..." : "Fetch"}
        </Button>
      </div>

      {busy ? (
        <div className="mt-2">
          <Spinner label="Downloading, parsing and indexing" />
        </div>
      ) : identified ? (
        <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-faint">
          <Badge tone="accent">{identified.kind}</Badge>
          {identified.arxiv_id ? <span>arXiv:{identified.arxiv_id}</span> : null}
          {identified.doi ? <span>{identified.doi}</span> : null}
          {identified.kind === "doi" ? (
            <span>
              A DOI is fetched only when the publisher registered a free PDF.
            </span>
          ) : null}
        </p>
      ) : (
        <p className="mt-1.5 text-[11px] text-ink-faint">
          Works with 2407.01449, arXiv:1706.03762, a doi.org link, or any link
          ending in .pdf.
        </p>
      )}

      {error ? (
        <div className="mt-2">
          <ErrorNotice error={error} />
        </div>
      ) : null}
    </div>
  );
}
