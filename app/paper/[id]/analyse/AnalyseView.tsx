"use client";

/**
 * The lens board: every ready made way of reading the paper, run on demand.
 *
 * Results are cached server side per paper and lens, because a lens costs a
 * real model call and running it twice on an unchanged paper spends money to
 * say the same thing. Refresh is explicit.
 */

import { useCallback, useEffect, useState } from "react";

import { EvidenceCard, Markdown } from "@/components/AnswerBody";
import { useConfig, useHasLlm } from "@/components/ConfigProvider";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorNotice,
  Notice,
  SectionTitle,
  Spinner,
  cx,
} from "@/components/ui";
import { listPaperLenses, runLens } from "@/lib/api";
import type { Analysis, Citation, LensSpec } from "@/lib/types";

export function AnalyseView({ paperId }: { paperId: number }) {
  const { config, prefs } = useConfig();
  const hasLlm = useHasLlm();

  const [active, setActive] = useState<string>("");
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState("");
  const [done, setDone] = useState<Set<string>>(new Set());
  const [highlighted, setHighlighted] = useState<number | null>(null);

  useEffect(() => {
    listPaperLenses(paperId)
      .then((response) => setDone(new Set(response.analyses.map((entry) => entry.lens))))
      .catch(() => setDone(new Set()));
  }, [paperId]);

  const run = useCallback(
    async (lensId: string, refresh = false) => {
      setActive(lensId);
      setBusy(true);
      setError(null);
      setNote("");
      setAnalysis(null);
      setCitations([]);
      try {
        const response = await runLens(paperId, lensId, {
          provider: prefs.provider || undefined,
          model: prefs.model || undefined,
          refresh,
        });
        if (response.analysis) {
          setAnalysis(response.analysis);
          setCitations(response.analysis.citations ?? []);
          setDone((current) => new Set(current).add(lensId));
        } else {
          setNote(response.note ?? "");
        }
      } catch (exception) {
        setError(exception);
      } finally {
        setBusy(false);
      }
    },
    [paperId, prefs.provider, prefs.model],
  );

  const groups = config?.lenses ?? [];
  const activeSpec: LensSpec | undefined = groups
    .flatMap((group) => group.lenses)
    .find((lens) => lens.id === active);

  return (
    <div className="grid gap-5 lg:grid-cols-[300px_1fr]">
      <aside className="space-y-5 lg:sticky lg:top-16 lg:self-start">
        {!hasLlm ? (
          <Notice tone="warn" title="These need a language model">
            Each lens reads retrieved evidence and writes a structured analysis.
            Add a provider key in Settings. Retrieval and browsing work without one.
          </Notice>
        ) : null}

        {groups.map((group) => (
          <div key={group.id}>
            <SectionTitle>{group.label}</SectionTitle>
            <ul className="space-y-1">
              {group.lenses.map((lens) => (
                <li key={lens.id}>
                  <button
                    onClick={() => run(lens.id)}
                    disabled={busy || !hasLlm}
                    className={cx(
                      "w-full rounded-lg border px-2.5 py-2 text-left transition-colors disabled:opacity-50",
                      active === lens.id
                        ? "border-accent bg-accent-soft"
                        : "border-line hover:bg-raised",
                    )}
                  >
                    <span className="flex items-center gap-1.5">
                      <span
                        className={cx(
                          "text-[13px] font-medium",
                          active === lens.id ? "text-accent-ink" : "text-ink",
                        )}
                      >
                        {lens.label}
                      </span>
                      {done.has(lens.id) ? <Badge tone="good">run</Badge> : null}
                    </span>
                    <span className="mt-0.5 block text-[11px] leading-snug text-ink-faint">
                      {lens.description}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </aside>

      <div>
        {error ? <ErrorNotice error={error} /> : null}
        {note ? <Notice tone="warn">{note}</Notice> : null}

        {busy ? (
          <div className="py-20 text-center">
            <Spinner label={`Running ${activeSpec?.label ?? "the analysis"}`} />
            <p className="mt-2 text-xs text-ink-faint">
              Retrieving from several phrasings, then writing.
            </p>
          </div>
        ) : null}

        {!busy && !analysis && !note ? (
          <Empty title="Pick an analysis">
            Each one retrieves from the sections that actually answer it, then
            writes with citations you can check.
          </Empty>
        ) : null}

        {analysis ? (
          <div className="grid gap-4 xl:grid-cols-[1fr_300px]">
            <Card className="p-4">
              <div className="mb-3 flex flex-wrap items-center gap-2 border-b border-line pb-2.5">
                <h2 className="text-sm font-semibold text-ink">{activeSpec?.label}</h2>
                <Badge tone="accent">{analysis.model || analysis.provider}</Badge>
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-auto"
                  onClick={() => run(active, true)}
                >
                  Run again
                </Button>
              </div>
              <Markdown
                text={analysis.content}
                onCite={(n) => {
                  setHighlighted(n);
                  document
                    .getElementById(`evidence-${n}`)
                    ?.scrollIntoView({ behavior: "smooth", block: "center" });
                }}
              />
            </Card>

            <div>
              <SectionTitle>Evidence ({citations.length})</SectionTitle>
              <ul className="space-y-2">
                {citations.map((citation) => (
                  <EvidenceCard
                    key={citation.n}
                    citation={citation}
                    highlighted={highlighted === citation.n}
                  />
                ))}
              </ul>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
