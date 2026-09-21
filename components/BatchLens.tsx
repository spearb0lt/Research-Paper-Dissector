"use client";

/**
 * Running one lens over many papers.
 *
 * This is what turns a lens from a thing you do to a paper into a thing you do
 * to a literature. "Limitations" across ten papers, side by side, is a piece of
 * work that otherwise takes an afternoon.
 *
 * Results arrive one at a time as the server finishes each paper, so a run over
 * ten papers is readable from the first one rather than after all ten. A paper
 * that already has this lens cached returns instantly and says so, which means
 * re-running over a library costs only the papers that are new.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { runBatchLens } from "@/lib/api";
import type { Analysis, BatchEvent, LensGroup } from "@/lib/types";

import { Markdown } from "./AnswerBody";
import { useConfig, useHasLlm } from "./ConfigProvider";
import {
  Badge,
  Button,
  Card,
  ErrorNotice,
  Notice,
  ProgressBar,
  SectionTitle,
  Select,
  Toggle,
} from "./ui";

type Row =
  | { state: "running"; paperId: number; title: string }
  | { state: "done"; paperId: number; title: string; cached: boolean; analysis: Analysis }
  | { state: "failed"; paperId: number; title: string; error: string };

export function BatchLens({ paperIds }: { paperIds: number[] }) {
  const { config, prefs } = useConfig();
  const hasLlm = useHasLlm();

  const [lens, setLens] = useState("");
  const [refresh, setRefresh] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [progress, setProgress] = useState({ index: 0, total: 0 });
  const [error, setError] = useState<unknown>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  // Only the per paper lenses. A comparison lens already reads every paper at
  // once, so running it per paper would be the same work done wrong.
  const groups: LensGroup[] = config?.lenses ?? [];

  const run = useCallback(async () => {
    if (!lens || !paperIds.length) return;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;

    setBusy(true);
    setError(null);
    setRows([]);
    setProgress({ index: 0, total: paperIds.length });

    try {
      await runBatchLens(
        {
          paper_ids: paperIds,
          lens,
          provider: prefs.provider || undefined,
          model: prefs.model || undefined,
          refresh,
        },
        (event: BatchEvent) => {
          if (event.type === "progress") {
            setProgress({ index: event.index, total: event.total });
            setRows((current) => [
              ...current,
              { state: "running", paperId: event.paper_id, title: event.title },
            ]);
          } else if (event.type === "result") {
            setRows((current) =>
              current.map((row) =>
                row.paperId === event.paper_id
                  ? {
                      state: "done",
                      paperId: event.paper_id,
                      title: event.title,
                      cached: event.cached,
                      analysis: event.analysis,
                    }
                  : row,
              ),
            );
          } else if (event.type === "failed") {
            setRows((current) =>
              current.map((row) =>
                row.paperId === event.paper_id
                  ? {
                      state: "failed",
                      paperId: event.paper_id,
                      title: event.title,
                      error: event.error,
                    }
                  : row,
              ),
            );
          }
        },
        controller.signal,
      );
    } catch (exception) {
      if (!controller.signal.aborted) setError(exception);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }, [lens, paperIds, prefs, refresh]);

  const finished = rows.filter((row) => row.state !== "running").length;

  return (
    <div>
      <Card className="p-3">
        <SectionTitle>Run one analysis over every selected paper</SectionTitle>

        {!hasLlm ? (
          <Notice tone="warn">
            This needs a language model. Add a provider key in Settings.
          </Notice>
        ) : null}

        <div className="flex flex-wrap items-end gap-2">
          <Select
            label="Analysis"
            value={lens}
            onChange={setLens}
            className="min-w-[220px] flex-1"
            options={[
              { value: "", label: "Pick one..." },
              ...groups.flatMap((group) =>
                group.lenses.map((entry) => ({
                  value: entry.id,
                  label: `${group.label}: ${entry.label}`,
                })),
              ),
            ]}
          />
          <Button variant="primary" onClick={run} disabled={busy || !lens || !hasLlm}>
            {busy ? "Running..." : `Run on ${paperIds.length} paper${paperIds.length === 1 ? "" : "s"}`}
          </Button>
          {busy ? (
            <Button
              variant="ghost"
              onClick={() => {
                abort.current?.abort();
                setBusy(false);
              }}
            >
              Stop
            </Button>
          ) : null}
        </div>

        <div className="mt-2">
          <Toggle
            checked={refresh}
            onChange={setRefresh}
            label="Re-run papers that already have this analysis"
            hint="Off, a cached result returns instantly and costs nothing, so running over a library only pays for what is new."
          />
        </div>

        {busy || progress.total ? (
          <div className="mt-3">
            <ProgressBar
              value={progress.total ? finished / progress.total : 0}
              label={`${finished} of ${progress.total} done`}
            />
          </div>
        ) : null}
      </Card>

      {error ? (
        <div className="mt-3">
          <ErrorNotice error={error} />
        </div>
      ) : null}

      {rows.length ? (
        <ul className="mt-4 space-y-3">
          {rows.map((row) => (
            <Card as="li" key={row.paperId} className="p-3">
              <div className="mb-2 flex flex-wrap items-center gap-2 border-b border-line pb-2">
                <span className="text-sm font-medium text-ink">{row.title}</span>
                {row.state === "running" ? (
                  <Badge tone="warn">working</Badge>
                ) : row.state === "done" ? (
                  <>
                    {row.cached ? (
                      <Badge title="Already had this analysis. Nothing was spent.">cached</Badge>
                    ) : (
                      <Badge tone="accent">{row.analysis.model || row.analysis.provider}</Badge>
                    )}
                  </>
                ) : (
                  <Badge tone="danger">failed</Badge>
                )}
              </div>

              {row.state === "done" ? (
                <Markdown text={row.analysis.content} />
              ) : row.state === "failed" ? (
                <p className="text-[13px] text-danger">{row.error}</p>
              ) : (
                <p className="text-[13px] text-ink-faint">Retrieving and writing...</p>
              )}
            </Card>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
